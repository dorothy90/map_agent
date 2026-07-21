"""
Agent Server — LangGraph Supervisor FastAPI Backend
====================================================
LangGraph yield_supervisor를 직접 실행하고 SSE로 스트리밍합니다.
React 프론트엔드 대응: 타입별 분리된 SSE 이벤트 (message / artifact / suggestion).

실행: uvicorn 08-YieldAgent.agent_server:app --port 8001
  또는 (08-YieldAgent 디렉터리 내): uvicorn agent_server:app --port 8001
"""

import asyncio
import json
import logging
from pydantic import BaseModel
import os
import traceback
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from langchain_core.messages import HumanMessage
from langfuse import get_client, observe
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.types import Command, Overwrite
from motor.motor_asyncio import AsyncIOMotorClient

# 이 파일의 디렉터리(08-YieldAgent/)를 sys.path에 추가 (로컬 모듈 임포트용)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from models import (  # noqa: E402
    ArtifactData,
    ArtifactEvent,
    ArtifactType,
    ChatRequest,
    ErrorEvent,
    HistoryMessage,
    InterruptEvent,
    MessageEvent,
    NodeCompleteEvent,
    SessionHistory,
    SessionSummary,
    StatusEvent,
    StreamEndEvent,
    StreamStartEvent,
    SuggestionEvent,
    ThinkingEvent,
    TokenEvent,
)
from common import to_user_message  # noqa: E402
from local_trace import (  # noqa: E402
    configure_runtime_terminal_logger,
    emit_runtime_detail,
    emit_trace_event,
    make_trace_id,
    new_turn_id,
    preview_text,
    reset_trace_context,
    set_trace_context,
)
from supervisor import workflow, _resume_is_interrupt_answer  # noqa: E402
from user_memory import update_profile_from_feedback  # noqa: E402
from control_knowledge_collector import (  # noqa: E402
    current_system_snapshot,
    incident_candidate,
    runtime_candidates,
    system_snapshot_candidates,
)
from control_knowledge_service import service_from_env  # noqa: E402

# 사용자 선호 메모리 백그라운드 flush 태스크 보관 (GC로 조기 소멸 방지)
_memory_tasks: set = set()
# control knowledge 제출 태스크 보관 (SSE 응답과 독립, shutdown 시 drain)
_control_knowledge_tasks: set[asyncio.Task] = set()
from repl_agent.router import router as repl_router  # noqa: E402

configure_runtime_terminal_logger()
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
))
_runtime_log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
for _name in ("agent_server", "yield_agent"):
    _lg = logging.getLogger(_name)
    _lg.setLevel(_runtime_log_level)
    if not _lg.handlers:
        _lg.addHandler(_handler)

logger = logging.getLogger("agent_server")
logger.info("terminal log level=%s", logging.getLevelName(_runtime_log_level))


async def _submit_control_candidates(service, candidates: list) -> None:
    for candidate in candidates:
        try:
            await service.submit(candidate)
        except Exception as exc:
            logger.warning(
                "[ControlKnowledge] candidate submission failed: %s",
                type(exc).__name__,
            )


def _schedule_control_submission(service, candidates: list) -> None:
    if not candidates:
        return
    task = asyncio.create_task(_submit_control_candidates(service, candidates))
    _control_knowledge_tasks.add(task)
    task.add_done_callback(_control_knowledge_tasks.discard)


def _pending_interrupt_from_state(state_snapshot) -> dict:
    """Return the first pending LangGraph interrupt payload for local logs."""

    for task in getattr(state_snapshot, "tasks", []) or []:
        interrupts = getattr(task, "interrupts", []) or []
        if not interrupts:
            continue
        value = getattr(interrupts[-1], "value", None)
        if isinstance(value, dict):
            return value
    return {}

MONGO_URI = "mongodb://localhost:27017"
MONGO_DB = "yield_agent"


async def _wiki_lint_cron_loop(interval_hours: float) -> None:
    """daily lint 백그라운드 task. env WIKI_LINT_CRON_HOURS>0 시에만 시작.

    매 N시간마다 wiki_lint.scan() → wiki/lint_logs/YYYY-MM-DD.md 누적 저장.
    """
    import wiki_lint
    import wiki_store
    interval_s = max(60.0, interval_hours * 3600.0)
    while True:
        try:
            await asyncio.sleep(interval_s)
            vault = wiki_store._VAULT
            issues = wiki_lint.scan(vault)
            total = sum(len(v) for v in issues.values())
            today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
            log_dir = vault / "lint_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{today}.md"
            lines = [f"# Wiki lint (auto cron) — {today}", "",
                     f"TOTAL ISSUES: **{total}** (vault=`{vault}`)", ""]
            for kind, items in issues.items():
                if not items:
                    continue
                lines.append(f"## {kind} ({len(items)})")
                lines.append("")
                for it in items:
                    lines.append(f"- {it}")
                lines.append("")
            log_path.write_text("\n".join(lines), encoding="utf-8")
            logger.info("[wiki_lint cron] total=%d → %s", total, log_path)
        except asyncio.CancelledError:
            logger.info("[wiki_lint cron] cancelled")
            return
        except Exception as e:
            logger.warning("[wiki_lint cron] error: %s", e)


# ── FastAPI lifespan — MongoDB 연결 + wiki_queue 워커 관리 ─
@asynccontextmanager
async def lifespan(app: FastAPI):
    # motor (async) — 대화 이력 저장용
    motor_client = AsyncIOMotorClient(MONGO_URI)
    app.state.motor_db = motor_client[MONGO_DB]

    # wiki ingest 큐 워커 (Day 2 추가, plan v3 §wiki_queue.py)
    # Day 3: placeholder summarizer를 실제 LLM summarizer로 교체
    from wiki_queue import wiki_queue
    from wiki_summarizer import summarize as wiki_summarize_fn
    wiki_queue.set_summarizer(wiki_summarize_fn)
    await wiki_queue.start()
    app.state.wiki_queue = wiki_queue

    control_knowledge = service_from_env()
    await control_knowledge.start()
    app.state.control_knowledge = control_knowledge
    if control_knowledge.enabled:
        await _submit_control_candidates(
            control_knowledge,
            system_snapshot_candidates(current_system_snapshot()),
        )

    # Day 6: lint daily cron (WIKI_LINT_CRON_HOURS=0 또는 미설정 시 비활성)
    lint_hours = float(os.getenv("WIKI_LINT_CRON_HOURS", "0") or "0")
    lint_task: asyncio.Task | None = None
    if lint_hours > 0:
        lint_task = asyncio.create_task(_wiki_lint_cron_loop(lint_hours))
        logger.info("[wiki_lint cron] started, interval=%sh", lint_hours)
    app.state.wiki_lint_task = lint_task

    # MongoDBSaver (sync) — LangGraph 체크포인터
    with MongoDBSaver.from_conn_string(MONGO_URI, db_name=MONGO_DB) as checkpointer:
        app.state.graph = workflow.compile(checkpointer=checkpointer)
        logger.info("MongoDB 체크포인터 + motor 연결 완료 (%s/%s)", MONGO_URI, MONGO_DB)
        try:
            yield
        finally:
            if lint_task is not None:
                lint_task.cancel()
                try:
                    await lint_task
                except (asyncio.CancelledError, Exception):
                    pass
            if _control_knowledge_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(
                            *list(_control_knowledge_tasks), return_exceptions=True
                        ),
                        timeout=10,
                    )
                except asyncio.TimeoutError:
                    logger.warning("[ControlKnowledge] submission drain timeout")
            await control_knowledge.stop(timeout=10)
            await wiki_queue.stop(timeout=10)

    motor_client.close()
    logger.info("MongoDB 연결 종료")


app = FastAPI(title="Yield Agent Server", lifespan=lifespan)

# ── CORS — dev React server + 사내 wiki 프론트 (cross-origin) ─────────────────
# 운영: 백엔드(:8001)와 프론트(사내 별도 호스트)가 독립 서버 → cross-origin 호출.
# WIKI_FRONTEND_ORIGINS=https://wiki.사내,https://other.사내 처럼 콤마 구분으로 추가.
_dev_origins = ["http://localhost:3000", "http://localhost:5173"]
_extra_origins = [
    o.strip() for o in os.getenv("WIKI_FRONTEND_ORIGINS", "").split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_dev_origins + _extra_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── REPL 검증 agent 라우터 마운트 (yield-agent 와 독립) ────
# plan: ~/.claude/plans/reactive-shimmying-lake.md — "단일 서버 확장" 원칙.
# repl_agent 패키지는 기존 supervisor/graph 와 import 의존이 없다.
app.include_router(repl_router, prefix="/repl", tags=["repl"])

# ── Wiki vault graph endpoint (Day 5) ────────────────────
from wiki_router import router as wiki_router  # noqa: E402

app.include_router(wiki_router, prefix="/api/wiki", tags=["wiki"])


_AGENT_META: dict[str, tuple[str, int, int]] = {
    "yield_agent":         ("PT1H/PT1C 수율 통계 조회 및 이상 감지", 5,  10),
    "wads_agent":          ("WADS 열화 파라미터 리포트 조회",        10, 20),
    "map_agent":           ("웨이퍼 불량 맵 시각화",                  5,  10),
    "fail_history_agent":  ("Fail 이력 OpenSearch 검색",              5,  10),
    "ppt_export":          ("분석 결과 PPT 생성",                    20, 30),
    "lot_history_agent":   ("LOT 이력 조회",                         10, 15),
    "relation_tree_agent": ("연관 LOT 관계 트리 분석",               10, 20),
}


def _build_plan_review_message(tasks: list[dict], missing_params: list[dict] | None = None) -> str:
    missing_set = {m["param"] for m in (missing_params or [])}
    missing_task_ids = {m["task_id"] for m in (missing_params or [])}

    total_lo = total_hi = 0
    blocks: list[str] = []

    for i, t in enumerate(tasks, 1):
        agent = t.get("agent", "")
        goal = t.get("goal", "")
        task_id = t.get("task_id", "")
        params: dict = t.get("params") or {}

        desc, lo, hi = _AGENT_META.get(agent, (agent, 5, 10))
        total_lo += lo
        total_hi += hi

        def _is_ph(v) -> bool:
            s = str(v).strip() if v is not None else ""
            return not s or s.startswith("<") or "task_" in s.lower() or "{{" in s

        clean = {k: v for k, v in params.items() if not _is_ph(v)}
        # 진짜 누락(state에도 없고 앞 task 제공도 없음) vs 진짜 chaining 구분
        genuinely_missing = [
            k for k, v in params.items()
            if _is_ph(v) and task_id in missing_task_ids and k in missing_set
        ]
        chained = [
            k for k, v in params.items()
            if _is_ph(v) and k not in genuinely_missing
        ]

        lines = [f"**{i}단계** `{agent}` — {desc}"]
        lines.append(f"  - 목표: {goal}")
        if clean:
            lines.append("  - 파라미터: " + ", ".join(f"{k}={v}" for k, v in clean.items()))
        if genuinely_missing:
            lines.append(f"  - ⚠️ 미입력: {', '.join(genuinely_missing)}")
        if chained:
            lines.append(f"  - ↳ {i-1}단계 결과 자동 연결: {', '.join(chained)}")
        elif i > 1 and not genuinely_missing:
            lines.append(f"  - ↳ {i-1}단계 완료 후 순차 실행")
        lines.append(f"  - 예상 소요: {lo}~{hi}초")
        blocks.append("\n".join(lines))

    header = (
        f"**분석 계획을 확인해주세요**"
        f" (총 {len(tasks)}개 작업 · 예상 {total_lo}~{total_hi}초)\n\n"
    )
    if missing_set:
        warning = (
            f"⚠️ **필수 파라미터 미입력**: {', '.join(sorted(missing_set))}\n"
            "수정 입력란에 포함해주세요. (예: '4SS로 분석해줘')\n\n"
        )
        footer = "\n\n제품코드 등 필수 파라미터를 포함하여 입력해주세요 (예: '4SS로 진행') | '취소' 입력 시 중단"
    else:
        warning = ""
        footer = "\n\n그대로 실행하려면 아무 내용이나 입력 | 수정이 필요하면 직접 입력 (예: 'WADS 기간 1주일로') | '취소' 입력 시 중단"
    return warning + header + "\n\n".join(blocks) + footer


def _sse(event: dict | object) -> str:
    """Pydantic model 또는 dict → SSE data line"""
    if hasattr(event, "model_dump"):
        payload = event.model_dump()
    else:
        payload = event
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _interrupt_sse_events(interrupt_data: dict) -> list[str]:
    """interrupt payload → SSE 라인 리스트. in-loop(astream의 __interrupt__ 청크)와
    post-loop(aget_state) 양쪽에서 동일 포맷으로 emit하기 위해 공유한다."""
    if not isinstance(interrupt_data, dict):
        return []
    if interrupt_data.get("type") == "plan_review":
        tasks = interrupt_data.get("tasks", [])
        missing_params = interrupt_data.get("missing_params", [])
        return [
            _sse(MessageEvent(
                agent="plan_review",
                content=_build_plan_review_message(tasks, missing_params),
            )),
            _sse(InterruptEvent(
                interrupt_type="plan_review",
                param="plan_review",
                message="분석 계획을 승인하시겠습니까?",
                route="",
            )),
        ]
    return [
        _sse(InterruptEvent(
            interrupt_type=interrupt_data.get(
                "interrupt_type", interrupt_data.get("type", "missing_param")
            ),
            param=interrupt_data.get("param", ""),
            message=interrupt_data.get("message", ""),
            route=interrupt_data.get("route", ""),
            options=interrupt_data.get("options", []),
            fields=interrupt_data.get("fields", []),
        )),
    ]


def _detect_artifact_type(data: str) -> ArtifactType:
    """아티팩트 데이터에서 타입 추론"""
    if not data:
        return ArtifactType.html
    stripped = data.strip()
    if stripped.startswith("<") or stripped.startswith("<!"):
        return ArtifactType.html
    if stripped.startswith("data:image") or stripped.endswith((".png", ".jpg", ".svg")):
        return ArtifactType.image
    return ArtifactType.html


SIMULATED_STREAM_DELAY = 0.02  # 20ms per chunk


def _chunk_text(text: str, min_size: int = 3) -> list[str]:
    """텍스트를 자연스러운 경계에서 청크로 분할 (토큰 스트리밍 시뮬레이션용)"""
    chunks, buf = [], []
    for ch in text:
        buf.append(ch)
        if len(buf) >= min_size and ch in (' ', '\n', '.', ',', '!', '?', ':', ';', ')', '」'):
            chunks.append("".join(buf))
            buf = []
    if buf:
        chunks.append("".join(buf))
    return chunks


# ── 헬스체크 ─────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}


# ── Mining TAS 트리거 (stub) ─────────────────────────────
# mining gini 표의 TAS 버튼이 그 행의 4필드를 보내 호출. 지금은 접수+에코 stub —
# 실제 분석/타 api 호출은 본문만 추후 교체(경계 유지). 프런트는 /mining 프록시 경유.
class TasRequest(BaseModel):
    lotcd: str
    oper_det_desc: str
    key_value: str
    fail_name: str


@app.post("/mining/tas")
async def mining_tas(body: TasRequest):
    logger.info(
        "[TAS] lotcd=%s oper_det_desc=%s key_value=%s fail_name=%s",
        body.lotcd,
        body.oper_det_desc,
        body.key_value,
        body.fail_name,
    )
    # TODO: 실제 TAS 분석/타 api 트리거를 여기에 연결.
    return {
        "status": "accepted",
        "received": body.model_dump(),
        "message": f"TAS 접수: {body.oper_det_desc} / {body.key_value}",
    }


# ── PPTX 파일 다운로드 ───────────────────────────────────
@app.get("/download/pptx/{filename}")
async def download_pptx(filename: str):
    """생성된 PPTX 파일 다운로드 엔드포인트"""
    generated_dir = Path(__file__).resolve().parent / "generated"
    file_path = generated_dir / filename
    if not file_path.exists():
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": "파일을 찾을 수 없습니다."})
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


# ── 세션 삭제 ─────────────────────────────────────────────
@app.delete("/session/{session_id}")
async def delete_session(session_id: str, request: Request):
    graph = request.app.state.graph
    try:
        await graph.checkpointer.adelete_thread(session_id)
    except Exception as e:
        logger.warning("세션 삭제 실패: %s", e)

    # chat_turns도 삭제
    db = request.app.state.motor_db
    await db.chat_turns.delete_many({"session_id": session_id})
    return {"deleted": session_id}


# ── 새 세션 생성 ─────────────────────────────────────────
@app.post("/session")
async def create_session():
    session_id = str(uuid.uuid4())
    return {"session_id": session_id}


# ── 세션 목록 ─────────────────────────────────────────────
@app.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(request: Request):
    db = request.app.state.motor_db
    pipeline = [
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": "$session_id",
            "last_query": {"$first": "$query"},
            "turn_count": {"$sum": 1},
            "updated_at": {"$first": "$timestamp"},
        }},
        {"$sort": {"updated_at": -1}},
        {"$limit": 50},
    ]
    results = []
    async for doc in db.chat_turns.aggregate(pipeline):
        results.append(SessionSummary(
            session_id=doc["_id"],
            last_query=doc.get("last_query", ""),
            turn_count=doc.get("turn_count", 0),
            updated_at=doc.get("updated_at", datetime.now(timezone.utc)),
        ))
    return results


# ── 세션 대화 이력 조회 ──────────────────────────────────
@app.get("/session/{session_id}/history", response_model=SessionHistory)
async def get_session_history(session_id: str, request: Request):
    db = request.app.state.motor_db
    turns: list[HistoryMessage] = []
    async for doc in db.chat_turns.find(
        {"session_id": session_id},
        {"_id": 0},
    ).sort("timestamp", 1):
        # user turn
        turns.append(HistoryMessage(
            role="user",
            content=doc.get("query", ""),
            timestamp=doc.get("timestamp", datetime.now(timezone.utc)),
        ))
        # assistant turns
        for msg in doc.get("messages", []):
            artifacts = [ArtifactData(**a) for a in msg.get("artifacts", [])]
            turns.append(HistoryMessage(
                role="assistant",
                agent=msg.get("agent", ""),
                content=msg.get("content", ""),
                artifacts=artifacts,
                suggestion=msg.get("suggestion", ""),
                timestamp=doc.get("timestamp", datetime.now(timezone.utc)),
            ))
    return SessionHistory(session_id=session_id, turns=turns)


# ── SSE 스트리밍 ──────────────────────────────────────────
@app.post("/chat/stream")
async def chat_stream(request: ChatRequest, req: Request):
    graph = req.app.state.graph
    db = req.app.state.motor_db
    # #23 fix: 5-task plan 처리 시 노드 호출 횟수가 ~12회 (rewrite + planner + supervisor×6 + agents×5)
    # → limit 20은 빠듯. interrupt resume이나 미래 replanner 추가 여유까지 30으로 상향.
    base_config = {"configurable": {"thread_id": request.session_id}, "recursion_limit": 30}
    trace_id = make_trace_id(request.session_id)
    turn_id = new_turn_id()
    pending_interrupt_for_trace: dict = {}
    if request.resume_value is not None:
        try:
            prev_state_for_trace = await graph.aget_state(base_config)
            pending_interrupt_for_trace = _pending_interrupt_from_state(prev_state_for_trace)
            previous_turn_id = (prev_state_for_trace.values or {}).get("turn_id", "")
            if previous_turn_id:
                turn_id = previous_turn_id
        except Exception:
            logger.warning("trace context resume state lookup failed", exc_info=True)
    config = {
        "configurable": {
            "thread_id": request.session_id,
            "trace_id": trace_id,
            "turn_id": turn_id,
        },
        "recursion_limit": 30,
    }

    # HITL 오인 가드: resume 입력이 대기 중 게이트의 '답'이 아니라 '새 질문'이면,
    # 게이트를 드롭(stale 작업 실행 방지)하고 fresh 턴으로 재처리한다. 자동 제안 게이트만 대상.
    # (missing_param/plan_review는 빈 응답이 재질문이라 드레인 부적합 → 제외.)
    if (
        request.resume_value is not None
        and pending_interrupt_for_trace.get("interrupt_type")
        in {"task_confirm", "postwads_choice"}
        and not _resume_is_interrupt_answer(request.resume_value, pending_interrupt_for_trace)
    ):
        logger.info(
            "[Resume] 새 의도 감지(pending=%s) → 게이트 드롭 후 fresh 재처리: %r",
            pending_interrupt_for_trace.get("interrupt_type"),
            preview_text(request.query),
        )
        try:
            await graph.ainvoke(Command(resume=""), config)  # 빈 응답→거절→제안 task 드롭→END
        except Exception as e:
            logger.warning("[Resume] 게이트 드롭 드레인 실패: %s", e)
        request.resume_value = None  # 아래 fresh 경로로 낙하 (request.query == 새 질문 텍스트)

    # resume 요청인 경우: interrupt에서 재개
    if request.resume_value is not None:
        stream_input = Command(resume=request.resume_value)
    else:
        # 이번 턴 입력: 새 HumanMessage + 퍼-턴 리셋 필드
        stream_input = {
            "trace_id": trace_id,
            "turn_id": turn_id,
            "messages": [HumanMessage(content=request.query)],
            # artifacts는 operator.add reducer → Overwrite로 퍼-턴 리셋
            "yield_artifacts": Overwrite([]),
            "wads_artifacts": Overwrite([]),
            "map_artifacts": Overwrite([]),
            "fail_history_artifacts": Overwrite([]),
            "ppt_artifacts": Overwrite([]),
            "lot_history_artifacts": Overwrite([]),
            "relation_tree_artifacts": Overwrite([]),
            "mining_artifacts": Overwrite([]),
            # past_steps도 매 turn reset (#8 phase 1) — 이전 turn의 task 결과는 다음 turn 라우팅에 무의미
            "past_steps": Overwrite([]),
            # step_count만 리셋 (퍼-턴 루프 카운터)
            "step_count": 0,
            # planner가 매 turn 덮어쓰지만 planner 실패 시 stale 방지
            "task_plan": [],
            "pending_tasks": [],
            "current_task": {},
            # ReferenceResolver v1 scratchpad (turn별 overwrite)
            "resolved_refs": {},
            "reference_issues": [],
            # Canonical request scratchpad (turn별 overwrite)
            "canonical_request": {},
            "canonical_requests": [],
            "canonical_trace": [],
            # Task normalizer/validator scratchpad (turn별 overwrite)
            "task_normalization_trace": [],
            "task_validation_issues": [],
            # HITL Gate scratchpad (turn별 overwrite)
            "hitl_issues": [],
            "hitl_responses": [],
            # 사용자 선호 학습 피드백 (operator.add reducer → Overwrite로 퍼-턴 리셋)
            "memory_feedback": Overwrite([]),
            # canonical plan-and-execute: 이전 턴의 종료 신호가 다음 턴으로 새지 않도록 리셋
            "response": "",
            # Day 4: wiki state는 turn별 overwrite (reducer 없음). 명시적 reset.
            "wiki_hit_ids": [],
            "wiki_update_status": "skipped",
            # 다음-턴 번호 선택 라우팅용 raw results (reducer 없음, turn별 overwrite)
            "fail_history_results": [],
            # anomaly_params는 매 새 사용자 turn 시 리셋 (#11 fix).
            # 동기: 이전 turn의 anomaly가 supervisor 라우팅 프롬프트(supervisor.py:409-414)에
            #       매번 "[이전 분석 결과]"로 주입되어 다른 product 분석에까지 stale 컨텍스트가
            #       누적되는 cross-product 오염을 차단.
            # Trade-off: 같은 product의 same-turn follow-up(예: T1 "4SS 수율(IOFF anomaly)" →
            #            T2 "WADS도")에서 supervisor 라우팅 LLM은 IOFF 힌트를 못 받음.
            #            그러나 yield_artifacts 내 anomaly 표시는 그대로 유지되고, wads_agent
            #            자체는 message history(IOFF가 포함된 yield 결과)를 보고 동작하므로
            #            최종 결과 영향은 미미함.
            # weeks_data, table_result, analysis_result는 리셋 안 함 — PPT follow-up에서 필요.
            "anomaly_params": [],
            # Step 5: WADS 검출 후속 선택 제안 가드 — 매 새 turn 리셋(resume에는 적용 안 됨).
            "postwads_offered": False,
            # relation_tree main_oper 선택 제안 가드 — 매 새 turn 리셋.
            "mainoper_offered": False,
            # (mining_offered 제거: wt_resp→mining은 confirm followup으로 이관 — guard_agents로 중복 차단)
            # wt_resp가 산출하는 양품/불량 그룹 — 매 새 turn 리셋(이전 turn 그룹 누수 방지).
            "group_good": [],
            "group_bad": [],
            # god-state 폐기: 의미 쿼리 슬롯은 턴 경계를 넘지 않는다 — 매 새 turn 리셋.
            # 이전 턴 fail_type가 다음 턴 워커에 stale하게 주입되던 멀티턴 버그 차단(예: IGATE(P) 잔존).
            # in-turn 상속은 followup default_slots, cross-turn은 planner 재도출. lotcd/ref_date는 세션 앵커라 제외.
            # (resume 경로엔 적용 안 됨 → interrupt 체인은 task.params로 컨텍스트 유지.)
            "fail_type": "",
            "cause_oper": "",
            "wads_category": "",
            # map_oper(PT1H/PT1C)도 동일 클래스 leak: map_agent가 결과로 state에 되돌려써(map_agent.py)
            # 다음 턴 무공정 맵 요청이 stale 공정을 묻지 않고 써버린다 → 매 턴 리셋. in-turn은
            # wads→map chaining(ResolveChained)·postwads map_slots가 task.params로 운반.
            "map_oper": "",
            # mining tech/rank_limit도 동일 클래스: mining이 결과로 되돌려써(mining_agent.py) 잔존 →
            # 매 턴 디폴트로 리셋(미지정="" / top-10). user_id는 멀티턴 '기억'이 아니라 신원이라
            # 매 요청에 프론트가 주입(request.user_id) — 기억이 아니라 재공급이라 stale 클래스 아님.
            "tech": "",
            "rank_limit": 10,
            "user_id": request.user_id,
            # lotcd/ref_date: 끈적한 god-state 앵커였음 → 매 턴 리셋(제품=재도출, 날짜=today 디폴트)로
            # cross-turn stale 차단. 같은 턴 내 god-state 공유(yield→ppt 라벨 등)는 dispatch persist가
            # 다시 채우므로 유지. cross-turn 제품 참조는 planner가 recent_results에서 재도출.
            "lotcd": "",
            "ref_date": datetime.now().strftime("%Y%m%d"),
            # 확인 대기 게이트 — 매 새 turn 리셋(미리셋 시 stale confirm 게이트가 턴 넘어 잔존).
            "confirm_tasks": {},
            # WADS report별 cummap fan-out 입력 — 매 새 turn 리셋(이전 turn 그룹 잔존 방지).
            "map_groups": [],
            # WADS report별 불량이력/연관분석 fan-out 입력 — 매 새 turn 리셋.
            "fail_groups": [],
            "rt_groups": [],
        }

        # 첫 번째 턴이면 나머지 기본값도 함께 전달 (#18 fix: YieldQueryState 전체 키 명시 init)
        prev_state = await graph.aget_state(config)
        if not (prev_state and prev_state.values):
            stream_input.update({
                "lotcd": "",
                "ref_date": "",
                "unit": "weekly",
                "periods": 0,
                "wads_start_tm": "",
                "wads_end_tm": "",
                "anomaly_params": [],
                "postwads_offered": False,
                "map_groups": [],
                "fail_groups": [],
                "rt_groups": [],
                "filter_params": [],
                "lot_ids":    [],
                "wf_ids":     [],
                "groupkey":   "",
                "fail_type":  "",
                "cause_oper": "",
                "map_type": "binmap",
                "map_oper": "",
                "map_result": "",
                "dh_query": "",
                "weeks_data": [],
                "table_result": "",
                "analysis_result": "",
                "agent_suggestion": "",
                "trace_id": trace_id,
                "turn_id": turn_id,
                # Derived resolver index only. Do not reset every turn; it is
                # bounded and refreshed from message ResultEnvelopes.
                "recent_results": [],
                "resolved_refs": {},
                "reference_issues": [],
                "canonical_request": {},
                "canonical_requests": [],
                "canonical_trace": [],
                "task_normalization_trace": [],
                "task_validation_issues": [],
                "hitl_issues": [],
                "hitl_responses": [],
                "memory_feedback": [],
                "task_plan": [],
                "pending_tasks": [],
                "current_task": {},
                "current_task_id": "",
                "current_task_goal": "",
                "response": "",
            })

    async def generate():
        trace_tokens = set_trace_context(trace_id, turn_id)
        start = time.time()
        step_count = 0
        turn_messages: list[dict] = []
        turn_artifacts: list[dict] = []
        turn_suggestion = ""

        try:
            # Langfuse trace 초기화
            try:
                lf = get_client()
                lf.set_current_trace_io(
                    input={"query": request.query, "session_id": request.session_id},
                )
            except Exception as exc:
                logger.debug("Langfuse trace init 실패 (무시): %s", exc)

            emit_trace_event(
                "user_turn_started",
                source="agent_server",
                payload={
                    "resume": request.resume_value is not None,
                    "question_preview": preview_text(request.query),
                    "resume_preview": preview_text(request.resume_value),
                    "pending_interrupt": pending_interrupt_for_trace,
                    "query_length": len(request.query or ""),
                    "session_hash": trace_id,
                },
            )
            emit_runtime_detail(
                "user_turn.input",
                {
                    "session_id": request.session_id,
                    "query": request.query,
                    "resume_value": request.resume_value,
                    "pending_interrupt": pending_interrupt_for_trace,
                    "stream_input_type": type(stream_input).__name__,
                },
            )

            # stream_start
            yield _sse(StreamStartEvent(
                session_id=request.session_id,
                query=request.query,
            ))

            interrupt_emitted = False
            try:
                # stream_mode=["updates", "custom"]:
                #   "updates" → 기존 node state 업데이트 (messages, artifacts 등)
                #   "custom"  → get_stream_writer()로 전송된 thinking/token/status 이벤트
                async for chunk in graph.astream(
                    stream_input, config=config, stream_mode=["updates", "custom"]
                ):
                    mode, data = chunk

                    # custom 이벤트: thinking/token/status — get_stream_writer()에서 직통
                    if mode == "custom":
                        kind = data.pop("kind", "")
                        emit_runtime_detail("stream.custom", {"kind": kind, "data": data})
                        if kind == "thinking":
                            yield _sse(ThinkingEvent(**data))
                        elif kind == "token":
                            yield _sse(TokenEvent(**data))
                        elif kind == "status":
                            yield _sse(StatusEvent(**data))
                        continue

                    # updates 이벤트: node state 업데이트 — 기존 처리 로직 유지
                    # interrupt() 발생 시 data가 dict가 아닐 수 있음 (tuple 등) → 스킵
                    if not isinstance(data, dict):
                        continue
                    # interrupt 청크: astream이 __interrupt__ 채널로 payload를 직접 흘려준다.
                    # post-loop aget_state는 무거운 노드(yield_agent) 직후 일시적으로 stale해
                    # gate interrupt를 놓칠 수 있으므로 in-loop에서 우선 emit한다 (post-loop는 fallback).
                    if "__interrupt__" in data:
                        for intr in data["__interrupt__"] or []:
                            for sse_line in _interrupt_sse_events(getattr(intr, "value", {})):
                                yield sse_line
                            interrupt_emitted = True
                        continue
                    for node_name, node_state in data.items():
                        # node_state가 dict가 아닌 경우 (interrupt 등) 스킵
                        if not isinstance(node_state, dict):
                            continue
                        emit_runtime_detail(
                            "graph.update",
                            {
                                "node": node_name,
                                "keys": sorted(node_state.keys()),
                                "current_task_id": node_state.get("current_task_id", ""),
                                "current_task_goal": node_state.get("current_task_goal", ""),
                                "pending_tasks": node_state.get("pending_tasks", []),
                                "task_plan": node_state.get("task_plan", []),
                                "validation_issues": node_state.get("task_validation_issues", []),
                            },
                            task_id=str(node_state.get("current_task_id", "")),
                        )
                        elapsed = round(time.time() - start, 1)
                        if "step_count" in node_state:
                            step_count = node_state["step_count"]
                        emit_trace_event(
                            "workflow_node",
                            source=str(node_name),
                            task_id=str(node_state.get("current_task_id", "")),
                            payload={
                                "node": node_name,
                                "step": step_count,
                                "elapsed": elapsed,
                                "keys": sorted(str(k) for k in node_state.keys()),
                                "current_task_id": str(node_state.get("current_task_id", "")),
                                "current_task_goal_preview": preview_text(node_state.get("current_task_goal", "")),
                                "task_plan_count": len(node_state.get("task_plan", []) or []),
                                "pending_task_count": len(node_state.get("pending_tasks", []) or []),
                            },
                        )

                        # 1) node_complete
                        yield _sse(NodeCompleteEvent(
                            node=node_name,
                            step=step_count,
                            elapsed=elapsed,
                        ))

                        # 2) AIMessage → token 스트리밍 + message 이벤트 (채팅 패널)
                        for msg in node_state.get("messages", []):
                            agent_name = getattr(msg, "name", None) or node_name
                            content = msg.content if hasattr(msg, "content") else str(msg)
                            if not content:
                                continue
                            additional_kwargs = getattr(msg, "additional_kwargs", {}) or {}
                            emit_runtime_detail(
                                "message",
                                {
                                    "node": node_name,
                                    "agent": agent_name,
                                    "content": content,
                                    "additional_kwargs": additional_kwargs,
                                },
                                task_id=str(node_state.get("current_task_id", "")),
                                result_id=str((additional_kwargs.get("result") or {}).get("result_id", "")) if isinstance(additional_kwargs.get("result"), dict) else "",
                            )
                            # 토큰 단위 스트리밍
                            chunks = _chunk_text(content)
                            delay = min(SIMULATED_STREAM_DELAY, 2.0 / max(len(chunks), 1))
                            for chunk_text in chunks:
                                yield _sse(TokenEvent(content=chunk_text, agent=agent_name, node=node_name))
                                await asyncio.sleep(delay)
                            # 최종 전체 메시지
                            yield _sse(MessageEvent(
                                agent=agent_name,
                                content=content,
                                step=step_count,
                            ))
                            turn_messages.append({
                                "agent": agent_name,
                                "content": content,
                            })

                        # 3) artifacts → artifact 이벤트 (오른쪽 패널)
                        artifact_sources = [
                            ("yield_artifacts", "yield_agent"),
                            ("wads_artifacts", "wads_agent"),
                            ("map_artifacts", "map_agent"),
                            ("fail_history_artifacts", "fail_history_agent"),
                            ("ppt_artifacts", "ppt_export"),
                            ("lot_history_artifacts", "lot_history_agent"),
                            ("relation_tree_artifacts", "relation_tree_agent"),
                            ("mining_artifacts", "mining_agent"),
                        ]
                        for key, default_agent in artifact_sources:
                            for art in node_state.get(key, []):
                                art_data = art.get("data", "")
                                if not art_data:
                                    continue
                                emit_runtime_detail(
                                    "artifact.raw",
                                    {
                                        "node": node_name,
                                        "key": key,
                                        "title": art.get("title", key.replace("_artifacts", "")),
                                        "agent": art.get("agent", default_agent),
                                        "type": art.get("type", ""),
                                        "mime": art.get("mime", ""),
                                        "data": art_data,
                                    },
                                    task_id=str(node_state.get("current_task_id", "")),
                                )

                                # PPTX artifact 특수 처리: file:// → 다운로드 URL (바이너리이므로 텍스트 읽기 전에 처리)
                                if art.get("type") == "pptx" or art.get("mime", "").startswith("application/vnd.openxmlformats"):
                                    pptx_path = art_data
                                    if pptx_path.startswith("file://"):
                                        pptx_path = pptx_path[7:]
                                    pptx_fname = os.path.basename(pptx_path)
                                    download_url = f"/download/pptx/{pptx_fname}"
                                    evt = ArtifactEvent(
                                        artifact_type=ArtifactType.pptx,
                                        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                                        title=art.get("title", "yield_report"),
                                        agent=art.get("agent", "ppt_export"),
                                        data=download_url,
                                        step=step_count,
                                    )
                                    yield _sse(evt)
                                    turn_artifact = evt.model_dump()
                                    turn_artifacts.append(turn_artifact)
                                    continue

                                # file:// 참조인 경우 파일에서 읽기 (텍스트 artifact만)
                                if art_data.startswith("file://"):
                                    file_path = art_data[7:]
                                    try:
                                        with open(file_path, "r", encoding="utf-8") as f:
                                            art_data = f.read()
                                        os.remove(file_path)
                                    except FileNotFoundError:
                                        continue
                                emit_runtime_detail(
                                    "artifact.data",
                                    {
                                        "node": node_name,
                                        "key": key,
                                        "title": art.get("title", key.replace("_artifacts", "")),
                                        "agent": art.get("agent", default_agent),
                                        "data": art_data,
                                    },
                                    task_id=str(node_state.get("current_task_id", "")),
                                )

                                art_type = _detect_artifact_type(art_data)
                                mime = {
                                    ArtifactType.html: "text/html",
                                    ArtifactType.image: "image/png",
                                    ArtifactType.markdown: "text/markdown",
                                }.get(art_type, "text/html")
                                evt = ArtifactEvent(
                                    artifact_type=art_type,
                                    mime=mime,
                                    title=art.get("title", key.replace("_artifacts", "")),
                                    agent=art.get("agent", default_agent),
                                    data=art_data,
                                    step=step_count,
                                )
                                yield _sse(evt)
                                # MongoDB에는 data 제외하여 저장 (크기 절감)
                                turn_artifact = evt.model_dump()
                                turn_artifact["data"] = ""
                                turn_artifacts.append(turn_artifact)

                        # 4) analysis_result → markdown artifact
                        analysis = node_state.get("analysis_result", "")
                        if analysis:
                            emit_runtime_detail(
                                "analysis_result",
                                {"node": node_name, "analysis": analysis},
                                task_id=str(node_state.get("current_task_id", "")),
                            )
                            evt = ArtifactEvent(
                                artifact_type=ArtifactType.markdown,
                                mime="text/markdown",
                                title="analysis",
                                agent=node_name,
                                data=analysis,
                                step=step_count,
                            )
                            yield _sse(evt)
                            turn_artifacts.append(evt.model_dump())

                        # 5) suggestion
                        suggestion = node_state.get("agent_suggestion", "")
                        if suggestion:
                            emit_runtime_detail(
                                "suggestion",
                                {"node": node_name, "suggestion": suggestion},
                                task_id=str(node_state.get("current_task_id", "")),
                            )
                            yield _sse(SuggestionEvent(
                                content=suggestion,
                                step=step_count,
                            ))
                            turn_suggestion = suggestion

            except Exception as e:
                logger.exception("Graph execution error")
                emit_trace_event(
                    "task_failed",
                    source="agent_server",
                    severity="error",
                    payload={
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "traceback": traceback.format_exc(),
                    },
                )
                yield _sse(ErrorEvent(message=to_user_message(e)))
                _schedule_control_submission(
                    req.app.state.control_knowledge,
                    [
                        incident_candidate(
                            e,
                            source="agent_server",
                            trace_id=trace_id,
                            turn_id=turn_id,
                            task_id="",
                        )
                    ],
                )
                try:
                    get_client().flush()
                except Exception:
                    pass
                return

            # ── interrupt 감지(fallback): in-loop에서 이미 emit했으면 건너뛴다.
            # aget_state는 무거운 노드 직후 stale할 수 있어 in-loop emit이 1차, 여기가 2차다.
            if not interrupt_emitted:
                try:
                    final_state = await graph.aget_state(config)
                    if final_state and final_state.tasks:
                        for task in final_state.tasks:
                            if hasattr(task, "interrupts") and task.interrupts:
                                for intr in task.interrupts:
                                    interrupt_data = intr.value
                                    emit_runtime_detail("interrupt", interrupt_data)
                                    for sse_line in _interrupt_sse_events(interrupt_data):
                                        yield sse_line
                                    interrupt_emitted = True
                except Exception as e:
                    logger.warning("Interrupt 감지 실패: %s", e)

            # Langfuse output 기록
            try:
                get_client().set_current_trace_io(output={"query": request.query})
            except Exception:
                pass

            # stream_end
            total_elapsed = round(time.time() - start, 1)
            yield _sse(StreamEndEvent(
                total_steps=step_count,
                elapsed=total_elapsed,
            ))

            # MongoDB에 이번 턴 저장
            turn_doc = {
                "session_id": request.session_id,
                "query": request.query,
                "messages": turn_messages,
                "artifacts": turn_artifacts,
                "suggestion": turn_suggestion,
                "step_count": step_count,
                "elapsed": total_elapsed,
                "timestamp": datetime.now(timezone.utc),
            }
            try:
                await db.chat_turns.insert_one(turn_doc)
            except Exception as e:
                logger.warning("대화 이력 저장 실패: %s", e)

            # 완료 state는 메모리 flush와 control knowledge 수집이 함께 재사용한다.
            if not interrupt_emitted:
                try:
                    _fs = await graph.aget_state(config)
                    _vals = _fs.values or {}
                    _schedule_control_submission(
                        req.app.state.control_knowledge,
                        runtime_candidates(_vals),
                    )
                    _uid = _vals.get("user_id") or request.user_id
                    _events = _vals.get("memory_feedback") or []
                    if _uid and _events:
                        _t = asyncio.create_task(
                            asyncio.to_thread(update_profile_from_feedback, _uid, list(_events))
                        )
                        _memory_tasks.add(_t)
                        _t.add_done_callback(_memory_tasks.discard)
                except Exception as e:
                    logger.warning("[UserMemory] 피드백 flush 실패 (무시): %s", e)

            try:
                get_client().flush()
            except Exception:
                pass
        finally:
            reset_trace_context(trace_tokens)

    return StreamingResponse(generate(), media_type="text/event-stream")

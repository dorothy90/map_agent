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
import sys
import uuid
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

# 이 파일의 디렉터리(08-YieldAgent/)를 sys.path에 추가 (로컬 모듈 임포트용)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from models import (  # noqa: E402
    ArtifactData,
    ChatRequest,
    ErrorEvent,
    HistoryMessage,
    SessionHistory,
    SessionSummary,
)
from admission import AdmissionController  # noqa: E402
from celery_app import celery_app  # noqa: E402
from celery_dispatcher import CeleryJobDispatcher  # noqa: E402
from identity import PlatformIdentity, get_platform_identity  # noqa: E402
from job_events import JobEventStore  # noqa: E402
from job_repository import JobRepository  # noqa: E402
from job_router import router as job_router  # noqa: E402
from job_service import JobService, reconcile_admission  # noqa: E402
from settings import get_settings  # noqa: E402
from graph_job_runner import GraphRunRequest, run_graph  # noqa: E402

# 사용자 선호 메모리 백그라운드 flush 태스크 보관 (GC로 조기 소멸 방지)
_memory_tasks: set = set()

_settings = get_settings()

if _settings.enable_local_trace:
    from local_trace import configure_runtime_terminal_logger  # noqa: E402
else:
    def configure_runtime_terminal_logger() -> None:
        pass

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


# ── FastAPI lifespan — shared job dependencies + optional legacy services ─
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.ready = False
    motor_client = AsyncIOMotorClient(settings.mongo_uri)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    repository = JobRepository(motor_client[settings.mongo_db])
    admission = AdmissionController(
        redis,
        user_limit=settings.user_job_limit,
        global_limit=settings.global_job_limit,
    )
    dispatcher = CeleryJobDispatcher(celery_app)
    event_store = JobEventStore(redis, ttl_seconds=86_400, max_events=2_000)
    service = JobService(repository, admission, dispatcher, event_store)
    app.state.motor_db = motor_client[settings.mongo_db]
    app.state.redis = redis
    app.state.job_repository = repository
    app.state.admission = admission
    app.state.job_dispatcher = dispatcher
    app.state.job_event_store = event_store
    app.state.job_service = service

    wiki_queue = None
    lint_task: asyncio.Task | None = None
    try:
        await repository.ensure_indexes()
        await reconcile_admission(repository, admission, redis)

        if settings.enable_wiki:
            from wiki_queue import wiki_queue as enabled_wiki_queue
            from wiki_summarizer import summarize as wiki_summarize_fn

            wiki_queue = enabled_wiki_queue
            wiki_queue.set_summarizer(wiki_summarize_fn)
            await wiki_queue.start()
            app.state.wiki_queue = wiki_queue
            lint_hours = float(os.getenv("WIKI_LINT_CRON_HOURS", "0") or "0")
            if lint_hours > 0:
                lint_task = asyncio.create_task(_wiki_lint_cron_loop(lint_hours))
                logger.info("[wiki_lint cron] started, interval=%sh", lint_hours)
        app.state.wiki_lint_task = lint_task

        with ExitStack() as stack:
            if settings.enable_legacy_chat:
                global to_user_message, update_profile_from_feedback
                global workflow

                from common import to_user_message
                from langgraph.checkpoint.mongodb import MongoDBSaver
                from supervisor import workflow
                from user_memory import update_profile_from_feedback

                checkpointer = stack.enter_context(
                    MongoDBSaver.from_conn_string(
                        settings.mongo_uri, db_name=settings.mongo_db
                    )
                )
                app.state.graph = workflow.compile(checkpointer=checkpointer)
                logger.info(
                    "MongoDB 체크포인터 + motor 연결 완료 (%s/%s)",
                    settings.mongo_uri,
                    settings.mongo_db,
                )

            app.state.ready = True
            yield
    finally:
        app.state.ready = False
        if lint_task is not None:
            lint_task.cancel()
            try:
                await lint_task
            except (asyncio.CancelledError, Exception):
                pass
        if wiki_queue is not None:
            await wiki_queue.stop(timeout=10)
        await redis.aclose()
        motor_client.close()
        logger.info("MongoDB/Redis 연결 종료")


app = FastAPI(title="Yield Agent Server", lifespan=lifespan)

# ── CORS — production uses only the exact configured origin allowlist ────────
_dev_origins = ["http://localhost:3000", "http://localhost:5173"]
_cors_origins = list(_settings.cors_origins)
if _settings.environment != "production":
    _cors_origins = _dev_origins + _cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(job_router)

if _settings.enable_repl:
    from repl_agent.router import router as repl_router  # noqa: E402

    app.include_router(repl_router, prefix="/repl", tags=["repl"])

if _settings.enable_wiki:
    from wiki_router import router as wiki_router  # noqa: E402

    app.include_router(wiki_router, prefix="/api/wiki", tags=["wiki"])


_LEGACY_EXACT_PATHS = {"/chat/stream", "/session", "/sessions"}
_LEGACY_PREFIXES = ("/session/", "/download/pptx/")


@app.middleware("http")
async def block_disabled_legacy_routes(request: Request, call_next):
    path = request.url.path
    if not _settings.enable_legacy_chat and (
        path in _LEGACY_EXACT_PATHS or path.startswith(_LEGACY_PREFIXES)
    ):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return await call_next(request)




def _sse(event: dict | object) -> str:
    """Pydantic model 또는 dict → SSE data line"""
    if hasattr(event, "model_dump"):
        payload = event.model_dump()
    else:
        payload = event
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"




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
async def mining_tas(
    body: TasRequest,
    _identity: PlatformIdentity = Depends(get_platform_identity),
):
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
    run_request = GraphRunRequest(
        job_id=str(uuid.uuid4()),
        owner_id=request.user_id,
        session_id=request.session_id,
        thread_id=request.session_id,
        query=request.query,
        resume_value=request.resume_value,
    )

    async def generate():
        queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

        async def emit(event: dict) -> None:
            await queue.put(("event", event))

        async def cancelled() -> bool:
            return await req.is_disconnected()

        async def execute() -> None:
            try:
                result = await run_graph(graph, run_request, emit, cancelled)
                await queue.put(("result", result))
            except Exception as exc:
                logger.exception("Graph execution error")
                await queue.put(("error", exc))

        task = asyncio.create_task(execute())
        result = None
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "event":
                    yield _sse(payload)
                    continue
                if kind == "error":
                    yield _sse(ErrorEvent(message=to_user_message(payload)))
                else:
                    result = payload
                break
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if result is None or result.final_result is None:
            return
        final_result = result.final_result
        turn_doc = {
            "session_id": request.session_id,
            "query": request.query,
            "messages": final_result["messages"],
            "artifacts": final_result["artifacts"],
            "suggestion": final_result["suggestion"],
            "step_count": final_result["step_count"],
            "elapsed": final_result["elapsed"],
            "timestamp": datetime.now(timezone.utc),
        }
        try:
            await db.chat_turns.insert_one(turn_doc)
        except Exception as exc:
            logger.warning("대화 이력 저장 실패: %s", exc)

        if result.outcome == "SUCCEEDED":
            user_id = final_result.get("user_id", "")
            memory_feedback = final_result.get("memory_feedback", [])
            if user_id and memory_feedback:
                memory_task = asyncio.create_task(
                    asyncio.to_thread(
                        update_profile_from_feedback,
                        user_id,
                        list(memory_feedback),
                    )
                )
                _memory_tasks.add(memory_task)
                memory_task.add_done_callback(_memory_tasks.discard)

    return StreamingResponse(generate(), media_type="text/event-stream")

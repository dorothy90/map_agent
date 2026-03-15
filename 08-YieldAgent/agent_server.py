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
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langfuse import get_client, observe
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.types import Overwrite
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
    MessageEvent,
    NodeCompleteEvent,
    SessionHistory,
    SessionSummary,
    StatusEvent,
    StreamEndEvent,
    StreamStartEvent,
    SuggestionEvent,
    TokenEvent,
)
from supervisor import workflow  # noqa: E402

_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
))
for _name in ("agent_server", "yield_agent"):
    _lg = logging.getLogger(_name)
    _lg.setLevel(logging.INFO)
    _lg.addHandler(_handler)

logger = logging.getLogger("agent_server")

MONGO_URI = "mongodb://localhost:27017"
MONGO_DB = "yield_agent"


# ── FastAPI lifespan — MongoDB 연결 관리 ──────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # motor (async) — 대화 이력 저장용
    motor_client = AsyncIOMotorClient(MONGO_URI)
    app.state.motor_db = motor_client[MONGO_DB]

    # MongoDBSaver (sync) — LangGraph 체크포인터
    with MongoDBSaver.from_conn_string(MONGO_URI, db_name=MONGO_DB) as checkpointer:
        app.state.graph = workflow.compile(checkpointer=checkpointer)
        logger.info("MongoDB 체크포인터 + motor 연결 완료 (%s/%s)", MONGO_URI, MONGO_DB)
        yield

    motor_client.close()
    logger.info("MongoDB 연결 종료")


app = FastAPI(title="Yield Agent Server", lifespan=lifespan)

# ── CORS — React dev server 허용 ─────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _on_task_error(task: asyncio.Task) -> None:
    """create_task 예외를 로그에 기록 (fire-and-forget 무음 실패 방지)"""
    if not task.cancelled() and task.exception():
        logger.exception("Graph task failed: %s", task.exception())


def _sse(event: dict | object) -> str:
    """Pydantic model 또는 dict → SSE data line"""
    if hasattr(event, "model_dump"):
        payload = event.model_dump()
    else:
        payload = event
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


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
    queue: asyncio.Queue = asyncio.Queue()
    config = {"configurable": {"thread_id": request.session_id, "sse_queue": queue}, "recursion_limit": 20}

    # 이번 턴 입력: 새 HumanMessage + 퍼-턴 리셋 필드
    input_state = {
        "messages": [HumanMessage(content=request.query)],
        "yield_artifacts": Overwrite([]),
        "wads_artifacts": Overwrite([]),
        "map_artifacts": Overwrite([]),
        "weeks_data": [],
        "table_result": "",
        "analysis_result": "",
        "step_count": 0,
        "anomaly_params": [],
    }

    # 첫 번째 턴이면 나머지 기본값도 함께 전달
    prev_state = await graph.aget_state(config)
    if not (prev_state and prev_state.values):
        input_state.update({
            "lotcd": "",
            "ref_date": "",
            "unit": "weekly",
            "periods": 0,
            "wads_end_tm": "",
            "anomaly_params": [],
            "filter_params": [],
            "map_lot_id": "",
            "map_lot_ids": "",
            "map_wf_ids": "",
            "map_groupkey": "",
            "map_type": "binmap",
            "map_bin_type": "pt1h_bin",
            "map_result": "",
            "yield_lot_ids": "",
            "yield_groupkey": "",
        })

    @observe(name="yield_agent_request")
    async def _run_graph():
        lf = get_client()
        lf.update_current_trace(
            session_id=request.session_id,
            input={"query": request.query},
            tags=["yield-agent", "v4-multistep"],
        )
        routes: list[str] = []
        try:
            async for step in graph.astream(input_state, config=config):
                await queue.put(("step", step))
            lf.update_current_trace(output={"routes": routes, "query": request.query})
            await queue.put(("done", None))
        except Exception as e:
            logger.exception("Graph execution error")
            lf.update_current_trace(output={"error": str(e)})
            await queue.put(("error", e))

    task = asyncio.create_task(_run_graph())
    task.add_done_callback(_on_task_error)

    async def generate():
        start = time.time()
        step_count = 0

        # 대화 이력 저장용 — 이번 턴에서 발생한 모든 이벤트 수집
        turn_messages: list[dict] = []
        turn_artifacts: list[dict] = []
        turn_suggestion = ""

        # stream_start
        yield _sse(StreamStartEvent(
            session_id=request.session_id,
            query=request.query,
        ))

        while True:
            kind, data = await queue.get()

            if kind == "error":
                yield _sse(ErrorEvent(message=f"[step {step_count}] {str(data)}"))
                try:
                    get_client().flush()
                except Exception:
                    pass
                return

            if kind == "status":
                yield _sse(data)
                continue

            if kind == "done":
                break

            for node_name, node_state in data.items():
                elapsed = round(time.time() - start, 1)
                if "step_count" in node_state:
                    step_count = node_state["step_count"]

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
                    # 토큰 단위 스트리밍
                    chunks = _chunk_text(content)
                    delay = min(SIMULATED_STREAM_DELAY, 2.0 / max(len(chunks), 1))
                    for chunk in chunks:
                        yield _sse(TokenEvent(content=chunk, agent=agent_name, node=node_name))
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
                ]
                for key, default_agent in artifact_sources:
                    for art in node_state.get(key, []):
                        art_data = art.get("data", "")
                        if not art_data:
                            continue

                        # file:// 참조인 경우 파일에서 읽기
                        if art_data.startswith("file://"):
                            file_path = art_data[7:]
                            try:
                                with open(file_path, "r", encoding="utf-8") as f:
                                    art_data = f.read()
                                # 전송 후 임시 파일 삭제
                                os.remove(file_path)
                            except FileNotFoundError:
                                continue

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
                    yield _sse(SuggestionEvent(
                        content=suggestion,
                        step=step_count,
                    ))
                    turn_suggestion = suggestion

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

        try:
            get_client().flush()
        except Exception:
            pass

    return StreamingResponse(generate(), media_type="text/event-stream")

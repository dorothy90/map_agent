"""REPL 검증 agent 의 FastAPI 라우터.

기존 `agent_server.py` 에 `include_router(router, prefix="/repl")` 한 줄만 추가한다.
노출 엔드포인트:
- POST /repl/session         — LOTCD/기간/fail_name (+ optional column_guideline) 으로 데이터 로드 + 세션 생성
- POST /repl/chat            — session_id + 자유 쿼리로 agent 스트리밍 (SSE)
- GET  /repl/health          — 헬스체크
- GET  /repl/mock/data       — 로컬 CSV 를 필터링한 mock 데이터 (디버그·단독 테스트용)

langgraph 1.0.x 는 StreamPart v2 미지원 → `(mode, data)` tuple 을 그대로 파싱.

P1 개선: 첫 턴 사용자 메시지 앞에 session_store 가 만든 요약(`__summary__`) 을 prefix.
checkpointer(InMemorySaver) 가 messages 를 보존하므로 이후 턴에는 재주입하지 않는다.
P4 개선: astream config 에 recursion_limit=30 을 넣어 도구 반복 폭주를 차단.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from pydantic import BaseModel

from .agent import get_agent
from .events import (
    ArtifactEvent,
    BaseReplEvent,
    EventEmitter,
    RunCancelled,
    RunError,
    RunFinished,
    RunStarted,
    TextMessageContent,
    TextMessageEnd,
    TextMessageStart,
    ToolCallArgs,
    ToolCallEnd,
    ToolCallStart,
    ToolResult,
)
from .mock.routes import router as mock_router
from .run_registry import run_registry
from .session_store import (
    SessionStateError,
    begin_run,
    cancel_run,
    close_session,
    create_session,
    finish_run,
    get_session_summary,
)

logger = logging.getLogger("repl_agent")

router = APIRouter()
router.include_router(mock_router, prefix="/mock")


class SessionIn(BaseModel):
    lotcd: str
    start: str  # YYYY-MM-DD
    end: str    # YYYY-MM-DD
    fail_name: str
    column_guideline: str | None = None  # 사용자가 제공하는 컬럼 설명(선택)


class ChatIn(BaseModel):
    session_id: str
    query: str


def _sse(event: BaseReplEvent) -> str:
    return f"data: {event.model_dump_json()}\n\n"


def _session_error(exc: SessionStateError) -> HTTPException:
    status_codes = {
        "session_not_found": 404,
        "session_busy": 409,
        "runtime_lost": 410,
    }
    return HTTPException(
        status_code=status_codes.get(exc.code, 409),
        detail={"code": exc.code, "message": str(exc)},
    )


def _tool_events(data: Any, emitter: EventEmitter) -> list[BaseReplEvent]:
    events: list[BaseReplEvent] = []
    if not isinstance(data, dict):
        return events
    for node_state in data.values():
        if not isinstance(node_state, dict):
            continue
        for message in node_state.get("messages", []):
            if isinstance(message, AIMessage):
                for tool_call in message.tool_calls or []:
                    tool_call_id = tool_call.get("id", "")
                    events.extend([
                        emitter.build(
                            ToolCallStart,
                            tool_call_id=tool_call_id,
                            name=tool_call.get("name", ""),
                        ),
                        emitter.build(
                            ToolCallArgs,
                            tool_call_id=tool_call_id,
                            args=tool_call.get("args", {}),
                        ),
                        emitter.build(ToolCallEnd, tool_call_id=tool_call_id),
                    ])
            elif isinstance(message, ToolMessage):
                raw = message.content
                try:
                    result = json.loads(raw) if isinstance(raw, str) else raw
                except json.JSONDecodeError:
                    result = {"content": raw}
                if not isinstance(result, dict):
                    result = {"content": result}
                events.append(emitter.build(
                    ToolResult,
                    tool_call_id=message.tool_call_id or "",
                    result=result,
                ))
    return events


async def _next_or_cancel(
    stream: AsyncIterator[Any],
    cancel_task: asyncio.Task[bool],
) -> tuple[bool, Any]:
    next_task = asyncio.create_task(anext(stream))
    cancel_won = False
    try:
        done, _ = await asyncio.wait(
            {next_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if cancel_task in done:
            cancel_won = True
            return True, None
        return False, next_task.result()
    finally:
        if not next_task.done():
            next_task.cancel()
        try:
            await next_task
        except (asyncio.CancelledError, StopAsyncIteration):
            pass
        except Exception:
            if not cancel_won:
                raise
            logger.warning(
                "discarded agent stream task failed after cancellation won",
                exc_info=True,
            )


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "repl_agent"}


@router.post("/session")
async def start_session(body: SessionIn) -> dict[str, Any]:
    """LOTCD/기간/fail_name (+ optional column_guideline) 으로 데이터를 한 번 불러 세션을 만든다.

    반환:
      {
        "session_id": <uuid>,
        "rowcount": N,
        "columns": [...],
        "numeric_columns": [...],
        "query": {...},
        "has_column_guideline": bool
      }

    이후 POST /repl/chat 에 이 session_id 를 넘기면, 같은 df 위에서 여러 질문을 이어서 검증할 수 있다.
    """
    try:
        info = await create_session(
            lotcd=body.lotcd,
            start=body.start,
            end=body.end,
            fail_name=body.fail_name,
            column_guideline=body.column_guideline,
        )
    except Exception as exc:
        logger.exception("repl_agent session creation failed")
        raise HTTPException(
            status_code=502,
            detail=f"데이터 로드 실패: {type(exc).__name__}: {exc}",
        ) from exc
    return info


@router.post("/chat")
async def chat(body: ChatIn) -> StreamingResponse:
    """세션 안에서 agent 에 질문을 던지고 SSE 로 응답."""
    thread_id = body.session_id
    run_id = str(uuid.uuid4())
    try:
        begin_run(thread_id, run_id)
    except SessionStateError as exc:
        raise _session_error(exc) from exc

    control = run_registry.register(run_id, thread_id)
    emitter = EventEmitter(run_id=run_id, thread_id=thread_id)
    # recursion_limit: 기존 yield supervisor 와 동일한 값(agent_server.py:243). 도구 반복 폭주 차단.
    config = {
        "configurable": {"thread_id": thread_id, "run_id": run_id},
        "recursion_limit": 30,
    }

    async def generate() -> AsyncIterator[str]:
        cancel_task: asyncio.Task[bool] | None = None
        agent_stream: AsyncIterator[Any] | None = None
        text_message_id: str | None = None
        try:
            yield _sse(emitter.build(RunStarted))
            agent = get_agent()
            prev_state = await agent.aget_state(config)
            is_first_turn = not (
                prev_state and prev_state.values and prev_state.values.get("messages")
            )
            user_content = body.query
            if is_first_turn:
                summary = get_session_summary(thread_id) or ""
                if summary:
                    user_content = f"{summary}\n\n[질문]\n{body.query}"

            agent_stream = agent.astream(
                {"messages": [{"role": "user", "content": user_content}]},
                config=config,
                stream_mode=["updates", "messages", "custom"],
            ).__aiter__()
            cancel_task = asyncio.create_task(control.cancel_event.wait())

            while True:
                try:
                    cancelled, part = await _next_or_cancel(agent_stream, cancel_task)
                except StopAsyncIteration:
                    break
                if cancelled:
                    if text_message_id is not None:
                        yield _sse(emitter.build(
                            TextMessageEnd, message_id=text_message_id
                        ))
                    yield _sse(emitter.build(RunCancelled))
                    return

                mode, data = part
                if mode == "custom":
                    if isinstance(data, dict) and data.get("kind") == "artifacts":
                        tool_call_id = data.get("tool_call_id", "")
                        for artifact in data.get("artifacts", []):
                            yield _sse(emitter.build(
                                ArtifactEvent,
                                tool_call_id=tool_call_id,
                                artifact=artifact,
                            ))
                    continue

                if mode == "messages":
                    msg, _meta = data
                    if not isinstance(msg, AIMessageChunk):
                        continue
                    content = msg.content
                    if isinstance(content, str) and content:
                        if text_message_id is None:
                            text_message_id = str(uuid.uuid4())
                            yield _sse(emitter.build(
                                TextMessageStart, message_id=text_message_id
                            ))
                        yield _sse(emitter.build(
                            TextMessageContent,
                            message_id=text_message_id,
                            content=content,
                        ))
                    continue

                if mode == "updates":
                    for event in _tool_events(data, emitter):
                        yield _sse(event)

            if text_message_id is not None:
                yield _sse(emitter.build(
                    TextMessageEnd, message_id=text_message_id
                ))
            yield _sse(emitter.build(RunFinished))
        except Exception as exc:
            logger.exception("repl_agent chat error")
            yield _sse(emitter.build(
                RunError,
                code="agent_error",
                message=f"{type(exc).__name__}: {exc}",
            ))
        finally:
            try:
                if cancel_task is not None and not cancel_task.done():
                    cancel_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await cancel_task
                if agent_stream is not None:
                    await agent_stream.aclose()
            finally:
                try:
                    finish_run(thread_id, run_id)
                finally:
                    run_registry.unregister(run_id)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/runs/{run_id}/cancel")
async def cancel_active_run(run_id: str) -> dict[str, Any]:
    control = run_registry.get(run_id)
    signalled = control is not None
    cancelled = await asyncio.to_thread(cancel_run, run_id)
    if control is not None:
        control.cancel_event.set()
    return {"run_id": run_id, "cancelled": signalled or cancelled}


@router.delete("/session/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    closed = await asyncio.to_thread(close_session, session_id)
    return {"session_id": session_id, "closed": closed}


# session_id 는 위의 POST /session 이 만드는 uuid 지만, 혹시 프론트가 직접 만들고 싶을 때 대비해
# 편의용 uuid 생성기도 노출. (현재 프론트는 /session 만 사용한다.)
@router.get("/uuid")
def new_uuid() -> dict[str, str]:
    return {"uuid": str(uuid.uuid4())}

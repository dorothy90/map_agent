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

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from pydantic import BaseModel

from .agent import get_agent
from .mock.routes import router as mock_router
from .session_store import create_session, get_namespace, get_session_summary

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


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


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
    if get_namespace(body.session_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"session '{body.session_id}' 가 없거나 만료됨. /repl/session 으로 먼저 시작하세요.",
        )

    thread_id = body.session_id
    # recursion_limit: 기존 yield supervisor 와 동일한 값(agent_server.py:243). 도구 반복 폭주 차단.
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 30}
    agent = get_agent()

    # 첫 턴 여부: checkpointer 에 해당 thread 의 messages 가 비어있으면 first-turn 으로 판단.
    # (agent_server.py:279-280 의 `prev_state = await graph.aget_state(config)` 패턴 차용)
    prev_state = await agent.aget_state(config)
    is_first_turn = not (
        prev_state and prev_state.values and prev_state.values.get("messages")
    )

    user_content = body.query
    if is_first_turn:
        summary = get_session_summary(body.session_id) or ""
        if summary:
            user_content = f"{summary}\n\n[질문]\n{body.query}"

    async def generate():
        yield _sse({"type": "start", "session_id": thread_id})
        try:
            async for mode, data in agent.astream(
                {"messages": [{"role": "user", "content": user_content}]},
                config=config,
                stream_mode=["updates", "messages", "custom"],
            ):
                if mode == "custom":
                    if isinstance(data, dict) and data.get("kind") == "plot":
                        yield _sse({"type": "plot", "payload": data["spec"]})
                    continue

                if mode == "messages":
                    msg, _meta = data
                    # AIMessageChunk 만 토큰 스트림으로 취급한다.
                    # (ToolMessage 도 이 채널로 올 수 있어 assistant 버블에 섞이는 걸 방지)
                    if not isinstance(msg, AIMessageChunk):
                        continue
                    content = msg.content
                    if isinstance(content, str) and content:
                        yield _sse({"type": "token", "content": content})
                    continue

                if mode == "updates":
                    if not isinstance(data, dict):
                        continue
                    for _node_name, node_state in data.items():
                        if not isinstance(node_state, dict):
                            continue
                        for m in node_state.get("messages", []):
                            if isinstance(m, AIMessage):
                                # LLM 이 호출한 도구들 — 각 tool_call 을 개별 이벤트로 방출
                                for tc in getattr(m, "tool_calls", None) or []:
                                    yield _sse({
                                        "type": "tool_call",
                                        "id": tc.get("id", ""),
                                        "name": tc.get("name", ""),
                                        "args": tc.get("args", {}),
                                    })
                                # 최종 assistant 텍스트 (토큰 스트림이 비어있을 때의 fallback)
                                content = m.content
                                if isinstance(content, str) and content:
                                    yield _sse({"type": "assistant", "content": content})
                            elif isinstance(m, ToolMessage):
                                raw = m.content
                                tm_content = raw if isinstance(raw, str) else str(raw)
                                yield _sse({
                                    "type": "tool_result",
                                    "tool_call_id": getattr(m, "tool_call_id", "") or "",
                                    "name": getattr(m, "name", "") or "",
                                    "content": tm_content,
                                })
        except Exception as exc:
            logger.exception("repl_agent chat error")
            yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            return

        yield _sse({"type": "end"})

    return StreamingResponse(generate(), media_type="text/event-stream")


# session_id 는 위의 POST /session 이 만드는 uuid 지만, 혹시 프론트가 직접 만들고 싶을 때 대비해
# 편의용 uuid 생성기도 노출. (현재 프론트는 /session 만 사용한다.)
@router.get("/uuid")
def new_uuid() -> dict[str, str]:
    return {"uuid": str(uuid.uuid4())}

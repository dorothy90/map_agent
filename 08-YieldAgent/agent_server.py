"""
Agent Server — LangGraph Supervisor FastAPI Backend
====================================================
LangGraph yield_supervisor를 직접 실행하고 SSE로 스트리밍합니다.
Streamlit UI는 이 서버에 HTTP 요청을 보내는 클라이언트입니다.

실행: uvicorn 08-YieldAgent.agent_server:app --port 8001
  또는 (08-YieldAgent 디렉터리 내): uvicorn agent_server:app --port 8001
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from threading import Thread

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langfuse import get_client, observe
from pydantic import BaseModel

# 이 파일의 디렉터리(08-YieldAgent/)를 sys.path에 추가 (로컬 모듈 임포트용)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from supervisor import yield_supervisor  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agent_server")

app = FastAPI(title="Yield Agent Server")

# ── 인메모리 세션 저장소 ─────────────────────────────────
# { session_id: {"lotcd": str, "prev_messages": list, "anomaly_params": list} }
sessions: dict[str, dict] = {}


class ChatRequest(BaseModel):
    query: str
    session_id: str


# ── 헬스체크 ─────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}


# ── 세션 삭제 ─────────────────────────────────────────────
@app.delete("/session/{session_id}")
def delete_session(session_id: str):
    sessions.pop(session_id, None)
    return {"deleted": session_id}


# ── SSE 스트리밍 ──────────────────────────────────────────
@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    session = sessions.setdefault(
        request.session_id,
        {"lotcd": "", "prev_messages": [], "anomaly_params": []},
    )
    prev_messages = session["prev_messages"]

    # rewrite_node는 prev_messages + rewritten HumanMessage 전체를 반환하므로
    # 현재 턴 메시지는 그 이후부터 시작 (버그 수정 핵심)
    n_prev_offset = len(prev_messages) + 1

    initial_state = {
        "messages": prev_messages + [HumanMessage(content=request.query)],
        "lotcd": session["lotcd"],
        "ref_date": "",
        "weeks_data": [],
        "table_result": "",
        "analysis_result": "",
        "yield_artifacts": [],
        "wads_end_tm": "",
        "wads_artifacts": [],
        "next": "",
        "anomaly_params": session["anomaly_params"],
        "agent_suggestion": "",
        "filter_params": [],
        # yield → map 연계 (category별 cummap)
        "need_cummap": False,
        "cummap_weeks": [],
        "cummap_target_bin": "",
        "cummap_category": "",
        # Map Agent 파라미터
        "map_lot_id":   "",
        "map_lot_ids":  "",
        "map_wf_ids":   "",
        "map_groupkey": "",
        "map_type":     "binmap",
        "map_bin_type": "pt1h_bin",
        "map_result":   "",
        "map_artifacts": [],
    }

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    @observe(name="yield_agent_request")
    def _run_graph():
        # 모든 노드의 @observe가 이 trace의 child span이 됨
        lf = get_client()
        lf.update_current_trace(
            session_id=request.session_id,
            input={"query": request.query},
            tags=["yield-agent", "v3"],
        )
        route = ""
        try:
            for step in yield_supervisor.stream(
                initial_state,
                config={
                    "configurable": {
                        "session_id": request.session_id,
                        "anomaly_count": len(session["anomaly_params"]),
                    }
                },
            ):
                # supervisor 스텝에서 라우팅 결정 캡처
                if "supervisor" in step:
                    route = step["supervisor"].get("next", route)
                asyncio.run_coroutine_threadsafe(
                    queue.put(("step", step)), loop
                ).result()
            # 루트 트레이스 output에 라우팅 결과 기록
            lf.update_current_trace(output={"route": route, "query": request.query})
            asyncio.run_coroutine_threadsafe(queue.put(("done", None)), loop).result()
        except Exception as e:
            logger.exception("Graph execution error")
            lf.update_current_trace(output={"error": str(e)})
            asyncio.run_coroutine_threadsafe(queue.put(("error", e)), loop).result()

    Thread(target=_run_graph, daemon=True).start()

    async def generate():
        start = time.time()
        all_messages: list = []
        final_state: dict = {}

        while True:
            kind, data = await queue.get()

            if kind == "error":
                yield f'data: {json.dumps({"type": "error", "message": str(data)})}\n\n'
                try:
                    get_client().flush()
                except Exception:
                    pass
                return

            if kind == "done":
                break

            # step: {node_name: node_state_update}
            for node_name, node_state in data.items():
                elapsed = time.time() - start
                if "messages" in node_state:
                    all_messages.extend(node_state["messages"])
                final_state.update(node_state)
                yield f'data: {json.dumps({"type": "node", "node": node_name, "elapsed": round(elapsed, 1)})}\n\n'

        # 현재 턴 메시지만 추출 — prev_messages + rewritten HumanMessage 건너뜀
        current_msgs = all_messages[n_prev_offset:]

        supervisor_msg = next(
            (m.content for m in current_msgs if getattr(m, "name", None) == "supervisor"),
            "처리 완료",
        )
        wads_answer = next(
            (m.content for m in current_msgs if getattr(m, "name", None) == "wads_agent"),
            "",
        )
        map_answer = next(
            (m.content for m in current_msgs if getattr(m, "name", None) == "map_agent"),
            "",
        )

        # 세션 업데이트 — 최근 AI 메시지 2개만 유지 (컨텍스트 토큰 절약)
        ai_msgs = [m for m in all_messages if getattr(m, "type", None) == "ai"]
        session["prev_messages"] = ai_msgs[-2:]
        if final_state.get("lotcd"):
            session["lotcd"] = final_state["lotcd"]
        if final_state.get("anomaly_params") is not None:
            session["anomaly_params"] = final_state["anomaly_params"]

        # HTML 아티팩트 추출
        yield_html = next(
            (a["data"] for a in final_state.get("yield_artifacts", []) if a.get("data")),
            "",
        )
        wads_html = next(
            (a["data"] for a in final_state.get("wads_artifacts", []) if a.get("data")),
            "",
        )
        map_html = next(
            (a["data"] for a in final_state.get("map_artifacts", []) if a.get("data")),
            "",
        )

        result = {
            "type": "result",
            "supervisor_msg": supervisor_msg,
            "yield_html": yield_html,
            "analysis": final_state.get("analysis_result", ""),
            "agent_suggestion": final_state.get("agent_suggestion", ""),
            "wads_answer": wads_answer,
            "wads_html": wads_html,
            "map_answer": map_answer,
            "map_html": map_html,
            "has_weeks_data": bool(final_state.get("weeks_data")),
        }
        yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"

        # Langfuse 트레이스 전송
        try:
            get_client().flush()
        except Exception:
            pass

    return StreamingResponse(generate(), media_type="text/event-stream")

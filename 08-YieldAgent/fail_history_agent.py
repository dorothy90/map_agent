"""
Fail History Agent — 불량이력 RAG 검색 + HTML 리포트
====================================================
OpenSearch 하이브리드 검색(BM25 + kNN)으로 불량이력을 조회하고
gui_v2.html 포맷의 HTML 리포트를 생성하는 ReAct 에이전트.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent
from langfuse import observe

from common import timed, get_llm
from lf_utils import lf_callbacks as _lf_callbacks
from prompts import FAIL_HISTORY_SYSTEM_PROMPT_TEMPLATE
from fail_history_tools import FAIL_HISTORY_TOOLS, _tool_payload_var, _get_tool_payload

load_dotenv(override=True)

logger = logging.getLogger("yield_agent.fail_history_agent")

# Fail History Agent용 모델
_fh_model = get_llm(model=os.getenv("RETRIEVE_CHAIN_MODEL"))


def _fh_prompt(state: dict) -> list:
    """호출 시점의 날짜 + supervisor 파싱 컨텍스트를 포함한 system prompt 생성"""
    current_date = datetime.now().strftime("%Y년 %m월 %d일")
    system_prompt = FAIL_HISTORY_SYSTEM_PROMPT_TEMPLATE.format(current_date=current_date)

    # supervisor가 파싱한 컨텍스트 주입
    lotcd = state.get("_lotcd", "")
    dh_query = state.get("_dh_query", "")
    dh_fail_type = state.get("_dh_fail_type", "")
    dh_cause_oper = state.get("_dh_cause_oper", "")

    ctx_parts = []
    if lotcd:
        ctx_parts.append(f"product={lotcd}")
    if dh_query:
        ctx_parts.append(f"검색어={dh_query}")
    if dh_fail_type:
        ctx_parts.append(f"불량유형={dh_fail_type}")
    if dh_cause_oper:
        ctx_parts.append(f"원인공정={dh_cause_oper}")

    if ctx_parts:
        system_prompt += f"\n\n[조회 컨텍스트] {', '.join(ctx_parts)}"

    return [SystemMessage(content=system_prompt)] + list(state.get("messages", []))


_fh_graph = create_react_agent(
    model=_fh_model,
    tools=FAIL_HISTORY_TOOLS,
    prompt=_fh_prompt,
)


# ── Fail History 리포트 HTML 렌더링 (ContextVar에서 수거) ──────
def _render_fh_report_html(reports: List[Dict[str, Any]]) -> str:
    """ContextVar에 저장된 리포트 HTML 반환"""
    if not reports:
        return ""
    # render_fail_report가 저장한 HTML을 그대로 반환
    html_parts = [r.get("html", "") for r in reports if r.get("html")]
    return "\n".join(html_parts)


# ── LangGraph 노드용 래퍼 ──────────────────────────────────────
@observe(name="fail_history_agent_node")
@timed
def fail_history_agent_node(state: dict, config: RunnableConfig) -> dict:
    """Fail History Agent 노드: OpenSearch RAG 검색 + HTML 리포트 생성"""
    lotcd = state.get("lotcd", "")
    dh_query = state.get("dh_query", "")
    dh_fail_type = state.get("dh_fail_type", "")
    dh_cause_oper = state.get("dh_cause_oper", "")

    logger.info(
        "[FH Agent] lotcd=%s, dh_query=%s, dh_fail_type=%s, dh_cause_oper=%s",
        lotcd, dh_query, dh_fail_type, dh_cause_oper,
    )

    # 요청별 격리된 저장소 초기화
    storage: Dict[str, Any] = {"reports": []}
    _tool_payload_var.set(storage)

    # messages에서 rewrite된 원본 쿼리 추출
    messages = state.get("messages", [])
    last_human = next(
        (m for m in reversed(messages) if isinstance(m, HumanMessage)),
        None,
    )
    query = last_human.content if last_human else f"{lotcd} 불량이력 조회"
    logger.info("[FH Agent] 쿼리: %s", query)

    # 선택적 히스토리 필터링 — 최근 3턴
    fh_history: List[Any] = []
    turn_count = 0
    for m in reversed(messages):
        if turn_count >= 3:
            break
        if isinstance(m, HumanMessage):
            fh_history.insert(0, m)
            turn_count += 1
        elif isinstance(m, AIMessage) and getattr(m, "name", "") == "fail_history_agent":
            fh_history.insert(0, m)

    if not fh_history or fh_history[-1].content != query:
        fh_history.append(HumanMessage(content=query))

    logger.info("[FH Agent] ReAct 그래프 invoke 시작 (history=%d msgs)", len(fh_history))
    try:
        sub_config = {**config, "callbacks": _lf_callbacks(), "recursion_limit": 10}
        result = _fh_graph.invoke(
            {
                "messages": fh_history,
                "_lotcd": lotcd,
                "_dh_query": dh_query,
                "_dh_fail_type": dh_fail_type,
                "_dh_cause_oper": dh_cause_oper,
            },
            config=sub_config,
        )
    except Exception as e:
        logger.error("[FH Agent] 실행 실패: %s", e, exc_info=True)
        error_message = AIMessage(
            content="불량이력 검색 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
            name="fail_history_agent",
        )
        return {"messages": [error_message], "fail_history_artifacts": []}

    ai_messages = [m for m in result.get("messages", []) if isinstance(m, AIMessage)]
    answer = ai_messages[-1].content if ai_messages else "불량이력 검색에 실패했습니다."

    reports_payload = storage.get("reports", [])

    logger.debug("[FH Agent] reports_payload count: %d", len(reports_payload))

    # HTML artifact 수거
    artifacts = []
    if reports_payload:
        html = _render_fh_report_html(reports_payload)
        if html:
            artifacts.append({
                "type": "html",
                "mime": "text/html",
                "data": html,
                "title": "fail_history_report",
            })

    # SUGGESTION 추출
    suggestion_match = re.search(r'\[SUGGESTION:\s*(.*?)\]', answer)
    agent_suggestion = suggestion_match.group(1).strip() if suggestion_match else ""
    answer = re.sub(r'\[SUGGESTION:.*?\]', '', answer).strip()

    result_message = AIMessage(content=answer, name="fail_history_agent")

    return {
        "messages": [result_message],
        "fail_history_artifacts": artifacts,
        "agent_suggestion": agent_suggestion,
    }

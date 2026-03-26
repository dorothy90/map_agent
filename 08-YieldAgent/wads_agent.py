from __future__ import annotations

import logging
import re
from dotenv import load_dotenv
import os
from datetime import date
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent

from langfuse import observe

from lf_utils import lf_callbacks as _lf_callbacks
from common import timed, get_llm
from prompts import WADS_SYSTEM_PROMPT_TEMPLATE
from wads_tools import WADS_TOOLS, _tool_payload_var, _get_tool_payload

# .env 로드 및 모델 설정
load_dotenv(override=True)

logger = logging.getLogger("yield_agent.wads_agent")

# WADS Agent용 모델
_wads_model = get_llm(model=os.getenv("RETRIEVE_CHAIN_MODEL"))


# create_react_agent로 WADS 그래프 생성 — 수동 StateGraph 대체
# prompt를 callable로 전달: 호출 시 현재 날짜를 SystemMessage로 주입
def _wads_prompt(state: dict) -> list:
    """호출 시점의 날짜를 포함한 system prompt 생성"""
    from datetime import datetime

    current_date = datetime.now().strftime("%Y년 %m월 %d일")
    system_prompt = WADS_SYSTEM_PROMPT_TEMPLATE.format(current_date=current_date)
    return [SystemMessage(content=system_prompt)] + list(state.get("messages", []))


_wads_graph = create_react_agent(
    model=_wads_model,
    tools=WADS_TOOLS,
    prompt=_wads_prompt,
)


def _create_wads_agent():
    """호환성 유지용 래퍼 — 싱글턴 그래프 반환"""
    return _wads_graph


# -------------------- HTML 렌더링 --------------------
def _html_escape(s: Any) -> str:
    txt = "" if s is None else str(s)
    return (
        txt.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _render_wads_query_html(payload: List[Dict[str, Any]]) -> str:
    """wads_query_data 결과를 HTML로 렌더링"""
    first = payload[0] if payload else {}
    is_error = bool(first.get("error"))

    style = """<style>
.wads-card{border:1px solid #e5e7eb;border-radius:12px;padding:12px;background:#fff}
.wads-title{font-weight:700;margin-bottom:8px;color:#1a1a2e}
.wads-sub{color:#6b7280;font-size:12px;margin-bottom:10px}
.wads-table{width:100%;border-collapse:collapse;font-size:13px}
.wads-table th,.wads-table td{border:1px solid #e5e7eb;padding:8px;text-align:left;vertical-align:top}
.wads-table th{background:#f8f9fa}
.wads-badge{display:inline-block;padding:2px 8px;border-radius:999px;background:#e0f2fe;color:#0369a1;font-size:12px}
.wads-empty{color:#6b7280}
</style>"""

    header = (
        "<div class='wads-card'><div class='wads-title'>WADS: 데이터 조회 결과</div>"
    )
    footer = "</div>"

    if is_error:
        msg = first.get("detail") or first.get("error") or "오류가 발생했습니다."
        body = f"<div class='wads-empty'>{_html_escape(msg)}</div>"
        return style + header + body + footer

    sub = f"<div class='wads-sub'>총 <span class='wads-badge'>{len(payload)}건</span> 조회됨</div>"

    rows_html = ""
    for row in payload:
        rows_html += (
            "<tr>"
            f"<td>{_html_escape(row.get('lotcd'))}</td>"
            f"<td>{_html_escape(row.get('end_tm'))}</td>"
            f"<td>{_html_escape(row.get('parameter'))}</td>"
            "</tr>"
        )

    table = (
        "<table class='wads-table'>"
        "<thead><tr><th>LOT코드</th><th>종료시간</th><th>스텝</th></tr></thead>"
        f"<tbody>{rows_html}</tbody></table>"
    )

    return style + header + sub + table + footer


def _render_wads_report_html(payload: Any) -> str:
    """wads_get_html_report 결과를 HTML로 렌더링 (단일 또는 여러 리포트)"""
    # 리스트인 경우 (여러 리포트)
    if isinstance(payload, list):
        if not payload:
            return "<div>HTML 콘텐츠가 없습니다.</div>"

        # 여러 리포트를 하나의 HTML로 합침
        html_parts = []
        for idx, report in enumerate(payload):
            if report.get("error"):
                msg = (
                    report.get("detail")
                    or report.get("error")
                    or "오류가 발생했습니다."
                )
                html_parts.append(f"<div>WADS 리포트 오류: {_html_escape(msg)}</div>")
            else:
                # 리포트 메타정보 헤더
                header = f"""<div style="background:#f0f9ff;padding:8px 12px;border-radius:8px;margin-bottom:8px;border-left:4px solid #0284c7;">
                    <strong>📊 리포트 {idx + 1}</strong>: {_html_escape(report.get('lotcd', ''))} / {_html_escape(report.get('parameter', ''))} ({_html_escape(report.get('end_tm', ''))})
                </div>"""
                html_content = report.get("html", "")
                if html_content:
                    html_parts.append(header + html_content)
                else:
                    html_parts.append(header + "<div>HTML 콘텐츠가 없습니다.</div>")

        # 구분선으로 여러 리포트 연결
        separator = (
            '<hr style="margin:20px 0;border:none;border-top:2px dashed #e5e7eb;">'
        )
        return separator.join(html_parts)

    # 단일 dict인 경우 (이전 버전 호환)
    if isinstance(payload, dict):
        if payload.get("error"):
            msg = (
                payload.get("detail") or payload.get("error") or "오류가 발생했습니다."
            )
            return f"<div>WADS 리포트 오류: {_html_escape(msg)}</div>"

        html_content = payload.get("html", "")
        if html_content:
            return html_content

    return "<div>HTML 콘텐츠가 없습니다.</div>"


# -------------------- LangGraph 노드용 래퍼 함수 --------------------
def wads_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    naive_rag.py의 LangGraph 노드로 연결되는 WADS 처리 함수.
    """
    logger.info("[WADS Agent] 시작")

    q = state["question"]
    wads_ctx = state.get("wads_ctx") or {}
    logger.debug("question: %s, wads_ctx: %s", q, wads_ctx)

    messages = state.get("messages", [])

    # 요청별 격리된 저장소 초기화
    storage: Dict[str, Any] = {"reports": []}
    _tool_payload_var.set(storage)

    agent = _create_wads_agent()
    result = agent.invoke(
        {"messages": messages + [HumanMessage(content=q)]},
        config={"callbacks": _lf_callbacks()},
    )

    logger.debug("result messages count: %d", len(result.get("messages", [])))

    ai_messages = [m for m in result.get("messages", []) if isinstance(m, AIMessage)]
    answer = ai_messages[-1].content if ai_messages else "WADS 조회에 실패했습니다."

    query_payload = storage.get("query")
    reports_payload = storage.get("reports", [])

    logger.debug("query_payload: %s, reports_payload count: %d", query_payload is not None, len(reports_payload))

    artifacts = []
    kind = None

    if reports_payload:
        kind = "report"
        html = _render_wads_report_html(reports_payload)
    elif query_payload:
        kind = "query"
        html = _render_wads_query_html(query_payload)
    else:
        html = None

    if html:
        artifacts = [
            {
                "type": "html",
                "mime": "text/html",
                "data": html,
                "title": f"wads_{kind}",
            }
        ]

    result_dict = {
        "answer": answer,
        "artifacts": artifacts,
        "messages": [HumanMessage(content=q), AIMessage(content=answer)],
        "wads_ctx": {
            "active": True,
            "last_kind": kind,
        },
    }
    return result_dict


# -------------------- WADS Agent Node (LangGraph 노드용) --------------------
@observe(name="wads_agent_node")
@timed
def wads_agent_node(state: dict, config: RunnableConfig) -> dict:
    """WADS Agent 노드: 열화 검출 리포트를 Oracle DB에서 조회"""
    lotcd = state.get("lotcd", "4SS")
    end_tm = state.get("wads_end_tm", "")
    start_tm = state.get("wads_start_tm", "")
    if not end_tm:
        end_tm = date.today().strftime("%Y-%m-%d")

    logger.info("[WADS Agent] lotcd=%s, start_tm=%s, end_tm=%s", lotcd, start_tm, end_tm)

    # 요청별 격리된 저장소 초기화
    storage: Dict[str, Any] = {"reports": []}
    _tool_payload_var.set(storage)

    if start_tm:
        query = f"{lotcd} 로트의 {start_tm}부터 {end_tm}까지 WADS 리포트를 보여줘"
    else:
        query = f"{lotcd} 로트의 {end_tm} WADS 리포트를 보여줘"
    logger.info("[WADS Agent] 쿼리: %s", query)

    try:
        sub_config = {"callbacks": _lf_callbacks(), "recursion_limit": 10}
        # prompt callable이 SystemMessage를 자동 주입하므로 HumanMessage만 전달
        result = _wads_graph.invoke(
            {"messages": [HumanMessage(content=query)]},
            config=sub_config,
        )
    except Exception as e:
        logger.error("[WADS Agent] 실행 실패: %s", e)
        error_message = AIMessage(
            content=f"WADS 리포트 조회 중 오류가 발생했습니다: {e}",
            name="wads_agent",
        )
        return {"messages": [error_message], "wads_artifacts": []}

    ai_messages = [m for m in result.get("messages", []) if isinstance(m, AIMessage)]
    answer = ai_messages[-1].content if ai_messages else "WADS 조회에 실패했습니다."

    query_payload = storage.get("query")
    reports_payload = storage.get("reports", [])

    logger.debug("[WADS Agent] query_payload: %s, reports_payload count: %d", query_payload is not None, len(reports_payload))

    artifacts = []
    if reports_payload:
        html = _render_wads_report_html(reports_payload)
        artifacts.append(
            {
                "type": "html",
                "mime": "text/html",
                "data": html,
                "title": "wads_report",
            }
        )
    elif query_payload:
        html = _render_wads_query_html(query_payload)
        artifacts.append(
            {
                "type": "html",
                "mime": "text/html",
                "data": html,
                "title": "wads_query",
            }
        )

    suggestion_match = re.search(r'\[SUGGESTION:\s*(.*?)\]', answer)
    agent_suggestion = suggestion_match.group(1).strip() if suggestion_match else ""
    answer = re.sub(r'\[SUGGESTION:.*?\]', '', answer).strip()

    result_message = AIMessage(content=answer, name="wads_agent")

    return {
        "messages": [result_message],
        "wads_artifacts": artifacts,
        "agent_suggestion": agent_suggestion,
    }


# -------------------- 직접 실행 테스트 --------------------
if __name__ == "__main__":
    from langchain_teddynote.messages import stream_graph

    print("=== WADS Agent 테스트 (StateGraph 패턴) ===\n")
    print("도구 결과는 LLM context에 포함되지 않고, 요약만 전달됩니다.\n")

    stream_graph(
        _create_wads_agent(),
        inputs={
            "messages": [HumanMessage(content="4SS 로트의 2월28일 리포트를 보여줘")]
        },
    )

    storage = _get_tool_payload()
    print("\n" + "=" * 60)
    print("[DEBUG] tool payload 내용:")
    print(f"  query: {storage.get('query') is not None}")
    reports = storage.get("reports", [])
    print(f"  reports count: {len(reports)}")

    for idx, report in enumerate(reports):
        print(f"  report[{idx}] lotcd: {report.get('lotcd')}")
        print(f"  report[{idx}] parameter: {report.get('parameter')}")
        print(f"  report[{idx}] html 길이: {len(report.get('html', ''))} 글자")

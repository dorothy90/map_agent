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
from common import timed, get_llm, html_escape as _html_escape, extract_suggestion
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
    """호출 시점의 날짜 + supervisor 파싱 컨텍스트를 포함한 system prompt 생성"""
    from datetime import datetime

    current_date = datetime.now().strftime("%Y년 %m월 %d일")
    system_prompt = WADS_SYSTEM_PROMPT_TEMPLATE.format(current_date=current_date)

    # supervisor가 파싱한 날짜/lotcd 컨텍스트 주입 (A-4: 시스템 프롬프트 방식)
    lotcd = state.get("_lotcd", "")
    start_tm = state.get("_start_tm", "")
    end_tm = state.get("_end_tm", "")
    if lotcd or end_tm:
        ctx = f"\n\n[조회 컨텍스트] lotcd={lotcd}"
        if start_tm:
            ctx += f", 기간: {start_tm} ~ {end_tm}"
        elif end_tm:
            ctx += f", 날짜: {end_tm}"
        system_prompt += ctx

    return [SystemMessage(content=system_prompt)] + list(state.get("messages", []))


_wads_graph = create_react_agent(
    model=_wads_model,
    tools=WADS_TOOLS,
    prompt=_wads_prompt,
)



# -------------------- HTML 렌더링 --------------------


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


def _render_wads_sql_html(payload: List[Dict[str, Any]]) -> str:
    """wads_query_sql 결과를 동적 컬럼 HTML로 렌더링 (I-2)"""
    if not payload:
        return "<div class='wads-empty'>SQL 쿼리 결과가 없습니다.</div>"

    first = payload[0]
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
        "<div class='wads-card'><div class='wads-title'>WADS: SQL 쿼리 결과</div>"
    )
    footer = "</div>"

    if is_error:
        msg = first.get("detail") or first.get("error") or "오류가 발생했습니다."
        body = f"<div class='wads-empty'>{_html_escape(msg)}</div>"
        return style + header + body + footer

    sub = f"<div class='wads-sub'>총 <span class='wads-badge'>{len(payload)}건</span> 조회됨</div>"

    # 동적 컬럼 헤더 생성
    col_names = list(first.keys())
    th_html = "".join(f"<th>{_html_escape(c.upper())}</th>" for c in col_names)

    rows_html = ""
    for row in payload:
        cells = ""
        for c in col_names:
            val = row.get(c, "")
            # 숫자 포맷팅
            if isinstance(val, (int, float)):
                cells += f"<td style='text-align:right'>{val:,}</td>"
            else:
                cells += f"<td>{_html_escape(val)}</td>"
        rows_html += f"<tr>{cells}</tr>"

    table = (
        "<table class='wads-table'>"
        f"<thead><tr>{th_html}</tr></thead>"
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

    # 변경1: messages에서 rewrite된 원본 쿼리 추출 (사용자 의도 보존)
    messages = state.get("messages", [])
    last_human = next(
        (m for m in reversed(messages) if isinstance(m, HumanMessage)),
        None,
    )
    query = last_human.content if last_human else f"{lotcd} 로트의 {end_tm} WADS 리포트를 보여줘"
    logger.info("[WADS Agent] 쿼리: %s", query)

    # S-3: 선택적 히스토리 필터링 — WADS 관련 메시지만 최근 3턴
    wads_history: List[Any] = []
    turn_count = 0
    for m in reversed(messages):
        if turn_count >= 3:
            break
        if isinstance(m, HumanMessage):
            wads_history.insert(0, m)
            turn_count += 1
        elif isinstance(m, AIMessage) and getattr(m, "name", "") == "wads_agent":
            wads_history.insert(0, m)

    # 현재 쿼리가 히스토리 마지막과 다르면 추가
    if not wads_history or wads_history[-1].content != query:
        wads_history.append(HumanMessage(content=query))

    logger.info("[WADS Agent] ReAct 그래프 invoke 시작 (history=%d msgs, recursion_limit=20)", len(wads_history))
    try:
        sub_config = {"callbacks": _lf_callbacks(), "recursion_limit": 20}
        # prompt callable이 SystemMessage를 자동 주입하므로 HumanMessage만 전달
        # A-4: _lotcd, _start_tm, _end_tm을 state에 포함하여 _wads_prompt가 읽을 수 있게 함
        result = _wads_graph.invoke(
            {
                "messages": wads_history,
                "_lotcd": lotcd,
                "_start_tm": start_tm,
                "_end_tm": end_tm,
            },
            config=sub_config,
        )
        # ReAct 결과 메시지 요약 로깅
        all_msgs = result.get("messages", [])
        for i, m in enumerate(all_msgs):
            mtype = type(m).__name__
            name = getattr(m, "name", "")
            tool_calls = getattr(m, "tool_calls", [])
            content_preview = (m.content[:200] if isinstance(m.content, str) else str(m.content)[:200])
            if tool_calls:
                tc_summary = ", ".join(f"{tc['name']}({tc.get('args',{})})" for tc in tool_calls)
                logger.info("[WADS Agent] ReAct msg[%d] %s(name=%s) tool_calls=[%s]", i, mtype, name, tc_summary)
            else:
                logger.info("[WADS Agent] ReAct msg[%d] %s(name=%s): %s", i, mtype, name, content_preview)
    except Exception as e:
        logger.error("[WADS Agent] 실행 실패: %s", e, exc_info=True)
        error_message = AIMessage(
            content=f"WADS 리포트 조회 중 오류가 발생했습니다: {e}",
            name="wads_agent",
        )
        return {"messages": [error_message], "wads_artifacts": []}

    ai_messages = [m for m in result.get("messages", []) if isinstance(m, AIMessage)]
    answer = ai_messages[-1].content if ai_messages else "WADS 조회에 실패했습니다."

    query_payload = storage.get("query")
    reports_payload = storage.get("reports", [])
    sql_result_payload = storage.get("sql_result")

    logger.debug(
        "[WADS Agent] query_payload: %s, reports_payload count: %d, sql_result: %s",
        query_payload is not None, len(reports_payload), sql_result_payload is not None,
    )

    # 렌더링 우선순위: reports > sql_result > query (S-1, I-2)
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
    elif sql_result_payload:
        html = _render_wads_sql_html(sql_result_payload)
        artifacts.append(
            {
                "type": "html",
                "mime": "text/html",
                "data": html,
                "title": "wads_sql_result",
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

    answer, agent_suggestion = extract_suggestion(answer)

    result_message = AIMessage(content=answer, name="wads_agent")

    return {
        "messages": [result_message],
        "wads_artifacts": artifacts,
        "agent_suggestion": agent_suggestion,
    }



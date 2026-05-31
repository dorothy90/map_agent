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
from common import (
    timed,
    get_llm,
    html_escape as _html_escape,
    extract_suggestion,
    is_transient_error,
)
from prompts import WADS_SYSTEM_PROMPT_TEMPLATE
from result_contracts import (
    attach_result_envelope,
    derive_summary_from_rows,
    extract_parameter_values,
)
from local_trace import emit_runtime_detail, preview_text
from wads_tools import WADS_TOOLS, _tool_payload_var, _get_tool_payload

# .env 로드 및 모델 설정
load_dotenv(override=True)

logger = logging.getLogger("yield_agent.wads_agent")

# WADS Agent용 모델
_wads_model = get_llm(
    model=os.getenv("WADS_AGENT_MODEL") or os.getenv("RETRIEVE_CHAIN_MODEL")
)


def _wads_result_rows(
    query_payload: Any,
    sql_result_payload: Any,
    reports_payload: Any,
) -> list[dict]:
    if isinstance(sql_result_payload, list) and sql_result_payload:
        return [row for row in sql_result_payload if isinstance(row, dict)]
    if isinstance(query_payload, list) and query_payload:
        return [row for row in query_payload if isinstance(row, dict)]
    if isinstance(reports_payload, list) and reports_payload:
        return [
            {k: v for k, v in report.items() if k != "html"}
            for report in reports_payload
            if isinstance(report, dict)
        ]
    return []


def _unique_non_empty(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _lotid_from_groupkey(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.rsplit(".", 1)[0] if "." in text else text


def _wads_parameters_from_rows(rows: list[dict]) -> list[str]:
    return extract_parameter_values(rows)


# create_react_agent로 WADS 그래프 생성 — 수동 StateGraph 대체
# prompt를 callable로 전달: 호출 시 현재 날짜를 SystemMessage로 주입
def _wads_prompt(state: dict) -> list:
    """호출 시점의 날짜 + supervisor 파싱 컨텍스트를 포함한 system prompt 생성"""
    from datetime import datetime

    current_date = datetime.now().strftime("%Y년 %m월 %d일")
    system_prompt = WADS_SYSTEM_PROMPT_TEMPLATE.format(current_date=current_date)

    # supervisor가 파싱한 날짜/lotcd/parameter 컨텍스트 주입 (A-4: 시스템 프롬프트 방식)
    lotcd = state.get("_lotcd", "")
    start_tm = state.get("_start_tm", "")
    end_tm = state.get("_end_tm", "")
    parameter = state.get("_parameter", "")
    if lotcd or end_tm or parameter:
        ctx = f"\n\n[조회 컨텍스트] lotcd={lotcd}"
        if start_tm:
            ctx += f", 기간: {start_tm} ~ {end_tm}"
        elif end_tm:
            ctx += f", 날짜: {end_tm}"
        if parameter:
            ctx += f", parameter={parameter}"
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
            f"<td>{_html_escape(row.get('lotid'))}</td>"
            f"<td>{_html_escape(row.get('wf_id'))}</td>"
            f"<td>{_html_escape(row.get('groupkey'))}</td>"
            f"<td>{_html_escape(row.get('lotcd'))}</td>"
            f"<td>{_html_escape(row.get('category'))}</td>"
            f"<td>{_html_escape(row.get('end_tm'))}</td>"
            f"<td>{_html_escape(row.get('parameter'))}</td>"
            "</tr>"
        )

    table = (
        "<table class='wads-table'>"
        "<thead><tr><th>LOT ID</th><th>WF</th><th>GROUPKEY</th><th>LOT코드</th><th>Category</th><th>종료시간</th><th>Parameter</th></tr></thead>"
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

    header = "<div class='wads-card'><div class='wads-title'>WADS: SQL 쿼리 결과</div>"
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
                    <strong>리포트 {idx + 1}</strong>: {_html_escape(report.get("lotcd", ""))} / {_html_escape(report.get("category", ""))} / {_html_escape(report.get("parameter", ""))} ({_html_escape(report.get("end_tm", ""))})
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
    parameter = state.get("fail_type", "")
    if not end_tm:
        end_tm = date.today().strftime("%Y-%m-%d")

    logger.info(
        "[WADS Agent] lotcd=%s, start_tm=%s, end_tm=%s, parameter=%r",
        lotcd,
        start_tm,
        end_tm,
        parameter,
    )
    emit_runtime_detail(
        "wads.query_context",
        {
            "lotcd": lotcd,
            "start_tm": start_tm,
            "end_tm": end_tm,
            "parameter": parameter,
            "task_goal": state.get("current_task_goal", ""),
        },
        task_id=str(state.get("current_task_id", "")),
    )

    # 요청별 격리된 저장소 초기화 — _defaults로 LLM 누락 파라미터 보완
    storage: Dict[str, Any] = {
        "reports": [],
        "_defaults": {
            "lotcd": lotcd,
            "start_tm": start_tm,
            "end_tm": end_tm,
            "parameter": parameter,
            "task_id": state.get("current_task_id", ""),
            "task_goal": state.get("current_task_goal", ""),
        },
    }
    _tool_payload_var.set(storage)

    # query 우선순위: planner가 만든 task goal > 사용자 last_human (#12 fix)
    # task goal은 planner가 task별로 분해한 명확한 의도를 담고 있어 멀티 task plan에서
    # worker가 어느 task인지 구분 가능. 큐 dispatch가 아닌 LLM-routed 분기에서는 빈 string.
    messages = state.get("messages", [])
    last_human = next(
        (m for m in reversed(messages) if isinstance(m, HumanMessage)),
        None,
    )
    task_goal = state.get("current_task_goal", "")
    query = task_goal or (
        last_human.content
        if last_human
        else f"{lotcd} 로트의 {end_tm} WADS 리포트를 보여줘"
    )
    # _wads_prompt가 시스템 프롬프트에 [조회 컨텍스트] lotcd/기간/parameter를 자동 주입하므로
    # query에 별도 embed 불필요. WADS_SYSTEM_PROMPT의 TASK SCOPE 룰이 ReAct 1회 호출 종료를 강제.
    logger.info("[WADS Agent] 쿼리: %s (task_goal=%r)", query, task_goal)

    # ReAct에 task_goal만 단일 user message로 전달 — scope를 wads task로 좁혀 recursion 방지.
    wads_history: List[Any] = [HumanMessage(content=query)]

    logger.info(
        "[WADS Agent] ReAct 그래프 invoke 시작 (history=%d msgs, recursion_limit=20)",
        len(wads_history),
    )
    try:
        sub_config = {"callbacks": _lf_callbacks(), "recursion_limit": 20}
        # prompt callable이 SystemMessage를 자동 주입하므로 HumanMessage만 전달
        # A-4: _lotcd, _start_tm, _end_tm, _parameter을 state에 포함하여 _wads_prompt가 읽을 수 있게 함
        result = _wads_graph.invoke(
            {
                "messages": wads_history,
                "_lotcd": lotcd,
                "_start_tm": start_tm,
                "_end_tm": end_tm,
                "_parameter": parameter,
            },
            config=sub_config,
        )
        # ReAct 결과 메시지 요약 로깅
        all_msgs = result.get("messages", [])
        for i, m in enumerate(all_msgs):
            mtype = type(m).__name__
            name = getattr(m, "name", "")
            tool_calls = getattr(m, "tool_calls", [])
            content_preview = (
                m.content[:200] if isinstance(m.content, str) else str(m.content)[:200]
            )
            if tool_calls:
                tc_summary = ", ".join(
                    f"{tc['name']}({tc.get('args', {})})" for tc in tool_calls
                )
                emit_runtime_detail(
                    "wads.tool_calls",
                    {
                        "message_index": i,
                        "tool_calls": [
                            {
                                "name": tc.get("name", ""),
                                "args": tc.get("args", {}),
                            }
                            for tc in tool_calls
                        ],
                    },
                    task_id=str(state.get("current_task_id", "")),
                )
                logger.info(
                    "[WADS Agent] ReAct msg[%d] %s(name=%s) tool_calls=[%s]",
                    i,
                    mtype,
                    name,
                    tc_summary,
                )
            else:
                logger.info(
                    "[WADS Agent] ReAct msg[%d] %s(name=%s): %s",
                    i,
                    mtype,
                    name,
                    content_preview,
                )
    except Exception as e:
        if is_transient_error(e):
            # transient: RetryPolicy(retry_on=is_transient_error)가 자동 재시도
            logger.warning("[WADS Agent] transient 오류, retry 위임: %s", e)
            raise
        logger.error("[WADS Agent] 영구 오류: %s", e, exc_info=True)
        error_message = AIMessage(
            content=f"WADS 리포트 조회 중 오류가 발생했습니다: {e}",
            name="wads_agent",
        )
        attach_result_envelope(
            error_message,
            logger=logger,
            source_agent="wads_agent",
            kind="summary",
            status="error",
            title="wads_result",
            summary=error_message.content,
            entities={
                "products": [lotcd] if lotcd else [],
                "parameters": [parameter] if parameter else [],
            },
            provenance={
                "task_id": state.get("current_task_id", ""),
                "task_goal": state.get("current_task_goal", ""),
            },
        )
        return {
            "messages": [error_message],
            "wads_artifacts": [],
            "past_steps": [(state.get("current_task_id", ""), f"WADS 영구 오류: {e}")],
        }

    ai_messages = [m for m in result.get("messages", []) if isinstance(m, AIMessage)]
    llm_answer = ai_messages[-1].content if ai_messages else "WADS 조회에 실패했습니다."

    query_payload = storage.get("query")
    reports_payload = storage.get("reports", [])
    sql_result_payload = storage.get("sql_result")
    sql_result_sort = storage.get("sql_result_sort") or {}

    logger.debug(
        "[WADS Agent] query_payload: %s, reports_payload count: %d, sql_result: %s",
        query_payload is not None,
        len(reports_payload),
        sql_result_payload is not None,
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

    # C1 (#카테고리 3): SQL 결과를 structured AIMessage로 messages에 push.
    # LangGraph native 패턴 — 새 state field 없이 additional_kwargs로 downstream에 structured data 전달.
    # `_resolve_chained_params`가 state.messages에서 이 메시지를 찾아 chained input 해소.
    wads_lot_ids: list[str] = []
    wads_groupkeys: list[str] = []
    wads_wf_ids: list[str] = []
    if query_payload:
        wads_groupkeys = _unique_non_empty(
            [r.get("groupkey", "") for r in query_payload]
        )
        wads_lot_ids = _unique_non_empty(
            [
                r.get("lotid", "") or _lotid_from_groupkey(r.get("groupkey", ""))
                for r in query_payload
            ]
        )
        wads_wf_ids = _unique_non_empty([r.get("wf_id", "") for r in query_payload])
    elif sql_result_payload:
        wads_groupkeys = _unique_non_empty(
            [r.get("groupkey", "") for r in sql_result_payload]
        )
        wads_lot_ids = _unique_non_empty(
            [
                r.get("lotid", "") or _lotid_from_groupkey(r.get("groupkey", ""))
                for r in sql_result_payload
            ]
        )
        wads_wf_ids = _unique_non_empty([r.get("wf_id", "") for r in sql_result_payload])
    elif reports_payload:
        report_groupkeys: list[Any] = []
        report_lot_ids: list[Any] = []
        report_wf_ids: list[Any] = []
        for report in reports_payload:
            if not isinstance(report, dict):
                continue
            report_groupkeys.extend(report.get("groupkeys") or [])
            report_lot_ids.extend(report.get("lot_ids") or [])
            report_wf_ids.extend(report.get("wf_ids") or [])
        wads_groupkeys = _unique_non_empty(report_groupkeys)
        wads_lot_ids = _unique_non_empty(
            report_lot_ids
            + [_lotid_from_groupkey(groupkey) for groupkey in wads_groupkeys]
        )
        wads_wf_ids = _unique_non_empty(report_wf_ids)
    result_rows = _wads_result_rows(query_payload, sql_result_payload, reports_payload)
    result_status = "success" if (result_rows or artifacts) else "empty"
    emit_runtime_detail(
        "wads.result_payload",
        {
            "status": result_status,
            "row_count": len(result_rows),
            "artifact_count": len(artifacts or []),
            "lot_ids": wads_lot_ids,
            "groupkeys": wads_groupkeys,
            "answer_preview": preview_text(llm_answer),
        },
        task_id=str(state.get("current_task_id", "")),
    )
    llm_answer, agent_suggestion = extract_suggestion(llm_answer)
    answer = derive_summary_from_rows(
        source_agent="wads_agent",
        rows=result_rows,
        artifacts=artifacts,
        fallback=llm_answer,
        title="wads_result",
        sort_direction=str(sql_result_sort.get("direction") or "preserve"),
    )
    result_message = AIMessage(content=answer, name="wads_agent")
    out_messages: list = [result_message]
    row_parameters = _wads_parameters_from_rows(result_rows)
    attach_result_envelope(
        result_message,
        logger=logger,
        source_agent="wads_agent",
        kind="table" if result_rows else "report",
        status=result_status,
        title="wads_result",
        summary=answer,
        rows=result_rows,
        entities={
            "lot_ids": wads_lot_ids,
            "products": [lotcd] if lotcd else [],
            "wafer_ids": wads_wf_ids,
            # WADS parameter is the same canonical parameter class as yield anomaly_params.
            "parameters": _unique_non_empty(
                ([parameter] if parameter else []) + row_parameters
            ),
        },
        artifacts=artifacts,
        provenance={
            "task_id": state.get("current_task_id", ""),
            "task_goal": state.get("current_task_goal", ""),
        },
        metadata={
            "row_count": len(result_rows),
            "artifact_count": len(artifacts or []),
        },
    )
    if wads_lot_ids:
        sql_result_msg = AIMessage(
            content=(
                f"[WADS SQL 결과] LOT ID {len(wads_lot_ids)}건: {','.join(wads_lot_ids)}"
                f" | GROUPKEY {len(wads_groupkeys)}건: {','.join(wads_groupkeys[:50])}"
                f" | 기간 {start_tm or '전체'}~{end_tm or '전체'} | parameter={parameter or '전체'}"
            ),
            name="wads_sql_result",  # supervisor LLM 호출 전 filter 대상 (내부 전달용)
            additional_kwargs={
                "wads_result": {
                    "lot_ids": wads_lot_ids,
                    "groupkeys": wads_groupkeys,
                    "parameter_filter": parameter or "",
                    "date_range": [start_tm or "", end_tm or ""],
                    "detected_count": len(wads_groupkeys) or len(wads_lot_ids),
                },
            },
        )
        out_messages.insert(0, sql_result_msg)

    return {
        "messages": out_messages,
        "wads_artifacts": artifacts,
        "agent_suggestion": agent_suggestion,
        "past_steps": [
            (
                state.get("current_task_id", ""),
                answer[:300]
                + (
                    f" | detected_lots({len(wads_lot_ids)}): {wads_lot_ids[:5]}"
                    f", detected_groupkeys({len(wads_groupkeys)}): {wads_groupkeys[:5]}"
                    f", detected_params: {[parameter] if parameter else []}"
                    if wads_lot_ids
                    else ""
                ),
            )
        ],
    }

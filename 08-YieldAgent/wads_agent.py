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
from artifact_context import save_artifact
from wads_tools import (
    WADS_TOOLS,
    _category_to_map_oper,
    _tool_payload_var,
    _get_tool_payload,
)

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


def _row_groupkey(row: dict[str, Any]) -> Any:
    return row.get("groupkey") or row.get("group_key")


def _append_unique_text(target: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if text and text not in target:
        target.append(text)


def _groupkeys_by_map_oper_from_rows(rows: Any) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    if not isinstance(rows, list):
        return grouped

    for row in rows:
        if not isinstance(row, dict):
            continue
        map_oper = str(row.get("map_oper") or "").strip().upper()
        if not map_oper:
            map_oper = _category_to_map_oper(row.get("category"))
        if not map_oper:
            continue

        groupkeys = row.get("groupkeys")
        if not isinstance(groupkeys, list):
            groupkeys = [_row_groupkey(row)]

        bucket = grouped.setdefault(map_oper, [])
        for groupkey in groupkeys:
            _append_unique_text(bucket, groupkey)

    return {oper: groupkeys for oper, groupkeys in grouped.items() if groupkeys}


def _wads_parameters_from_rows(rows: list[dict]) -> list[str]:
    return extract_parameter_values(rows)


def _norm_map_oper(raw: str) -> str:
    """map_oper 포맷 정규화('1h'→'PT1H', 'pt1c'→'PT1C'). 의미 판단 아님 — 형식만."""
    v = str(raw or "").strip().upper()
    if v in ("PT1H", "PT1C"):
        return v
    if v in ("1H", "1C"):
        return f"PT{v}"
    if v.startswith("PT1") and len(v) > 3 and v[3] in ("H", "C"):
        return v[:4]
    return ""


def _postwads_option_set(reports: list[dict], lotcd: str, wads_category: str = "") -> list[dict]:
    """검출 후속 분석종류 3옵션(맵/불량이력/연계분석)을 주어진 report들로 스코핑해 만든다.
    각 옵션 slots에 per-report fan-out 입력(map_groups/fail_groups/rt_groups)을 prebuilt로 싣는다 —
    supervisor는 선택된 옵션을 그대로 task로 치환만 한다. reports=[]면 그룹 없이(=lot-only fallback,
    dispatch chained/missing_param가 보완). reports=[단일]이면 그 report 1건으로 스코핑.
    wads_category(공정)는 god-state 대신 연계분석 task.params로 명시 운반(relation→wt_resp→mining)."""
    map_groups: list[dict] = []
    for r in reports:
        gks = [str(g).strip() for g in (r.get("groupkeys") or []) if str(g).strip()]
        if not gks:
            continue
        map_groups.append({
            "parameter": str(r.get("parameter") or ""),
            "map_oper": _norm_map_oper(str(r.get("map_oper") or "")),
            "groupkeys": gks,
        })
    map_slots: dict[str, Any] = {"lotcd": lotcd, "map_type": "cummap"}
    if map_groups:
        union: list[str] = []
        for g in map_groups:
            for gk in g["groupkeys"]:
                if gk not in union:
                    union.append(gk)
        map_slots.update({
            "map_groups": map_groups,
            "groupkey": ",".join(union),
            "map_oper": map_groups[0]["map_oper"],
        })

    params: list[str] = []
    groups: list[dict] = []
    for r in reports:
        p = str(r.get("parameter") or "").strip()
        if p and p not in params:
            params.append(p)
        if p:
            groups.append({
                "lotcd": str(r.get("lotcd") or lotcd).strip(),
                "parameter": p,
                "lot_ids": [str(x).strip() for x in (r.get("lot_ids") or []) if str(x).strip()],
            })
    fail_type = ",".join(params)
    fail_slots: dict[str, Any] = {"lotcd": lotcd, "fail_type": fail_type}
    rt_slots: dict[str, Any] = {"lotcd": lotcd, "fail_type": fail_type}
    if wads_category:  # 공정 narrowing을 연계분석으로 운반(god-state 폐기)
        rt_slots["wads_category"] = wads_category
    if groups:
        fail_slots["fail_groups"] = groups
        rt_slots["rt_groups"] = groups

    return [
        {"label": "cummap/binmap 맵 조회", "value": "map_agent", "agent": "map_agent",
         "slots": map_slots, "goal": f"{lotcd} WADS 검출 wafer cummap 조회"},
        {"label": "불량이력 조회", "value": "fail_history_agent", "agent": "fail_history_agent",
         "slots": fail_slots, "goal": f"{lotcd} {fail_type or '검출 파라미터'} 불량이력 검색".strip()},
        {"label": "LOTCD 연계 분석", "value": "relation_tree_agent", "agent": "relation_tree_agent",
         "slots": rt_slots, "goal": f"{lotcd} {fail_type or '검출 파라미터'} Inline-WT 연관 분석".strip()},
    ]


def _build_postwads_followup(report_rows: list[dict], lotcd: str, wads_category: str = "") -> dict:
    """WADS 검출 후속 선택을 선언적 followup으로 구성(트리거+데이터+메뉴를 결과 에이전트가 소유).
    report breakdown 있으면 2-step(report별 fail_type 1차 → 분석종류 2차), 없으면(lot-only) 1-step
    단일 옵션셋. supervisor의 generic _resolve_single_choice가 옵션 스펙만 보고 해소한다."""
    if report_rows:
        prefilter_options: list[dict] = []
        choice_option_sets: list[list[dict]] = []
        for idx, r in enumerate(report_rows):
            param = str(r.get("parameter") or "").strip()
            oper = _norm_map_oper(str(r.get("map_oper") or "")).strip()
            label = param or "(unknown)"
            prefilter_options.append({
                "label": f"{label} @ {oper}" if oper else label,
                "value": str(idx),
                "fail_type": param,
            })
            choice_option_sets.append(_postwads_option_set([r], lotcd, wads_category))
    else:
        prefilter_options = []
        choice_option_sets = [_postwads_option_set([], lotcd, wads_category)]
    return {
        "agent": "__choice__",
        "goal": "WADS 검출 후속 선택",
        "choice_kind": "single",
        "guard_key": "postwads_offered",
        "guard_agents": ["map_agent", "fail_history_agent", "relation_tree_agent"],
        "prefilter_message": "어느 fail_type의 후속을 분석할까요?",
        "prefilter_options": prefilter_options,
        "choice_message": "WADS 검출 결과로 이어서 무엇을 조회할까요?",
        "choice_option_sets": choice_option_sets,
    }


def _anomaly_overlap_note(state: dict, detected_params: list[str]) -> str:
    """이번 WADS 조회 기간 검출 파라미터 중 yield 열화감지(anomaly_params)와 겹치는 항목을
    사후 언급한다 — 필터링이 아니라 단순 교차참조. yield→wads 체인에서만 의미가 있어
    anomaly_params가 비어 있으면(standalone wads) 빈 문자열을 반환해 아무 것도 덧붙이지 않는다.
    겹치는 항목이 있으면 그 목록을, 없으면 '없습니다'만 언급한다."""
    anomalies = state.get("anomaly_params") or []
    if not anomalies:
        return ""
    detected = {str(p).strip().upper() for p in (detected_params or []) if str(p).strip()}
    overlap = [a for a in anomalies if str(a.get("param", "")).strip().upper() in detected]
    if not overlap:
        return "\n\n---\n\n📌 이번 조회 기간 열화감지 검출 파라미터 중 수율 조회에서 열화파라미터로 검출된 항목은 **없습니다**."

    def _fmt(a: dict) -> str:
        extra = ", ".join(x for x in (a.get("process", ""), a.get("direction", "")) if x)
        param = a.get("param", "")
        return f"{param}({extra})" if extra else str(param)

    items = ", ".join(_fmt(a) for a in overlap)
    return f"\n\n---\n\n📌 이번 조회 기간 열화감지 검출 파라미터 중 수율 조회에서 **열화파라미터로 검출된 검출된 항목**: {items}"


def _is_wafer_list_request(text: str) -> bool:
    lowered = str(text or "").lower()
    wafer_terms = ("wafer", "웨이퍼", "wf", "groupkey", "group key")
    list_terms = ("list", "리스트", "목록", "검출")
    return any(term in lowered for term in wafer_terms) and any(
        term in lowered for term in list_terms
    )


def _query_with_slot_context(
    query: str,
    *,
    lotcd: str,
    start_tm: str,
    end_tm: str,
    parameter: str,
    category: str = "",
) -> str:
    slots: list[str] = []
    if lotcd:
        slots.append(f"- lotcd: {lotcd}")
    if start_tm or end_tm:
        slots.append(f"- date_range: {start_tm or '전체'} ~ {end_tm or '전체'}")
    if parameter:
        slots.append(f"- parameter: {parameter}")
    if category:
        slots.append(f"- category: {category} (공정 — PT1H_TEST/PT1C_TEST 중 해당 공정만)")
    if not slots:
        return query
    return (
        f"{query}\n\n"
        "[실행 슬롯]\n"
        + "\n".join(slots)
        + "\n위 실행 슬롯은 supervisor가 확정한 값입니다. "
        "도구 호출과 SQL 설명에서 이 값을 그대로 사용하고 다른 parameter로 대체하지 마세요."
    )


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
    category = state.get("_category", "")
    if lotcd or end_tm or parameter:
        ctx = f"\n\n[조회 컨텍스트] lotcd={lotcd}"
        if start_tm:
            ctx += f", 기간: {start_tm} ~ {end_tm}"
        elif end_tm:
            ctx += f", 날짜: {end_tm}"
        if parameter:
            ctx += f", parameter={parameter}"
        if category:
            ctx += f", category={category}(공정)"
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
    state = {**state, **((state.get("current_task") or {}).get("params") or {})}  # S1-a: task params 우선, scalar fallback
    lotcd = state.get("lotcd", "")
    end_tm = state.get("wads_end_tm", "")
    start_tm = state.get("wads_start_tm", "")
    parameter = state.get("fail_type", "")
    category = state.get("wads_category", "")
    if not end_tm:
        end_tm = date.today().strftime("%Y-%m-%d")

    logger.info(
        "[WADS Agent] lotcd=%s, start_tm=%s, end_tm=%s, parameter=%r",
        lotcd,
        start_tm,
        end_tm,
        parameter,
    )
    logger.warning(
        "[WADS Agent] context: lotcd=%s, start_tm=%s, end_tm=%s, parameter=%r, goal=%r",
        lotcd,
        start_tm,
        end_tm,
        parameter,
        state.get("current_task_goal", ""),
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
            "category": category,
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
    query_for_worker = _query_with_slot_context(
        query,
        lotcd=lotcd,
        start_tm=start_tm,
        end_tm=end_tm,
        parameter=parameter,
        category=category,
    )
    logger.info("[WADS Agent] 쿼리: %s (task_goal=%r)", query_for_worker, task_goal)

    # ReAct에는 task goal과 supervisor가 확정한 slot만 전달한다.
    wads_history: List[Any] = [HumanMessage(content=query_for_worker)]

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
                "_category": category,
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
                logger.warning("[WADS Agent] tool_calls=[%s]", tc_summary)
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
    query_stats = storage.get("query_stats") or {}
    report_stats = storage.get("report_stats") or {}

    logger.debug(
        "[WADS Agent] query_payload: %s, reports_payload count: %d, sql_result: %s",
        query_payload is not None,
        len(reports_payload),
        sql_result_payload is not None,
    )
    if not reports_payload and not sql_result_payload and not query_payload:
        logger.warning("[WADS Agent] no tool payload produced. answer=%s", llm_answer[:300])

    # 렌더링 우선순위: reports > sql_result > query (S-1, I-2)
    artifacts = []
    if reports_payload:
        html = _render_wads_report_html(reports_payload)
        ref = save_artifact(html, "text/html", "wads_report", "wads_agent", "html")
        artifacts.append(
            {
                "type": "html",
                "mime": "text/html",
                "artifact_ref": ref.model_dump(),
                "title": "wads_report",
            }
        )
    elif sql_result_payload:
        html = _render_wads_sql_html(sql_result_payload)
        ref = save_artifact(html, "text/html", "wads_sql_result", "wads_agent", "html")
        artifacts.append(
            {
                "type": "html",
                "mime": "text/html",
                "artifact_ref": ref.model_dump(),
                "title": "wads_sql_result",
            }
        )
    elif query_payload:
        html = _render_wads_query_html(query_payload)
        ref = save_artifact(html, "text/html", "wads_query", "wads_agent", "html")
        artifacts.append(
            {
                "type": "html",
                "mime": "text/html",
                "artifact_ref": ref.model_dump(),
                "title": "wads_query",
            }
        )

    # C1 (#카테고리 3): SQL 결과를 structured AIMessage로 messages에 push.
    # LangGraph native 패턴 — 새 state field 없이 additional_kwargs로 downstream에 structured data 전달.
    # `_resolve_chained_params`가 state.messages에서 이 메시지를 찾아 chained input 해소.
    wads_lot_ids: list[str] = []
    wads_groupkeys: list[str] = []
    wads_wf_ids: list[str] = []
    wads_groupkeys_by_map_oper: dict[str, list[str]] = {}
    if query_payload:
        wads_groupkeys_by_map_oper = _groupkeys_by_map_oper_from_rows(query_payload)
        wads_groupkeys = _unique_non_empty(
            [_row_groupkey(r) for r in query_payload]
        )
        wads_lot_ids = _unique_non_empty(
            [
                r.get("lotid", "") or _lotid_from_groupkey(_row_groupkey(r))
                for r in query_payload
            ]
        )
        wads_wf_ids = _unique_non_empty([r.get("wf_id", "") for r in query_payload])
    elif sql_result_payload:
        wads_groupkeys_by_map_oper = _groupkeys_by_map_oper_from_rows(sql_result_payload)
        wads_groupkeys = _unique_non_empty(
            [_row_groupkey(r) for r in sql_result_payload]
        )
        wads_lot_ids = _unique_non_empty(
            [
                r.get("lotid", "") or _lotid_from_groupkey(_row_groupkey(r))
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
        wads_groupkeys_by_map_oper = _groupkeys_by_map_oper_from_rows(reports_payload)
        wads_groupkeys = _unique_non_empty(report_groupkeys)
        wads_lot_ids = _unique_non_empty(
            report_lot_ids
            + [_lotid_from_groupkey(groupkey) for groupkey in wads_groupkeys]
        )
        wads_wf_ids = _unique_non_empty(report_wf_ids)
    wads_map_opers = [
        oper
        for oper, groupkeys in wads_groupkeys_by_map_oper.items()
        if groupkeys
    ]
    result_rows = _wads_result_rows(query_payload, sql_result_payload, reports_payload)
    active_stats = (
        query_stats
        if isinstance(query_stats, dict) and query_stats
        else report_stats
        if isinstance(report_stats, dict) and report_stats
        else {}
    )
    join_missing = bool(active_stats.get("join_missing"))
    wafer_list_request = _is_wafer_list_request(query)
    if join_missing and wafer_list_request:
        result_status = "partial"
        logger.warning(
            "[WADS Agent] wafer list requested but no GROUPKEY rows: "
            "report_rows=%s joined_rows=%s missing_groupkey_rows=%s query=%s",
            active_stats.get("report_rows"),
            active_stats.get("joined_rows"),
            active_stats.get("missing_groupkey_rows"),
            query,
        )
    else:
        result_status = "success" if (result_rows or artifacts) else "empty"
    emit_runtime_detail(
        "wads.result_payload",
        {
            "status": result_status,
            "row_count": len(result_rows),
            "artifact_count": len(artifacts or []),
            "report_row_count": active_stats.get("report_rows"),
            "joined_row_count": active_stats.get("joined_rows"),
            "wafer_groupkey_count": active_stats.get("unique_groupkeys"),
            "missing_groupkey_rows": active_stats.get("missing_groupkey_rows"),
            "join_missing": join_missing,
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
    if join_missing and wafer_list_request:
        answer = (
            "WADS 리포트는 "
            f"{active_stats.get('report_rows', 0)}건 조회됐지만 연결된 wafer GROUPKEY는 0건입니다. "
            "DF_WADS_WF_LIST의 LOT_CD, OPER_PARA, END_TM 날짜 조인 키를 확인해야 합니다."
        )
    row_parameters = _wads_parameters_from_rows(result_rows)
    # yield 열화감지(anomaly_params)와의 교차참조 사후 언급 — 필터가 아니라 단순 언급.
    # 조인 실패 에러 메시지 경로에는 붙이지 않는다.
    if not (join_missing and wafer_list_request):
        answer += _anomaly_overlap_note(state, row_parameters)
    result_message = AIMessage(content=answer, name="wads_agent")
    out_messages: list = [result_message]
    # carry-both (i): keep the per-report structure (html-stripped) alongside the
    # displayed rows so report-ordinal (#RN) can resolve the Nth report even when
    # `result_rows` is per-wafer (sql_result-driven). Order by END_TM DESC so the
    # channel matches the displayed table (SQL ORDER BY END_TM DESC) — i.e. "첫번째
    # 리포트" == the first report the user sees — instead of get_html_report call order.
    report_index_rows = sorted(
        (
            {k: v for k, v in report.items() if k != "html"}
            for report in (reports_payload or [])
            if isinstance(report, dict)
        ),
        key=lambda rep: str(rep.get("end_tm") or ""),
        reverse=True,
    )
    # S2: 검출(groupkey/lot) 발생 시 후속 선택(map/불량이력/연계분석)을 envelope에 선언한다.
    # 트리거+옵션+per-report 데이터를 결과 생성 에이전트가 소유 — 2-step 선택(fail_type→분석종류)과
    # per-report 그룹(map_groups/fail_groups/rt_groups)을 followup 스펙에 prebuilt로 싣고,
    # supervisor의 generic _resolve_single_choice가 옵션 스펙만 보고 기계적으로 해소한다.
    followups = []
    if wads_groupkeys or wads_lot_ids:
        followups = [_build_postwads_followup(report_index_rows, lotcd, category)]
    attach_result_envelope(
        result_message,
        logger=logger,
        source_agent="wads_agent",
        kind="table" if result_rows else "report",
        status=result_status,
        title="wads_result",
        summary=answer,
        rows=result_rows,
        extensions=(
            {"wads_agent": {"reports": report_index_rows}} if report_index_rows else None
        ),
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
            "report_row_count": active_stats.get("report_rows"),
            "joined_row_count": active_stats.get("joined_rows"),
            "wafer_groupkey_count": active_stats.get("unique_groupkeys"),
            "missing_groupkey_rows": active_stats.get("missing_groupkey_rows"),
            "wads_join_missing": join_missing,
            # cross-turn time-window 인계: 이 결과를 만든 실행 조회 기간을 durable하게 실어
            # recent_results → planner가 report 후속에서 같은 window를 재사용할 수 있게 한다.
            "wads_start_tm": start_tm or "",
            "wads_end_tm": end_tm or "",
        },
        followups=followups,
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
                    "map_oper": wads_map_opers[0] if len(wads_map_opers) == 1 else "",
                    "map_opers": wads_map_opers,
                    "groupkeys_by_map_oper": wads_groupkeys_by_map_oper,
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

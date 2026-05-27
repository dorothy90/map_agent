from __future__ import annotations

import logging
import re
import uuid
from dotenv import load_dotenv
import os
from datetime import date
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent

from langfuse import observe

from lf_utils import lf_callbacks as _lf_callbacks
from common import timed, get_llm, html_escape as _html_escape, extract_suggestion, extract_render_mode, is_transient_error
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
            f"<td>{_html_escape(row.get('lotcd'))}</td>"
            f"<td>{_html_escape(row.get('category'))}</td>"
            f"<td>{_html_escape(row.get('parameter'))}</td>"
            f"<td>{_html_escape(row.get('end_tm'))}</td>"
            "</tr>"
        )

    table = (
        "<table class='wads-table'>"
        "<thead><tr>"
        "<th>LOTCD</th><th>CATEGORY</th><th>PARAMETER</th><th>END_TM</th>"
        "</tr></thead>"
        f"<tbody>{rows_html}</tbody></table>"
    )

    return style + header + sub + table + footer


# ── SQL 결과 HTML 렌더링 — 레이아웃 레지스트리 ──────────────────────

_WADS_STYLE = """<style>
.wads-card{border:1px solid #e5e7eb;border-radius:12px;padding:12px;background:#fff}
.wads-title{font-weight:700;margin-bottom:8px;color:#1a1a2e}
.wads-sub{color:#6b7280;font-size:12px;margin-bottom:10px}
.wads-table{width:100%;border-collapse:collapse;font-size:13px}
.wads-table th,.wads-table td{border:1px solid #e5e7eb;padding:8px;text-align:left;vertical-align:top}
.wads-table th{background:#f8f9fa}
.wads-badge{display:inline-block;padding:2px 8px;border-radius:999px;background:#e0f2fe;color:#0369a1;font-size:12px}
.wads-empty{color:#6b7280}
.wads-kpi-grid{display:flex;gap:12px;flex-wrap:wrap}
.wads-kpi{flex:1;min-width:120px;border:1px solid #e5e7eb;border-radius:10px;padding:14px;background:#f8fafc;text-align:center}
.wads-kpi-num{font-size:28px;font-weight:700;color:#0f172a;line-height:1.2}
.wads-kpi-cap{font-size:12px;color:#64748b;margin-top:4px;text-transform:uppercase;letter-spacing:.04em}
.wads-bar-row{display:flex;align-items:center;gap:8px;margin:4px 0;font-size:13px}
.wads-bar-label{flex:0 0 30%;color:#334155;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wads-bar-track{flex:1;background:#f1f5f9;border-radius:6px;height:18px;position:relative;overflow:hidden}
.wads-bar-fill{background:#3b82f6;height:100%;border-radius:6px}
.wads-bar-num{flex:0 0 auto;font-variant-numeric:tabular-nums;color:#1f2937;min-width:56px;text-align:right}
.wads-trend-svg{width:100%;height:120px;display:block}
</style>"""


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _looks_like_date_col(name: str) -> bool:
    n = name.lower()
    return ("end_tm" in n) or ("date" in n) or n.endswith("_tm")


def _detect_layout(payload: List[Dict[str, Any]]) -> str:
    """SQL 결과 shape 로부터 적절한 layout 자동 감지.

    반환: 'kpi' | 'distribution' | 'pivot' | 'trend' | 'table'
    """
    if not payload:
        return "table"
    first = payload[0]
    cols = list(first.keys())
    ncols = len(cols)
    nrows = len(payload)

    # kpi: 1행 × 1-3 컬럼, 대부분 숫자
    if nrows == 1 and 1 <= ncols <= 3:
        num_cnt = sum(1 for c in cols if _is_number(first.get(c)))
        if num_cnt >= 1:
            return "kpi"

    # trend: END_TM/date 컬럼 + 수치 컬럼이 있는 경우
    has_date = any(_looks_like_date_col(c) for c in cols)
    has_num = any(_is_number(payload[0].get(c)) for c in cols if not _looks_like_date_col(c))
    if has_date and has_num and nrows >= 2:
        return "trend"

    # distribution: 2 컬럼 (범주, 숫자), 행 2-30개
    if ncols == 2 and 2 <= nrows <= 30:
        num_cols = [c for c in cols if _is_number(first.get(c))]
        if len(num_cols) == 1:
            return "distribution"

    # pivot: 3+ 컬럼, 앞 2개 범주축 (모두 문자열) — 단순 휴리스틱
    if ncols >= 3:
        first_two_str = all(
            not _is_number(first.get(cols[i])) for i in range(2)
        )
        if first_two_str:
            return "pivot"

    return "table"


def _render_kpi(payload: List[Dict[str, Any]]) -> str:
    row = payload[0]
    cells = []
    for k, v in row.items():
        if _is_number(v):
            num = f"{v:,}"
        elif v is None:
            num = "—"
        else:
            num = _html_escape(v)
        cells.append(
            f"<div class='wads-kpi'><div class='wads-kpi-num'>{num}</div>"
            f"<div class='wads-kpi-cap'>{_html_escape(k.upper())}</div></div>"
        )
    return f"<div class='wads-kpi-grid'>{''.join(cells)}</div>"


def _render_distribution(payload: List[Dict[str, Any]]) -> str:
    cols = list(payload[0].keys())
    cat_col = next(c for c in cols if not _is_number(payload[0].get(c)))
    num_col = next(c for c in cols if _is_number(payload[0].get(c)))
    sorted_rows = sorted(payload, key=lambda r: r.get(num_col) or 0, reverse=True)
    max_v = max((r.get(num_col) or 0) for r in sorted_rows) or 1
    total = sum((r.get(num_col) or 0) for r in sorted_rows) or 1
    bars = []
    for r in sorted_rows:
        v = r.get(num_col) or 0
        pct_track = (v / max_v) * 100
        pct_total = (v / total) * 100
        bars.append(
            "<div class='wads-bar-row'>"
            f"<div class='wads-bar-label'>{_html_escape(r.get(cat_col))}</div>"
            f"<div class='wads-bar-track'><div class='wads-bar-fill' style='width:{pct_track:.1f}%'></div></div>"
            f"<div class='wads-bar-num'>{v:,} ({pct_total:.1f}%)</div>"
            "</div>"
        )
    return (
        f"<div style='font-size:12px;color:#64748b;margin-bottom:6px'>{_html_escape(num_col.upper())} by {_html_escape(cat_col.upper())}</div>"
        + "".join(bars)
    )


def _render_pivot(payload: List[Dict[str, Any]]) -> str:
    # 단순 휴리스틱: 앞 2개 컬럼이 범주, 마지막 컬럼이 값
    cols = list(payload[0].keys())
    if len(cols) < 3:
        return _render_table(payload)
    row_col, col_col = cols[0], cols[1]
    val_col = cols[-1]
    row_keys: list = []
    col_keys: list = []
    pivot: Dict[Any, Dict[Any, Any]] = {}
    for r in payload:
        rk, ck, v = r.get(row_col), r.get(col_col), r.get(val_col)
        if rk not in row_keys:
            row_keys.append(rk)
        if ck not in col_keys:
            col_keys.append(ck)
        pivot.setdefault(rk, {})[ck] = v
    header = "<th>" + _html_escape(row_col.upper()) + "</th>" + "".join(
        f"<th>{_html_escape(c)}</th>" for c in col_keys
    )
    body = ""
    for rk in row_keys:
        cells = "<td>" + _html_escape(rk) + "</td>"
        for ck in col_keys:
            v = pivot.get(rk, {}).get(ck, "")
            cells += (
                f"<td style='text-align:right'>{v:,}</td>" if _is_number(v) else f"<td>{_html_escape(v)}</td>"
            )
        body += f"<tr>{cells}</tr>"
    return (
        f"<table class='wads-table'><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"
    )


def _render_trend(payload: List[Dict[str, Any]]) -> str:
    cols = list(payload[0].keys())
    date_col = next(c for c in cols if _looks_like_date_col(c))
    num_col = next(c for c in cols if c != date_col and _is_number(payload[0].get(c)))
    points = [(str(r.get(date_col)), float(r.get(num_col) or 0)) for r in payload]
    if not points:
        return _render_table(payload)
    vals = [v for _, v in points]
    vmax = max(vals) or 1
    n = len(points)
    width, height, pad = 600, 120, 8
    inner_w = width - pad * 2
    inner_h = height - pad * 2
    coords = []
    for i, (_, v) in enumerate(points):
        x = pad + (inner_w * (i / max(n - 1, 1)))
        y = pad + (inner_h * (1 - v / vmax))
        coords.append((x, y))
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    dots = "".join(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='3' fill='#3b82f6'/>" for x, y in coords)
    labels_html = " · ".join(
        f"<span>{_html_escape(d)}: <b>{v:,.0f}</b></span>" for d, v in points
    )
    svg = (
        f"<svg class='wads-trend-svg' viewBox='0 0 {width} {height}' preserveAspectRatio='none'>"
        f"<polyline fill='none' stroke='#3b82f6' stroke-width='2' points='{path}'/>"
        f"{dots}</svg>"
    )
    return (
        f"<div style='font-size:12px;color:#64748b;margin-bottom:6px'>{_html_escape(num_col.upper())} over {_html_escape(date_col.upper())}</div>"
        + svg
        + f"<div style='font-size:11px;color:#475569;margin-top:6px;line-height:1.6'>{labels_html}</div>"
    )


def _render_table(payload: List[Dict[str, Any]]) -> str:
    col_names = list(payload[0].keys())
    th_html = "".join(f"<th>{_html_escape(c.upper())}</th>" for c in col_names)
    rows_html = ""
    for row in payload:
        cells = ""
        for c in col_names:
            val = row.get(c, "")
            if _is_number(val):
                cells += f"<td style='text-align:right'>{val:,}</td>"
            else:
                cells += f"<td>{_html_escape(val)}</td>"
        rows_html += f"<tr>{cells}</tr>"
    return (
        f"<table class='wads-table'><thead><tr>{th_html}</tr></thead>"
        f"<tbody>{rows_html}</tbody></table>"
    )


_LAYOUT_DISPATCH = {
    "kpi": _render_kpi,
    "distribution": _render_distribution,
    "pivot": _render_pivot,
    "trend": _render_trend,
    "table": _render_table,
}


def _render_wads_sql_html(payload: List[Dict[str, Any]], layout: str = "") -> str:
    """wads_query_sql 결과를 layout 별로 렌더링.

    layout 가 비어있으면 _detect_layout 으로 자동 결정.
    """
    if not payload:
        return _WADS_STYLE + "<div class='wads-card'><div class='wads-empty'>SQL 쿼리 결과가 없습니다.</div></div>"

    first = payload[0]
    if first.get("error"):
        msg = first.get("detail") or first.get("error") or "오류가 발생했습니다."
        return (
            _WADS_STYLE
            + "<div class='wads-card'><div class='wads-title'>WADS: SQL 쿼리 결과</div>"
            + f"<div class='wads-empty'>{_html_escape(msg)}</div></div>"
        )

    layout = (layout or "").lower()
    if layout not in _LAYOUT_DISPATCH:
        layout = _detect_layout(payload)

    body = _LAYOUT_DISPATCH[layout](payload)
    sub = (
        f"<div class='wads-sub'>총 <span class='wads-badge'>{len(payload)}건</span>"
        f" · layout=<span class='wads-badge'>{layout}</span></div>"
    )
    return (
        _WADS_STYLE
        + "<div class='wads-card'><div class='wads-title'>WADS: SQL 쿼리 결과</div>"
        + sub
        + body
        + "</div>"
    )


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


# ── HTML artifact → file:// swap (MongoDB BSON 16MB 한계 우회) ───────────
# WADS report HTML (Oracle CLOB) 이 state["wads_artifacts"] 에 그대로 누적되면
# LangGraph MongoDBSaver 가 체크포인트 직렬화 시 BSON 한계를 초과한다. 대신
# generated/ 에 파일로 dump 하고 state 에는 짧은 file:// reference 만 남긴 뒤,
# agent_server.py 의 SSE 발사 로직이 file:// 를 자동으로 read + inline + 삭제.
_WADS_GEN_DIR = os.path.join(os.path.dirname(__file__), "generated")
os.makedirs(_WADS_GEN_DIR, exist_ok=True)


def _save_artifact_html(html: str, prefix: str = "wads") -> str:
    """HTML 문자열을 임시 파일로 저장하고 'file://<abs_path>' 를 반환."""
    fname = f"{prefix}_{uuid.uuid4().hex[:8]}.html"
    fpath = os.path.join(_WADS_GEN_DIR, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(html)
    return f"file://{fpath}"


def _make_html_artifact(html: str, title: str) -> Dict[str, Any]:
    """HTML 카드를 file:// reference 형태의 artifact dict 로 변환.

    state 에 들어가는 dict 의 data 는 짧은 file:// 한 줄뿐 — MongoDB BSON 부담 0.
    """
    return {
        "type": "html",
        "mime": "text/html",
        "data": _save_artifact_html(html, prefix=title),
        "title": title,
    }


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

    logger.info("[WADS Agent] lotcd=%s, start_tm=%s, end_tm=%s, parameter=%r", lotcd, start_tm, end_tm, parameter)

    # 요청별 격리된 저장소 초기화 — _defaults로 LLM 누락 파라미터 보완
    storage: Dict[str, Any] = {
        "reports": [],
        "_defaults": {"lotcd": lotcd, "start_tm": start_tm, "end_tm": end_tm, "parameter": parameter},
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
    query = task_goal or (last_human.content if last_human else f"{lotcd} 로트의 {end_tm} WADS 리포트를 보여줘")
    # _wads_prompt가 시스템 프롬프트에 [조회 컨텍스트] lotcd/기간/parameter를 자동 주입하므로
    # query에 별도 embed 불필요. WADS_SYSTEM_PROMPT의 TASK SCOPE 룰이 ReAct 1회 호출 종료를 강제.
    logger.info("[WADS Agent] 쿼리: %s (task_goal=%r)", query, task_goal)

    # ReAct에 task_goal만 단일 user message로 전달 — scope를 wads task로 좁혀 recursion 방지.
    wads_history: List[Any] = [HumanMessage(content=query)]

    logger.info("[WADS Agent] ReAct 그래프 invoke 시작 (history=%d msgs, recursion_limit=20)", len(wads_history))
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
            content_preview = (m.content[:200] if isinstance(m.content, str) else str(m.content)[:200])
            if tool_calls:
                tc_summary = ", ".join(f"{tc['name']}({tc.get('args',{})})" for tc in tool_calls)
                logger.info("[WADS Agent] ReAct msg[%d] %s(name=%s) tool_calls=[%s]", i, mtype, name, tc_summary)
            else:
                logger.info("[WADS Agent] ReAct msg[%d] %s(name=%s): %s", i, mtype, name, content_preview)
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
        return {
            "messages": [error_message],
            "wads_artifacts": [],
            "past_steps": [(state.get("current_task_id", ""), f"WADS 영구 오류: {e}")],
        }

    ai_messages = [m for m in result.get("messages", []) if isinstance(m, AIMessage)]
    answer = ai_messages[-1].content if ai_messages else "WADS 조회에 실패했습니다."

    query_payload = storage.get("query")
    reports_payload = storage.get("reports", [])
    sql_result_payload = storage.get("sql_result")
    wf_list_payload = storage.get("wf_list")

    logger.debug(
        "[WADS Agent] query_payload: %s, reports_payload count: %d, sql_result: %s, wf_list: %s",
        query_payload is not None, len(reports_payload),
        sql_result_payload is not None, wf_list_payload is not None,
    )

    # [RENDER: <mode>[:layout]] 파싱 — LLM 이 답변 본문에서 artifact 모드를 결정
    answer, render_mode, render_layout = extract_render_mode(answer, default="auto")
    answer, agent_suggestion = extract_suggestion(answer)

    has_reports = bool(reports_payload)
    has_sql = bool(sql_result_payload)
    has_query = bool(query_payload)
    has_wf_list = bool(wf_list_payload) and not (
        isinstance(wf_list_payload, list)
        and wf_list_payload
        and isinstance(wf_list_payload[0], dict)
        and wf_list_payload[0].get("error")
    )

    # 모드별 artifact 발사 결정
    artifacts: list = []
    if render_mode == "text":
        # 명시적 text 모드 — artifact 0개. LLM 본문만.
        pass
    elif render_mode == "md":
        # markdown artifact — LLM 본문을 그대로 패널에 표시
        if answer:
            artifacts.append(
                {
                    "type": "markdown",
                    "mime": "text/markdown",
                    "data": answer,
                    "title": "wads_summary",
                }
            )
    elif render_mode == "report" and has_reports:
        artifacts.append(_make_html_artifact(
            _render_wads_report_html(reports_payload), "wads_report",
        ))
    elif render_mode == "table" and (has_sql or has_query or has_wf_list):
        if has_sql:
            # LLM 지정 layout > 도구 측 힌트 > 자동 감지 (_render_wads_sql_html 내부)
            layout = render_layout or storage.get("render_layout", "")
            artifacts.append(_make_html_artifact(
                _render_wads_sql_html(sql_result_payload, layout=layout), "wads_sql_result",
            ))
        elif has_wf_list:
            # WF_LIST 결과도 SQL 렌더러 재활용 — table layout 강제
            artifacts.append(_make_html_artifact(
                _render_wads_sql_html(wf_list_payload, layout=render_layout or "table"),
                "wads_wf_list",
            ))
        else:
            artifacts.append(_make_html_artifact(
                _render_wads_query_html(query_payload), "wads_query",
            ))
    else:
        # render_mode == "auto" 또는 LLM 이 태그를 안 단 경우 → 기존 우선순위 분기
        if has_reports:
            artifacts.append(_make_html_artifact(
                _render_wads_report_html(reports_payload), "wads_report",
            ))
        elif has_sql:
            layout = render_layout or storage.get("render_layout", "")
            artifacts.append(_make_html_artifact(
                _render_wads_sql_html(sql_result_payload, layout=layout), "wads_sql_result",
            ))
        elif has_wf_list:
            artifacts.append(_make_html_artifact(
                _render_wads_sql_html(wf_list_payload, layout=render_layout or "table"),
                "wads_wf_list",
            ))
        elif has_query:
            artifacts.append(_make_html_artifact(
                _render_wads_query_html(query_payload), "wads_query",
            ))

    logger.info(
        "[WADS Agent] render_mode=%s layout=%s artifacts=%d (reports=%s sql=%s query=%s wf_list=%s)",
        render_mode, render_layout, len(artifacts),
        has_reports, has_sql, has_query, has_wf_list,
    )

    result_message = AIMessage(content=answer, name="wads_agent")

    # 검출 결과를 structured AIMessage로 messages에 push.
    # LangGraph native 패턴 — 새 state field 없이 additional_kwargs로 downstream에 structured data 전달.
    # `_resolve_chained_params`가 state.messages에서 이 메시지를 찾아 chained input 해소.
    #
    # 식별자 의미 (PR0/PR2 schema + 확인된 GROUPKEY 포맷):
    #   LOTCD/LOT_CD = 제품코드 (예: "4SS") — lot 단위 식별자 아님
    #   GROUPKEY     = "lot_id.wf_id" 형식 (예: "4SS2DPD.03") — 진짜 wafer 식별자
    # map_agent 는 lot.wf 통합 식별자를 `groupkey` 채널로 받으므로 (wf_ids 는 wafer slot 번호 int 만),
    # chain output 의 wafer 식별자는 groupkey 콤마구분 string 으로 발사한다.
    out_messages: list = [result_message]
    wads_group_keys: list[str] = []
    wads_lot_cds: list[str] = []

    wf_payload = storage.get("wf_list") or []
    wf_payload = [r for r in wf_payload if isinstance(r, dict) and not r.get("error")]
    if wf_payload:
        wads_group_keys = sorted({str(r.get("groupkey")) for r in wf_payload if r.get("groupkey")})
        wads_lot_cds = sorted({str(r.get("lot_cd")) for r in wf_payload if r.get("lot_cd")})

    # wf_list 가 비어있어도 lot_cds (제품코드) 는 REPORT/SQL 결과에서 추출 가능
    if not wads_lot_cds:
        src = []
        if query_payload and isinstance(query_payload, list):
            src += [r for r in query_payload if isinstance(r, dict) and not r.get("error")]
        if sql_result_payload and isinstance(sql_result_payload, list):
            src += [r for r in sql_result_payload if isinstance(r, dict) and not r.get("error")]
        wads_lot_cds = sorted({str(r.get("lotcd")) for r in src if r.get("lotcd")})

    if wads_group_keys or wads_lot_cds:
        groupkey_str = ",".join(wads_group_keys)
        parts = []
        if wads_group_keys:
            parts.append(f"GROUPKEY {len(wads_group_keys)}건: {','.join(wads_group_keys[:10])}")
        if wads_lot_cds:
            parts.append(f"LOTCD {len(wads_lot_cds)}건: {','.join(wads_lot_cds[:10])}")
        sql_result_msg = AIMessage(
            content=(
                f"[WADS 결과] " + " | ".join(parts)
                + f" | 기간 {start_tm or '전체'}~{end_tm or '전체'}"
                + f" | parameter={parameter or '전체'}"
            ),
            name="wads_sql_result",  # supervisor LLM 호출 전 filter 대상 (내부 전달용)
            additional_kwargs={
                "wads_result": {
                    "groupkey": groupkey_str,         # ← map_agent.groupkey 채널 (lot.wf 콤마구분)
                    "lot_cds": wads_lot_cds,          # 제품코드 (참고용 — lot id 아님)
                    "wf_ids": [],                     # WADS 에 wafer slot 번호 단독 컬럼 없음 — 명시적 빈
                    "lot_ids": [],                    # WADS schema 에 lot id 없음 — 명시적 빈
                    "parameter_filter": parameter or "",
                    "date_range": [start_tm or "", end_tm or ""],
                    "detected_count": len(wads_group_keys) or len(wads_lot_cds),
                },
            },
        )
        out_messages.insert(0, sql_result_msg)

    return {
        "messages": out_messages,
        "wads_artifacts": artifacts,
        "agent_suggestion": agent_suggestion,
        "past_steps": [(
            state.get("current_task_id", ""),
            answer[:300] + (
                f" | detected_wafers({len(wads_group_keys)}): {wads_group_keys[:5]}"
                f", lot_cds: {wads_lot_cds[:5]}"
                f", detected_params: {[parameter] if parameter else []}"
                if (wads_group_keys or wads_lot_cds) else ""
            ),
        )],
    }



"""
LOT History Agent 노드
=======================
5개 Oracle 테이블에서 LOT_ID 종합 이력을 조회하고
HTML 아티팩트로 렌더링합니다.
"""
from __future__ import annotations

import logging
import re
import os
from typing import Any, Dict, List

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent
from langfuse import observe

from lf_utils import lf_callbacks as _lf_callbacks
from common import timed, get_llm, html_escape as _h, extract_suggestion, is_transient_error
from lot_history_tools import LOT_HISTORY_TOOLS, _tool_payload_var, _get_tool_payload

load_dotenv(override=True)

logger = logging.getLogger("yield_agent.lot_history_agent")

_lh_model = get_llm(model=os.getenv("RETRIEVE_CHAIN_MODEL"))

_LH_SYSTEM_PROMPT = """\
당신은 반도체 LOT 종합 이력 조회 에이전트입니다.

=== LOT ID 입력 우선순위 (반드시 지킬 것) ===
1. **시스템 프롬프트 끝에 `[조회 컨텍스트] lot_ids=...`가 주입되어 있으면 그 lot_ids를 그대로 사용해 즉시 query_lot_history 도구를 호출하라. 사용자에게 LOT ID를 다시 묻지 마라. 이는 supervisor가 이전 task 결과(예: WADS 검출)에서 추출한 LOT 목록이다.**
2. 사용자 자연어 메시지 안에 LOT ID(7자 영숫자, 예: 4SS2DPD)가 명시된 경우에만 그것을 사용.
3. 위 두 경우 모두 LOT ID를 찾지 못한 경우에만 사용자에게 LOT ID를 한 번 묻고 종료.

조회 후 결과를 한국어로 간결하게 요약하세요.

규칙:
- LOT_ID가 여러 개면 콤마로 구분하여 한 번에 조회
- 조회 결과 중 위험 항목(HALT, Q-TIME 큰 초과, 장시간 Hold)을 우선 언급
- 데이터가 0건인 항목은 "해당 없음"으로 간단히 처리
- 도구 호출 실패 시 최대 2회 재시도, 3회 실패 시 사용자에게 오류 보고
"""


def _lh_prompt(state: dict) -> list:
    """system prompt + 조회 컨텍스트 주입"""
    system_prompt = _LH_SYSTEM_PROMPT
    lot_ids = state.get("_lot_ids", "")
    if lot_ids:
        system_prompt += f"\n\n[조회 컨텍스트] lot_ids={lot_ids}"
    return [SystemMessage(content=system_prompt)] + list(state.get("messages", []))


_lh_graph = create_react_agent(
    model=_lh_model,
    tools=LOT_HISTORY_TOOLS,
    prompt=_lh_prompt,
)


# ── HTML 렌더링 ──────────────────────────────────────────────


_CSS = """\
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Pretendard',-apple-system,sans-serif;background:#f5f6fa;color:#222;padding:24px;max-width:50%;margin:0 auto}
.header{display:flex;align-items:center;gap:16px;margin-bottom:20px}
.header h1{font-size:20px;font-weight:700}
.header .lot-count{background:#3b82f6;color:#fff;padding:2px 10px;border-radius:12px;font-size:13px}
.summary-table{width:100%;border-collapse:collapse;margin-bottom:24px;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.07)}
.summary-table th{background:#1e293b;color:#fff;padding:10px 14px;font-size:13px;font-weight:600;text-align:center}
.summary-table td{padding:10px 14px;text-align:center;font-size:13px;border-bottom:1px solid #f0f0f0}
.summary-table tr:last-child td{border-bottom:none}
.summary-table .lot-id{text-align:left;font-weight:600;color:#1e40af}
.cnt-zero{color:#bbb}.cnt-nonzero{font-weight:700;color:#1e293b}
.risk-red{color:#dc2626;font-size:15px}.risk-yellow{color:#f59e0b;font-size:15px}.risk-green{color:#22c55e;font-size:15px}
.lot-card{background:#fff;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,0.07);margin-bottom:20px;overflow:hidden}
.lot-card-header{padding:14px 20px;background:#1e293b;color:#fff;display:flex;align-items:center;gap:12px}
.lot-card-header h2{font-size:16px;font-weight:700}
.lot-card-header .badge{padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600}
.badge-red{background:#fecaca;color:#dc2626}.badge-yellow{background:#fef3c7;color:#b45309}.badge-green{background:#dcfce7;color:#16a34a}
.section{padding:16px 20px;border-bottom:1px solid #f0f0f0}
.section:last-child{border-bottom:none}
.section-title{font-size:14px;font-weight:700;margin-bottom:10px;display:flex;align-items:center;gap:8px}
.section-title .icon{font-size:16px}
.section-title .cnt{background:#e2e8f0;color:#475569;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600}
.section-title .cnt-alert{background:#fee2e2;color:#dc2626}
.empty{color:#94a3b8;font-size:13px;padding:4px 0}
.data-table{width:100%;border-collapse:collapse;font-size:12.5px}
.data-table th{background:#f8fafc;color:#64748b;padding:7px 10px;text-align:left;font-weight:600;border-bottom:1px solid #e2e8f0}
.data-table td{padding:7px 10px;border-bottom:1px solid #f1f5f9}
.data-table tr:last-child td{border-bottom:none}
.level{padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700;display:inline-block}
.level-halt{background:#fee2e2;color:#dc2626}.level-warning{background:#fef3c7;color:#b45309}.level-watch{background:#e0f2fe;color:#0369a1}
.spec-over{color:#dc2626;font-weight:600}
.lot-summary{padding:12px 20px;background:#f8fafc;font-size:13px;color:#475569;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.lot-summary .tag{padding:2px 10px;border-radius:8px;font-size:12px;font-weight:600}
.tag-halt{background:#fee2e2;color:#dc2626}.tag-warn{background:#fef3c7;color:#b45309}
.tag-qtime{background:#ede9fe;color:#7c3aed}.tag-hold{background:#fce7f3;color:#be185d}
</style>
"""


def _risk_level(data: Dict[str, List[Dict]]) -> str:
    for row in data.get("fdc_alarm", []):
        if row.get("alarm_level_cd") == "HALT":
            return "red"
    if data.get("fdc_alarm") or data.get("qtime_over") or data.get("trouble_lot"):
        return "yellow"
    return "green"


def _render_fdc_section(rows: List[Dict]) -> str:
    cnt = len(rows)
    cnt_cls = "cnt-alert" if cnt else "cnt"
    html = f'<div class="section"><div class="section-title"><span class="icon">🔴</span> FDC ALARM <span class="cnt {cnt_cls}">{cnt}건</span></div>'
    if not rows:
        return html + '<div class="empty">해당 없음</div></div>'
    html += '<table class="data-table"><thead><tr><th>시간</th><th>장비</th><th>항목</th><th>레벨</th><th>결과값</th><th>SPEC 범위</th></tr></thead><tbody>'
    for r in rows:
        tm = _h(str(r.get("transfer_tm", ""))[5:16])
        level = r.get("alarm_level_cd", "")
        level_cls = f"level-{level.lower()}" if level else ""
        html += f'<tr><td>{tm}</td><td>{_h(r.get("eqp_id"))}</td><td>{_h(r.get("item_nm"))}</td>'
        html += f'<td><span class="level {level_cls}">{_h(level)}</span></td>'
        html += f'<td class="spec-over">{_h(r.get("rslt_val"))}</td>'
        html += f'<td>{_h(r.get("spec_min_val"))} ~ {_h(r.get("spec_max_val"))}</td></tr>'
    return html + '</tbody></table></div>'


def _render_qtime_section(rows: List[Dict]) -> str:
    cnt = len(rows)
    cnt_cls = "cnt-alert" if cnt else "cnt"
    html = f'<div class="section"><div class="section-title"><span class="icon">⏱</span> Q-TIME 초과 <span class="cnt {cnt_cls}">{cnt}건</span></div>'
    if not rows:
        return html + '<div class="empty">해당 없음</div></div>'
    html += '<table class="data-table"><thead><tr><th>FROM</th><th>→</th><th>TO</th><th>제한</th><th>실제</th><th>초과</th></tr></thead><tbody>'
    for r in rows:
        html += f'<tr><td>{_h(r.get("from_oper"))}</td><td>→</td><td>{_h(r.get("to_oper"))}</td>'
        html += f'<td>{_h(r.get("control_limit"))}분</td><td class="spec-over">{_h(r.get("q_time"))}분</td>'
        html += f'<td class="spec-over">{_h(r.get("bal"))}분</td></tr>'
    return html + '</tbody></table></div>'


def _render_trouble_section(rows: List[Dict]) -> str:
    cnt = len(rows)
    cnt_cls = "cnt-alert" if cnt else "cnt"
    html = f'<div class="section"><div class="section-title"><span class="icon">🔧</span> TROUBLE LOT <span class="cnt {cnt_cls}">{cnt}건</span></div>'
    if not rows:
        return html + '<div class="empty">해당 없음</div></div>'
    html += '<table class="data-table"><thead><tr><th>Hold시간</th><th>공정</th><th>원인장비</th><th>지연시간</th><th>코드</th><th>내용</th></tr></thead><tbody>'
    for r in rows:
        tm = _h(str(r.get("hold_time", ""))[5:16])
        content = _h(str(r.get("contents", ""))[:40])
        html += f'<tr><td>{tm}</td><td>{_h(r.get("step_desc"))}</td><td>{_h(r.get("cause_eq"))}</td>'
        html += f'<td>{_h(r.get("delay_time"))}</td><td>{_h(r.get("h_code"))}</td><td>{content}</td></tr>'
    return html + '</tbody></table></div>'


def _render_action_section(rows: List[Dict]) -> str:
    cnt = len(rows)
    html = f'<div class="section"><div class="section-title"><span class="icon">📌</span> FUTURE ACTION <span class="cnt">{cnt}건</span></div>'
    if not rows:
        return html + '<div class="empty">해당 없음</div></div>'
    html += '<table class="data-table"><thead><tr><th>시간</th><th>STEP</th><th>내용</th><th>FLAG</th><th>영역</th></tr></thead><tbody>'
    for r in rows:
        tm = _h(str(r.get("action_time", ""))[5:16])
        comment = _h(str(r.get("action_comment", ""))[:40])
        html += f'<tr><td>{tm}</td><td>{_h(r.get("action_step"))}</td><td>{comment}</td>'
        html += f'<td>{_h(r.get("action_flag"))}</td><td>{_h(r.get("reason_area"))}</td></tr>'
    return html + '</tbody></table></div>'


def _render_sample_section(rows: List[Dict]) -> str:
    cnt = len(rows)
    html = f'<div class="section"><div class="section-title"><span class="icon">🧪</span> SAMPLE SPLIT <span class="cnt">{cnt}건</span></div>'
    if not rows:
        return html + '<div class="empty">해당 없음</div></div>'
    html += '<table class="data-table"><thead><tr><th>이벤트</th><th>STEP</th><th>공정</th><th>슬롯</th><th>SPLIT_ID</th><th>수량</th></tr></thead><tbody>'
    for r in rows:
        html += f'<tr><td>{_h(r.get("event"))}</td><td>{_h(r.get("step"))}</td><td>{_h(r.get("oper_desc"))}</td>'
        html += f'<td>{_h(r.get("sample_slot"))}</td><td>{_h(r.get("sample_split_id"))}</td><td>{_h(r.get("qty"))}</td></tr>'
    return html + '</tbody></table></div>'


def _render_lot_summary_bar(data: Dict[str, List[Dict]]) -> str:
    """LOT 카드 하단 요약 태그 바"""
    tags = []
    halt_cnt = sum(1 for r in data.get("fdc_alarm", []) if r.get("alarm_level_cd") == "HALT")
    warn_cnt = sum(1 for r in data.get("fdc_alarm", []) if r.get("alarm_level_cd") == "WARNING")
    if halt_cnt:
        tags.append(f'<span class="tag tag-halt">HALT {halt_cnt}건</span>')
    if warn_cnt:
        tags.append(f'<span class="tag tag-warn">WARNING {warn_cnt}건</span>')
    if data.get("qtime_over"):
        tags.append(f'<span class="tag tag-qtime">Q-TIME 초과 {len(data["qtime_over"])}건</span>')
    if data.get("trouble_lot"):
        tags.append(f'<span class="tag tag-hold">Hold {len(data["trouble_lot"])}건</span>')
    if not tags:
        return '<div class="lot-summary">⚡ 요약: 특이사항 없음</div>'
    return '<div class="lot-summary">⚡ 요약: ' + " ".join(tags) + '</div>'


def _render_lot_card(lot_id: str, data: Dict[str, List[Dict]]) -> str:
    """단일 LOT 카드 HTML"""
    risk = _risk_level(data)
    risk_label = {"red": "HIGH RISK", "yellow": "MEDIUM", "green": "NORMAL"}[risk]

    # LOT_CD 추출
    lot_cd = ""
    for key in ["fdc_alarm", "qtime_over", "trouble_lot"]:
        if data.get(key):
            lot_cd = data[key][0].get("lot_cd", "")
            break

    html = f'<div class="lot-card">'
    html += f'<div class="lot-card-header"><h2>{_h(lot_id)}</h2>'
    if lot_cd:
        html += f'<span class="badge badge-{risk}">{_h(lot_cd)}</span>'
    html += f'<span class="badge badge-{risk}">● {risk_label}</span></div>'

    html += _render_fdc_section(data.get("fdc_alarm", []))
    html += _render_qtime_section(data.get("qtime_over", []))
    html += _render_trouble_section(data.get("trouble_lot", []))
    html += _render_action_section(data.get("future_action", []))
    html += _render_sample_section(data.get("sample_split", []))
    html += _render_lot_summary_bar(data)
    html += '</div>'
    return html


def _render_lot_history_html(all_results: Dict[str, Dict[str, List[Dict]]]) -> str:
    """전체 결과를 HTML로 렌더링"""
    lot_ids = list(all_results.keys())
    multi = len(lot_ids) > 1

    html = _CSS
    html += f'<div class="header"><h1>📋 LOT 종합 이력</h1>'
    if multi:
        html += f'<span class="lot-count">{len(lot_ids)} LOTs</span>'
    html += '</div>'

    # 복수 LOT: 총괄 요약 테이블
    if multi:
        html += '<table class="summary-table"><thead><tr>'
        html += '<th style="text-align:left">LOT_ID</th><th>FDC</th><th>Q-TIME</th><th>TROUBLE</th><th>ACTION</th><th>SAMPLE</th><th>위험도</th>'
        html += '</tr></thead><tbody>'
        for lid in lot_ids:
            d = all_results[lid]
            risk = _risk_level(d)
            def _cnt_td(n):
                cls = "cnt-nonzero" if n else "cnt-zero"
                return f'<td class="{cls}">{n}</td>'
            html += f'<tr><td class="lot-id">{_h(lid)}</td>'
            html += _cnt_td(len(d.get("fdc_alarm", [])))
            html += _cnt_td(len(d.get("qtime_over", [])))
            html += _cnt_td(len(d.get("trouble_lot", [])))
            html += _cnt_td(len(d.get("future_action", [])))
            html += _cnt_td(len(d.get("sample_split", [])))
            html += f'<td><span class="risk-{risk}">●</span></td></tr>'
        html += '</tbody></table>'

    # LOT별 카드
    for lid in lot_ids:
        html += _render_lot_card(lid, all_results[lid])

    return html


# ── Agent 노드 ──────────────────────────────────────────────

@observe(name="lot_history_agent_node")
@timed
def lot_history_agent_node(state: dict, config: RunnableConfig) -> dict:
    """LOT History Agent 노드: 5개 테이블에서 LOT 종합 이력 조회"""
    lh_lot_ids = state.get("lh_lot_ids", "")
    logger.info("[LOT History Agent] lot_ids=%s", lh_lot_ids)

    # 요청별 격리된 저장소 초기화
    storage: Dict[str, Any] = {}
    _tool_payload_var.set(storage)

    # query 우선순위: planner task goal > 사용자 last_human (#12 fix)
    messages = state.get("messages", [])
    last_human = next(
        (m for m in reversed(messages) if isinstance(m, HumanMessage)),
        None,
    )
    task_goal = state.get("current_task_goal", "")
    query = task_goal or (last_human.content if last_human else f"{lh_lot_ids} lot 이력을 조회해줘")

    # 히스토리 필터링 — 최근 3턴
    lh_history: list = []
    turn_count = 0
    for m in reversed(messages):
        if turn_count >= 3:
            break
        if isinstance(m, HumanMessage):
            lh_history.insert(0, m)
            turn_count += 1
        elif isinstance(m, AIMessage) and getattr(m, "name", "") == "lot_history_agent":
            lh_history.insert(0, m)

    if not lh_history or lh_history[-1].content != query:
        lh_history.append(HumanMessage(content=query))

    logger.info("[LOT History Agent] ReAct invoke 시작 (history=%d msgs)", len(lh_history))
    try:
        sub_config = {"callbacks": _lf_callbacks(), "recursion_limit": 20}
        result = _lh_graph.invoke(
            {
                "messages": lh_history,
                "_lot_ids": lh_lot_ids,
            },
            config=sub_config,
        )
    except Exception as e:
        if is_transient_error(e):
            logger.warning("[LOT History Agent] transient 오류, retry 위임: %s", e)
            raise
        logger.error("[LOT History Agent] 영구 오류: %s", e, exc_info=True)
        error_message = AIMessage(
            content=f"LOT 이력 조회 중 오류가 발생했습니다: {e}",
            name="lot_history_agent",
        )
        return {
            "messages": [error_message],
            "lot_history_artifacts": [],
            "past_steps": [(state.get("current_task_id", ""), f"LOT 이력 영구 오류: {e}")],
        }

    ai_messages = [m for m in result.get("messages", []) if isinstance(m, AIMessage)]
    answer = ai_messages[-1].content if ai_messages else "LOT 이력 조회에 실패했습니다."

    # ContextVar에서 결과 추출 → HTML 렌더링
    lot_history_data = storage.get("lot_history")
    artifacts = []
    if isinstance(lot_history_data, dict) and "error" not in lot_history_data and lot_history_data:
        html = _render_lot_history_html(lot_history_data)
        artifacts.append({
            "type": "html",
            "mime": "text/html",
            "data": html,
            "title": "lot_history_report",
        })

    answer, agent_suggestion = extract_suggestion(answer)

    result_message = AIMessage(content=answer, name="lot_history_agent")

    return {
        "messages": [result_message],
        "lot_history_artifacts": artifacts,
        "agent_suggestion": agent_suggestion,
        "past_steps": [(state.get("current_task_id", ""), answer[:300])],
    }

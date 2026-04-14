"""
LOT History Agent 노드
=======================
5개 Oracle 테이블에서 LOT_ID 종합 이력을 조회하고
HTML 아티팩트로 렌더링합니다.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langfuse import observe

from common import timed, html_escape as _h, is_transient_error
from lot_history_tools import _tool_payload_var, query_lot_history

load_dotenv(override=True)

logger = logging.getLogger("yield_agent.lot_history_agent")


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
    """LOT History deterministic 노드 — ReAct 없이 query_lot_history 직접 호출.

    LangGraph canonical pattern (custom-workflow "Deterministic node"): LLM reasoning/
    tool-selection 불필요한 단일 도구 worker는 plain function node가 최적. create_react_agent는
    langgraph v1에서 deprecated이고, lot_history는 reasoning 자체가 불필요 → deterministic
    function 전환. C1 패턴(lot_history_sql_result AIMessage)으로 downstream chained 확장 지원.
    """
    lh_lot_ids = state.get("lh_lot_ids", "")
    current_task_id = state.get("current_task_id", "")
    logger.info("[LOT History Agent] lot_ids=%s", lh_lot_ids)

    if not lh_lot_ids:
        msg = AIMessage(
            content="LOT ID가 제공되지 않았습니다. 조회하려면 LOT ID를 알려주세요.",
            name="lot_history_agent",
        )
        return {
            "messages": [msg],
            "lot_history_artifacts": [],
            "past_steps": [(current_task_id, "LOT ID 없음 — 조회 스킵")],
        }

    # 요청별 격리된 저장소 초기화 + @tool decorated 함수 직접 호출 (LLM 불필요)
    storage: Dict[str, Any] = {}
    _tool_payload_var.set(storage)

    try:
        tool_summary = query_lot_history.invoke({"lot_ids": lh_lot_ids})
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
            "past_steps": [(current_task_id, f"LOT 이력 영구 오류: {e}")],
        }

    # ContextVar에서 structured 결과 추출 → HTML 렌더링
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

    # query_lot_history tool의 한국어 요약을 사용자 메시지로 사용
    answer = tool_summary if isinstance(tool_summary, str) else str(tool_summary)
    result_message = AIMessage(content=answer, name="lot_history_agent")

    # C1 패턴 확장: lot_history_sql_result structured AIMessage 발행.
    # downstream chained task가 per-lot 위험도에 접근할 수 있도록 additional_kwargs에 dict 저장.
    out_messages: list = [result_message]
    if isinstance(lot_history_data, dict) and "error" not in lot_history_data and lot_history_data:
        per_lot_summary = {
            lid: {
                "fdc_alarm_count": len(data.get("fdc_alarm", [])),
                "qtime_over_count": len(data.get("qtime_over", [])),
                "trouble_lot_count": len(data.get("trouble_lot", [])),
                "future_action_count": len(data.get("future_action", [])),
                "sample_split_count": len(data.get("sample_split", [])),
            }
            for lid, data in lot_history_data.items()
        }
        sql_result_msg = AIMessage(
            content=f"[LOT History 결과] {len(per_lot_summary)}개 LOT 이력 조회 완료",
            name="lot_history_sql_result",
            additional_kwargs={
                "lot_history_result": {
                    "lot_ids": list(per_lot_summary.keys()),
                    "per_lot_summary": per_lot_summary,
                },
            },
        )
        out_messages.insert(0, sql_result_msg)

    return {
        "messages": out_messages,
        "lot_history_artifacts": artifacts,
        "agent_suggestion": "",
        "past_steps": [(current_task_id, answer[:300])],
    }

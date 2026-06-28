"""
Relation Tree Agent 노드
========================
입력된 main_oper 공정과 연관된 inline 계측 step의 trend·상관분석을
트리/관계도 HTML로 렌더링한다.

본 단계: graph wiring·라우팅·SSE 흐름 검증을 위한 더미 placeholder HTML.
실제 상관분석·trend 시각화는 후속 작업.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langfuse import observe

from common import timed, html_escape as _h, get_oracle_connection
from result_contracts import attach_result_envelope

logger = logging.getLogger("yield_agent.relation_tree_agent")

_MAINOPER_CHOICE_MESSAGE = "어느 main_oper로 WT Resp 분석을 이어갈까요?"


def _mainoper_followups(
    lotcd: str, fail_type: str, main_opers: list[str], wads_category: str = ""
) -> list[dict]:
    """S2: 연관 main_oper 후보를 택1 choice followup으로 선언한다(트리거를 결과 생성 에이전트가 소유).
    선택 → wt_resp_agent task로 치환(cause_oper=선택 oper, lotcd/fail_type/wads_category 상속). main_opers 없으면 []."""
    if not main_opers:
        return []
    options = [
        {"label": m, "value": str(i), "slots": {"cause_oper": m}}
        for i, m in enumerate(main_opers)
    ]
    # god-state 폐기: 공정(wads_category)을 wt_resp(→mining) task.params로 명시 운반.
    default_slots = {"lotcd": lotcd, "fail_type": fail_type}
    if wads_category:
        default_slots["wads_category"] = wads_category
    return [{
        "agent": "__choice__",
        "goal": "relation_tree main_oper 선택",
        "choice_kind": "single",
        "choice_message": _MAINOPER_CHOICE_MESSAGE,
        "choice_target_agent": "wt_resp_agent",
        "choice_options": options,
        "default_slots": default_slots,
        "guard_key": "mainoper_offered",
        "guard_agents": ["wt_resp_agent", "mining_agent"],
    }]


def _query_main_opers(lotcd: str, fail_type: str, category: str = "") -> list[str]:
    """DF_WADS_MAIN_OPER에서 (lotcd, fail_type[, category])로 연관 main_oper 목록 조회.
    키=(LOTCD,CATEGORY,PARAMETER). PARAMETER=fail_type. category(wads_category) 있으면 narrowing.
    LIKE %val% — lotcd 제품코드("4SS") 부분일치 (wads_tools _query_wads_data 패턴 미러)."""
    if not lotcd or not fail_type:
        return []
    # LOTCD에는 제품코드(예 "4SS")가 들어있고 입력 lotcd는 제품코드 또는 풀 lot(예 "4SS2DPD")일 수
    # 있다 → 테이블의 LOTCD가 입력 lotcd의 부분문자열인 행을 매칭(양쪽 모두 동작).
    sql = (
        "SELECT DISTINCT MAIN_OPER FROM DF_WADS_MAIN_OPER "
        "WHERE LOTCD IS NOT NULL AND UPPER(:lotcd) LIKE '%' || UPPER(LOTCD) || '%' "
        "AND UPPER(PARAMETER) LIKE UPPER(:fail_type)"
    )
    binds = {"lotcd": lotcd, "fail_type": f"%{fail_type}%"}
    if category:
        sql += " AND UPPER(CATEGORY) LIKE UPPER(:category)"
        binds["category"] = f"%{category}%"
    sql += " ORDER BY MAIN_OPER"
    conn = get_oracle_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, binds)
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    out: list[str] = []
    for r in rows:
        v = str((r[0] if r else "") or "").strip()
        if v and v not in out:
            out.append(v)
    return out


@observe(name="relation_tree_agent_node")
@timed
def relation_tree_agent_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    state = {**state, **((state.get("current_task") or {}).get("params") or {})}  # S1-a: task params 우선, scalar fallback
    lot_code  = (state.get("lotcd") or "").strip()
    # TEMP(relation_tree fail_type): fail_type is now the required analyzed parameter;
    # cause_oper stays optional. (Full relation analysis is still a placeholder below.)
    fail_type = (state.get("fail_type") or "").strip()
    main_oper = (state.get("cause_oper") or "").strip()
    current_task_id = state.get("current_task_id", "")

    logger.info("[Relation Tree Agent] lot_code=%s fail_type=%s main_oper=%s", lot_code, fail_type, main_oper)

    # WADS 검출 후속(rt_groups): report별로 각각 연관 분석(파라미터 뭉치기 금지). cummap fan-out 대칭.
    rt_groups = state.get("rt_groups") or []
    if rt_groups:
        category = (state.get("wads_category") or "").strip()
        artifacts: list[dict] = []
        labels: list[str] = []
        main_opers: list[str] = []  # group들의 연관 main_oper 합집합 → main_oper 선택 HITL 선택지
        for g in rt_groups:
            g_lotcd = (str(g.get("lotcd") or lot_code)).strip()
            g_param = (str(g.get("parameter") or "")).strip()
            g_lots = ",".join(str(x).strip() for x in (g.get("lot_ids") or []) if str(x).strip())
            for m in _query_main_opers(g_lotcd, g_param, category):
                if m not in main_opers:
                    main_opers.append(m)
            opers_html = "".join(f"<li>{_h(m)}</li>" for m in main_opers) or "<li>-</li>"
            artifacts.append({
                "type": "html",
                "mime": "text/html",
                "data": (
                    f"<h1>Inline-WT 연관 분석: {_h(g_lotcd)} / {_h(g_param) or '-'}</h1>"
                    f"<p>lots: {_h(g_lots) or '-'}</p>"
                    f"<p>연관 main_oper 후보:</p><ul>{opers_html}</ul>"
                    f"<p>(상관분석·trend 본구현 예정)</p>"
                ),
                "title": f"relation_tree_{g_param or g_lotcd}",
            })
            labels.append(g_param or g_lotcd)
        summary = (
            f"WADS 검출 {len(artifacts)}개 리포트 연관 분석(파라미터별): {', '.join(labels)}"
            f" — 연관 main_oper 후보 {len(main_opers)}개"
        )
        msg = AIMessage(content=summary, name="relation_tree_agent")
        # main_opers를 envelope에 첨부 → main_oper 선택 HITL 발동(standalone 경로와 동일).
        attach_result_envelope(
            msg,
            logger=logger,
            source_agent="relation_tree_agent",
            kind="report",
            status="success",
            title="relation_tree",
            summary=summary,
            extensions={"relation_tree_agent": {"main_opers": main_opers}},
            followups=_mainoper_followups(lot_code, fail_type, main_opers, category),
        )
        return {
            "messages": [msg],
            "relation_tree_artifacts": artifacts,
            "agent_suggestion": "",
            "past_steps": [(current_task_id, summary[:300])],
        }

    if not lot_code:
        msg = AIMessage(
            content="LOT 코드가 제공되지 않았습니다. 연관 분석을 수행하려면 LOT 코드를 알려주세요.",
            name="relation_tree_agent",
        )
        return {
            "messages": [msg],
            "relation_tree_artifacts": [],
            "agent_suggestion": "",
            "past_steps": [(current_task_id, "lot_code 없음 — 연관 분석 스킵")],
        }

    # lotcd+fail_type로 연관 main_oper 후보 조회 → 후속 main_oper 선택 HITL의 선택지가 된다.
    category = (state.get("wads_category") or "").strip()
    main_opers = _query_main_opers(lot_code, fail_type, category)
    logger.info("[Relation Tree Agent] main_opers=%d", len(main_opers))
    opers_html = "".join(f"<li>{_h(m)}</li>" for m in main_opers) or "<li>-</li>"
    html_content = (
        f"<h1>Inline-WT 연관 분석: {_h(lot_code)}</h1>"
        f"<p>fail_type: {_h(fail_type) or '-'}</p>"
        f"<p>연관 main_oper 후보 ({len(main_opers)}개):</p><ul>{opers_html}</ul>"
        f"<p>(상관분석·trend 본구현 예정)</p>"
    )

    summary = (
        f"{lot_code} 연관 분석 (fail_type={fail_type or '-'}) — 연관 main_oper 후보 {len(main_opers)}개"
    )
    msg = AIMessage(content=summary, name="relation_tree_agent")
    # main_opers를 envelope extensions에 첨부 → supervisor가 읽어 main_oper 선택 HITL 선택지로 사용.
    attach_result_envelope(
        msg,
        logger=logger,
        source_agent="relation_tree_agent",
        kind="report",
        status="success",
        title="relation_tree",
        summary=summary,
        extensions={"relation_tree_agent": {"main_opers": main_opers}},
        followups=_mainoper_followups(lot_code, fail_type, main_opers, category),
    )
    return {
        "messages": [msg],
        "relation_tree_artifacts": [{
            "type": "html",
            "mime": "text/html",
            "data": html_content,
            "title": "relation_tree",
        }],
        "agent_suggestion": "",
        "past_steps": [(current_task_id, summary[:300])],
    }

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

from common import timed, html_escape as _h

logger = logging.getLogger("yield_agent.relation_tree_agent")


@observe(name="relation_tree_agent_node")
@timed
def relation_tree_agent_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    lot_code  = (state.get("lotcd") or "").strip()
    # TEMP(relation_tree fail_type): fail_type is now the required analyzed parameter;
    # cause_oper stays optional. (Full relation analysis is still a placeholder below.)
    fail_type = (state.get("fail_type") or "").strip()
    main_oper = (state.get("cause_oper") or "").strip()
    current_task_id = state.get("current_task_id", "")

    logger.info("[Relation Tree Agent] lot_code=%s fail_type=%s main_oper=%s", lot_code, fail_type, main_oper)

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

    html_content = (
        f"<h1>Inline-WT 연관 분석: {_h(lot_code)}</h1>"
        f"<p>fail_type: {_h(fail_type) or '-'}</p>"
        f"<p>main_oper_det_desc: {_h(main_oper) or '-'}</p>"
        f"<p>(상관분석·trend 본구현 예정)</p>"
    )

    summary = (
        f"{lot_code} 연관 분석 (fail_type={fail_type or '-'}, main_oper={main_oper or '-'}) HTML 리포트 생성 완료"
    )
    return {
        "messages": [AIMessage(content=summary, name="relation_tree_agent")],
        "relation_tree_artifacts": [{
            "type": "html",
            "mime": "text/html",
            "data": html_content,
            "title": "relation_tree",
        }],
        "agent_suggestion": "",
        "past_steps": [(current_task_id, summary[:300])],
    }

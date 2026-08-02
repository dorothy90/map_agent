"""
Supervisor Node — Yield/WADS/Map 라우팅 담당
=============================================
planner → queue dispatch 방식으로 라우팅을 수행합니다.

라우팅 대상:
  yield_agent  → pt1h 수율 조회
  wads_agent   → WADS 열화 검출 리포트 조회
  map_agent    → 웨이퍼 맵 시각화
  FINISH       → 범위 외 요청
"""

from __future__ import annotations

# ── 분리된 모듈에서 import (그래프 배선/HITL용) ──
from node_normalizer import task_normalizer_validator_node
from node_plan_review import plan_review_node
from node_planner import planner_node
from node_replanner import replanner_node
from node_supervisor import supervisor_node
from orch_utils import _model, logger
from query_state import YieldQueryState

import json
from typing import Any, Dict

from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from langgraph.types import RetryPolicy

from common import is_transient_error
from lf_utils import lf_callbacks as _lf_callbacks  # noqa: E402

load_dotenv(override=True)


_MAX_CHECKPOINT_MESSAGES = 30


_RESUME_INTENT_SYSTEM = (
    "아래 JSON은 현재 HITL의 message/options/fields와 사용자의 입력이다. "
    "입력이 현재 HITL이 허용하는 선택, 필드 제공, 승인 또는 현재 작업의 지원 슬롯 수정이면 "
    "'answer'를 반환한다. 입력이 현재 HITL로 표현할 수 없는 상위 분석 대상 변경, 이전 선택 "
    "단계로의 이동, 또는 별도 작업 요청이면 'new'를 반환한다. 표현의 키워드가 아니라 현재 "
    "HITL 계약으로 그 요청을 실제 반영할 수 있는지를 의미적으로 판단한다. fields의 slot/type은 "
    "허용된 값의 의미 영역을 엄격히 나타내며, 다른 의미 영역의 값을 현재 field 값으로 강제 변환하지 "
    "않는다. 이 시스템에서 fail_type은 WADS가 검출한 전기적 검사 파라미터 또는 불량 분류이고, "
    "cause_oper는 원인 분석의 기준이 되는 제조 공정이며, wads_category는 검사 단계다. 사용자의 "
    "요청 대상과 현재 fields/options의 의미 영역이 같은지 판단한다. "
    "오직 'answer' 또는 'new' 한 단어만 출력한다."
)


def _resume_is_interrupt_answer(resume_value: Any, pending_interrupt: dict) -> bool:
    """resume 입력이 대기 중 interrupt에 대한 '답'인지(True) '새 의도'인지(False) 판정.
    키워드 매칭 금지(LLM). 옵션 value/label과 정확히 일치하면 LLM 없이 답으로 본다.
    빈/구조화 응답·LLM 실패 시 안전하게 True(답) 반환 — 기존 resume 동작 유지(회귀 방지)."""
    if isinstance(resume_value, dict):
        text = next((str(v) for v in resume_value.values() if str(v).strip()), "")
    else:
        text = str(resume_value or "")
    text = text.strip()
    if not text:
        return True
    options = [o for o in (pending_interrupt.get("options") or []) if isinstance(o, dict)]
    for opt in options:
        if text in (str(opt.get("value", "")), str(opt.get("label", ""))):
            return True
    payload = {
        "interrupt_type": pending_interrupt.get(
            "interrupt_type", pending_interrupt.get("type", "")
        ),
        "route": pending_interrupt.get("route", ""),
        "representative_param": pending_interrupt.get("param", ""),
        "message": pending_interrupt.get("message", ""),
        "options": [
            {
                "label": option.get("label"),
                "value": option.get("value"),
                "slot_keys": sorted(
                    str(key)
                    for key in (option.get("slots") or {})
                    if str(key).strip()
                ),
            }
            for option in options
        ],
        "fields": [
            {
                "slot": field.get("slot"),
                "label": field.get("label"),
                "type": field.get("type"),
            }
            for field in (pending_interrupt.get("fields") or [])
            if isinstance(field, dict)
        ],
        "input": text,
    }
    try:
        verdict = (
            _model.invoke(
                [
                    {"role": "system", "content": _RESUME_INTENT_SYSTEM},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                config={"callbacks": _lf_callbacks()},
            ).content
            or ""
        ).strip().lower()
    except Exception as e:
        logger.warning("[Resume] 의도 판정 LLM 실패 (%s) — 답으로 처리(기존 동작)", e)
        return True
    return not verdict.startswith("new")


# ── 그래프 조립 (순환 import 방지: yield_query_agent/wads_agent/map_agent는 supervisor를 import하지 않음)
from yield_query_agent import yield_agent_node  # noqa: E402
from wads_agent import wads_agent_node  # noqa: E402
from map_agent import map_agent_node  # noqa: E402
from fail_history_agent import fail_history_agent_node  # noqa: E402
from ppt_export_agent import ppt_export_node  # noqa: E402
from lot_history_agent import lot_history_agent_node  # noqa: E402
from relation_tree_agent import relation_tree_agent_node  # noqa: E402
from mining_agent import mining_agent_node  # noqa: E402
from wt_resp_agent import wt_resp_agent_node  # noqa: E402

# 에이전트 노드 재시도 정책 (Oracle/LLM 일시적 오류 자동 재시도)
# LangGraph 기본(default_retry_on)은 OSError/TimeoutError를 거부하므로
# common.is_transient_error를 명시적으로 위임 — supervisor 노드와 worker 노드의
# transient 분류 로직을 한 곳에서 일관 관리.
_retry = RetryPolicy(max_attempts=3, initial_interval=1.0, retry_on=is_transient_error)

workflow = StateGraph(YieldQueryState)
workflow.add_node("planner", planner_node, retry_policy=_retry)
workflow.add_node("task_normalizer_validator", task_normalizer_validator_node)
workflow.add_node("plan_review", plan_review_node)
workflow.add_node("supervisor", supervisor_node, retry_policy=_retry)
workflow.add_node("replanner", replanner_node, retry_policy=_retry)
workflow.add_node("yield_agent", yield_agent_node, retry_policy=_retry)
workflow.add_node("wads_agent", wads_agent_node, retry_policy=_retry)
workflow.add_node("map_agent", map_agent_node, retry_policy=_retry)
workflow.add_node("fail_history_agent", fail_history_agent_node, retry_policy=_retry)
workflow.add_node("ppt_export", ppt_export_node, retry_policy=_retry)
workflow.add_node("lot_history_agent", lot_history_agent_node, retry_policy=_retry)
workflow.add_node("relation_tree_agent", relation_tree_agent_node, retry_policy=_retry)
workflow.add_node("mining_agent", mining_agent_node, retry_policy=_retry)
workflow.add_node("wt_resp_agent", wt_resp_agent_node, retry_policy=_retry)

workflow.add_edge(START, "planner")
workflow.add_edge("planner", "task_normalizer_validator")
workflow.add_edge("task_normalizer_validator", "plan_review")
# plan_review → supervisor / plan_review(self-loop): Command(goto=...)가 처리.
# 정적 엣지를 두면 modify self-loop과 충돌하고, interrupt 사이 LLM replay 버그를 유발한다.
# supervisor → agent: Command(goto=...)가 처리 (conditional_edges 불필요)
# agent → replanner → supervisor: 공식 plan-and-execute 패턴 (#8 phase 2)
workflow.add_edge("yield_agent", "replanner")
workflow.add_edge("wads_agent", "replanner")
workflow.add_edge("map_agent", "replanner")
workflow.add_edge("fail_history_agent", "replanner")
workflow.add_edge("ppt_export", "replanner")
workflow.add_edge("lot_history_agent", "replanner")
workflow.add_edge("relation_tree_agent", "replanner")
workflow.add_edge("mining_agent", "replanner")
workflow.add_edge("wt_resp_agent", "replanner")


# canonical plan-and-execute should_end: replanner가 set한 response로 END 분기 결정.
# LangChain OpenTutorial `{END: END, "agent": "agent"}` 패턴 그대로, "agent" 자리는 "supervisor".
def should_end(state: Dict[str, Any]) -> str:
    if state.get("response"):
        return END
    return "supervisor"


workflow.add_conditional_edges(
    "replanner",
    should_end,
    {END: END, "supervisor": "supervisor"},
)

# workflow는 빌더(StateGraph)로 export — agent_server.py에서 checkpointer와 함께 compile
# 로컬 테스트:
if __name__ == "__main__":
    yield_supervisor = workflow.compile()
    print("OK — yield_supervisor compiled for local test")

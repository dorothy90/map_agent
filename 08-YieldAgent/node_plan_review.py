"""node_plan_review — supervisor.py에서 분리(노드/응집 헬퍼). 자동 분할 codemod 생성."""
from __future__ import annotations


import json
from typing import Any, Dict, Literal

from dotenv import load_dotenv
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command, interrupt

from common import extract_json_from_llm
from canonical_request import build_tasks_from_canonical_requests, canonical_requests_from_tasks, normalize_canonical_request
from task_normalizer_validator import apply_ordinal_ref

load_dotenv(override=True)

from orch_utils import _model, logger
from query_state import PlanReviewResult
from recent_results import _recent_results_prompt_context
from user_memory import make_feedback_event




_PLAN_REVIEW_SYSTEM = """
현재 분석 계획(canonical request 목록)과 사용자 응답을 보고 최종 요청 목록을 JSON으로 반환해라.

action 필드는 반드시 아래 영문 문자열 중 하나여야 한다:
- "approve" : 승인 (응/ok/확인/좋아/네/그렇게 해/빈 응답 등)
- "cancel"  : 취소 (취소/cancel/no/그만/중단 등)
- "modify"  : 수정 요청 (구체적인 변경 지시)

출력 형식:
{"action": "approve"|"cancel"|"modify", "requests": [...]}

규칙:
- requests는 항상 전체 canonical request 목록이다. 수정하지 않은 request도 포함한다.
- request는 intent, agent, slots, goal 필드를 사용한다. task_id/params/tasks를 출력하지 마라.
- agent는 반드시 다음 중 하나만 사용한다: yield_agent(수율), wads_agent(열화 리포트),
  map_agent(웨이퍼 맵), fail_history_agent(불량이력), lot_history_agent(lot 이력),
  relation_tree_agent(연관 분석), ppt_export. 그 외 이름(defect_agent 등) 금지.
- approve면 현재 요청 그대로 requests에 넣는다.
- cancel이면 requests는 []로 둔다.
- modify면 사용자 응답을 반영한 수정된 전체 요청 목록을 requests에 넣는다
  (기존 request 유지 + 요청된 작업 추가/변경).
- 사용자가 이전 결과를 참조하면("N번째 리포트", "그 lot들", "검출 parameter" 등) 아래 제공되는
  Structured context의 reports/rows에서 값을 읽어 slots에 넣어라. 값을 직접 모르면 슬롯에
  "#N"(N번째 결과 행) 또는 "#RN"(N번째 리포트) 토큰을 넣으면 시스템이 정확히 채운다.
""".strip()


def _plan_review_commit(canonical_requests: list, task_plan: list) -> dict:
    """plan_review가 state에 commit하는 4개 키 (approve/modify 공통)."""
    return {
        "canonical_request": canonical_requests[0] if canonical_requests else {},
        "canonical_requests": canonical_requests,
        "task_plan": task_plan,
        "pending_tasks": task_plan,
    }


def plan_review_node(
    state: Dict[str, Any], config: RunnableConfig
) -> Command[Literal["plan_review", "supervisor"]]:
    """2개 이상 task일 때만 사용자 승인을 기다린다. 단일 task는 즉시 통과.

    Validation/HITL handles missing params. This node only handles plan review.

    구조 계약 (LangGraph interrupt replay 안전): **이 노드 1회 실행 = interrupt 1개 + LLM 1회.**
    modify면 갱신 plan을 state에 commit하고 Command(goto="plan_review")로 새 super-step에
    재진입한다. while 루프 안에서 interrupt와 LLM을 반복하면, resume 시 노드가 처음부터
    재실행되며 직전 interrupt 사이의 비결정적 _model.invoke가 다시 호출되어(replay) modify
    결과를 덮어쓴다. 노드(super-step) 분리로 각 LLM 분류가 자기 interrupt 뒤에서 정확히
    한 번만 실행되도록 한다.
    참고: https://docs.langchain.com/oss/python/langgraph/interrupts#side-effects-called-before-interrupt-must-be-idempotent
    """
    task_plan = state.get("task_plan", [])
    canonical_requests = state.get(
        "canonical_requests"
    ) or canonical_requests_from_tasks(task_plan)
    if len(task_plan) < 2:
        return Command(goto="supervisor")

    # ── interrupt 1회 (이 노드 실행당 하나만) ──
    user_response = interrupt(
        {"type": "plan_review", "tasks": task_plan, "missing_params": []}
    )
    resp = (user_response or "").strip()

    if not resp:
        # 빈 응답 → approve, LLM 호출 없이 현재 plan commit 후 통과
        return Command(
            goto="supervisor",
            update=_plan_review_commit(canonical_requests, task_plan),
        )

    # ── LLM 분류 1회 (interrupt 뒤 = resume마다 정확히 한 번, replay 안 됨) ──
    # #1: give the modify LLM the same structured-result context the planner gets,
    # so result-dependent modifications ("3·4번째 리포트 파라미터도 추가") can resolve.
    recent_results = state.get("recent_results", []) or []
    recent_context = _recent_results_prompt_context(recent_results)
    try:
        review_messages: list[dict] = [{"role": "system", "content": _PLAN_REVIEW_SYSTEM}]
        if recent_context:
            review_messages.append({
                "role": "system",
                "content": f"Structured context (prior results you may reference):\n{recent_context}",
            })
        review_messages.append({
            "role": "user",
            "content": (
                f"현재 canonical requests:\n{json.dumps(canonical_requests, ensure_ascii=False)}\n\n"
                f"화면 표시용 현재 task 계획:\n{json.dumps(task_plan, ensure_ascii=False)}\n\n"
                f'사용자 응답: "{resp}"'
            ),
        })
        raw = _model.invoke(review_messages).content.strip()
        result = extract_json_from_llm(raw, PlanReviewResult)
    except Exception as e:
        logger.warning("[PlanReview] LLM 판단 실패 (%s) — 계획 재표시", e)
        # 같은 plan으로 재질문: 새 super-step에서 interrupt 재생성
        return Command(goto="plan_review")

    result_requests = [
        normalize_canonical_request(request.model_dump())
        for request in result.requests
    ]
    # #1: resolve ordinal refs ("#N"/"#RN") in the modified requests against
    # recent_results, exactly like the planner — so a token the modify LLM emits
    # for "N번째 리포트/parameter" is filled deterministically (never a wrong value).
    for _cr in result_requests:
        _cr["slots"], _ = apply_ordinal_ref(
            _cr.get("agent", ""), _cr.get("slots") or {}, recent_results
        )
    logger.info(
        "[PlanReview] action=%s requests=%s",
        result.action,
        [(r.get("intent"), r.get("agent")) for r in result_requests],
    )

    # 사용자 선호 학습: 계획 형태에 대한 피드백(취소/수정) 이벤트 (approve는 신호 없음).
    _plan_summary = [(t.get("agent", ""), t.get("goal", "")) for t in task_plan]

    if result.action == "cancel":
        return Command(
            goto="supervisor",
            update={
                "response": "사용자가 분석 계획을 취소했습니다.",
                "canonical_request": {},
                "canonical_requests": [],
                "task_plan": [],
                "pending_tasks": [],
                "memory_feedback": [make_feedback_event(
                    touchpoint="plan_review", decision="cancelled",
                    message=json.dumps(_plan_summary, ensure_ascii=False),
                    user_answer=resp,
                )],
            },
        )
    if result.action == "approve":
        return Command(
            goto="supervisor",
            update=_plan_review_commit(canonical_requests, task_plan),
        )

    # modify → 갱신 plan을 state에 commit하고 self-loop (다음 super-step에서 새 interrupt)
    new_task_plan = build_tasks_from_canonical_requests(result_requests)
    return Command(
        goto="plan_review",
        update={
            **_plan_review_commit(result_requests, new_task_plan),
            "memory_feedback": [make_feedback_event(
                touchpoint="plan_review", decision="modified",
                message=json.dumps(_plan_summary, ensure_ascii=False),
                user_answer=resp,
            )],
        },
    )

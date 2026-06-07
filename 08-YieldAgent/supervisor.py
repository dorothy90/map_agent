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

import json
import operator
import logging
import re
from datetime import date, timedelta
from typing import Annotated, Any, Dict, Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    trim_messages,
)
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables import RunnableConfig
from langfuse import get_client, observe
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.types import Command, RetryPolicy, interrupt
from pydantic import BaseModel, Field

from langchain_core.messages import ToolMessage

from common import stream_event, get_llm, extract_json_from_llm, is_transient_error
from canonical_request import (
    AGENT_SLOT_SCHEMAS,
    build_tasks_from_canonical_requests,
    canonical_requests_from_tasks,
    normalize_canonical_request,
)
from lf_utils import lf_callbacks as _lf_callbacks  # noqa: E402
from models import StatusEvent
from prompts import (
    CANONICAL_PLANNER_SYSTEM_PROMPT,
    REPLANNER_SYSTEM_PROMPT,
    REWRITE_SYSTEM_PROMPT_TEMPLATE,
)
from result_contracts import (
    ResultContractError,
    build_recent_result_index_entry,
    prune_recent_results,
)
from rewrite_tools import REWRITE_TOOLS
from task_normalizer_validator import (
    UNRESOLVED_REF,
    apply_ordinal_ref,
    normalize_task_fields,
    validate_tasks,
)
from local_trace import (
    emit_runtime_detail,
    emit_trace_event,
    preview_text,
    summarize_params,
    summarize_result_envelope,
    summarize_tasks,
    task_flow,
)

load_dotenv(override=True)

logger = logging.getLogger("yield_agent.supervisor")

# ── LLM 모델 ────────────────────────────────────────────────
_model = get_llm()
_rewrite_model = _model.bind_tools(REWRITE_TOOLS)

# 도구 이름 → 함수 매핑 (rewrite tool-calling에서 사용)
_rewrite_tool_map = {t.name: t for t in REWRITE_TOOLS}


# ── Pydantic 라우팅 결정 모델 ────────────────────────────────
class TaskItem(BaseModel):
    """deterministic task builder가 생성하는 공통 작업 단위"""

    task_id: str = Field(description="고유 ID (예: 'task_1')")
    agent: Literal[
        "yield_agent",
        "wads_agent",
        "map_agent",
        "fail_history_agent",
        "ppt_export",
        "lot_history_agent",
        "relation_tree_agent",
    ] = Field(description="실행할 에이전트")
    params: dict = Field(
        default={}, description="에이전트별 파라미터 (map_lot_ids, map_type 등)"
    )
    goal: str = Field(
        description="이 작업의 목표 (한국어, 예: 'lot 3,4번 cummap 생성')"
    )


class PlanResponse(BaseModel):
    """공통 task contract 래퍼. Runtime LLM planner 출력은 CanonicalPlanResponse를 사용한다."""

    tasks: list[TaskItem] = Field(description="실행할 작업 목록")


class CanonicalRequestItem(BaseModel):
    """LLM canonicalizer output before deterministic task building."""

    intent: str = Field(
        default="", description="정규화된 intent (예: wads_report, map)"
    )
    agent: Literal[
        "",
        "yield_agent",
        "wads_agent",
        "map_agent",
        "fail_history_agent",
        "ppt_export",
        "lot_history_agent",
        "relation_tree_agent",
    ] = Field(default="", description="실행 대상 agent")
    slots: dict = Field(
        default_factory=dict,
        description="agent slot schema에 맞춘 structured parameters",
    )
    goal: str = Field(default="", description="사용자에게 표시할 한국어 목표")
    answer: str = Field(default="", description="잘못 중첩된 direct answer 보정용")


class CanonicalPlanResponse(BaseModel):
    """LLM canonicalizer output schema."""

    requests: list[CanonicalRequestItem] = Field(
        default_factory=list, description="정규화된 요청 목록"
    )
    answer: str = Field(
        default="",
        description="도구 실행 없이 제공 context만으로 답할 수 있을 때의 사용자 응답",
    )


_EMPTY_PLAN_RESPONSE_SYSTEM = """
너는 반도체 수율 분석 시스템의 대화형 assistant다.
planner가 실행할 agent task를 만들지 못한 상황에서 사용자에게 자연스럽게 답한다.

규칙:
- 사용자가 인사하면 짧게 인사하고, 필요하면 어떤 분석을 도울 수 있는지 자연스럽게 안내한다.
- 사용자가 기능/사용법을 물으면 수율 조회, WADS 열화 리포트, 웨이퍼 맵, LOT 이력, 불량 이력 검색을 예시와 함께 안내한다.
- 일반 질문이면 억지로 분석 task를 만들지 말고, 이 시스템에서 도울 수 있는 방향을 부드럽게 제안한다.
- "죄송합니다. ... 쿼리만 지원합니다"처럼 딱딱한 거절문을 그대로 쓰지 않는다.
- SQL, 내부 schema, planner, supervisor, task 같은 내부 구현 용어는 말하지 않는다.
- 한국어로 1~4문장만 답한다.
""".strip()


def _llm_empty_plan_response(user_text: str, *, planner_text: str = "") -> str:
    """Ask the LLM for a natural answer when no executable task exists."""

    try:
        response = _model.invoke(
            [
                {"role": "system", "content": _EMPTY_PLAN_RESPONSE_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"사용자 입력:\n{str(user_text or '').strip()}\n\n"
                        f"planner 원문 응답 참고:\n{str(planner_text or '').strip()[:800]}"
                    ),
                },
            ],
            config={"callbacks": _lf_callbacks()},
        )
        content = (getattr(response, "content", "") or "").strip()
        if content:
            emit_runtime_detail(
                "planner.empty_response",
                {
                    "user_text": user_text,
                    "planner_text": planner_text,
                    "response": content,
                },
            )
            return content
    except Exception as exc:
        logger.warning(
            "[Planner] empty-plan natural response generation failed: %s", exc
        )

    return "지금 요청은 바로 실행할 분석 작업으로 이어지지는 않았습니다. 확인할 제품코드, LOT ID, WADS, 맵, 이력 같은 대상을 알려주시면 이어서 도와드릴게요."


class PlanReviewResult(BaseModel):
    """plan_review LLM의 출력 스키마"""

    action: Literal["approve", "cancel", "modify"]
    requests: list[CanonicalRequestItem] = Field(
        default_factory=list,
        description="최종 canonical request 목록 (approve 시 현재 요청 그대로, modify 시 수정된 전체 목록)",
    )


_CONFIRMATION_REVIEW_SYSTEM = """
현재 실행 확인 질문, 현재 canonical request 목록, 사용자 응답을 보고 최종 요청 목록을 JSON으로 반환해라.

action 필드는 반드시 아래 영문 문자열 중 하나여야 한다:
- "approve" : 사용자가 현재 계획 그대로 계속 진행하겠다는 의도
- "cancel"  : 사용자가 실행 중단 또는 거절을 의도
- "modify"  : 사용자가 범위, 파라미터, 작업 수, 조건 변경을 요청함

출력 형식:
{"action": "approve"|"cancel"|"modify", "requests": [...]}

규칙:
- requests는 항상 전체 canonical request 목록이다. 수정하지 않은 request도 포함한다.
- request는 intent, agent, slots, goal 필드를 사용한다. task_id/params/tasks를 출력하지 마라.
- approve면 현재 요청 그대로 requests에 넣는다.
- cancel이면 requests는 []로 둔다.
- modify면 사용자 응답을 반영한 수정된 전체 요청 목록을 requests에 넣는다.
""".strip()


_MAX_CHECKPOINT_MESSAGES = 30

# worker AIMessage 판별용 name 집합 — supervisor_node/replanner_node 양쪽에서 사용.
_AGENT_NAMES = {
    "yield_agent",
    "wads_agent",
    "map_agent",
    "fail_history_agent",
    "ppt_export",
    "lot_history_agent",
    "relation_tree_agent",
}


_MAX_CONTEXT_TOKENS = 30_000


def _get_recent_turns(
    messages: list, max_turns: int = 5, exclude_last: HumanMessage | None = None
) -> list[dict]:
    """최근 N턴의 Human/AI 메시지를 chat format으로 변환.

    ToolMessage, SystemMessage 등은 스킵.
    exclude_last로 지정된 메시지는 제외 (rewrite 대상이므로 별도 전달).
    turn 수 제한 후 토큰 예산 초과 시 오래된 턴부터 추가 제거.
    """
    eligible = [
        m
        for m in messages
        if (exclude_last is None or m is not exclude_last)
        and isinstance(m, (HumanMessage, AIMessage))
        and (
            isinstance(m, HumanMessage)
            or (isinstance(m.content, str) and m.content.strip())
        )
    ]
    # 1차: 턴 수 제한
    turn_limited = eligible[-(max_turns * 2) :]
    # 2차: 토큰 예산 제한 (SQL 결과·아티팩트 JSON 등 긴 메시지 대응)
    trimmed = trim_messages(
        turn_limited,
        strategy="last",
        token_counter=count_tokens_approximately,
        max_tokens=_MAX_CONTEXT_TOKENS,
    )
    result = []
    for m in trimmed:
        if isinstance(m, HumanMessage):
            result.append(
                {
                    "role": "user",
                    "content": m.content
                    if isinstance(m.content, str)
                    else str(m.content),
                }
            )
        elif isinstance(m, AIMessage):
            content = m.content if isinstance(m.content, str) else str(m.content)
            if content.strip():
                result.append({"role": "assistant", "content": content})

    # 멀티턴 중 LLM에 "안 들어간" 턴을 가시화 (verbose에서만 렌더)
    excluded = [m for m in eligible if all(m is not t for t in trimmed)]
    if excluded:
        emit_runtime_detail(
            "history.excluded",
            {
                "eligible": len(eligible),
                "kept_for_llm": len(trimmed),
                "excluded": [
                    {
                        "role": "user" if isinstance(m, HumanMessage) else "ai",
                        "preview": preview_text(
                            m.content if isinstance(m.content, str) else str(m.content)
                        ),
                    }
                    for m in excluded[:6]
                ],
            },
        )
    return result


def _recent_results_prompt_context(recent_results: list[dict[str, Any]]) -> str:
    """Compact structured result context for follow-up planning.

    This intentionally exposes result metadata and row order, not raw assistant
    prose, so follow-ups can refer to prior tables without replaying suggestions.
    """

    if not recent_results:
        return ""
    lines = [
        "Recent structured results are ordered as displayed to the user. Follow-up references to ranks, rows, or prior items refer to that displayed order.",
    ]
    preferred_keys = (
        "parameter",
        "param",
        "fail_type",
        "cnt",
        "count",
        "detection_count",
        "lot_id",
        "lot_ids",
        "wf_ids",
        "lotcd",
        "groupkey",
        "groupkeys",
        "map_oper",
        "category",
        "end_tm",
    )
    for result in recent_results[-3:]:
        rows = result.get("rows") or []
        columns = [
            {
                "name": column.get("name"),
                "semantic": column.get("semantic"),
            }
            for column in (result.get("columns") or [])
            if isinstance(column, dict)
        ][:8]
        lines.append(
            "result: "
            f"result_id={result.get('result_id', '')} "
            f"source_agent={result.get('source_agent', '')} "
            f"kind={result.get('kind', '')} "
            f"title={preview_text(result.get('title', ''), max_chars=80)} "
            f"row_count={len(rows)} "
            f"columns={json.dumps(columns, ensure_ascii=False)}"
        )
        for index, row in enumerate(rows[:5], start=1):
            if not isinstance(row, dict):
                continue
            compact_row = {
                key: row.get(key)
                for key in preferred_keys
                if row.get(key) not in (None, "")
            }
            if compact_row:
                lines.append(
                    f"row_{index}: {json.dumps(compact_row, ensure_ascii=False)}"
                )
    return "\n".join(lines)


def _previous_assistant_prompt_context(messages: list, last_human: Any) -> str:
    """Return the assistant message immediately preceding the latest user turn."""

    if not messages or last_human is None:
        return ""
    try:
        last_index = max(
            index
            for index, message in enumerate(messages)
            if message is last_human
            or getattr(message, "id", None) == getattr(last_human, "id", None)
        )
    except ValueError:
        last_index = len(messages)

    for message in reversed(messages[:last_index]):
        if not isinstance(message, AIMessage) and getattr(message, "type", "") != "ai":
            continue
        content = (
            message.content
            if isinstance(message.content, str)
            else str(message.content)
        )
        content = content.strip()
        if content:
            return preview_text(content, max_chars=4000)
    return ""


def _normalize_map_oper(raw: str) -> str:
    """interrupt 응답을 정규화: '1h'→'PT1H', 'pt1c'→'PT1C' 등"""
    v = raw.strip().upper()
    if v in ("PT1H", "PT1C"):
        return v
    if v in ("1H", "1C"):
        return f"PT{v}"
    if v.startswith("PT1") and len(v) > 3 and v[3] in ("H", "C"):
        return v[:4]
    return ""


@observe(name="rewrite_node")
def rewrite_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    messages = state.get("messages", [])
    if not messages:
        logger.warning("[Rewrite] early return: no messages in state")
        return {}

    # 체크포인트 메시지 pruning: 오래된 메시지 제거하여 체크포인트 크기 제한
    prune_ops = []
    retained_messages = messages
    if len(messages) > _MAX_CHECKPOINT_MESSAGES:
        excess = messages[: len(messages) - _MAX_CHECKPOINT_MESSAGES]
        prune_ops = [RemoveMessage(id=m.id) for m in excess if getattr(m, "id", None)]
        excess_ids = {m.id for m in excess if getattr(m, "id", None)}
        retained_messages = [
            m for m in messages if getattr(m, "id", None) not in excess_ids
        ]

    # 마지막 HumanMessage 추출 (MongoDBSaver 역직렬화 후 isinstance 실패 방어)
    last_human = next(
        (
            m
            for m in reversed(messages)
            if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human"
        ),
        None,
    )
    if not last_human:
        logger.warning(
            "[Rewrite] early return: no HumanMessage found (last 5 types: %s)",
            [type(m).__name__ for m in messages[-5:]],
        )
        return _recent_results_update_from_messages(
            retained_messages,
            state.get("recent_results", []),
        )

    if state.get("resolved_refs"):
        logger.info(
            "[Rewrite] resolved_refs present; preserving raw user reference text"
        )
        emit_runtime_detail(
            "rewrite.output",
            {
                "input": last_human.content,
                "rewritten": last_human.content,
                "reason": "resolved_refs_authoritative",
            },
        )
        emit_trace_event(
            "rewrite_output",
            source="rewrite",
            payload={
                "input_preview": preview_text(last_human.content),
                "rewritten_preview": preview_text(last_human.content),
                "changed": False,
                "reason": "resolved_refs_authoritative",
                "input_length": len(str(last_human.content)),
                "rewritten_length": len(str(last_human.content)),
            },
        )
        update = _recent_results_update_from_messages(
            retained_messages,
            state.get("recent_results", []),
        )
        if prune_ops:
            update["messages"] = prune_ops
        return update

    # 최근 5턴 대화 히스토리 추출 (마지막 HumanMessage 제외)
    recent = _get_recent_turns(messages, max_turns=5, exclude_last=last_human)

    # state 메타데이터 (lotcd, agent_suggestion 등 — 대화에 없을 수 있는 정보)
    meta_parts = []
    if state.get("lotcd"):
        meta_parts.append(f"현재 제품: {state['lotcd']}")
    if state.get("lotcd"):
        meta_parts.append(f"이전 relation_tree lot: {state['lotcd']}")
    if state.get("cause_oper"):
        meta_parts.append(f"이전 relation_tree main_oper: {state['cause_oper']}")
    if state.get("agent_suggestion"):
        meta_parts.append(f"이전 에이전트 제안: {state['agent_suggestion']}")
    meta = "\n".join(meta_parts) if meta_parts else ""

    # LLM 호출: system + recent messages + user message
    today = date.today()
    rewrite_prompt = REWRITE_SYSTEM_PROMPT_TEMPLATE.format(
        today=today.strftime("%Y년 %m월 %d일 (%A)"),
        year=today.year,
    )
    invoke_messages: list[dict] = [{"role": "system", "content": rewrite_prompt}]
    if meta:
        invoke_messages.append(
            {"role": "system", "content": f"State metadata:\n{meta}"}
        )
    invoke_messages.extend(recent)
    invoke_messages.append(
        {"role": "user", "content": f"Rewrite this message: {last_human.content}"}
    )
    emit_runtime_detail(
        "rewrite.input",
        {
            "last_human": last_human.content,
            "meta": meta,
            "recent_turns": recent,
            "invoke_messages": invoke_messages,
        },
    )

    logger.debug(
        "[Rewrite] context_summary meta=%s recent_turns=%d user_len=%d suggestion_present=%s",
        bool(meta),
        len(recent),
        len(str(last_human.content)),
        bool(state.get("agent_suggestion")),
    )

    try:
        # Multi-round tool calling loop: LLM이 여러 tool을 순차/병렬로 호출할 수 있으므로
        # tool_calls가 나오지 않을 때까지 반복. max_rounds로 무한루프 방지.
        max_rounds = 4
        conversation = list(invoke_messages)
        response = _rewrite_model.invoke(
            conversation,
            config={"callbacks": _lf_callbacks()},
        )
        for _ in range(max_rounds):
            tool_calls = getattr(response, "tool_calls", None)
            if not tool_calls:
                break
            conversation.append(response)  # AIMessage with tool_calls
            for tc in tool_calls:
                tool_fn = _rewrite_tool_map.get(tc["name"])
                if tool_fn:
                    result = tool_fn.invoke(tc["args"])
                    conversation.append(
                        ToolMessage(content=str(result), tool_call_id=tc["id"])
                    )
                    emit_runtime_detail(
                        "rewrite.tool",
                        {
                            "name": tc["name"],
                            "args": tc.get("args", {}),
                            "result": result,
                        },
                    )
                    logger.debug("[Rewrite] tool '%s' executed", tc["name"])
                else:
                    conversation.append(
                        ToolMessage(content="unknown tool", tool_call_id=tc["id"])
                    )
            response = _rewrite_model.invoke(
                conversation,
                config={"callbacks": _lf_callbacks()},
            )
        rewritten = (response.content or "").strip()
        if not rewritten:
            logger.warning("[Rewrite] 최종 content 비어있음 — 원문 유지")
            rewritten = last_human.content
    except Exception as e:
        logger.error("[Rewrite] LLM 호출 실패: %s", e, exc_info=True)
        # 원본 메시지 유지 (rewrite 실패 시 원문으로 진행)
        return {
            **_recent_results_update_from_messages(
                retained_messages,
                state.get("recent_results", []),
            ),
            "messages": prune_ops,
        }

    logger.debug(
        "[Rewrite] completed input_len=%d output_len=%d",
        len(str(last_human.content)),
        len(rewritten),
    )
    emit_runtime_detail(
        "rewrite.output", {"input": last_human.content, "rewritten": rewritten}
    )
    emit_trace_event(
        "rewrite_output",
        source="rewrite",
        payload={
            "input_preview": preview_text(last_human.content),
            "rewritten_preview": preview_text(rewritten),
            "changed": str(last_human.content) != str(rewritten),
            "input_length": len(str(last_human.content)),
            "rewritten_length": len(str(rewritten)),
        },
    )

    # 동일 ID로 교체 → add_messages가 제자리 업데이트, 풀 리스트 반환 불필요
    return {
        **_recent_results_update_from_messages(
            retained_messages,
            state.get("recent_results", []),
        ),
        "messages": prune_ops + [HumanMessage(content=rewritten, id=last_human.id)],
    }


def _build_tasks_update(canonical_requests: list[dict]) -> dict:
    """Build the task contract from canonical request(s) and emit plan status.

    Folded from the former task_builder graph node into planner_node. Emissions
    (status + trace) are kept identical so downstream/UI behavior is unchanged.
    """
    tasks = build_tasks_from_canonical_requests(canonical_requests)
    emit_runtime_detail(
        "task_builder.output",
        {
            "canonical_requests": canonical_requests,
            "tasks": tasks,
        },
    )
    if not tasks:
        return {"task_plan": [], "pending_tasks": []}

    logger.info("[TaskBuilder] %d task(s) built: %s", len(tasks), task_flow(tasks))
    stream_event(
        "status",
        StatusEvent(
            message=f"📋 계획 ({len(tasks)}개): "
            + " → ".join(f"[{t['task_id']}]{t['agent']}" for t in tasks),
            node="task_builder",
        ),
    )
    emit_trace_event(
        "task_builder_output",
        source="task_builder",
        payload={
            "status": "ok",
            "task_count": len(tasks),
            "task_flow": task_flow(tasks),
            "tasks": summarize_tasks(tasks),
        },
    )
    return {
        "task_plan": tasks,
        "pending_tasks": tasks,
    }


# ── Planner 노드 ────────────────────────────────────────────
_MAX_TASKS = 5


def _planner_empty_canonical_retry(
    invoke_messages: list[dict],
    *,
    user_text: str,
    previous_output: str,
) -> tuple[list[dict[str, Any]], str]:
    """Ask the LLM canonicalizer to re-evaluate an empty canonical plan."""

    retry_messages = list(invoke_messages)
    retry_messages.append(
        {
            "role": "system",
            "content": (
                "The previous canonicalizer output produced zero requests. Re-evaluate the latest user query using "
                "the same canonical request schema and structured context. If the request can be executed with "
                "omitted optional filters or worker defaults, return canonical requests with empty slots for the "
                'omitted values. Only return {"requests": []} when the request is truly out of scope or meaningless. '
                "Never output task_id, params, or tasks. Output JSON only."
            ),
        }
    )
    fallback_previous = previous_output or '{"requests": []}'
    retry_messages.append(
        {
            "role": "user",
            "content": (
                f"Previous canonicalizer output:\n{fallback_previous}\n\n"
                f"Latest user query:\n{user_text}"
            ),
        }
    )
    response = _model.invoke(
        retry_messages,
        config={"callbacks": _lf_callbacks()},
    )
    raw_retry = (response.content or "").strip()
    retry_plan = extract_json_from_llm(raw_retry, CanonicalPlanResponse)
    if retry_plan.answer.strip():
        return [], raw_retry
    return [
        normalize_canonical_request(request.model_dump())
        for request in retry_plan.requests
        if request.intent and request.agent
    ], raw_retry


@observe(name="planner_node")
def planner_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    """Primary LLM canonicalizer for the latest user request.

    The planner does not read raw history and does not emit executable tasks.
    It receives only the latest user text plus structured context, then emits
    canonical request(s). The downstream task_builder creates task_plan.
    """
    messages = state.get("messages", [])
    if not messages:
        return {}

    last_human = next(
        (
            m
            for m in reversed(messages)
            if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human"
        ),
        None,
    )
    if not last_human:
        return {}

    today = date.today()
    prompt = CANONICAL_PLANNER_SYSTEM_PROMPT.format(
        today=today.strftime("%Y년 %m월 %d일 (%A)"),
        today_yyyymmdd=today.strftime("%Y%m%d"),
        today_yyyy_mm_dd=today.strftime("%Y-%m-%d"),
        week_ago_yyyy_mm_dd=(today - timedelta(days=6)).strftime("%Y-%m-%d"),
        year=today.year,
    )

    meta_parts: list[str] = []
    # Reference resolution is planner-owned (reference_resolver removed): build the
    # recent-results context here each turn so follow-up references resolve from the
    # displayed prior results. Also returned to state below for downstream chaining.
    recent_results = _build_recent_results_index(messages)
    recent_context = _recent_results_prompt_context(recent_results)
    if recent_context:
        meta_parts.append(recent_context)
    if state.get("lotcd"):
        meta_parts.append(f"현재 제품: {state['lotcd']}")
    prev_lots = state.get("lot_ids") or []
    if prev_lots:
        meta_parts.append(f"이전 lot: {','.join(prev_lots)}")
    if state.get("cause_oper"):
        meta_parts.append(f"이전 main_oper: {state['cause_oper']}")
    if state.get("agent_suggestion"):
        meta_parts.append(f"이전 에이전트 제안: {state['agent_suggestion']}")
    meta = "\n".join(meta_parts)

    invoke_messages: list[dict] = [{"role": "system", "content": prompt}]
    if meta:
        invoke_messages.append(
            {"role": "system", "content": f"Structured context:\n{meta}"}
        )
    previous_assistant = _previous_assistant_prompt_context(messages, last_human)
    if previous_assistant:
        invoke_messages.append(
            {
                "role": "assistant",
                "content": f"Previous assistant message for follow-up resolution:\n{previous_assistant}",
            }
        )
    invoke_messages.append({"role": "user", "content": last_human.content})
    emit_runtime_detail(
        "planner.input",
        {
            "last_human": last_human.content,
            "meta": meta,
            "previous_assistant": previous_assistant,
            "recent_turns": [],
            "invoke_messages": invoke_messages,
        },
    )

    # 수동 JSON 파싱 (with_structured_output은 OpenRouter 호환성 문제)
    raw_text = ""
    try:
        response = _model.invoke(
            invoke_messages,
            config={"callbacks": _lf_callbacks()},
        )
        raw_text = response.content.strip()
        emit_runtime_detail("planner.raw", {"raw_text": raw_text})
        plan = extract_json_from_llm(raw_text, CanonicalPlanResponse)
    except Exception as e:
        logger.error("[Planner] canonical 파싱 실패: %s", e)
        emit_trace_event(
            "planner_output",
            source="planner",
            severity="error",
            payload={
                "status": "parse_failed",
                "task_count": 0,
                "error_type": type(e).__name__,
                "raw_length": len(raw_text or ""),
                "question_preview": preview_text(last_human.content),
                "raw_preview": preview_text(raw_text),
            },
        )
        # plain text 거절 메시지이면 state에 보존 (supervisor fallback에서 사용)
        refusal = (
            raw_text
            if (raw_text and "{" not in raw_text and len(raw_text) < 400)
            else None
        )
        result: dict = {"canonical_request": {}, "canonical_requests": []}
        if refusal:
            content = _llm_empty_plan_response(
                str(last_human.content), planner_text=refusal
            )
            result["messages"] = [AIMessage(content=content, name="planner")]
        return result

    # canonical request 수 상한 제한 — 초과 시 사용자에게 명시적으로 알림 (#22 fix)
    if len(plan.requests) > _MAX_TASKS:
        dropped = len(plan.requests) - _MAX_TASKS
        logger.warning(
            "[Planner] request 수 %d → %d로 제한 (%d개 dropped)",
            len(plan.requests),
            _MAX_TASKS,
            dropped,
        )
        stream_event(
            "status",
            StatusEvent(
                message=f"⚠ 요청하신 작업이 너무 많아 처음 {_MAX_TASKS}개만 처리합니다 ({dropped}개 생략).",
                node="planner",
            ),
        )
        plan.requests = plan.requests[:_MAX_TASKS]

    nested_answer = next(
        (request.answer.strip() for request in plan.requests if request.answer.strip()),
        "",
    )
    canonical_requests = [
        normalize_canonical_request(request.model_dump())
        for request in plan.requests
        if request.intent and request.agent
    ]
    direct_answer = plan.answer.strip() or nested_answer
    if not canonical_requests and direct_answer:
        emit_runtime_detail(
            "planner.direct_answer",
            {
                "question": last_human.content,
                "answer": direct_answer,
                "raw_text": raw_text,
            },
        )
        emit_trace_event(
            "planner_output",
            source="planner",
            payload={
                "status": "direct_answer",
                "request_count": 0,
                "question_preview": preview_text(last_human.content),
                "raw_preview": preview_text(raw_text),
            },
        )
        return {
            "canonical_request": {},
            "canonical_requests": [],
            "messages": [AIMessage(content=direct_answer, name="planner")],
            "response": direct_answer,
        }
    if not canonical_requests:
        try:
            retry_requests, retry_raw = _planner_empty_canonical_retry(
                invoke_messages,
                user_text=str(last_human.content),
                previous_output=raw_text,
            )
        except Exception as exc:
            retry_requests = []
            retry_raw = ""
            logger.warning("[Planner] empty canonical retry failed: %s", exc)
        if retry_requests:
            canonical_requests = retry_requests
            emit_runtime_detail(
                "planner.empty_retried",
                {
                    "question": last_human.content,
                    "raw_text": raw_text,
                    "retry_raw": retry_raw,
                    "canonical_requests": canonical_requests,
                },
            )
            emit_trace_event(
                "planner_output",
                source="planner",
                severity="warning",
                payload={
                    "status": "empty_retried",
                    "request_count": len(canonical_requests),
                    "question_preview": preview_text(last_human.content),
                    "raw_preview": preview_text(raw_text),
                    "retry_raw_preview": preview_text(retry_raw),
                    "canonical_requests": canonical_requests,
                },
            )
    emit_runtime_detail(
        "planner.canonical_requests", {"canonical_requests": canonical_requests}
    )
    logger.info(
        "[Planner] %d canonical request(s) 생성: %s",
        len(canonical_requests),
        [(r.get("intent"), r.get("agent")) for r in canonical_requests],
    )

    if not canonical_requests:
        # LLM이 지원 범위 외로 판단 — planner message를 state에 남겨 supervisor가 relay하도록 함
        emit_trace_event(
            "planner_output",
            source="planner",
            payload={
                "status": "empty",
                "request_count": 0,
                "question_preview": preview_text(last_human.content),
                "raw_preview": preview_text(raw_text),
                "canonical_requests": [],
            },
        )
        return {
            "canonical_request": {},
            "canonical_requests": [],
            "task_plan": [],
            "pending_tasks": [],
            "messages": [
                AIMessage(
                    content=_llm_empty_plan_response(
                        str(last_human.content), planner_text=raw_text
                    ),
                    name="planner",
                )
            ],
        }

    # Step 5②-a: resolve planner ordinal tokens ("#N"/"#last") into concrete values
    # from recent_results — deterministic. The planner judged the ordinal (semantics);
    # code fills row[N-1] (mechanical), so planner_output and downstream carry the real
    # value. Unresolvable tokens are cleared -> dispatch backstop (Step 5②-b).
    for _cr in canonical_requests:
        _cr["slots"], _ref_trace = apply_ordinal_ref(
            _cr.get("agent", ""), _cr.get("slots") or {}, recent_results
        )
        for _ev in _ref_trace:
            logger.info("[OrdinalRef] %s", _ev)

    emit_trace_event(
        "planner_output",
        source="planner",
        payload={
            "status": "ok",
            "request_count": len(canonical_requests),
            "max_tasks": _MAX_TASKS,
            "question_preview": preview_text(last_human.content),
            "canonical_requests": canonical_requests,
        },
    )

    update = {
        "canonical_request": canonical_requests[0],
        "canonical_requests": canonical_requests,
        # recent_results was populated by reference_resolver; planner owns it now.
        # Downstream (validator's _apply_recent_wads_to_map_tasks) reads it from state.
        "recent_results": recent_results,
        "canonical_trace": list(state.get("canonical_trace", []) or [])
        + [
            {
                "event": "llm_canonicalized",
                "source": "planner",
                "request_count": len(canonical_requests),
            }
        ],
    }
    # task_builder folded in: build tasks + emit plan status in the same node
    update.update(_build_tasks_update(canonical_requests))
    return update


@observe(name="task_normalizer_validator_node")
def task_normalizer_validator_node(
    state: Dict[str, Any], config: RunnableConfig
) -> dict:
    """Normalize and validate task-builder output before supervisor dispatch.

    This layer does not ask the user anything. Issues are surfaced in state and
    consumed by the downstream HITL gate.
    """

    tasks = state.get("task_plan", []) or []
    if not tasks:
        return {}

    normalized_tasks, normalization_trace = normalize_task_fields(tasks)
    normalized_tasks, recent_wads_trace = _apply_recent_wads_to_map_tasks(
        normalized_tasks,
        state,
    )
    normalization_trace.extend(recent_wads_trace)
    emit_runtime_detail("normalizer.input", {"tasks": tasks})
    validation = validate_tasks(normalized_tasks)
    emit_runtime_detail(
        "normalizer.output",
        {
            "normalized_tasks": normalized_tasks,
            "normalization_trace": normalization_trace,
            "validation_trace": validation.get("trace", []),
            "issues": validation.get("issues", []),
        },
    )
    trace = list(state.get("task_normalization_trace", []) or [])
    trace.extend(normalization_trace)
    trace.extend(validation.get("trace", []))

    for event in normalization_trace:
        logger.info("[TaskNormalizer] %s", event)
        emit_trace_event(
            "normalization_applied",
            source="task_normalizer",
            task_id=str(event.get("task_id") or ""),
            payload=event,
        )
    for event in validation.get("trace", []):
        logger.info("[TaskValidator] %s", event)
        emit_trace_event(
            "normalization_applied",
            source="task_validator",
            task_id=str(event.get("task_id") or ""),
            payload=event,
        )

    seen_issue_keys = set()
    issues = []
    for issue in validation.get("issues", []):
        key = json.dumps(issue, sort_keys=True, ensure_ascii=False)
        if key not in seen_issue_keys:
            seen_issue_keys.add(key)
            issues.append(issue)
    if issues:
        logger.info("[TaskValidator] issues=%s", issues)
    for issue in issues:
        emit_trace_event(
            "validation_issue",
            source="task_validator",
            severity=str(issue.get("severity") or "warning"),
            task_id=str(issue.get("task_id") or ""),
            payload=issue,
        )

    validated_tasks = validation.get("tasks", [])
    validated_canonical_requests = canonical_requests_from_tasks(validated_tasks)
    update: dict = {
        "canonical_request": validated_canonical_requests[0]
        if validated_canonical_requests
        else {},
        "canonical_requests": validated_canonical_requests,
        "task_plan": validated_tasks,
        "pending_tasks": validated_tasks,
        "task_normalization_trace": trace,
        "task_validation_issues": issues,
    }

    return update


_PLAN_REVIEW_SYSTEM = """
현재 분석 계획과 사용자 응답을 보고 최종 계획을 JSON으로 반환해라.

action 필드는 반드시 아래 영문 문자열 중 하나여야 한다:
- "approve" : 승인 (응/ok/확인/좋아/네/그렇게 해/빈 응답 등)
- "cancel"  : 취소 (취소/cancel/no/그만/중단 등)
- "modify"  : 수정 요청 (구체적인 변경 지시)

출력 형식:
{"action": "approve"|"cancel"|"modify", "tasks": [...]}

규칙:
- tasks는 항상 전체 task 목록 (수정 안 한 task도 포함)
- task_id, agent, params, goal 필드 유지
- approve/cancel 시에도 tasks 필드 필수 (approve → 현재 계획 그대로, cancel → [])
""".strip()


def _groupkey_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _unique_texts(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def plan_review_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    """2개 이상 task일 때만 사용자 승인을 기다린다. 단일 task는 즉시 통과.

    Validation/HITL handles missing params. This node only handles plan review.
    """
    task_plan = state.get("task_plan", [])
    canonical_requests = state.get(
        "canonical_requests"
    ) or canonical_requests_from_tasks(task_plan)
    if len(task_plan) < 2:
        return {}

    # plan_review 루프 — approve/cancel/modify 반복 가능
    # sequential interrupt 패턴: 루프 각 반복마다 새 interrupt() 생성 → resume 시 순서대로 재생
    while True:
        user_response = interrupt(
            {"type": "plan_review", "tasks": task_plan, "missing_params": []}
        )
        resp = (user_response or "").strip()

        if not resp:
            break  # 빈 응답 → approve, LLM 호출 없이 즉시 통과

        try:
            raw = _model.invoke(
                [
                    {"role": "system", "content": _PLAN_REVIEW_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"현재 canonical requests:\n{json.dumps(canonical_requests, ensure_ascii=False)}\n\n"
                            f"화면 표시용 현재 task 계획:\n{json.dumps(task_plan, ensure_ascii=False)}\n\n"
                            f'사용자 응답: "{resp}"'
                        ),
                    },
                ]
            ).content.strip()
            result = extract_json_from_llm(raw, PlanReviewResult)
        except Exception as e:
            logger.warning("[PlanReview] LLM 판단 실패 (%s) — 계획 재표시", e)
            continue  # interrupt 재호출 → 사용자에게 계획 다시 표시

        result_requests = [
            normalize_canonical_request(request.model_dump())
            for request in result.requests
        ]
        logger.info(
            "[PlanReview] action=%s requests=%s",
            result.action,
            [(r.get("intent"), r.get("agent")) for r in result_requests],
        )

        if result.action == "cancel":
            return {
                "response": "사용자가 분석 계획을 취소했습니다.",
                "canonical_request": {},
                "canonical_requests": [],
                "task_plan": [],
                "pending_tasks": [],
            }
        if result.action == "approve":
            break
        # modify → task_plan 갱신 후 루프 재시작 (새 interrupt로 수정된 플랜 재표시)
        canonical_requests = result_requests
        task_plan = build_tasks_from_canonical_requests(canonical_requests)

    return {
        "canonical_request": canonical_requests[0] if canonical_requests else {},
        "canonical_requests": canonical_requests,
        "task_plan": task_plan,
        "pending_tasks": task_plan,
    }


# ── Replanner 노드 (#8 phase 3a) ──────────────────────────────
# 공식 LangGraph plan-and-execute 패턴의 replan 단계.
# Phase 3a: past_steps 결과를 LLM에게 보여주고 남은 pending_tasks의 빈 chained-input을 채운다.
# (예: task_1 wads 결과의 lot ID들을 task_2 map_agent의 map_lot_ids에 채움)
# DO NOT 추가/삭제/순서변경 — 단순 input 채우기. Phase 3b에서 plan 전체 재구성 추가 예정.
# 그래프 wiring: agent → replanner → supervisor (#8 phase 2에서 이미 wiring 완료).


def _parse_lot_ids(params: dict) -> list[str]:
    val = (
        params.get("lot_ids")
        or params.get("map_lot_ids")
        or params.get("lh_lot_ids")
        or params.get("yield_lot_ids")
        or params.get("map_lot_id")
        or ""
    )
    if isinstance(val, list):
        return [v.strip() for v in val if v.strip()]
    return [v.strip() for v in str(val).split(",") if v.strip()]


def _parse_wf_ids(params: dict) -> list[str]:
    val = params.get("wf_ids") or params.get("map_wf_ids") or ""
    if isinstance(val, list):
        return [v.strip() for v in val if v.strip()]
    return [v.strip() for v in str(val).split(",") if v.strip()]


def _parse_fail_type(params: dict) -> str:
    return (
        params.get("fail_type")
        or params.get("wads_parameter")
        or params.get("dh_fail_type")
        or ""
    )


def _parse_cause_oper(params: dict) -> str:
    return (
        params.get("cause_oper")
        or params.get("dh_cause_oper")
        or params.get("rt_main_oper_det_desc")
        or ""
    )


_PRODUCT_LOTCD_RE = re.compile(r"^[0-9][A-Z0-9]{2}$")


def _normalize_product_lotcd(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    return text if _PRODUCT_LOTCD_RE.fullmatch(text) else ""


def _lotcd_prompt(agent: str, *, invalid_value: Any = "") -> str:
    if invalid_value:
        return (
            f"`{invalid_value}`는 제품코드 형식이 아닙니다. "
            "영문/숫자 3자리 제품코드를 다시 입력해주세요. 예: 4SA, 4SS"
        )
    if agent == "wads_agent":
        return "제품코드를 입력해주세요. 예: 4SA, 4SS"
    return "제품코드를 입력해주세요. 영문/숫자 3자리 형식입니다. 예: 4SS, 5NA, 6E2"


def _invalid_lotcd_update(
    *,
    agent: str,
    task_id: str,
    value: Any,
    message: str,
    step_count: int,
) -> Command:
    emit_runtime_detail(
        "param.invalid",
        {
            "agent": agent,
            "param": "lotcd",
            "value": value,
            "reason": "lotcd_must_be_ascii_product_code",
            "action": "abort",
        },
        task_id=task_id,
    )
    emit_trace_event(
        "validation_issue",
        source="supervisor",
        severity="error",
        task_id=task_id,
        payload={
            "type": "invalid_param",
            "agent": agent,
            "param": "lotcd",
            "reason": "lotcd_must_be_ascii_product_code",
            "value_preview": preview_text(value),
        },
    )
    return Command(
        update={
            "response": message,
            "step_count": step_count,
        },
        goto=END,
    )


def _is_placeholder_or_empty(val) -> bool:
    """빈 값 + LLM이 흔히 출력하는 placeholder 패턴 감지 (#L1+L2 fix).

    Planner LLM이 chained input에 narrative placeholder를 박는 경우가 있음:
      - "<task_1 결과 lot IDs>", "<task_1_result_lot_ids>"
      - "{{from_task_1}}", "__from_task__"
      - "task_1 결과", "result of task_1"
    이 모든 경우를 빈 input으로 간주하여 replanner LLM이 채우도록 한다.
    """
    if val is None:
        return True
    if not isinstance(val, str):
        return not val
    v = val.strip()
    if not v:
        return True
    if (
        (v.startswith("<") and v.endswith(">"))
        or (v.startswith("{{") and v.endswith("}}"))
        or (v.startswith("__") and v.endswith("__"))
    ):
        return True
    lower = v.lower()
    if any(
        k in lower
        for k in (
            "task_1",
            "task_2",
            "task_3",
            "결과",
            "from_task",
            "result of",
            "from task",
        )
    ):
        return True
    return False


def _latest_wads_result(state: dict) -> dict:
    for message in reversed(state.get("messages", []) or []):
        if (
            isinstance(message, AIMessage)
            and getattr(message, "name", "") == "wads_sql_result"
        ):
            wads_data = (getattr(message, "additional_kwargs", None) or {}).get(
                "wads_result"
            ) or {}
            if isinstance(wads_data, dict) and wads_data:
                return wads_data
    return {}


def _wads_groupkeys_by_map_oper(wads_data: dict) -> dict[str, list[str]]:
    raw = wads_data.get("groupkeys_by_map_oper") or {}
    if not isinstance(raw, dict):
        return {}

    grouped: dict[str, list[str]] = {}
    for raw_oper, raw_groupkeys in raw.items():
        oper = _normalize_map_oper(str(raw_oper or ""))
        groupkeys = _unique_texts(_groupkey_list(raw_groupkeys))
        if oper and groupkeys:
            grouped[oper] = groupkeys
    return grouped


def _map_oper_from_wads_row(row: dict[str, Any]) -> str:
    oper = _normalize_map_oper(str(row.get("map_oper") or ""))
    if oper:
        return oper
    category = str(row.get("category") or "").upper()
    if "PT1C" in category:
        return "PT1C"
    if "PT1H" in category:
        return "PT1H"
    return ""


def _recent_wads_groupkeys_by_map_oper(state: dict) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for result in state.get("recent_results", []) or []:
        if not isinstance(result, dict) or result.get("source_agent") != "wads_agent":
            continue
        for row in result.get("rows", []) or []:
            if not isinstance(row, dict):
                continue
            oper = _map_oper_from_wads_row(row)
            groupkeys = _groupkey_list(row.get("groupkeys") or row.get("groupkey"))
            if not oper or not groupkeys:
                continue
            bucket = grouped.setdefault(oper, [])
            bucket.extend(groupkeys)
    return {
        oper: _unique_texts(groupkeys)
        for oper, groupkeys in grouped.items()
        if groupkeys
    }


_REPORT_TOKEN_RE = re.compile(r"^#R(\d+)$")


def _is_report_row(row: Any) -> bool:
    """A report row = one degradation report (one parameter×lot with its wafers)."""
    return isinstance(row, dict) and (
        row.get("report_index") not in (None, "")
        or (row.get("groupkeys") and row.get("parameter"))
    )


def _latest_report_result(state: dict) -> dict | None:
    """Most-recent wads result whose rows are report rows (report_index/groupkeys)."""
    for result in reversed(state.get("recent_results", []) or []):
        if not isinstance(result, dict) or result.get("source_agent") != "wads_agent":
            continue
        if any(_is_report_row(r) for r in (result.get("rows") or [])):
            return result
    return None


def _resolve_report_ordinal(state: dict, ordinal: int) -> tuple[list[str], str] | None:
    """row[ordinal-1] of the latest report result -> (groupkeys, map_oper).

    None if there is no report-structured result or the ordinal is out of range —
    the caller marks it unresolved so the dispatch missing-param backstop asks
    (never silently substitutes another report / all lots).
    """
    result = _latest_report_result(state)
    if not result:
        return None
    rows = result.get("rows") or []
    idx = ordinal - 1
    if idx < 0 or idx >= len(rows) or not _is_report_row(rows[idx]):
        return None
    row = rows[idx]
    groupkeys = _unique_texts(_groupkey_list(row.get("groupkeys") or row.get("groupkey")))
    if not groupkeys:
        return None
    return groupkeys, _map_oper_from_wads_row(row)


def _apply_report_ordinal_to_map_task(task: dict, state: dict, trace: list[dict]) -> dict:
    """Resolve a report-ordinal token ("#RN") in a map task's groupkey by slicing the
    Nth report row's groupkeys (+map_oper). Deterministic — the planner only judged
    "Nth report"; the row[N-1] slice is pure code. Unresolvable -> UNRESOLVED_REF."""
    if task.get("agent") != "map_agent":
        return task
    params = dict(task.get("params") or {})
    match = _REPORT_TOKEN_RE.match(str(params.get("groupkey") or "").strip())
    if not match:
        return task
    ordinal = int(match.group(1))
    resolved = _resolve_report_ordinal(state, ordinal)
    if resolved:
        groupkeys, oper = resolved
        params["groupkey"] = ",".join(groupkeys)
        if oper and _is_placeholder_or_empty(params.get("map_oper")):
            params["map_oper"] = oper
        trace.append({
            "event": "report_ordinal_resolved", "task_id": task.get("task_id", ""),
            "agent": "map_agent", "ordinal": ordinal,
            "groupkey_count": len(groupkeys), "map_oper": oper,
        })
    else:
        params["groupkey"] = UNRESOLVED_REF
        trace.append({
            "event": "report_ordinal_unresolved", "task_id": task.get("task_id", ""),
            "agent": "map_agent", "ordinal": ordinal,
        })
    return {**task, "params": params}


def _apply_recent_wads_to_map_tasks(
    tasks: list[dict],
    state: dict,
) -> tuple[list[dict], list[dict]]:
    trace: list[dict] = []
    # Step 5②-c: resolve report-ordinal tokens ("#RN") first — slice the latest report
    # result's row[N-1]. These tasks then carry a non-empty groupkey (real or the
    # UNRESOLVED_REF sentinel), so the per-map_oper chaining below skips them.
    tasks = [_apply_report_ordinal_to_map_task(t, state, trace) for t in tasks]

    groups = _recent_wads_groupkeys_by_map_oper(state)
    if not groups:
        return tasks, trace

    expanded: list[dict] = []
    for task in tasks:
        params = dict(task.get("params") or {})
        needs_groupkey = (
            task.get("agent") == "map_agent"
            and _is_placeholder_or_empty(params.get("groupkey"))
            and _is_placeholder_or_empty(params.get("map_groupkey"))
            and _is_placeholder_or_empty(params.get("lot_ids"))
        )
        if not needs_groupkey:
            expanded.append(task)
            continue

        selected_oper = _normalize_map_oper(str(params.get("map_oper") or ""))
        if selected_oper:
            selected_groupkeys = groups.get(selected_oper) or []
            if selected_groupkeys:
                updated = {
                    **task,
                    "params": {**params, "groupkey": ",".join(selected_groupkeys)},
                }
                expanded.append(updated)
                trace.append(
                    {
                        "event": "recent_wads_groupkey_applied",
                        "task_id": task.get("task_id", ""),
                        "agent": "map_agent",
                        "map_oper": selected_oper,
                        "groupkey_count": len(selected_groupkeys),
                    }
                )
                continue
            expanded.append(task)
            continue

        if len(groups) == 1:
            inferred_oper, inferred_groupkeys = next(iter(groups.items()))
            updated = {
                **task,
                "params": {
                    **params,
                    "map_oper": inferred_oper,
                    "groupkey": ",".join(inferred_groupkeys),
                },
            }
            expanded.append(updated)
            trace.append(
                {
                    "event": "recent_wads_map_oper_groupkey_applied",
                    "task_id": task.get("task_id", ""),
                    "agent": "map_agent",
                    "map_oper": inferred_oper,
                    "groupkey_count": len(inferred_groupkeys),
                }
            )
            continue

        base_request = canonical_requests_from_tasks([task])[0]
        request_expansion: list[dict] = []
        task_ids: list[str] = []
        for index, (oper, groupkeys) in enumerate(groups.items(), start=1):
            slots = dict(base_request.get("slots") or {})
            slots["map_oper"] = oper
            slots["groupkey"] = ",".join(groupkeys)
            slots.pop("lot_ids", None)
            slots.pop("wf_ids", None)
            request_expansion.append(
                {
                    **base_request,
                    "slots": slots,
                    "goal": f"[{oper}] {task.get('goal', '')}".strip(),
                    "source": {
                        **dict(base_request.get("source") or {}),
                        "type": "recent_wads_map_oper_fanout",
                    },
                }
            )
            task_ids.append(f"{task.get('task_id', 'task_map')}_p{index}")
        task_expansion = build_tasks_from_canonical_requests(
            request_expansion, task_ids=task_ids
        )
        expanded.extend(task_expansion)
        trace.append(
            {
                "event": "recent_wads_map_oper_fanout",
                "task_id": task.get("task_id", ""),
                "agent": "map_agent",
                "map_opers": list(groups.keys()),
                "task_count": len(task_expansion),
            }
        )

    return expanded, trace


def _resolve_chained_params(task: dict, state: dict) -> dict:
    """task.params의 빈 chained 필드를 state.messages의 structured tool result에서 코드로 자동 채움."""
    params = dict(task.get("params") or {})
    wads_data = _latest_wads_result(state)

    lot_ids = wads_data.get("lot_ids") or []
    groupkeys = wads_data.get("groupkeys") or []
    groupkeys_by_oper = _wads_groupkeys_by_map_oper(wads_data)

    if task.get("agent") == "map_agent" and groupkeys_by_oper:
        selected_oper = _normalize_map_oper(str(params.get("map_oper") or ""))
        if selected_oper and selected_oper in groupkeys_by_oper:
            if (
                _is_placeholder_or_empty(params.get("groupkey"))
                and _is_placeholder_or_empty(params.get("map_groupkey"))
                and _is_placeholder_or_empty(params.get("lot_ids"))
            ):
                params["groupkey"] = ",".join(groupkeys_by_oper[selected_oper])
                logger.info(
                    "[ResolveChained] groupkey ← wads_sql_result.%s (%d wafers)",
                    selected_oper,
                    len(groupkeys_by_oper[selected_oper]),
                )
        elif not selected_oper and len(groupkeys_by_oper) == 1:
            inferred_oper, inferred_groupkeys = next(iter(groupkeys_by_oper.items()))
            params["map_oper"] = inferred_oper
            if (
                _is_placeholder_or_empty(params.get("groupkey"))
                and _is_placeholder_or_empty(params.get("map_groupkey"))
                and _is_placeholder_or_empty(params.get("lot_ids"))
            ):
                params["groupkey"] = ",".join(inferred_groupkeys)
            logger.info(
                "[ResolveChained] map_oper/groupkey ← wads_sql_result.%s (%d wafers)",
                inferred_oper,
                len(inferred_groupkeys),
            )

    if (
        task.get("agent") == "map_agent"
        and _is_placeholder_or_empty(params.get("groupkey"))
        and _is_placeholder_or_empty(params.get("map_groupkey"))
        and _is_placeholder_or_empty(params.get("lot_ids"))
        and groupkeys
        and not (groupkeys_by_oper and _is_placeholder_or_empty(params.get("map_oper")))
    ):
        params["groupkey"] = ",".join(
            str(v).strip() for v in groupkeys if str(v).strip()
        )
        logger.info(
            "[ResolveChained] groupkey ← wads_sql_result (%d wafers)", len(groupkeys)
        )
    elif (
        _is_placeholder_or_empty(params.get("lot_ids"))
        and lot_ids
        and (
            task.get("agent") != "map_agent"
            or (
                _is_placeholder_or_empty(params.get("groupkey"))
                and _is_placeholder_or_empty(params.get("map_groupkey"))
            )
        )
    ):
        params["lot_ids"] = lot_ids
        logger.info(
            "[ResolveChained] lot_ids ← wads_sql_result (%d lots)", len(lot_ids)
        )

    if _is_placeholder_or_empty(params.get("cause_oper")):
        fallback = state.get("cause_oper")
        if fallback:
            params["cause_oper"] = fallback
            logger.info("[ResolveChained] cause_oper ← %s", fallback)

    if task.get("agent") == "map_agent" and _is_placeholder_or_empty(
        params.get("map_oper")
    ):
        fallback_oper = _normalize_map_oper(str(wads_data.get("map_oper") or ""))
        if fallback_oper:
            params["map_oper"] = fallback_oper
            logger.info(
                "[ResolveChained] map_oper ← wads_sql_result (%s)", fallback_oper
            )

    # Step 5②-b: after ALL chaining/injection above, an unresolved-reference slot
    # still holds the UNRESOLVED_REF sentinel (chaining skipped it as non-empty).
    # Strip it to "" so the dispatch missing-param guard asks the user, instead of
    # silently substituting chained wads lots. Plain-empty slots (real chaining
    # intent) were already filled above and are unaffected.
    for _slot, _val in list(params.items()):
        if _val == UNRESOLVED_REF:
            params[_slot] = ""

    return params


def _extract_result_payloads(message: Any) -> list[Any]:
    """Return full ResultEnvelope payloads stored on a message, if present."""

    additional_kwargs = getattr(message, "additional_kwargs", None) or {}
    result_payload = additional_kwargs.get("result")
    if result_payload is None:
        return []
    if isinstance(result_payload, list):
        return result_payload
    return [result_payload]


def _build_recent_results_index(messages: list) -> list[dict]:
    """Derive the bounded resolver index from message ResultEnvelopes.

    Source of truth stays in AIMessage.additional_kwargs["result"]. This index
    is rebuilt from messages, pruned to compact metadata/rows, and stored only
    as supervisor scratchpad for later reference resolution.
    """

    entries: list[dict] = []
    for message in messages:
        for payload in _extract_result_payloads(message):
            try:
                entries.append(build_recent_result_index_entry(payload))
            except ResultContractError as exc:
                logger.warning(
                    "[RecentResults] invalid ResultEnvelope skipped: %s",
                    str(exc).splitlines()[0],
                )
            except Exception as exc:
                logger.warning("[RecentResults] result index build failed: %s", exc)

    return prune_recent_results(entries)


def _recent_results_update_from_messages(
    messages: list, current_recent_results: list | None
) -> dict:
    """Return an overwrite update for the derived recent_results index."""

    recent_results = _build_recent_results_index(messages)
    if recent_results == (current_recent_results or []):
        return {}
    return {"recent_results": recent_results}


def _recent_results_update(state: dict) -> dict:
    """Return a state update only when the derived recent_results index changed."""

    return _recent_results_update_from_messages(
        state.get("messages", []),
        state.get("recent_results", []),
    )


def _latest_result_envelope_for_task(messages: list, task_id: str) -> dict | None:
    """Find the latest ResultEnvelope attached to an agent message for task_id."""

    if not task_id:
        return None
    for message in reversed(messages or []):
        for payload in reversed(_extract_result_payloads(message)):
            if not isinstance(payload, dict):
                continue
            provenance = payload.get("provenance") or {}
            if provenance.get("task_id") == task_id:
                return payload
    return None


def _emit_task_outcome_trace(state: dict, task_id: str) -> None:
    envelope = _latest_result_envelope_for_task(state.get("messages", []), task_id)
    if not envelope:
        emit_trace_event(
            "task_completed",
            source="replanner",
            task_id=task_id,
            payload={"status": "unknown", "result_envelope_found": False},
        )
        return

    status = str(envelope.get("status") or "")
    event_type = "task_failed" if status in {"error", "invalid"} else "task_completed"
    emit_trace_event(
        event_type,
        source="replanner",
        task_id=task_id,
        result_id=str(envelope.get("result_id") or ""),
        payload=summarize_result_envelope(envelope),
        severity="error" if event_type == "task_failed" else "info",
    )


def _needs_replan(pending: list[dict]) -> bool:
    """Phase 3a 휴리스틱: pending task에 빈 chained-input이 있으면 LLM 호출 필요."""
    for task in pending:
        agent = task.get("agent", "")
        params = task.get("params", {}) or {}
        if agent == "map_agent":
            if _is_placeholder_or_empty(params.get("map_oper")):
                return True
            if (
                _is_placeholder_or_empty(params.get("lot_ids"))
                and _is_placeholder_or_empty(params.get("groupkey"))
                and all(
                    _is_placeholder_or_empty(params.get(k))
                    for k in ("map_lot_id", "map_lot_ids", "map_groupkey")
                )
            ):
                return True
        elif agent == "lot_history_agent":
            if _is_placeholder_or_empty(params.get("lot_ids")) and all(
                _is_placeholder_or_empty(params.get(k))
                for k in ("map_lot_id", "map_lot_ids", "lh_lot_ids")
            ):
                return True
        elif agent == "relation_tree_agent":
            # relation_tree_agent는 lot_ids 아닌 lotcd+cause_oper 사용
            if _is_placeholder_or_empty(
                params.get("cause_oper")
            ) and _is_placeholder_or_empty(params.get("rt_main_oper_det_desc")):
                return True
        elif agent == "fail_history_agent":
            if all(
                _is_placeholder_or_empty(params.get(k))
                for k in ("dh_query", "fail_type", "dh_fail_type", "cause_oper")
            ):
                return True
    return False


def _expand_map_tasks_by_wads_map_oper(
    pending: list[dict],
    state: dict,
) -> tuple[list[dict], dict[str, list[dict]]]:
    groups = _wads_groupkeys_by_map_oper(_latest_wads_result(state))
    if len(groups) < 2:
        return pending, {}

    expanded_pending: list[dict] = []
    replacements: dict[str, list[dict]] = {}
    for task in pending:
        params = dict(task.get("params") or {})
        if (
            task.get("agent") != "map_agent"
            or not _is_placeholder_or_empty(params.get("map_oper"))
            or not _is_placeholder_or_empty(params.get("groupkey"))
            or not _is_placeholder_or_empty(params.get("map_groupkey"))
            or not _is_placeholder_or_empty(params.get("lot_ids"))
        ):
            expanded_pending.append(task)
            continue

        request_expansion: list[dict] = []
        task_ids: list[str] = []
        base_request = canonical_requests_from_tasks([task])[0]
        for index, (oper, groupkeys) in enumerate(groups.items(), start=1):
            next_slots = dict(base_request.get("slots") or {})
            next_slots["map_oper"] = oper
            next_slots["groupkey"] = ",".join(groupkeys)
            next_slots.pop("lot_ids", None)
            next_slots.pop("wf_ids", None)
            request_expansion.append(
                {
                    **base_request,
                    "slots": next_slots,
                    "goal": f"[{oper}] {task.get('goal', '')}".strip(),
                    "source": {
                        **dict(base_request.get("source") or {}),
                        "type": "wads_map_oper_fanout",
                    },
                }
            )
            task_ids.append(f"{task.get('task_id', 'task_map')}_p{index}")

        task_expansion = build_tasks_from_canonical_requests(
            request_expansion, task_ids=task_ids
        )
        replacements[str(task.get("task_id") or "")] = task_expansion
        expanded_pending.extend(task_expansion)

    return expanded_pending, replacements


def _replace_plan_tasks(
    task_plan: list[dict],
    replacements: dict[str, list[dict]],
) -> list[dict]:
    if not replacements:
        return task_plan

    updated: list[dict] = []
    for task in task_plan:
        task_id = str(task.get("task_id") or "")
        if task_id in replacements:
            updated.extend(replacements[task_id])
        else:
            updated.append(task)
    return updated


def _task_ids_for_replanned_requests(
    canonical_requests: list[dict[str, Any]],
    pending_tasks: list[dict],
) -> list[str]:
    if len(canonical_requests) <= len(pending_tasks):
        return [
            str(task.get("task_id") or f"task_{index}")
            for index, task in enumerate(
                pending_tasks[: len(canonical_requests)], start=1
            )
        ]

    if len(pending_tasks) == 1:
        base_id = str(pending_tasks[0].get("task_id") or "task_1")
        return [
            f"{base_id}_p{index}" for index in range(1, len(canonical_requests) + 1)
        ]

    task_ids = [
        str(task.get("task_id") or f"task_{index}")
        for index, task in enumerate(pending_tasks, start=1)
    ]
    task_ids.extend(
        f"task_extra_p{index}"
        for index in range(1, len(canonical_requests) - len(task_ids) + 1)
    )
    return task_ids


def _replacements_from_replanned_tasks(
    pending_tasks: list[dict],
    new_tasks: list[dict],
) -> dict[str, list[dict]]:
    if not pending_tasks or not new_tasks:
        return {}
    if len(pending_tasks) == 1:
        return {str(pending_tasks[0].get("task_id") or ""): new_tasks}
    return {
        str(old.get("task_id") or ""): [new]
        for old, new in zip(pending_tasks, new_tasks)
    }


@observe(name="replanner_node")
def replanner_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    """phase 3a replanner: past_steps 기반 LLM dynamic input 채우기.

    호출 조건:
    - past_steps와 pending_tasks 둘 다 존재
    - pending_tasks 중 어느 하나라도 빈 chained-input을 가짐 (`_needs_replan`)

    호출 결과:
    - LLM이 갱신한 새 pending_tasks 반환 (state.pending_tasks overwrite)
    - LLM 실패 시 pass-through (phase 2 동작 유지)
    """
    past = state.get("past_steps", [])
    pending = state.get("pending_tasks", [])
    task_plan = state.get("task_plan", [])
    scratchpad_update = _recent_results_update(state)

    # 항상 마지막 task 결과 로깅 (관측성)
    if past:
        last_task_id, last_summary = past[-1]
        logger.info(
            "[Replanner] last_task=%s pending=%d summary=%s",
            last_task_id,
            len(pending),
            str(last_summary)[:120],
        )
        stream_event(
            "status",
            StatusEvent(
                message=f"✅ [{last_task_id}] 완료",
                node="replanner",
            ),
        )
        _emit_task_outcome_trace(state, str(last_task_id or ""))

    # ── canonical plan-and-execute 종료 판정 ──
    # 모든 planned task가 실행되었고 남은 pending이 없으면 plan 완료.
    # past_steps는 per-turn reset(`agent_server.py`)이라 턴 내 실행 기록만 반영.
    # should_end conditional edge가 `state["response"]`를 보고 END 분기.
    # TODO(phase 3b): replanner가 pending_tasks를 확장하는 단계 도입 시 이 조건을 ID 기반으로 교체.
    #   현재는 plan 크기 고정 가정. ID 기반 예: {t["task_id"] for t in task_plan} <= {tid for tid, _ in past}
    if not pending and task_plan:
        messages = state.get("messages", [])
        last_agent_msg = next(
            (
                m.content
                for m in reversed(messages)
                if isinstance(m, AIMessage)
                and getattr(m, "name", "") in _AGENT_NAMES
                and isinstance(m.content, str)
                and m.content.strip()
            ),
            "분석을 완료했습니다.",
        )
        logger.info(
            "[Replanner] plan 완료 감지 (tasks=%d) → response set, should_end → END",
            len(task_plan),
        )
        return {
            **scratchpad_update,
            "response": last_agent_msg,
        }

    # Fast path: 큐 비었거나 past 비었거나 chained-input 의존이 없으면 LLM 호출 생략
    if not pending or not past:
        return scratchpad_update
    expanded_pending, replacements = _expand_map_tasks_by_wads_map_oper(pending, state)
    if replacements:
        logger.info(
            "[Replanner] WADS CATEGORY 기반 map_oper fan-out: %d → %d pending tasks",
            len(pending),
            len(expanded_pending),
        )
        expanded_task_plan = _replace_plan_tasks(task_plan, replacements)
        return {
            **scratchpad_update,
            "canonical_requests": canonical_requests_from_tasks(expanded_task_plan),
            "pending_tasks": expanded_pending,
            "task_plan": expanded_task_plan,
        }
    # C1: 코드 해소 시뮬레이션 — _resolve_chained_params가 모든 pending을 해소할 수 있으면 LLM 호출 생략
    simulated_pending = [
        {**t, "params": _resolve_chained_params(t, state)} for t in pending
    ]
    if not _needs_replan(simulated_pending):
        logger.info(
            "[Replanner] 코드 해소로 chained input 충족 → LLM 호출 생략 (pass-through)"
        )
        return scratchpad_update

    # 사용자 원본 query (rewrite 결과)
    messages = state.get("messages", [])
    last_human = next(
        (
            m
            for m in reversed(messages)
            if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human"
        ),
        None,
    )
    if not last_human:
        return scratchpad_update

    # past + pending을 LLM 입력으로 직렬화
    past_str = "\n".join(f"- {tid}: {str(summary)[:400]}" for tid, summary in past)
    pending_requests = canonical_requests_from_tasks(pending)
    pending_ids = [str(task.get("task_id") or "") for task in pending]
    pending_str = json.dumps(pending_requests, ensure_ascii=False)

    today = date.today()
    prompt = REPLANNER_SYSTEM_PROMPT.format(
        today=today.strftime("%Y년 %m월 %d일 (%A)"),
    )
    user_msg = (
        f"원본 사용자 요청: {last_human.content}\n\n"
        f"이미 실행된 task와 결과:\n{past_str}\n\n"
        f"남은 canonical requests (slots에 빈 값이 있으면 위 결과에서 추출하여 채워라):\n{pending_str}\n\n"
        f"참고용 pending task ids(출력하지 마라): {pending_ids}\n\n"
        f"업데이트된 남은 canonical request 목록을 CanonicalPlanResponse JSON 형식으로 반환:"
    )

    try:
        response = _model.invoke(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_msg},
            ],
            config={"callbacks": _lf_callbacks()},
        )
        raw = (response.content or "").strip()
        plan = extract_json_from_llm(raw, CanonicalPlanResponse)
    except Exception as e:
        logger.warning("[Replanner] LLM 호출 실패 — pass-through: %s", e)
        return scratchpad_update

        new_requests = [
            normalize_canonical_request(request.model_dump())
            for request in plan.requests
            if request.intent and request.agent
        ]
    if not new_requests:
        logger.warning("[Replanner] LLM이 빈 requests 반환 — pass-through")
        return scratchpad_update
    new_tasks = build_tasks_from_canonical_requests(
        new_requests,
        task_ids=_task_ids_for_replanned_requests(new_requests, pending),
    )
    import re as _re

    orig_ids = {t.get("task_id") for t in pending}
    new_ids = {t.get("task_id") for t in new_tasks}
    # R2 fix: task 추가는 fan-out(_p{n} suffix) 패턴만 허용, 그 외 거부
    if len(new_tasks) > len(pending):
        _fanout_ok = all(
            tid in orig_ids
            or (
                bool(_re.match(r"^(.+)_p\d+$", tid))
                and _re.match(r"^(.+)_p\d+$", tid).group(1) in orig_ids
            )
            for tid in new_ids
        )
        if not _fanout_ok:
            logger.warning(
                "[Replanner] 허용되지 않은 task 추가 (%d → %d) — 거부",
                len(pending),
                len(new_tasks),
            )
            return scratchpad_update
        logger.info(
            "[Replanner] fan-out 허용: %d → %d tasks", len(pending), len(new_tasks)
        )
    # base task_id 기반 subset 검증 (_p{n} suffix 허용)
    _base_ids = {_re.sub(r"_p\d+$", "", tid) for tid in new_ids}
    if not _base_ids.issubset(orig_ids):
        logger.warning(
            "[Replanner] 새 base task_id 추가 %s — 거부",
            sorted(_base_ids - orig_ids),
        )
        return scratchpad_update
    if new_tasks == pending:
        logger.info("[Replanner] LLM 결과 변경 없음 (pass-through)")
        return scratchpad_update

    # R3 fix: LLM이 채운 params에 여전히 placeholder가 있으면 거부
    # (예: "task_1의 결과를 사용하세요" 같은 narrative placeholder)
    if _needs_replan(new_tasks):
        logger.warning(
            "[Replanner] LLM 결과에 여전히 빈 chained params 존재 — 거부, supervisor interrupt로 위임"
        )
        return scratchpad_update

    logger.info(
        "[Replanner] plan 갱신: %d → %d tasks (chained input filled)",
        len(pending),
        len(new_tasks),
    )
    replacements = _replacements_from_replanned_tasks(pending, new_tasks)
    updated_task_plan = _replace_plan_tasks(task_plan, replacements)
    return {
        **scratchpad_update,
        "canonical_requests": canonical_requests_from_tasks(updated_task_plan),
        "task_plan": updated_task_plan,
        "pending_tasks": new_tasks,
    }


# ── Supervisor 노드 ──────────────────────────────────────────
@observe(name="supervisor_node")
def _project_task_params(agent: str, task_params: dict, state: dict) -> dict:
    """Project a task's resolved params into the per-agent state fields the agent reads.

    Pure mapping only — no validation/HITL (that stays in supervisor_node). Two
    subtleties preserved from the inline version:
    - Order: the common fields set lot_ids/wf_ids/groupkey first, then yield_agent
      CLEARS them ([]/"") because yield queries by lotcd/period, not by lot.
    - lotcd inheritance from stale state differs per agent: yield and fail_history
      fall back to state["lotcd"]; wads does NOT (it can query by date/param alone,
      so it must not implicitly inherit a previous product code).
    """
    proj: dict = {}

    # lotcd 공유 — yield/wads/fail_history만, agent별 상속 차등 유지.
    if agent == "yield_agent":
        proj["lotcd"] = task_params.get("lotcd") or state.get("lotcd", "")
    elif agent == "wads_agent":
        proj["lotcd"] = task_params.get("lotcd", "")
    elif agent == "fail_history_agent":
        proj["lotcd"] = task_params.get("lotcd", state.get("lotcd", ""))

    # 통합 필드: 모든 agent에 공통 적용
    proj["lot_ids"] = _parse_lot_ids(task_params)
    proj["wf_ids"] = _parse_wf_ids(task_params)
    proj["groupkey"] = (
        task_params.get("groupkey")
        or task_params.get("map_groupkey")
        or task_params.get("yield_groupkey")
        or ""
    )
    proj["fail_type"] = _parse_fail_type(task_params)
    proj["cause_oper"] = _parse_cause_oper(task_params)

    if agent == "yield_agent":
        proj.update(
            {
                "ref_date": task_params.get("ref_date", state.get("ref_date", "")),
                "unit": task_params.get("unit", state.get("unit", "weekly")),
                "periods": task_params.get("periods", state.get("periods", 0)),
                "lot_ids": [],
                "wf_ids": [],
                "groupkey": "",
                "filter_params": [],
            }
        )

    elif agent == "wads_agent":
        proj.update(
            {
                "wads_start_tm": task_params.get("wads_start_tm", ""),
                "wads_end_tm": task_params.get("wads_end_tm")
                or date.today().strftime("%Y-%m-%d"),
            }
        )

    elif agent == "map_agent":
        proj.update(
            {
                "map_type": task_params.get("map_type", "binmap"),
                "map_oper": task_params.get("map_oper") or state.get("map_oper", ""),
            }
        )

    elif agent == "fail_history_agent":
        proj["dh_query"] = task_params.get("dh_query", "")

    elif agent == "relation_tree_agent":
        # lotcd(3자)는 rt_lot_code와 동일 개념 — 구 필드 fallback 포함
        if not proj.get("lotcd"):
            proj["lotcd"] = task_params.get("rt_lot_code") or state.get("lotcd", "")

    return proj


def _missing_required_fields(
    agent: str, update_dict: dict, rejections: dict | None = None
) -> list[dict]:
    """Required slots still missing for this agent, as fields[] specs — the source of
    truth for "what is missing". required_any (map lot_ids|groupkey) is ONE item.
    lotcd VALUE validity is NOT here; that is a separate validation step.

    `rejections` maps slot -> reason for a value just rejected by its format guard; the
    re-ask shows that reason so the user knows WHY they are being asked again."""
    fields: list[dict] = []
    if agent == "map_agent":
        if not update_dict.get("lot_ids") and not update_dict.get("groupkey"):
            fields.append({
                "slot": "lot_ids",
                "label": "맵을 조회할 Lot ID를 입력해주세요. (예: 4SS2DPD 또는 4SS2DPD,4SSXCEW)",
                "type": "lot_ids",
                "required_any_group": "lot_or_groupkey",
            })
        if not _normalize_map_oper(update_dict.get("map_oper", "")):
            fields.append({
                "slot": "map_oper",
                "label": "PT1H / PT1C 중 어떤 공정의 맵을 조회할까요?",
                "type": "map_oper",
                "validation_hint": "PT1H|PT1C",
            })
    elif agent == "relation_tree_agent":
        if not update_dict.get("lotcd"):
            fields.append({
                "slot": "lotcd",
                "label": "연관 분석할 LOT 코드를 입력해주세요. (예: 4SS2DPD)",
                "type": "lotcd",
            })
        if not update_dict.get("cause_oper"):
            fields.append({
                "slot": "cause_oper",
                "label": "연관 분석할 main 공정명을 입력해주세요. (예: STEP07 또는 STEP07,STEP08)",
                "type": "cause_oper",
            })
    elif agent == "yield_agent":
        if not update_dict.get("lotcd"):
            fields.append({"slot": "lotcd", "label": _lotcd_prompt(agent), "type": "lotcd"})
    elif agent == "lot_history_agent":
        if not update_dict.get("lot_ids"):
            fields.append({
                "slot": "lot_ids",
                "label": "이력을 조회할 LOT ID를 입력해주세요. (예: 4SS2DPD 또는 4SS2DPD,4SSXCEW)",
                "type": "lot_ids",
            })
    for field in fields:  # re-ask shows WHY the previous value was rejected
        reason = (rejections or {}).get(field["slot"])
        if reason:
            field["label"] = reason
    return fields


def _compose_fields_message(fields: list[dict]) -> str:
    """Human prompt for the Streamlit fallback. Single field shows its own label;
    multiple shows them together so one answer can address all."""
    if len(fields) == 1:
        return fields[0]["label"]
    return "다음 정보를 입력해주세요 — " + " / ".join(f["label"] for f in fields)


def _ask_fields(fields: list[dict], current_task: dict) -> dict:
    """ONE interrupt for all missing slots; returns {slot: raw_value}.

    Canonical resume is a {slot: value} dict (React form / e2e client). A bare-string
    resume (degraded Streamlit fallback) fills ONLY the first slot — the rest stay
    missing and are re-asked next loop. No positional parsing of a string into multiple
    slots (that would silently mis-map values). `param` is the first slot for
    observability only; the source of truth is `fields`."""
    slots = [f["slot"] for f in fields]
    answer = interrupt({
        "type": "missing_param",
        "param": slots[0],
        "fields": fields,
        "message": _compose_fields_message(fields),
        "route": current_task.get("agent", ""),
    })
    if isinstance(answer, dict):
        return {s: answer.get(s, "") for s in slots}
    return {slots[0]: str(answer).strip()}


# REMOVE-IN-축4: this lot_ids format guard is an INTERIM backstop for the HITL
# str-fallback raw-insert silent-wrong. Remove it once 축4 unifies resume parsing
# through the planner (lot_ids single-path: planner-canonicalized values only) and
# that single path is verified — at which point a format guard here is redundant.
_LOT_ID_RE = re.compile(r"^[0-9Tt][A-Za-z0-9]{6,8}$")


def _validate_lot_ids(raw: Any) -> tuple[list[str] | None, list[str]]:
    """Split a comma-separated lot_ids answer, validate each token, UPPERCASE the valid
    ones so they match DB-stored lot ids (lot_id_variants only does 4<->T, not case —
    so a lowercased lot would otherwise miss and 0-row silently).

    Format (type sanity, not semantics): lotcd[3] + 4 chars, plus 1-2 for experimental
    lots = 7-9 alphanumeric, first char a digit or T (lotcd is digit-prefixed across
    products: 4SS/5QQ/6AG/…, with the 4<->T variant). All-or-nothing: if ANY token is
    malformed, returns (None, [bad tokens]) — never a partial fill (dropping a lot
    silently is itself a silent-wrong). Empty -> (None, [])."""
    tokens = [t.strip() for t in str(raw).split(",") if t.strip()]
    if not tokens:
        return None, []
    invalid = [t for t in tokens if not _LOT_ID_RE.match(t)]
    if invalid:
        return None, invalid
    return [t.upper() for t in tokens], []


def _apply_field(slot: str, value: Any, update_dict: dict) -> str | None:
    """Apply a HITL answer to its slot. Returns None on success, or a Korean rejection
    reason (slot left UNfilled) when the value fails its format guard — the caller
    re-asks with that reason instead of querying garbage (silent-wrong -> backstop)."""
    v = str(value).strip()
    if slot == "lot_ids":
        valid, invalid = _validate_lot_ids(v)
        if valid is None:
            bad = ", ".join(invalid) if invalid else v
            return (
                f"'{bad}'는 lot ID 형식이 맞지 않습니다 (공백·특수문자 없이 7~9자, "
                f"예: 4SS2DPD). 전체를 다시 입력해주세요. (다중: 4SS2DPD,4SSXCEW)"
            )
        update_dict["lot_ids"] = valid
        return None
    if slot == "map_oper":
        update_dict["map_oper"] = _normalize_map_oper(v) or "PT1H"
        return None
    update_dict[slot] = v
    return None


def _validate_lotcd_or_early_return(
    agent: str, update_dict: dict, current_task: dict, step_count: int
):
    """yield/wads lotcd VALUE validation — separate from the batch (an invalid value is
    not a missing slot). Invalid product code -> re-prompt interrupt -> persistent
    invalid -> early return. 6d ANCHOR: param=lotcd, message contains '형식이 아닙니다'."""
    normalized_lotcd = _normalize_product_lotcd(update_dict.get("lotcd"))
    if normalized_lotcd:
        update_dict["lotcd"] = normalized_lotcd
        return None
    invalid_value = update_dict.get("lotcd", "")
    emit_runtime_detail(
        "param.invalid",
        {
            "agent": agent,
            "param": "lotcd",
            "value": invalid_value,
            "reason": "lotcd_must_be_ascii_product_code",
            "action": "interrupt",
        },
        task_id=str(current_task.get("task_id") or ""),
    )
    emit_trace_event(
        "validation_issue",
        source="supervisor",
        severity="error",
        task_id=str(current_task.get("task_id") or ""),
        payload={
            "type": "invalid_param",
            "agent": agent,
            "param": "lotcd",
            "reason": "lotcd_must_be_ascii_product_code",
            "value_preview": preview_text(invalid_value),
        },
    )
    answer = interrupt({
        "type": "missing_param",
        "param": "lotcd",
        "fields": [{
            "slot": "lotcd",
            "label": _lotcd_prompt(agent, invalid_value=invalid_value),
            "type": "lotcd",
        }],
        "message": _lotcd_prompt(agent, invalid_value=invalid_value),
        "route": agent,
    })
    resumed = answer.get("lotcd", "") if isinstance(answer, dict) else answer
    normalized_lotcd = _normalize_product_lotcd(resumed)
    if not normalized_lotcd:
        return _invalid_lotcd_update(
            agent=agent,
            task_id=str(current_task.get("task_id") or ""),
            value=resumed,
            message=_lotcd_prompt(agent, invalid_value=resumed),
            step_count=step_count,
        )
    update_dict["lotcd"] = normalized_lotcd
    return None


def _require_agent_params(
    update_dict: dict, state: dict, current_task: dict, step_count: int
):
    """Validate per-agent required params via the structured HITL contract (축 1).

    Collects ALL missing required slots for the agent and asks them in ONE interrupt
    (fields[]) — two empty slots = one prompt, not two. Resume is a {slot: value} dict.
    Mutates update_dict in place; returns None to continue, or the invalid-lotcd
    early-return value to be returned as-is.

    interrupt() count: with a dict resume every slot fills in the first loop round, so
    there is exactly ONE interrupt() call. (A bare-string fallback fills one slot per
    round, re-asking the rest — never mis-mapping.) lotcd VALUE validation (yield always,
    wads if present) stays OUTSIDE the batch as a separate re-prompt + early-return.
    """
    agent = current_task["agent"]

    # 1. Batch-ask all missing required slots in one interrupt (dict resume). A value
    # that fails its format guard is NOT filled and is re-asked with the reason — so a
    # malformed answer becomes a backstop, never a silent garbage query.
    rejections: dict = {}
    while True:
        fields = _missing_required_fields(agent, update_dict, rejections)
        if not fields:
            break
        answers = _ask_fields(fields, current_task)
        progressed = False
        rejections = {}
        for field in fields:
            value = answers.get(field["slot"], "")
            if not str(value).strip():
                continue  # unanswered (str fallback) — stays missing, re-asked next round
            progressed = True
            reason = _apply_field(field["slot"], value, update_dict)
            if reason:
                rejections[field["slot"]] = reason  # invalid -> re-ask with reason (unfilled)
        if not progressed:
            break  # nothing answered this round — avoid an infinite loop

    # map_oper is always normalized/defaulted (covers the present-but-not-asked case).
    if agent == "map_agent":
        update_dict["map_oper"] = _normalize_map_oper(update_dict.get("map_oper", "")) or "PT1H"

    # 2. lotcd value validation — separate from batch, early-return on persistent invalid.
    if agent == "yield_agent" or (agent == "wads_agent" and update_dict.get("lotcd")):
        early = _validate_lotcd_or_early_return(agent, update_dict, current_task, step_count)
        if early is not None:
            return early

    return None


def supervisor_node(
    state: Dict[str, Any], config: RunnableConfig
) -> Command[
    Literal[
        "yield_agent",
        "wads_agent",
        "map_agent",
        "fail_history_agent",
        "lot_history_agent",
        "ppt_export",
        "relation_tree_agent",
        "__end__",
    ]
]:
    """Supervisor 노드: ReAct 스타일 멀티스텝 루프.

    각 스텝마다 에이전트 결과를 확인하고 다음 행동을 결정합니다.
    Command를 반환하여 state 업데이트 + 라우팅을 하나로 통합합니다.
    """
    step_count = state.get("step_count", 0) + 1

    # 최대 스텝 강제 종료 (무한루프 방지) — recursion_limit=30과 정합 맞춤 (#R1 fix).
    # 사이클당 3노드(supervisor + agent + replanner) + setup 3노드 = 3n+3.
    # recursion_limit 30 안전 margin 유지를 위해 n+3 (cap 8)로 제한.
    # n=5 → max_steps=8 → 8 supervisor 진입 × 3노드 + setup 3 = 27 ≤ 30 ✅
    max_steps = min(len(state.get("task_plan", [])) + 3, 8) or 4
    if step_count > max_steps:
        logger.warning(
            "[Supervisor] 최대 스텝(%d/%d) 초과 → 강제 종료", step_count, max_steps
        )
        emit_trace_event(
            "supervisor_dispatch",
            source="supervisor",
            severity="warning",
            payload={
                "target": "__end__",
                "reason": "max_steps_exceeded",
                "step_count": step_count,
                "max_steps": max_steps,
            },
        )
        return Command(
            update={
                "step_count": step_count,
                "messages": [
                    AIMessage(content="분석을 완료했습니다.", name="supervisor")
                ],
            },
            goto=END,
        )

    # HITL Gate나 plan_review가 실행 보류/취소를 결정한 경우 dispatch 전에 종료.
    if state.get("response"):
        emit_trace_event(
            "supervisor_dispatch",
            source="supervisor",
            payload={
                "target": "__end__",
                "reason": "response_set",
                "step_count": step_count,
            },
        )
        return Command(update={"step_count": step_count}, goto=END)

    # ── pending_tasks 큐 기반 dispatch (task_builder가 생성한 작업 큐) ──
    pending = state.get("pending_tasks", [])
    if pending:
        current_task = pending[0]
        remaining = pending[1:]
        # C1: 코드 기반 chained input 해소 — wads_sql_result 메시지에서 lot_ids 자동 주입 (LLM 불필요)
        task_params = _resolve_chained_params(current_task, state)

        task_message = AIMessage(
            content=f"[Task {current_task.get('task_id', '?')}] {current_task.get('goal', '')}",
            name="supervisor",
        )
        logger.info(
            "[Supervisor] queued task dispatch: %s → %s params=%s goal=%r (remaining=%d)",
            current_task.get("task_id"),
            current_task.get("agent"),
            summarize_params(task_params),
            current_task.get("goal", ""),
            len(remaining),
        )
        emit_runtime_detail(
            "supervisor.current_task",
            {
                "current_task": current_task,
                "remaining_tasks": remaining,
                "resolved_task_params": task_params,
            },
            task_id=str(current_task.get("task_id") or ""),
        )
        # task params를 state 필드로 projection — agent별 조건부로 관련 파라미터만 기록.
        # 전 agent 파라미터를 항상 덮으면 checkpoint의 다른 agent 값이 소실됨.
        agent = current_task["agent"]
        update_dict: dict = {
            "step_count": step_count,
            "current_task": {
                **current_task,
                "params": task_params,
            },
            "current_task_id": current_task.get("task_id", ""),
            "current_task_goal": current_task.get("goal", ""),
            "pending_tasks": remaining,
            "messages": [task_message],
            "agent_suggestion": "",
        }

        # Project resolved params into per-agent state fields (6b: extracted to a
        # helper; order/inheritance preserved — see _project_task_params).
        update_dict.update(_project_task_params(agent, task_params, state))

        # Required-param validation + HITL interrupts (6c: extracted to a helper).
        # The helper mutates update_dict in place and either returns None (continue)
        # or an early-return value (invalid-lotcd dead end). interrupt() call order is
        # preserved exactly — see _require_agent_params.
        early_return = _require_agent_params(
            update_dict, state, current_task, step_count
        )
        if early_return is not None:
            return early_return

        final_task_params = dict(task_params)
        for slot in AGENT_SLOT_SCHEMAS.get(agent, set()):
            if slot in update_dict:
                final_task_params[slot] = update_dict[slot]
        update_dict["current_task"] = {
            **current_task,
            "params": {
                key: value
                for key, value in final_task_params.items()
                if value not in (None, "", [], {})
            },
        }

        stream_event(
            "status",
            StatusEvent(
                message=f"▶ [{current_task['task_id']}] {current_task.get('goal', '')}",
                node="supervisor",
            ),
        )
        emit_runtime_detail(
            "supervisor.dispatch_state",
            {
                "goto": current_task["agent"],
                "update": update_dict,
            },
            task_id=str(current_task.get("task_id") or ""),
        )
        # supervisor_dispatch and agent_started report the same dispatched params —
        # build the summary once (6a: was duplicated across both emits).
        dispatched_params = summarize_params(
            {
                key: update_dict.get(key)
                for key in (
                    "lotcd",
                    "lot_ids",
                    "wf_ids",
                    "groupkey",
                    "fail_type",
                    "cause_oper",
                    "map_type",
                    "map_oper",
                    "dh_query",
                    "ref_date",
                    "unit",
                    "periods",
                    "wads_start_tm",
                    "wads_end_tm",
                )
                if key in update_dict
            }
        )
        emit_trace_event(
            "supervisor_dispatch",
            source="supervisor",
            task_id=str(current_task.get("task_id") or ""),
            payload={
                "target": current_task.get("agent", ""),
                "task_goal_preview": preview_text(current_task.get("goal", "")),
                "remaining_tasks": len(remaining),
                "step_count": step_count,
                "params": dispatched_params,
            },
        )
        emit_trace_event(
            "agent_started",
            source="supervisor",
            task_id=str(current_task.get("task_id") or ""),
            payload={
                "agent": current_task.get("agent", ""),
                "task_goal_preview": preview_text(current_task.get("goal", "")),
                "step_count": step_count,
                "params": dispatched_params,
            },
        )
        return Command(update=update_dict, goto=current_task["agent"])

    # planner가 빈 계획 반환 (JSON 파싱 실패 fallback)
    logger.warning(
        "[Supervisor] pending_tasks 없음 — canonical/task_builder 실패 fallback"
    )
    emit_trace_event(
        "supervisor_dispatch",
        source="supervisor",
        payload={
            "target": "__end__",
            "reason": "pending_tasks_empty",
            "step_count": step_count,
        },
    )
    messages = state.get("messages", [])
    planner_refusal = next(
        (
            m.content
            for m in reversed(messages)
            if isinstance(m, AIMessage)
            and getattr(m, "name", "") == "planner"
            and isinstance(m.content, str)
            and m.content.strip()
        ),
        None,
    )
    fallback_content = (
        planner_refusal or "요청을 이해하지 못했습니다. 다시 시도해 주세요."
    )
    return Command(
        update={
            "step_count": step_count,
            "messages": [AIMessage(content=fallback_content, name="supervisor")],
            "response": fallback_content,
        },
        goto=END,
    )


# ── 공유 State 정의 ──────────────────────────────────────
class YieldQueryState(TypedDict):
    """Yield Query Supervisor의 공유 State

    모든 agent들이 이 State를 통해 구조화된 데이터를 공유합니다.
    멀티스텝 루프에서 artifacts는 operator.add reducer로 누적됩니다.

    State ownership:
    - Source of truth: messages, especially AIMessage.additional_kwargs["result"]
      when agents attach a full ResultEnvelope.
    - UI delivery: *_artifacts fields may contain artifact payloads for the
      current turn and are not resolver memory.
    - Scratchpad/index: recent_results is a bounded, payload-free projection
      rebuilt from message ResultEnvelopes. It is never canonical storage.
    """

    messages: Annotated[list, add_messages]
    step_count: int  # supervisor 루프 카운터
    trace_id: str  # local observability trace id (overwrite)
    turn_id: str  # local observability turn id (overwrite)

    # 조회 파라미터
    lotcd: str
    ref_date: str
    unit: str  # "weekly" | "monthly" | "daily"
    periods: int  # 조회 기간 수 (0 = 기본값)

    # 결과 데이터
    weeks_data: list
    table_result: str
    analysis_result: str

    # Yield 관련 — reducer로 누적 (멀티스텝에서 여러 에이전트 결과 보존)
    yield_artifacts: Annotated[list, operator.add]

    # WADS 관련
    wads_start_tm: str
    wads_end_tm: str
    wads_artifacts: Annotated[list, operator.add]

    # 이상감지
    anomaly_params: list

    # 파라미터 필터
    filter_params: list  # deprecated: yield_agent always returns the full artifact

    # 통합 파라미터 (agent별 분산 → 공통)
    lot_ids: list[str]  # 7자 lot 번호 목록
    wf_ids: list[str]  # wafer ID 목록
    groupkey: str  # 그룹 집계 키
    fail_type: str  # 파라미터/불량유형 코드
    cause_oper: str  # 원인 공정/step명

    # Map Agent 파라미터 (map-specific)
    map_type: str
    map_oper: str

    # Fail History 파라미터
    dh_query: str

    # Fail History 결과
    fail_history_artifacts: Annotated[list, operator.add]
    fail_history_results: list[
        dict
    ]  # 다음-턴 번호 선택 라우팅용 raw results (overwrite, per-turn reset in agent_server)

    # Day 4: wiki memory 메타 (둘 다 turn별 overwrite, reducer 없음 — plan v3 §State/Checkpoint 가드)
    wiki_hit_ids: list[str]  # 이번 turn에 wiki_memory가 참조한 노드 id (디버그용)
    wiki_update_status: (
        str  # "queued" | "summarized" | "persisted" | "dropped" | "skipped"
    )

    # Lot History 결과
    lot_history_artifacts: Annotated[list, operator.add]

    # Relation Tree (Inline-WT 연관 분석) 결과
    relation_tree_artifacts: Annotated[list, operator.add]

    # Map 결과
    map_result: str
    map_artifacts: Annotated[list, operator.add]

    # PPT Export 결과
    ppt_artifacts: Annotated[list, operator.add]

    # 에이전트 제안 (UI 렌더링용)
    agent_suggestion: str

    # Resolver scratchpad index (overwrite)
    # Full ResultEnvelope source remains in message.additional_kwargs["result"].
    # recent_results stores at most 3 pruned entries with at most 50 rows each.
    # Consumers must use result_id to retrieve the canonical message payload.
    recent_results: list[dict]

    # ReferenceResolver v1 scratchpad (overwrite, deterministic only)
    resolved_refs: dict
    reference_issues: list[dict]

    # Canonical request scratchpad (overwrite). Planner/replanner produce this
    # contract; task_builder converts it into executable tasks.
    canonical_request: dict
    canonical_requests: list[dict]
    canonical_trace: list[dict]

    # Task normalizer/validator scratchpad (overwrite, Phase 5)
    task_normalization_trace: list[dict]
    task_validation_issues: list[dict]

    # HITL Gate scratchpad (overwrite, Phase 6)
    hitl_issues: list[dict]
    hitl_responses: list[dict]

    # Planner 관련
    task_plan: list[dict]  # task_builder가 생성한 전체 계획 (overwrite)
    pending_tasks: list[dict]  # 아직 실행 안 된 TaskItem들 (overwrite)
    current_task: (
        dict  # 현재 executor가 받는 공통 task contract (task_id, agent, params, goal)
    )
    current_task_id: str  # 현재 실행 중인 task의 ID
    current_task_goal: str  # 현재 실행 중인 task의 한국어 goal — worker가 query 우선순위로 사용 (#12 fix)

    # 워커 task별 결과 누적 (#8 phase 1, replanner 사전작업)
    # 각 worker가 정상/에러 종료 시 [(task_id, summary)]를 append.
    # 향후 replanner_node가 plan 갱신·chained input 해소에 사용.
    past_steps: Annotated[list, operator.add]

    # canonical plan-and-execute 종료 신호 (LangChain OpenTutorial Act = Union[Response, Plan] 대응).
    # replanner_node가 plan 완료 감지 시 최종 응답 문자열을 set → should_end conditional edge가 END 분기.
    response: str


# ── 그래프 조립 (순환 import 방지: yield_query_agent/wads_agent/map_agent는 supervisor를 import하지 않음)
from yield_query_agent import yield_agent_node  # noqa: E402
from wads_agent import wads_agent_node  # noqa: E402
from map_agent import map_agent_node  # noqa: E402
from fail_history_agent import fail_history_agent_node  # noqa: E402
from ppt_export_agent import ppt_export_node  # noqa: E402
from lot_history_agent import lot_history_agent_node  # noqa: E402
from relation_tree_agent import relation_tree_agent_node  # noqa: E402

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

workflow.add_edge(START, "planner")
workflow.add_edge("planner", "task_normalizer_validator")
workflow.add_edge("task_normalizer_validator", "plan_review")
workflow.add_edge("plan_review", "supervisor")
# supervisor → agent: Command(goto=...)가 처리 (conditional_edges 불필요)
# agent → replanner → supervisor: 공식 plan-and-execute 패턴 (#8 phase 2)
workflow.add_edge("yield_agent", "replanner")
workflow.add_edge("wads_agent", "replanner")
workflow.add_edge("map_agent", "replanner")
workflow.add_edge("fail_history_agent", "replanner")
workflow.add_edge("ppt_export", "replanner")
workflow.add_edge("lot_history_agent", "replanner")
workflow.add_edge("relation_tree_agent", "replanner")


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

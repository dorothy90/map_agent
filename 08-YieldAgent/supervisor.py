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
from datetime import date
from typing import Annotated, Any, Dict, Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables import RunnableConfig
from langfuse import get_client, observe
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.types import Command, RetryPolicy, interrupt
from pydantic import BaseModel, Field

from langchain_core.messages import ToolMessage

from common import stream_event, get_llm, extract_json_from_llm, is_transient_error
from lf_utils import lf_callbacks as _lf_callbacks  # noqa: E402
from models import StatusEvent
from prompts import PLANNER_SYSTEM_PROMPT, REPLANNER_SYSTEM_PROMPT, REWRITE_SYSTEM_PROMPT_TEMPLATE
from rewrite_tools import REWRITE_TOOLS

load_dotenv(override=True)

logger = logging.getLogger("yield_agent.supervisor")

# ── LLM 모델 ────────────────────────────────────────────────
_model = get_llm()
_rewrite_model = _model.bind_tools(REWRITE_TOOLS)

# 도구 이름 → 함수 매핑 (rewrite tool-calling에서 사용)
_rewrite_tool_map = {t.name: t for t in REWRITE_TOOLS}



# ── Pydantic 라우팅 결정 모델 ────────────────────────────────
class TaskItem(BaseModel):
    """planner가 생성하는 개별 작업 단위"""
    task_id: str = Field(description="고유 ID (예: 'task_1')")
    agent: Literal["yield_agent", "wads_agent", "map_agent", "fail_history_agent", "ppt_export", "lot_history_agent", "relation_tree_agent"] = Field(
        description="실행할 에이전트"
    )
    params: dict = Field(default={}, description="에이전트별 파라미터 (map_lot_ids, map_type 등)")
    goal: str = Field(description="이 작업의 목표 (한국어, 예: 'lot 3,4번 cummap 생성')")


class PlanResponse(BaseModel):
    """planner LLM의 출력 스키마"""
    tasks: list[TaskItem] = Field(description="실행할 작업 목록")


class PlanReviewResult(BaseModel):
    """plan_review LLM의 출력 스키마"""
    action: Literal["approve", "cancel", "modify"]
    tasks: list[TaskItem] = Field(default=[], description="최종 task 목록 (approve 시 현재 계획 그대로, modify 시 수정된 전체 목록)")



_MAX_CHECKPOINT_MESSAGES = 30

# worker AIMessage 판별용 name 집합 — supervisor_node/replanner_node 양쪽에서 사용.
_AGENT_NAMES = {"yield_agent", "wads_agent", "map_agent", "fail_history_agent", "ppt_export", "lot_history_agent", "relation_tree_agent"}



_MAX_CONTEXT_TOKENS = 30_000


def _get_recent_turns(messages: list, max_turns: int = 5, exclude_last: HumanMessage | None = None) -> list[dict]:
    """최근 N턴의 Human/AI 메시지를 chat format으로 변환.

    ToolMessage, SystemMessage 등은 스킵.
    exclude_last로 지정된 메시지는 제외 (rewrite 대상이므로 별도 전달).
    turn 수 제한 후 토큰 예산 초과 시 오래된 턴부터 추가 제거.
    """
    eligible = [
        m for m in messages
        if (exclude_last is None or m is not exclude_last)
        and isinstance(m, (HumanMessage, AIMessage))
        and (isinstance(m, HumanMessage) or (isinstance(m.content, str) and m.content.strip()))
    ]
    # 1차: 턴 수 제한
    turn_limited = eligible[-(max_turns * 2):]
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
            result.append({"role": "user", "content": m.content if isinstance(m.content, str) else str(m.content)})
        elif isinstance(m, AIMessage):
            content = m.content if isinstance(m.content, str) else str(m.content)
            if content.strip():
                result.append({"role": "assistant", "content": content})
    return result


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
    if len(messages) > _MAX_CHECKPOINT_MESSAGES:
        excess = messages[: len(messages) - _MAX_CHECKPOINT_MESSAGES]
        prune_ops = [RemoveMessage(id=m.id) for m in excess if getattr(m, "id", None)]

    # 마지막 HumanMessage 추출 (MongoDBSaver 역직렬화 후 isinstance 실패 방어)
    last_human = next(
        (m for m in reversed(messages)
         if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human"),
        None,
    )
    if not last_human:
        logger.warning(
            "[Rewrite] early return: no HumanMessage found (last 5 types: %s)",
            [type(m).__name__ for m in messages[-5:]],
        )
        return {}

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
        invoke_messages.append({"role": "system", "content": f"State metadata:\n{meta}"})
    invoke_messages.extend(recent)
    invoke_messages.append({"role": "user", "content": f"Rewrite this message: {last_human.content}"})

    # 디버깅: rewrite LLM에 전달되는 전체 메시지 로깅
    logger.info("[Rewrite DEBUG] meta: %s", meta)
    logger.info("[Rewrite DEBUG] recent turns (%d): %s", len(recent), recent)
    logger.info("[Rewrite DEBUG] user input: '%s'", last_human.content)
    logger.info("[Rewrite DEBUG] agent_suggestion state: '%s'", state.get("agent_suggestion", ""))

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
                    logger.info("[Rewrite] tool '%s'(%s) → %s", tc["name"], tc["args"], result)
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
        return {"messages": prune_ops}

    logger.info("[Rewrite] '%s' → '%s'", last_human.content, rewritten)

    # 동일 ID로 교체 → add_messages가 제자리 업데이트, 풀 리스트 반환 불필요
    return {"messages": prune_ops + [HumanMessage(content=rewritten, id=last_human.id)]}


# ── Planner 노드 ────────────────────────────────────────────
_MAX_TASKS = 5


@observe(name="planner_node")
def planner_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    """사용자 질문을 TaskItem 리스트로 분해.

    단순 질문이면 task 1개, 복합이면 N개 생성.
    task_plan은 디버깅용, pending_tasks는 실행 큐.
    """
    messages = state.get("messages", [])
    if not messages:
        return {}

    last_human = next(
        (m for m in reversed(messages)
         if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human"),
        None,
    )
    if not last_human:
        return {}

    today = date.today()
    prompt = PLANNER_SYSTEM_PROMPT.format(
        today=today.strftime("%Y년 %m월 %d일 (%A)"),
        today_yyyymmdd=today.strftime("%Y%m%d"),
        today_yyyy_mm_dd=today.strftime("%Y-%m-%d"),
        year=today.year,
    )

    # #14 fix — planner context-blind 해소: 최근 3턴 + state metadata 주입
    # rewrite_node가 follow-up 컨텍스트를 놓쳤을 때 planner가 직접 복구할 수 있도록.
    recent = _get_recent_turns(messages, max_turns=3, exclude_last=last_human)
    meta_parts: list[str] = []
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
        invoke_messages.append({"role": "system", "content": f"State context:\n{meta}"})
    invoke_messages.extend(recent)
    invoke_messages.append({"role": "user", "content": last_human.content})

    # 수동 JSON 파싱 (with_structured_output은 OpenRouter 호환성 문제)
    raw_text = ""
    try:
        response = _model.invoke(
            invoke_messages,
            config={"callbacks": _lf_callbacks()},
        )
        raw_text = response.content.strip()
        plan = extract_json_from_llm(raw_text, PlanResponse)
    except Exception as e:
        logger.error("[Planner] 파싱 실패: %s — 단일 task fallback", e)
        # plain text 거절 메시지이면 state에 보존 (supervisor fallback에서 사용)
        refusal = raw_text if (raw_text and "{" not in raw_text and len(raw_text) < 400) else None
        result: dict = {"task_plan": [], "pending_tasks": []}
        if refusal:
            result["messages"] = [AIMessage(content=refusal, name="planner")]
        return result

    # task 수 상한 제한 — 초과 시 사용자에게 명시적으로 알림 (#22 fix)
    if len(plan.tasks) > _MAX_TASKS:
        dropped = len(plan.tasks) - _MAX_TASKS
        logger.warning("[Planner] task 수 %d → %d로 제한 (%d개 dropped)", len(plan.tasks), _MAX_TASKS, dropped)
        stream_event("status", StatusEvent(
            message=f"⚠ 요청하신 작업이 너무 많아 처음 {_MAX_TASKS}개만 처리합니다 ({dropped}개 생략).",
            node="planner",
        ))
        plan.tasks = plan.tasks[:_MAX_TASKS]

    tasks_dicts = [t.model_dump() for t in plan.tasks]
    logger.info("[Planner] %d task(s) 생성: %s", len(tasks_dicts),
                [(t["task_id"], t["agent"]) for t in tasks_dicts])

    if not tasks_dicts:
        # LLM이 지원 범위 외로 판단 — planner message를 state에 남겨 supervisor가 relay하도록 함
        return {
            "task_plan": [],
            "pending_tasks": [],
            "messages": [AIMessage(
                content="죄송합니다. 수율 분석, WADS 열화 리포트, 웨이퍼 맵, LOT 이력 등의 쿼리만 지원합니다.",
                name="planner",
            )],
        }

    task_summary = " → ".join(
        f"[{t['task_id']}]{t['agent']}" for t in tasks_dicts
    )
    stream_event("status", StatusEvent(
        message=f"📋 계획 ({len(tasks_dicts)}개): {task_summary}",
        node="planner",
    ))

    return {
        "task_plan": tasks_dicts,
        "pending_tasks": tasks_dicts,
    }


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


def plan_review_node(state: Dict[str, Any], config: RunnableConfig) -> dict:
    """2개 이상 task일 때만 사용자 승인을 기다린다. 단일 task는 즉시 통과.

    missing_params → interrupt 수집 → plan_review interrupt → LLM이 approve/cancel/modify 판단.
    """
    task_plan = state.get("task_plan", [])
    if len(task_plan) < 2:
        return {}

    missing_params = _detect_missing_global_params(task_plan, state)

    # Step 1: missing param이 있으면 plan_review 전에 먼저 수집
    _MISSING_PARAM_MESSAGES = {
        "lotcd":   "계획 검토 전에 제품코드를 입력해주세요. (예: 4SS, 5NA, 6E2)",
        "map_oper": "계획 검토 전에 공정을 선택해주세요. PT1H / PT1C",
    }
    _MISSING_PARAM_AGENTS = {
        "lotcd":   ("yield_agent", "wads_agent"),
        "map_oper": ("map_agent",),
    }
    collected: dict[str, str] = {}
    if missing_params:
        for mp in missing_params:
            param = mp["param"]
            val = interrupt({
                "type": "missing_param",
                "param": param,
                "message": _MISSING_PARAM_MESSAGES.get(param, f"{param}을 입력해주세요."),
                "route": "plan_review",
            })
            collected[param] = str(val).strip()
        updated = []
        for task in task_plan:
            t = dict(task)
            agent = t.get("agent", "")
            t_params = dict(t.get("params") or {})
            for param, val in collected.items():
                if agent in _MISSING_PARAM_AGENTS.get(param, ()):
                    if param == "map_oper":
                        normalized = _normalize_map_oper(val)
                        t_params["map_oper"] = normalized or val
                    else:
                        t_params[param] = val
            t["params"] = t_params
            updated.append(t)
        task_plan = updated

    # Step 2: plan_review 루프 — approve/cancel/modify 반복 가능
    # sequential interrupt 패턴: 루프 각 반복마다 새 interrupt() 생성 → resume 시 순서대로 재생
    while True:
        user_response = interrupt({"type": "plan_review", "tasks": task_plan, "missing_params": []})
        resp = (user_response or "").strip()

        if not resp:
            break  # 빈 응답 → approve, LLM 호출 없이 즉시 통과

        try:
            raw = _model.invoke([
                {"role": "system", "content": _PLAN_REVIEW_SYSTEM},
                {"role": "user", "content": f"현재 계획:\n{json.dumps(task_plan, ensure_ascii=False)}\n\n사용자 응답: \"{resp}\""},
            ]).content.strip()
            result = extract_json_from_llm(raw, PlanReviewResult)
        except Exception as e:
            logger.warning("[PlanReview] LLM 판단 실패 (%s) — 계획 재표시", e)
            continue  # interrupt 재호출 → 사용자에게 계획 다시 표시

        logger.info("[PlanReview] action=%s tasks=%s", result.action,
                    [(t.task_id, t.agent) for t in result.tasks])

        if result.action == "cancel":
            return {"response": "사용자가 분석 계획을 취소했습니다.", "task_plan": [], "pending_tasks": []}
        if result.action == "approve":
            break
        # modify → task_plan 갱신 후 루프 재시작 (새 interrupt로 수정된 플랜 재표시)
        task_plan = [t.model_dump() for t in result.tasks]

    return {"task_plan": task_plan, "pending_tasks": task_plan}


# ── Replanner 노드 (#8 phase 3a) ──────────────────────────────
# 공식 LangGraph plan-and-execute 패턴의 replan 단계.
# Phase 3a: past_steps 결과를 LLM에게 보여주고 남은 pending_tasks의 빈 chained-input을 채운다.
# (예: task_1 wads 결과의 lot ID들을 task_2 map_agent의 map_lot_ids에 채움)
# DO NOT 추가/삭제/순서변경 — 단순 input 채우기. Phase 3b에서 plan 전체 재구성 추가 예정.
# 그래프 wiring: agent → replanner → supervisor (#8 phase 2에서 이미 wiring 완료).

def _parse_lot_ids(params: dict) -> list[str]:
    val = (params.get("lot_ids") or params.get("map_lot_ids") or params.get("lh_lot_ids")
           or params.get("yield_lot_ids") or params.get("map_lot_id") or "")
    if isinstance(val, list):
        return [v.strip() for v in val if v.strip()]
    return [v.strip() for v in str(val).split(",") if v.strip()]

def _parse_wf_ids(params: dict) -> list[str]:
    val = params.get("wf_ids") or params.get("map_wf_ids") or ""
    if isinstance(val, list):
        return [v.strip() for v in val if v.strip()]
    return [v.strip() for v in str(val).split(",") if v.strip()]

def _parse_fail_type(params: dict) -> str:
    return (params.get("fail_type") or params.get("wads_parameter")
            or params.get("dh_fail_type") or "")

def _parse_cause_oper(params: dict) -> str:
    return (params.get("cause_oper") or params.get("dh_cause_oper")
            or params.get("rt_main_oper_det_desc") or "")


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
    if (v.startswith("<") and v.endswith(">")) or \
       (v.startswith("{{") and v.endswith("}}")) or \
       (v.startswith("__") and v.endswith("__")):
        return True
    lower = v.lower()
    if any(k in lower for k in ("task_1", "task_2", "task_3", "결과", "from_task", "result of", "from task")):
        return True
    return False


def _detect_missing_global_params(task_plan: list[dict], state: dict) -> list[dict]:
    """state에도 없고 앞 task가 제공하지도 않는 진짜 누락 파라미터 목록 반환.

    '↳ 0단계 자동 연결'처럼 보이지만 실제로는 입력이 필요한 경우를 구분한다.
    returns: [{"task_id": ..., "agent": ..., "param": "lotcd"}, ...]
    """
    missing = []
    state_lotcd = state.get("lotcd", "")
    state_map_oper = state.get("map_oper", "")
    for i, task in enumerate(task_plan):
        agent = task.get("agent", "")
        params = task.get("params") or {}

        if agent in ("yield_agent", "wads_agent"):
            task_lotcd = params.get("lotcd", "")
            if not _is_placeholder_or_empty(task_lotcd):
                continue
            if state_lotcd:
                continue
            earlier_providers = [
                t for t in task_plan[:i]
                if t.get("agent") in ("yield_agent", "wads_agent")
            ]
            if not earlier_providers:
                missing.append({"task_id": task["task_id"], "agent": agent, "param": "lotcd"})

        elif agent == "map_agent":
            # map_oper(PT1H/PT1C)가 없고 앞 task에서 공정 정보가 올 수 없는 경우 수집
            task_oper = params.get("map_oper", "")
            if _is_placeholder_or_empty(task_oper) and not _normalize_map_oper(state_map_oper):
                missing.append({"task_id": task["task_id"], "agent": agent, "param": "map_oper"})

    return missing


def _resolve_chained_params(task: dict, state: dict) -> dict:
    """task.params의 빈 chained 필드를 state.messages의 structured tool result에서 코드로 자동 채움."""
    params = dict(task.get("params") or {})
    messages = state.get("messages", [])

    wads_data: dict = {}
    for m in reversed(messages):
        if isinstance(m, AIMessage) and getattr(m, "name", "") == "wads_sql_result":
            wads_data = (getattr(m, "additional_kwargs", None) or {}).get("wads_result") or {}
            if wads_data:
                break

    lot_ids = wads_data.get("lot_ids") or []
    wf_ids = wads_data.get("wf_ids") or []
    groupkey = wads_data.get("groupkey") or ""

    if _is_placeholder_or_empty(params.get("lot_ids")) and lot_ids:
        params["lot_ids"] = lot_ids
        logger.info("[ResolveChained] lot_ids ← wads_sql_result (%d lots)", len(lot_ids))

    if _is_placeholder_or_empty(params.get("wf_ids")) and wf_ids:
        params["wf_ids"] = wf_ids
        logger.info("[ResolveChained] wf_ids ← wads_sql_result (%d wafers)", len(wf_ids))

    # PR0/PR2 schema: WADS 의 진짜 wafer 식별자는 DF_WADS_WF_LIST.GROUPKEY ("lot.wf" 형식).
    # map_agent 는 lot.wf 통합 식별자를 groupkey 채널로 받으므로 그 자리에 자동 주입.
    if _is_placeholder_or_empty(params.get("groupkey")) and groupkey:
        params["groupkey"] = groupkey
        n = len([s for s in groupkey.split(",") if s.strip()])
        logger.info("[ResolveChained] groupkey ← wads_sql_result (%d wafers, lot.wf)", n)

    if _is_placeholder_or_empty(params.get("cause_oper")):
        fallback = state.get("cause_oper")
        if fallback:
            params["cause_oper"] = fallback
            logger.info("[ResolveChained] cause_oper ← %s", fallback)

    return params


def _needs_replan(pending: list[dict]) -> bool:
    """Phase 3a 휴리스틱: pending task에 빈 chained-input이 있으면 LLM 호출 필요."""
    for task in pending:
        agent = task.get("agent", "")
        params = task.get("params", {}) or {}
        if agent in ("map_agent", "lot_history_agent"):
            if _is_placeholder_or_empty(params.get("lot_ids")) and all(
                _is_placeholder_or_empty(params.get(k))
                for k in ("map_lot_id", "map_lot_ids", "lh_lot_ids")
            ):
                return True
        elif agent == "relation_tree_agent":
            # relation_tree_agent는 lot_ids 아닌 lotcd+cause_oper 사용
            if _is_placeholder_or_empty(params.get("cause_oper")) and _is_placeholder_or_empty(params.get("rt_main_oper_det_desc")):
                return True
        elif agent == "fail_history_agent":
            if all(_is_placeholder_or_empty(params.get(k)) for k in ("dh_query", "fail_type", "dh_fail_type", "cause_oper")):
                return True
    return False


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

    # 항상 마지막 task 결과 로깅 (관측성)
    if past:
        last_task_id, last_summary = past[-1]
        logger.info(
            "[Replanner] last_task=%s pending=%d summary=%s",
            last_task_id, len(pending), str(last_summary)[:120],
        )
        stream_event("status", StatusEvent(
            message=f"✅ [{last_task_id}] 완료",
            node="replanner",
        ))

    # ── canonical plan-and-execute 종료 판정 ──
    # 모든 planned task가 실행되었고 남은 pending이 없으면 plan 완료.
    # past_steps는 per-turn reset(`agent_server.py`)이라 턴 내 실행 기록만 반영.
    # should_end conditional edge가 `state["response"]`를 보고 END 분기.
    # TODO(phase 3b): replanner가 pending_tasks를 확장하는 단계 도입 시 이 조건을 ID 기반으로 교체.
    #   현재는 plan 크기 고정 가정. ID 기반 예: {t["task_id"] for t in task_plan} <= {tid for tid, _ in past}
    if not pending and task_plan:
        messages = state.get("messages", [])
        last_agent_msg = next(
            (m.content for m in reversed(messages)
             if isinstance(m, AIMessage)
             and getattr(m, "name", "") in _AGENT_NAMES
             and isinstance(m.content, str)
             and m.content.strip()),
            "분석을 완료했습니다.",
        )
        logger.info(
            "[Replanner] plan 완료 감지 (tasks=%d) → response set, should_end → END",
            len(task_plan),
        )
        return {
            "response": last_agent_msg,
        }

    # Fast path: 큐 비었거나 past 비었거나 chained-input 의존이 없으면 LLM 호출 생략
    if not pending or not past:
        return {}
    # C1: 코드 해소 시뮬레이션 — _resolve_chained_params가 모든 pending을 해소할 수 있으면 LLM 호출 생략
    simulated_pending = [
        {**t, "params": _resolve_chained_params(t, state)} for t in pending
    ]
    if not _needs_replan(simulated_pending):
        logger.info("[Replanner] 코드 해소로 chained input 충족 → LLM 호출 생략 (pass-through)")
        return {}

    # 사용자 원본 query (rewrite 결과)
    messages = state.get("messages", [])
    last_human = next(
        (m for m in reversed(messages)
         if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human"),
        None,
    )
    if not last_human:
        return {}

    # past + pending을 LLM 입력으로 직렬화
    past_str = "\n".join(
        f"- {tid}: {str(summary)[:400]}" for tid, summary in past
    )
    pending_str = "\n".join(
        f"- {t.get('task_id','?')}({t.get('agent','?')}): goal={t.get('goal','')!r} params={t.get('params',{})}"
        for t in pending
    )

    today = date.today()
    prompt = REPLANNER_SYSTEM_PROMPT.format(
        today=today.strftime("%Y년 %m월 %d일 (%A)"),
    )
    user_msg = (
        f"원본 사용자 요청: {last_human.content}\n\n"
        f"이미 실행된 task와 결과:\n{past_str}\n\n"
        f"남은 task (params에 빈 값이 있으면 위 결과에서 추출하여 채워라):\n{pending_str}\n\n"
        f"업데이트된 남은 task 목록을 PlanResponse JSON 형식으로 반환:"
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
        plan = extract_json_from_llm(raw, PlanResponse)
    except Exception as e:
        logger.warning("[Replanner] LLM 호출 실패 — pass-through: %s", e)
        return {}

    new_tasks = [t.model_dump() for t in plan.tasks]
    if not new_tasks:
        logger.warning("[Replanner] LLM이 빈 tasks 반환 — pass-through")
        return {}
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
                len(pending), len(new_tasks),
            )
            return {}
        logger.info("[Replanner] fan-out 허용: %d → %d tasks", len(pending), len(new_tasks))
    # base task_id 기반 subset 검증 (_p{n} suffix 허용)
    _base_ids = {_re.sub(r"_p\d+$", "", tid) for tid in new_ids}
    if not _base_ids.issubset(orig_ids):
        logger.warning(
            "[Replanner] 새 base task_id 추가 %s — 거부",
            sorted(_base_ids - orig_ids),
        )
        return {}
    if new_tasks == pending:
        logger.info("[Replanner] LLM 결과 변경 없음 (pass-through)")
        return {}

    # R3 fix: LLM이 채운 params에 여전히 placeholder가 있으면 거부
    # (예: "task_1의 결과를 사용하세요" 같은 narrative placeholder)
    if _needs_replan(new_tasks):
        logger.warning("[Replanner] LLM 결과에 여전히 빈 chained params 존재 — 거부, supervisor interrupt로 위임")
        return {}

    logger.info(
        "[Replanner] plan 갱신: %d → %d tasks (chained input filled)",
        len(pending), len(new_tasks),
    )
    return {"pending_tasks": new_tasks}


# ── Supervisor 노드 ──────────────────────────────────────────
@observe(name="supervisor_node")
def supervisor_node(
    state: Dict[str, Any], config: RunnableConfig
) -> Command[Literal["yield_agent", "wads_agent", "map_agent", "fail_history_agent", "lot_history_agent", "ppt_export", "relation_tree_agent", "__end__"]]:
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
        logger.warning("[Supervisor] 최대 스텝(%d/%d) 초과 → 강제 종료", step_count, max_steps)
        return Command(
            update={
                "step_count": step_count,
                "messages": [AIMessage(content="분석을 완료했습니다.", name="supervisor")],
            },
            goto=END,
        )

    # ── pending_tasks 큐 기반 dispatch (planner가 생성한 작업 큐) ──
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
            "[Supervisor] queued task dispatch: %s → %s (remaining=%d)",
            current_task.get("task_id"), current_task.get("agent"), len(remaining),
        )

        # task params를 state 필드로 projection — agent별 조건부로 관련 파라미터만 기록.
        # 전 agent 파라미터를 항상 덮으면 checkpoint의 다른 agent 값이 소실됨.
        agent = current_task["agent"]
        update_dict: dict = {
            "step_count": step_count,
            "current_task_id": current_task.get("task_id", ""),
            "current_task_goal": current_task.get("goal", ""),
            "pending_tasks": remaining,
            "messages": [task_message],
            "agent_suggestion": "",
        }

        # lotcd는 yield/wads/fail_history가 공유 — 해당 agent 실행 시에만 업데이트
        if agent in ("yield_agent", "wads_agent", "fail_history_agent"):
            update_dict["lotcd"] = task_params.get("lotcd") or state.get("lotcd", "")

        # 통합 필드: 모든 agent에 공통 적용
        update_dict["lot_ids"]    = _parse_lot_ids(task_params)
        update_dict["wf_ids"]     = _parse_wf_ids(task_params)
        update_dict["groupkey"]   = task_params.get("groupkey") or task_params.get("map_groupkey") or task_params.get("yield_groupkey") or ""
        update_dict["fail_type"]  = _parse_fail_type(task_params)
        update_dict["cause_oper"] = _parse_cause_oper(task_params)

        if agent == "yield_agent":
            update_dict.update({
                "ref_date":      task_params.get("ref_date", state.get("ref_date", "")),
                "unit":          task_params.get("unit", state.get("unit", "weekly")),
                "periods":       task_params.get("periods", state.get("periods", 0)),
                "filter_params": task_params.get("filter_params", []),
            })

        elif agent == "wads_agent":
            update_dict.update({
                "wads_start_tm": task_params.get("wads_start_tm", ""),
                "wads_end_tm":   task_params.get("wads_end_tm") or date.today().strftime("%Y-%m-%d"),
            })

        elif agent == "map_agent":
            update_dict.update({
                "map_type": task_params.get("map_type", "binmap"),
                "map_oper": task_params.get("map_oper") or state.get("map_oper", ""),
            })

        elif agent == "fail_history_agent":
            update_dict["dh_query"] = task_params.get("dh_query", "")

        elif agent == "relation_tree_agent":
            # lotcd(3자)는 rt_lot_code와 동일 개념 — 구 필드 fallback 포함
            if not update_dict.get("lotcd"):
                update_dict["lotcd"] = (task_params.get("rt_lot_code") or state.get("lotcd", ""))
        # map_agent 필수 파라미터 검증
        if current_task["agent"] == "map_agent":
            if not update_dict.get("lot_ids") and not update_dict.get("groupkey"):
                user_response = interrupt({
                    "type": "missing_param",
                    "param": "lot_ids",
                    "message": "맵을 조회할 Lot ID를 입력해주세요. (예: 4SS2DPD 또는 4SS2DPD,4SSXCEW)",
                    "route": "map_agent",
                })
                update_dict["lot_ids"] = _parse_lot_ids({"lot_ids": str(user_response).strip()})

            normalized = _normalize_map_oper(update_dict.get("map_oper", ""))
            if not normalized:
                user_response = interrupt({
                    "type": "missing_param",
                    "param": "map_oper",
                    "message": "PT1H / PT1C 중 어떤 공정의 맵을 조회할까요?",
                    "route": "map_agent",
                })
                normalized = _normalize_map_oper(str(user_response))
            update_dict["map_oper"] = normalized or "PT1H"

        # relation_tree_agent 필수 파라미터 검증
        if current_task["agent"] == "relation_tree_agent":
            if not update_dict.get("lotcd"):
                user_response = interrupt({
                    "type": "missing_param",
                    "param": "lotcd",
                    "message": "연관 분석할 LOT 코드를 입력해주세요. (예: 4SS2DPD)",
                    "route": "relation_tree_agent",
                })
                update_dict["lotcd"] = str(user_response).strip()
            if not update_dict.get("cause_oper"):
                user_response = interrupt({
                    "type": "missing_param",
                    "param": "cause_oper",
                    "message": "연관 분석할 main 공정명을 입력해주세요. (예: STEP07 또는 STEP07,STEP08)",
                    "route": "relation_tree_agent",
                })
                update_dict["cause_oper"] = str(user_response).strip()

        # yield_agent / wads_agent: lotcd 필수
        if current_task["agent"] in ("yield_agent", "wads_agent"):
            if not update_dict.get("lotcd"):
                user_response = interrupt({
                    "type": "missing_param",
                    "param": "lotcd",
                    "message": "제품코드를 입력해주세요. (예: 4SS, 5NA, 6E2)",
                    "route": current_task["agent"],
                })
                update_dict["lotcd"] = str(user_response).strip()

        # lot_history_agent: lot_ids 필수
        if current_task["agent"] == "lot_history_agent":
            if not update_dict.get("lot_ids"):
                user_response = interrupt({
                    "type": "missing_param",
                    "param": "lot_ids",
                    "message": "이력을 조회할 LOT ID를 입력해주세요. (예: 4SS2DPD 또는 4SS2DPD,4SSXCEW)",
                    "route": "lot_history_agent",
                })
                update_dict["lot_ids"] = _parse_lot_ids({"lot_ids": str(user_response).strip()})

        stream_event("status", StatusEvent(
            message=f"▶ [{current_task['task_id']}] {current_task.get('goal', '')}",
            node="supervisor",
        ))
        return Command(update=update_dict, goto=current_task["agent"])

    # plan_review cancel 등으로 이미 response가 설정된 경우 바로 종료
    if state.get("response"):
        return Command(update={"step_count": step_count}, goto=END)

    # planner가 빈 계획 반환 (JSON 파싱 실패 fallback)
    logger.warning("[Supervisor] pending_tasks 없음 — planner 실패 fallback")
    messages = state.get("messages", [])
    planner_refusal = next(
        (m.content for m in reversed(messages)
         if isinstance(m, AIMessage) and getattr(m, "name", "") == "planner"
         and isinstance(m.content, str) and m.content.strip()),
        None,
    )
    fallback_content = planner_refusal or "요청을 이해하지 못했습니다. 다시 시도해 주세요."
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
    """

    messages: Annotated[list, add_messages]
    step_count: int  # supervisor 루프 카운터

    # 조회 파라미터
    lotcd: str
    ref_date: str
    unit: str      # "weekly" | "monthly" | "daily"
    periods: int   # 조회 기간 수 (0 = 기본값)

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
    filter_params: list  # 표시할 파라미터 필터 (빈 list = 전체)

    # 통합 파라미터 (agent별 분산 → 공통)
    lot_ids:    list[str]   # 7자 lot 번호 목록
    wf_ids:     list[str]   # wafer ID 목록
    groupkey:   str         # 그룹 집계 키
    fail_type:  str         # 파라미터/불량유형 코드
    cause_oper: str         # 원인 공정/step명

    # Map Agent 파라미터 (map-specific)
    map_type: str
    map_oper: str

    # Fail History 파라미터
    dh_query: str

    # Fail History 결과
    fail_history_artifacts: Annotated[list, operator.add]
    fail_history_results: list[dict]     # 다음-턴 번호 선택 라우팅용 raw results (overwrite, per-turn reset in agent_server)

    # Day 4: wiki memory 메타 (둘 다 turn별 overwrite, reducer 없음 — plan v3 §State/Checkpoint 가드)
    wiki_hit_ids: list[str]              # 이번 turn에 wiki_memory가 참조한 노드 id (eval/디버그용)
    wiki_update_status: str              # "queued" | "summarized" | "persisted" | "dropped" | "skipped"

    # Lot History 결과
    lot_history_artifacts: Annotated[list, operator.add]

    # Relation Tree (Inline-WT 연관 분석) 결과
    relation_tree_artifacts: Annotated[list, operator.add]

    # Map 결과
    map_result:    str
    map_artifacts: Annotated[list, operator.add]

    # PPT Export 결과
    ppt_artifacts: Annotated[list, operator.add]

    # 에이전트 제안 (UI 렌더링용)
    agent_suggestion: str

    # Planner 관련
    task_plan: list[dict]           # planner가 생성한 전체 계획 (overwrite)
    pending_tasks: list[dict]       # 아직 실행 안 된 TaskItem들 (overwrite)
    current_task_id: str            # 현재 실행 중인 task의 ID
    current_task_goal: str          # 현재 실행 중인 task의 한국어 goal — worker가 query 우선순위로 사용 (#12 fix)

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
workflow.add_node("rewrite", rewrite_node, retry_policy=_retry)
workflow.add_node("planner", planner_node, retry_policy=_retry)
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

workflow.add_edge(START, "rewrite")
workflow.add_edge("rewrite", "planner")        # rewrite → planner
workflow.add_edge("planner", "plan_review")    # planner → plan_review → supervisor
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

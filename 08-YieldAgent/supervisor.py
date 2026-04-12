"""
Supervisor Node — Yield/WADS/Map 라우팅 담당
=============================================
수동 JSON 파싱 + RouteResponse Pydantic 검증 방식으로 라우팅을 수행합니다.

라우팅 대상:
  yield_agent  → pt1h 수율 조회
  wads_agent   → WADS 열화 검출 리포트 조회
  map_agent    → 웨이퍼 맵 시각화
  FINISH       → 범위 외 요청
"""

from __future__ import annotations

import operator
import logging
from datetime import date
from typing import Annotated, Any, Dict, Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, trim_messages
from langchain_core.runnables import RunnableConfig
from langfuse import get_client, observe
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.types import Command, RetryPolicy, interrupt
from pydantic import BaseModel, Field

from langchain_core.messages import ToolMessage

from common import stream_event, get_llm, extract_json_from_llm, is_transient_error
from lf_utils import lf_callbacks as _lf_callbacks  # noqa: E402
from models import StatusEvent, ThinkingEvent
from prompts import PLANNER_SYSTEM_PROMPT, REPLANNER_SYSTEM_PROMPT, REWRITE_SYSTEM_PROMPT_TEMPLATE, SUPERVISOR_SYSTEM_PROMPT
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
    agent: Literal["yield_agent", "wads_agent", "map_agent", "fail_history_agent", "ppt_export", "lot_history_agent"] = Field(
        description="실행할 에이전트"
    )
    params: dict = Field(default={}, description="에이전트별 파라미터 (map_lot_ids, map_type 등)")
    goal: str = Field(description="이 작업의 목표 (한국어, 예: 'lot 3,4번 cummap 생성')")


class PlanResponse(BaseModel):
    """planner LLM의 출력 스키마"""
    tasks: list[TaskItem] = Field(description="실행할 작업 목록")


class RouteResponse(BaseModel):
    """Supervisor의 라우팅 결정 — with_structured_output으로 타입 보장"""

    next: Literal["yield_agent", "wads_agent", "map_agent", "fail_history_agent", "ppt_export", "lot_history_agent", "FINISH"] = Field(
        description="다음에 실행할 에이전트"
    )
    lotcd: str = Field(default="", description="3~4자리 제품코드만 (예: 4SS, 5NA, 6E2). 전체 lot ID(예: 4SS2DPD)는 절대 입력하지 말 것. 사용자가 미지정 시 빈 문자열")
    ref_date: str = Field(
        default="", description="Yield 기준날짜 YYYYMMDD (yield_agent 전용)"
    )
    wads_start_tm: str = Field(
        default="", description="WADS 조회 시작 날짜 YYYY-MM-DD (범위 조회 시, 비어있으면 wads_end_tm 단일 날짜 조회)"
    )
    wads_end_tm: str = Field(
        default="", description="WADS 조회 끝 날짜 YYYY-MM-DD (wads_agent 전용)"
    )
    wads_parameter: str = Field(
        default="", description='WADS step 코드 필터 (예: "step07"). 사용자가 특정 step을 지정한 경우만'
    )
    filter_params: list[str] = Field(
        default=[],
        description="표시할 파라미터 목록 (비어있으면 전체 표시). 예: ['VTH', 'IDSAT']"
    )
    unit: str = Field(default="weekly", description='"weekly" | "monthly" | "daily"')
    periods: int = Field(default=0, description="조회 기간 수 (0 = 기본값: weekly=4, monthly=3, daily=4)")
    message: str = Field(description="사용자에게 전달할 한국어 메시지")
    # Map 파라미터
    map_lot_id:   str = Field(default="", description="단일 lot ID (예: 'LOTABC123')")
    map_lot_ids:  str = Field(default="", description="복수 lot IDs, 쉼표 구분")
    map_wf_ids:   str = Field(default="", description="wafer IDs, 쉼표 구분")
    map_groupkey: str = Field(default="", description="lot_id.wf_id 형식 (예: 'LOT001.01,LOT001.02')")
    map_type:     str = Field(default="binmap", description="binmap | cummap | all")
    map_oper:     str = Field(default="", description="PT1H | PT1C (필수)")
    # Yield lot 비교 파라미터
    yield_lot_ids:  str = Field(default="", description="수율 조회용 lot ID 목록, 쉼표 구분 (예: '4SS2DPD,4SSXCEW')")
    yield_groupkey: str = Field(default="", description="수율 조회용 lot.wf 형식 (예: '4SS2DPD.01,4SS2DPD.05')")
    # Fail History 파라미터
    dh_query: str = Field(default="", description="불량이력 검색 쿼리 (자유 텍스트)")
    dh_fail_type: str = Field(default="", description="불량 유형 필터 (예: TWT, IOFF)")
    dh_cause_oper: str = Field(default="", description="원인 공정 필터 (예: M0C ETCH)")
    # Lot History 파라미터
    lh_lot_ids: str = Field(default="", description="LOT 이력 조회용 lot ID, 쉼표 구분 (예: '4SS2DPD,4SSXCEW')")




_MAX_CHECKPOINT_MESSAGES = 30


def _params_signature(d: dict) -> dict:
    """dup guard 비교용 파라미터 signature.

    LLM-routed 분기와 큐 dispatch 분기 양쪽에서 동일 헬퍼로 추출하여
    저장(`_last_agent_params`)·비교가 일관되게 작동하도록 한다.
    누락 필드는 RouteResponse 기본값과 동일하게 채운다.
    """
    return {
        "map_lot_id":    d.get("map_lot_id", ""),
        "map_lot_ids":   d.get("map_lot_ids", ""),
        "map_wf_ids":    d.get("map_wf_ids", ""),
        "map_groupkey":  d.get("map_groupkey", ""),
        "map_type":      d.get("map_type", "binmap"),
        "map_oper":      d.get("map_oper", ""),
        "lotcd":         d.get("lotcd", ""),
        "ref_date":      d.get("ref_date", ""),
        "unit":          d.get("unit", "weekly"),
        "periods":       d.get("periods", 0),
        "filter_params": tuple(d.get("filter_params") or ()),
        "yield_lot_ids":  d.get("yield_lot_ids", ""),
        "yield_groupkey": d.get("yield_groupkey", ""),
        "wads_start_tm":  d.get("wads_start_tm", ""),
        "wads_end_tm":    d.get("wads_end_tm", ""),
        "wads_parameter": d.get("wads_parameter", ""),
        "dh_query":       d.get("dh_query", ""),
        "dh_fail_type":  d.get("dh_fail_type", ""),
        "dh_cause_oper": d.get("dh_cause_oper", ""),
        "lh_lot_ids":    d.get("lh_lot_ids", ""),
    }


def _get_recent_turns(messages: list, max_turns: int = 5, exclude_last: HumanMessage | None = None) -> list[dict]:
    """최근 N턴의 Human/AI 메시지를 chat format으로 변환.

    ToolMessage, SystemMessage 등은 스킵.
    exclude_last로 지정된 메시지는 제외 (rewrite 대상이므로 별도 전달).
    """
    filtered = []
    for m in messages:
        if exclude_last and m is exclude_last:
            continue
        if isinstance(m, HumanMessage):
            filtered.append({"role": "user", "content": m.content})
        elif isinstance(m, AIMessage):
            # AIMessage content가 텍스트인 경우만 (artifact는 별도 state 필드)
            content = m.content if isinstance(m.content, str) else str(m.content)
            if content.strip():
                filtered.append({"role": "assistant", "content": content})
    # 최근 max_turns * 2개 (Human+AI 각 1개 = 1턴)
    return filtered[-(max_turns * 2):]


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
    recent = _get_recent_turns(messages, max_turns=30, exclude_last=last_human)

    # state 메타데이터 (lotcd, agent_suggestion 등 — 대화에 없을 수 있는 정보)
    meta_parts = []
    if state.get("lotcd"):
        meta_parts.append(f"현재 제품: {state['lotcd']}")
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
    )

    # #14 fix — planner context-blind 해소: 최근 3턴 + state metadata 주입
    # rewrite_node가 follow-up 컨텍스트를 놓쳤을 때 planner가 직접 복구할 수 있도록.
    recent = _get_recent_turns(messages, max_turns=3, exclude_last=last_human)
    meta_parts: list[str] = []
    if state.get("lotcd"):
        meta_parts.append(f"현재 제품: {state['lotcd']}")
    prev_lot = state.get("map_lot_id") or state.get("map_lot_ids")
    if prev_lot:
        meta_parts.append(f"이전 map lot: {prev_lot}")
    if state.get("yield_lot_ids"):
        meta_parts.append(f"이전 yield lot: {state['yield_lot_ids']}")
    if state.get("agent_suggestion"):
        meta_parts.append(f"이전 에이전트 제안: {state['agent_suggestion']}")
    meta = "\n".join(meta_parts)

    invoke_messages: list[dict] = [{"role": "system", "content": prompt}]
    if meta:
        invoke_messages.append({"role": "system", "content": f"State context:\n{meta}"})
    invoke_messages.extend(recent)
    invoke_messages.append({"role": "user", "content": last_human.content})

    # 수동 JSON 파싱 (with_structured_output은 OpenRouter 호환성 문제)
    try:
        response = _model.invoke(
            invoke_messages,
            config={"callbacks": _lf_callbacks()},
        )
        raw_text = response.content.strip()
        plan = extract_json_from_llm(raw_text, PlanResponse)
    except Exception as e:
        logger.error("[Planner] 파싱 실패: %s — 단일 task fallback", e)
        # fallback: 단일 task 없이 supervisor에게 위임
        return {"task_plan": [], "pending_tasks": []}

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

    return {
        "task_plan": tasks_dicts,
        "pending_tasks": tasks_dicts,
    }


# ── Replanner 노드 (#8 phase 3a) ──────────────────────────────
# 공식 LangGraph plan-and-execute 패턴의 replan 단계.
# Phase 3a: past_steps 결과를 LLM에게 보여주고 남은 pending_tasks의 빈 chained-input을 채운다.
# (예: task_1 wads 결과의 lot ID들을 task_2 map_agent의 map_lot_ids에 채움)
# DO NOT 추가/삭제/순서변경 — 단순 input 채우기. Phase 3b에서 plan 전체 재구성 추가 예정.
# 그래프 wiring: agent → replanner → supervisor (#8 phase 2에서 이미 wiring 완료).

def _needs_replan(pending: list[dict]) -> bool:
    """Phase 3a 휴리스틱: 어떤 pending task가 빈 chained-input을 가지면 LLM 호출 필요.

    독립 task만 남았으면 LLM 호출 생략 — 불필요한 latency·비용 절감.
    """
    for task in pending:
        agent = task.get("agent", "")
        params = task.get("params", {}) or {}
        if agent == "map_agent":
            if not (params.get("map_lot_id") or params.get("map_lot_ids") or params.get("map_groupkey")):
                return True
        elif agent == "lot_history_agent":
            if not params.get("lh_lot_ids"):
                return True
        elif agent == "fail_history_agent":
            if not (params.get("dh_query") or params.get("dh_fail_type") or params.get("dh_cause_oper")):
                return True
        elif agent == "yield_agent":
            # yield는 lotcd만 있어도 동작하므로 chained input 의존도 낮음 — 패스
            pass
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

    # 항상 마지막 task 결과 로깅 (관측성)
    if past:
        last_task_id, last_summary = past[-1]
        logger.info(
            "[Replanner] last_task=%s pending=%d summary=%s",
            last_task_id, len(pending), str(last_summary)[:120],
        )

    # Fast path: 큐 비었거나 past 비었거나 chained-input 의존이 없으면 LLM 호출 생략
    if not pending or not past:
        return {}
    if not _needs_replan(pending):
        logger.info("[Replanner] 빈 chained-input 없음 → LLM 호출 생략 (pass-through)")
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
    if new_tasks == pending:
        logger.info("[Replanner] LLM 결과 변경 없음 (pass-through)")
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
) -> Command[Literal["yield_agent", "wads_agent", "map_agent", "fail_history_agent", "lot_history_agent", "ppt_export", "__end__"]]:
    """Supervisor 노드: ReAct 스타일 멀티스텝 루프.

    각 스텝마다 에이전트 결과를 확인하고 다음 행동을 결정합니다.
    Command를 반환하여 state 업데이트 + 라우팅을 하나로 통합합니다.
    """
    step_count = state.get("step_count", 0) + 1

    # 최대 스텝 강제 종료 (무한루프 방지) — planner task 수에 맞게 동적 조정
    # n task 처리 = 2n-1 step (dispatch n번 + agent 복귀 후 LLM-routed FINISH n번, 마지막 합쳐짐)
    # _MAX_TASKS=5까지 안전하게 통과시키려면 최소 2n+3 여유 필요
    max_steps = min(2 * len(state.get("task_plan", [])) + 3, 15) or 4
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
        task_params = current_task.get("params", {})

        task_message = AIMessage(
            content=f"[Task {current_task.get('task_id', '?')}] {current_task.get('goal', '')}",
            name="supervisor",
        )
        logger.info(
            "[Supervisor] queued task dispatch: %s → %s (remaining=%d)",
            current_task.get("task_id"), current_task.get("agent"), len(remaining),
        )

        # task params를 state 필드로 projection
        update_dict = {
            "step_count": step_count,
            "current_task_id": current_task.get("task_id", ""),
            "current_task_goal": current_task.get("goal", ""),
            "pending_tasks": remaining,
            "messages": [task_message],
            # Map 파라미터
            "map_lot_id":   task_params.get("map_lot_id", ""),
            "map_lot_ids":  task_params.get("map_lot_ids", ""),
            "map_wf_ids":   task_params.get("map_wf_ids", ""),
            "map_groupkey": task_params.get("map_groupkey", ""),
            "map_type":     task_params.get("map_type", "binmap"),
            # task_params가 비면 state 기존 값 재사용 — 같은 plan 내 task 간 map_oper 계승
            "map_oper":     task_params.get("map_oper") or state.get("map_oper", ""),
            # Yield 파라미터
            "lotcd":          task_params.get("lotcd", state.get("lotcd", "")),
            "ref_date":       task_params.get("ref_date", state.get("ref_date", "")),
            "unit":           task_params.get("unit", state.get("unit", "weekly")),
            "periods":        task_params.get("periods", state.get("periods", 0)),
            "filter_params":  task_params.get("filter_params", []),
            "yield_lot_ids":  task_params.get("yield_lot_ids", ""),
            "yield_groupkey": task_params.get("yield_groupkey", ""),
            # WADS 파라미터
            "wads_start_tm":  task_params.get("wads_start_tm", ""),
            "wads_end_tm":    task_params.get("wads_end_tm", ""),
            "wads_parameter": task_params.get("wads_parameter") or task_params.get("parameter", ""),
            # Fail History 파라미터
            "dh_query":      task_params.get("dh_query", ""),
            "dh_fail_type":  task_params.get("dh_fail_type", ""),
            "dh_cause_oper": task_params.get("dh_cause_oper", ""),
            # Lot History 파라미터
            "lh_lot_ids":    task_params.get("lh_lot_ids", ""),
            # 이전 task가 남긴 stateful 필드 정리 — 다음 task에 stale로 영향 차단 (#19 fix).
            # worker가 정상 종료 시 자체 agent_suggestion을 다시 set하므로 빈 string은 안전.
            "agent_suggestion": "",
        }
        # 큐 dispatch 후에도 dup guard가 stale 비교를 하지 않도록 signature 갱신
        update_dict["_last_agent_params"] = _params_signature(update_dict)

        # map_agent 필수 파라미터 검증 (planner 경로)
        if current_task["agent"] == "map_agent":
            # task_params에서 먼저 정규화 시도
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

        return Command(update=update_dict, goto=current_task["agent"])

    configurable = config.get("configurable", {}) if config else {}
    messages = state.get("messages", [])
    today = date.today()
    today_yyyymmdd = today.strftime("%Y%m%d")
    today_yyyy_mm_dd = today.strftime("%Y-%m-%d")

    prompt = SUPERVISOR_SYSTEM_PROMPT.format(
        today=today.strftime("%Y년 %m월 %d일 (%A)"),
        today_yyyymmdd=today_yyyymmdd,
        today_yyyy_mm_dd=today_yyyy_mm_dd,
    )

    # 이전 이상감지 결과가 있으면 컨텍스트 정보로 주입 (라우팅은 rewrite된 메시지 기반)
    anomaly_params = state.get("anomaly_params", [])
    if anomaly_params:
        param_names = ", ".join(a["param"] for a in anomaly_params)
        prompt += (
            f"\n\n[이전 분석 결과] 이상 감지된 파라미터 ({len(anomaly_params)}개): {param_names}"
        )

    # 에이전트 메시지 요약 — 최근 2턴은 full 유지 (멀티스텝 lot ID 전달용)
    _AGENT_NAMES = {"yield_agent", "wads_agent", "map_agent", "fail_history_agent", "ppt_export", "lot_history_agent"}
    _RECENT_FULL_TURNS = 2
    _MAX_OLD_MSG_LEN = 300

    agent_indices = [i for i, m in enumerate(messages)
                     if isinstance(m, AIMessage) and getattr(m, "name", "") in _AGENT_NAMES]
    full_keep = set(agent_indices[-_RECENT_FULL_TURNS:])

    condensed = []
    for i, m in enumerate(messages):
        agent_name = getattr(m, "name", "")
        if i in full_keep:
            condensed.append(m)  # 최근 2턴: full 유지
        elif isinstance(m, AIMessage) and agent_name in _AGENT_NAMES and len(m.content) > _MAX_OLD_MSG_LEN:
            summary = m.content[:_MAX_OLD_MSG_LEN].rsplit("\n", 1)[0]
            condensed.append(AIMessage(
                content=f"[AGENT_RESULT:{agent_name}] {summary}...(결과 생략)",
                name=agent_name,
            ))
        else:
            condensed.append(m)

    # token_counter=len → 메시지 개수 기준 (문자수 아님). 최근 50개 메시지만 전달.
    # #21 fix: 위 condensation(_RECENT_FULL_TURNS=2, _MAX_OLD_MSG_LEN=300)이 이미 오래된
    # agent 메시지를 축약하므로 trim limit이 너무 작으면 condensation 작업이 무의미.
    # _MAX_TASKS=5 plan 처리 시 task_message + agent_result + supervisor_decision이
    # 빠르게 20개를 초과하므로 50으로 상향. follow-up 다중 turn에서도 컨텍스트 보존.
    trimmed_messages = trim_messages(condensed, max_tokens=50, strategy="last", token_counter=len)
    # ── 수동 JSON 파싱 방식 (with_structured_output 사용 금지) ──
    # 이유: gpt-oss-120b는 function calling을 지원하지만, OpenRouter 프록시가
    #       structured output 파라미터를 제대로 전달하지 못함.
    #       _model.with_structured_output(RouteResponse).invoke() 사용 시
    #       예외 발생 → fallback "요청을 이해하지 못했습니다" 반환되는 버그 발생.
    # 해결: LLM에게 raw JSON 출력을 요청하고, regex로 추출 후 Pydantic 검증.
    # 참고: with_structured_output 재도입 시 OpenRouter 호환성 먼저 확인할 것.
    try:
        # ── stream() 루프: <think> 구간은 실시간 전송, 나머지는 누적 (#20 fix) ──
        # 견고성: ① 닫는 </think> 누락 시 post-loop fallback emit, ② 다중 <think> 블록 지원
        # (search_offset으로 처리된 블록 이후만 검색), ③ 같은 chunk 안 open+close 처리.
        # raw_text 자체는 가공 안 함 — extract_json_from_llm이 자체적으로 think 태그 제거.
        raw_text = ""
        search_offset = 0    # 이미 처리된 think 블록 이후 검색 시작 위치
        think_open_idx = -1  # 현재 열린 <think>의 raw_text 내 위치 (-1 = 없음)
        think_emit_len = 0   # 현재 open 블록에서 emit한 char 수

        def _emit_thinking(content: str) -> None:
            if content:
                stream_event("thinking", ThinkingEvent(
                    content=content, agent="supervisor", node="supervisor",
                ))

        for chunk in _model.stream(
            [{"role": "system", "content": prompt}, *trimmed_messages],
            config={"callbacks": _lf_callbacks()},
        ):
            token = chunk.content or ""
            if not token:
                continue
            raw_text += token

            # 누적된 raw_text에서 처리 가능한 만큼의 think 블록을 처리
            while True:
                if think_open_idx == -1:
                    idx = raw_text.find("<think>", search_offset)
                    if idx == -1:
                        break
                    think_open_idx = idx
                    think_emit_len = 0

                content_start = think_open_idx + len("<think>")
                close_idx = raw_text.find("</think>", content_start)
                if close_idx >= 0:
                    # 완전한 think 블록: 미전송 부분만 emit, 다음 블록 검색 준비
                    full = raw_text[content_start:close_idx]
                    _emit_thinking(full[think_emit_len:])
                    search_offset = close_idx + len("</think>")
                    think_open_idx = -1
                    think_emit_len = 0
                    # while loop 계속 — 같은 chunk에 다음 <think>가 있을 수 있음
                else:
                    # 아직 닫히지 않음 — 새로 추가된 부분만 emit하되, 마지막 7글자는 보류
                    # (다음 chunk에서 '</think>'(8자)의 시작 조각으로 판명될 수 있음)
                    current = raw_text[content_start:]
                    safe_len = max(0, len(current) - (len("</think>") - 1))
                    if safe_len > think_emit_len:
                        _emit_thinking(current[think_emit_len:safe_len])
                        think_emit_len = safe_len
                    break

        # stream 종료 후 fallback: 닫는 </think> 없이 끝났으면 남은 thinking emit
        if think_open_idx >= 0:
            content_start = think_open_idx + len("<think>")
            full = raw_text[content_start:]
            _emit_thinking(full[think_emit_len:])
            logger.warning("[Supervisor] <think> 닫는 태그 누락 — fallback emit (len=%d)", len(full))

        raw_text = raw_text.strip()
        logger.debug("[Supervisor] raw LLM response: %s", raw_text[:500])

        decision = extract_json_from_llm(raw_text, RouteResponse)
    except (ConnectionError, TimeoutError, OSError):
        raise  # Tier 1: RetryPolicy가 재시도
    except Exception as e:
        logger.error(
            "Supervisor JSON 파싱 실패: %s | raw_len=%d has_think_open=%s "
            "has_think_close=%s has_open_brace=%s has_close_brace=%s",
            e, len(raw_text), "<think>" in raw_text, "</think>" in raw_text,
            "{" in raw_text, "}" in raw_text,
        )
        logger.error("[Supervisor] raw LLM response (full): %r", raw_text)
        decision = RouteResponse(
            next="FINISH",
            lotcd=state.get("lotcd", ""),
            ref_date=today_yyyymmdd,
            wads_end_tm="",
            message="요청을 이해하지 못했습니다. 다시 시도해 주세요.",
        )

    # 동일 에이전트 + 동일 파라미터 재호출 방지: 파라미터가 바뀌면 허용
    # step_count > 1 조건: 첫 스텝은 새 질의이므로 이전 대화 히스토리의 에이전트와 겹쳐도 허용
    if step_count > 1 and decision.next not in ("FINISH",):
        last_agent = next(
            (getattr(m, "name", "") for m in reversed(messages)
             if isinstance(m, AIMessage) and getattr(m, "name", "") in _AGENT_NAMES),
            None,
        )
        if last_agent == decision.next:
            prev_params = state.get("_last_agent_params", {})
            curr_params = _params_signature(decision.model_dump())
            if prev_params == curr_params:
                logger.info("[Supervisor] 동일 에이전트+파라미터(%s) 재호출 방지 → FINISH", last_agent)
                decision = RouteResponse(
                    next="FINISH",
                    lotcd=decision.lotcd,
                    ref_date=decision.ref_date,
                    wads_end_tm=decision.wads_end_tm,
                    message=decision.message or "분석을 완료했습니다.",
                )
            else:
                logger.info("[Supervisor] 동일 에이전트(%s) 파라미터 변경 → 재호출 허용", last_agent)

    # lotcd: 이전 State 값 유지 (follow-up 대화)
    prev_lotcd = state.get("lotcd", "")
    new_lotcd = decision.lotcd or prev_lotcd

    # ── 필수 파라미터 검증 + interrupt (HITL) ──
    if decision.next in ("yield_agent", "wads_agent") and not new_lotcd:
        user_response = interrupt({
            "type": "missing_param",
            "param": "lotcd",
            "message": "제품코드(lotcd)를 입력해주세요. (예: 4SS, 5NA, 6E2)",
            "route": decision.next,
        })
        new_lotcd = str(user_response).strip()

    if decision.next == "map_agent":
        has_lot = decision.map_lot_id or decision.map_lot_ids or decision.map_groupkey
        if not has_lot:
            user_response = interrupt({
                "type": "missing_param",
                "param": "map_lot_id",
                "message": "맵을 조회할 Lot ID를 입력해주세요. (예: 4SS2DPD 또는 4SS2DPD,4SSXCEW)",
                "route": "map_agent",
            })
            resp = str(user_response).strip()
            if "," in resp:
                decision.map_lot_ids = resp
            else:
                decision.map_lot_id = resp

        if not decision.map_oper:
            user_response = interrupt({
                "type": "missing_param",
                "param": "map_oper",
                "message": "PT1H / PT1C 중 어떤 공정의 맵을 조회할까요?",
                "route": "map_agent",
            })
            decision.map_oper = _normalize_map_oper(str(user_response)) or "PT1H"

    # Langfuse — 라우팅 결정 후 메타데이터 기록
    try:
        get_client().update_current_span(
            metadata={
                "anomaly_count": configurable.get("anomaly_count", 0),
                "route": decision.next,
                "lotcd": new_lotcd,
                "step": step_count,
            }
        )
    except Exception:
        pass

    # 빈 문자열 기본값 처리
    ref_date = decision.ref_date or today_yyyymmdd
    wads_end_tm = decision.wads_end_tm or (
        today_yyyy_mm_dd if decision.next == "wads_agent" else ""
    )

    result_message = AIMessage(content=decision.message, name="supervisor")

    logger.info(
        "[Supervisor] step=%d next=%-18s lotcd=%-6s ref_date=%s wads_end_tm=%s periods=%s unit=%s filter_params=%s map_lot_id=%r map_lot_ids=%r map_wf_ids=%r map_groupkey=%r yield_lot_ids=%r dh_query=%r lh_lot_ids=%r",
        step_count,
        decision.next,
        new_lotcd,
        ref_date,
        wads_end_tm,
        decision.periods,
        decision.unit,
        decision.filter_params,
        decision.map_lot_id,
        decision.map_lot_ids,
        decision.map_wf_ids,
        decision.map_groupkey,
        decision.yield_lot_ids,
        decision.dh_query,
        decision.lh_lot_ids,
    )

    update_dict = {
        "step_count": step_count,
        "messages": [result_message],
        "lotcd": new_lotcd,
        "ref_date": ref_date,
        "wads_start_tm": decision.wads_start_tm or "",
        "wads_end_tm": wads_end_tm,
        "wads_parameter": decision.wads_parameter,
        "filter_params": decision.filter_params,
        "unit": decision.unit or "weekly",
        "periods": decision.periods,
        "map_lot_id":   decision.map_lot_id,
        "map_lot_ids":  decision.map_lot_ids,
        "map_wf_ids":   decision.map_wf_ids,
        "map_groupkey": decision.map_groupkey,
        "map_type":     decision.map_type or "binmap",
        "map_oper":     decision.map_oper,
        "yield_lot_ids":  decision.yield_lot_ids,
        "yield_groupkey": decision.yield_groupkey,
        "dh_query":      decision.dh_query,
        "dh_fail_type":  decision.dh_fail_type,
        "dh_cause_oper": decision.dh_cause_oper,
        "lh_lot_ids":    decision.lh_lot_ids,
        # LLM-routed 분기는 planner task가 아니므로 task goal 없음 (#12 fix)
        "current_task_goal": "",
        "_last_agent_params": _params_signature(decision.model_dump()),
    }

    if decision.next == "FINISH":
        return Command(update=update_dict, goto=END)

    return Command(update=update_dict, goto=decision.next)


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
    wads_parameter: str   # WADS step code 필터 (#13 fix)
    wads_artifacts: Annotated[list, operator.add]

    # 이상감지
    anomaly_params: list

    # 파라미터 필터
    filter_params: list  # 표시할 파라미터 필터 (빈 list = 전체)

    # Map Agent 파라미터
    map_lot_id:   str
    map_lot_ids:  str
    map_wf_ids:   str
    map_groupkey: str
    map_type:     str
    map_oper:     str

    # Yield lot 비교 파라미터
    yield_lot_ids:  str
    yield_groupkey: str

    # Fail History 파라미터
    dh_query: str
    dh_fail_type: str
    dh_cause_oper: str

    # Fail History 결과
    fail_history_artifacts: Annotated[list, operator.add]

    # Lot History 파라미터 & 결과
    lh_lot_ids: str
    lot_history_artifacts: Annotated[list, operator.add]

    # Map 결과
    map_result:    str
    map_artifacts: Annotated[list, operator.add]

    # PPT Export 결과
    ppt_artifacts: Annotated[list, operator.add]

    # 에이전트 제안 (UI 렌더링용)
    agent_suggestion: str

    # 동일 에이전트 재호출 방지용 파라미터 스냅샷
    _last_agent_params: dict

    # Planner 관련
    task_plan: list[dict]           # planner가 생성한 전체 계획 (overwrite)
    pending_tasks: list[dict]       # 아직 실행 안 된 TaskItem들 (overwrite)
    current_task_id: str            # 현재 실행 중인 task의 ID
    current_task_goal: str          # 현재 실행 중인 task의 한국어 goal — worker가 query 우선순위로 사용 (#12 fix)

    # 워커 task별 결과 누적 (#8 phase 1, replanner 사전작업)
    # 각 worker가 정상/에러 종료 시 [(task_id, summary)]를 append.
    # 향후 replanner_node가 plan 갱신·chained input 해소에 사용.
    past_steps: Annotated[list, operator.add]


# ── 그래프 조립 (순환 import 방지: yield_query_agent/wads_agent/map_agent는 supervisor를 import하지 않음)
from yield_query_agent import yield_agent_node  # noqa: E402
from wads_agent import wads_agent_node  # noqa: E402
from map_agent import map_agent_node  # noqa: E402
from fail_history_agent import fail_history_agent_node  # noqa: E402
from ppt_export_agent import ppt_export_node  # noqa: E402
from lot_history_agent import lot_history_agent_node  # noqa: E402

# 에이전트 노드 재시도 정책 (Oracle/LLM 일시적 오류 자동 재시도)
# LangGraph 기본(default_retry_on)은 OSError/TimeoutError를 거부하므로
# common.is_transient_error를 명시적으로 위임 — supervisor 노드와 worker 노드의
# transient 분류 로직을 한 곳에서 일관 관리.
_retry = RetryPolicy(max_attempts=3, initial_interval=1.0, retry_on=is_transient_error)

workflow = StateGraph(YieldQueryState)
workflow.add_node("rewrite", rewrite_node, retry_policy=_retry)
workflow.add_node("planner", planner_node, retry_policy=_retry)
workflow.add_node("supervisor", supervisor_node, retry_policy=_retry)
workflow.add_node("replanner", replanner_node, retry_policy=_retry)
workflow.add_node("yield_agent", yield_agent_node, retry_policy=_retry)
workflow.add_node("wads_agent", wads_agent_node, retry_policy=_retry)
workflow.add_node("map_agent", map_agent_node, retry_policy=_retry)
workflow.add_node("fail_history_agent", fail_history_agent_node, retry_policy=_retry)
workflow.add_node("ppt_export", ppt_export_node, retry_policy=_retry)
workflow.add_node("lot_history_agent", lot_history_agent_node, retry_policy=_retry)

workflow.add_edge(START, "rewrite")
workflow.add_edge("rewrite", "planner")        # rewrite → planner
workflow.add_edge("planner", "supervisor")     # planner → supervisor
# supervisor → agent: Command(goto=...)가 처리 (conditional_edges 불필요)
# agent → replanner → supervisor: 공식 plan-and-execute 패턴 (#8 phase 2)
workflow.add_edge("yield_agent", "replanner")
workflow.add_edge("wads_agent", "replanner")
workflow.add_edge("map_agent", "replanner")
workflow.add_edge("fail_history_agent", "replanner")
workflow.add_edge("ppt_export", "replanner")
workflow.add_edge("lot_history_agent", "replanner")
workflow.add_edge("replanner", "supervisor")

# workflow는 빌더(StateGraph)로 export — agent_server.py에서 checkpointer와 함께 compile
# 로컬 테스트:
if __name__ == "__main__":
    yield_supervisor = workflow.compile()
    print("OK — yield_supervisor compiled for local test")

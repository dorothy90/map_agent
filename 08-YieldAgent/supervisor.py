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
import os
import logging
import re
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

from common import stream_event, get_llm, extract_json_from_llm
from lf_utils import lf_callbacks as _lf_callbacks  # noqa: E402
from models import ThinkingEvent
from prompts import PLANNER_SYSTEM_PROMPT, REWRITE_SYSTEM_PROMPT_TEMPLATE, SUPERVISOR_SYSTEM_PROMPT
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
        response = _rewrite_model.invoke(
            invoke_messages,
            config={"callbacks": _lf_callbacks()},
        )

        # tool call이 있으면 실행 후 결과를 LLM에 다시 전달하여 최종 리라이팅
        if getattr(response, "tool_calls", None):
            tool_messages = [response]  # AIMessage with tool_calls
            for tc in response.tool_calls:
                tool_fn = _rewrite_tool_map.get(tc["name"])
                if tool_fn:
                    result = tool_fn.invoke(tc["args"])
                    tool_messages.append(
                        ToolMessage(content=str(result), tool_call_id=tc["id"])
                    )
                    logger.info("[Rewrite] tool '%s'(%s) → %s", tc["name"], tc["args"], result)
            # tool 결과를 포함하여 최종 리라이팅 요청
            final_response = _model.invoke(
                invoke_messages + tool_messages,
                config={"callbacks": _lf_callbacks()},
            )
            rewritten = final_response.content.strip()
        else:
            rewritten = response.content.strip()
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

    # 수동 JSON 파싱 (with_structured_output은 OpenRouter 호환성 문제)
    try:
        response = _model.invoke(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": last_human.content},
            ],
            config={"callbacks": _lf_callbacks()},
        )
        raw_text = response.content.strip()
        plan = extract_json_from_llm(raw_text, PlanResponse)
    except Exception as e:
        logger.error("[Planner] 파싱 실패: %s — 단일 task fallback", e)
        # fallback: 단일 task 없이 supervisor에게 위임
        return {"task_plan": [], "pending_tasks": []}

    # task 수 상한 제한
    if len(plan.tasks) > _MAX_TASKS:
        logger.warning("[Planner] task 수 %d → %d로 제한", len(plan.tasks), _MAX_TASKS)
        plan.tasks = plan.tasks[:_MAX_TASKS]

    tasks_dicts = [t.model_dump() for t in plan.tasks]
    logger.info("[Planner] %d task(s) 생성: %s", len(tasks_dicts),
                [(t["task_id"], t["agent"]) for t in tasks_dicts])

    return {
        "task_plan": tasks_dicts,
        "pending_tasks": tasks_dicts,
    }


# ── Supervisor 노드 ──────────────────────────────────────────
@observe(name="supervisor_node")
def supervisor_node(
    state: Dict[str, Any], config: RunnableConfig
) -> Command[Literal["yield_agent", "wads_agent", "map_agent", "fail_history_agent", "ppt_export", "__end__"]]:
    """Supervisor 노드: ReAct 스타일 멀티스텝 루프.

    각 스텝마다 에이전트 결과를 확인하고 다음 행동을 결정합니다.
    Command를 반환하여 state 업데이트 + 라우팅을 하나로 통합합니다.
    """
    step_count = state.get("step_count", 0) + 1

    # 최대 스텝 강제 종료 (무한루프 방지) — planner task 수에 맞게 동적 조정
    max_steps = min(len(state.get("task_plan", [])) + 3, 10) or 4
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
            "pending_tasks": remaining,
            "messages": [task_message],
            # Map 파라미터
            "map_lot_id":   task_params.get("map_lot_id", ""),
            "map_lot_ids":  task_params.get("map_lot_ids", ""),
            "map_wf_ids":   task_params.get("map_wf_ids", ""),
            "map_groupkey": task_params.get("map_groupkey", ""),
            "map_type":     task_params.get("map_type", "binmap"),
            "map_oper":     task_params.get("map_oper", ""),
            # Yield 파라미터
            "lotcd":          task_params.get("lotcd", state.get("lotcd", "")),
            "ref_date":       task_params.get("ref_date", state.get("ref_date", "")),
            "unit":           task_params.get("unit", state.get("unit", "weekly")),
            "periods":        task_params.get("periods", state.get("periods", 0)),
            "filter_params":  task_params.get("filter_params", []),
            "yield_lot_ids":  task_params.get("yield_lot_ids", ""),
            "yield_groupkey": task_params.get("yield_groupkey", ""),
            # WADS 파라미터
            "wads_start_tm": task_params.get("wads_start_tm", ""),
            "wads_end_tm":   task_params.get("wads_end_tm", ""),
            # Fail History 파라미터
            "dh_query":      task_params.get("dh_query", ""),
            "dh_fail_type":  task_params.get("dh_fail_type", ""),
            "dh_cause_oper": task_params.get("dh_cause_oper", ""),
            # Lot History 파라미터
            "lh_lot_ids":    task_params.get("lh_lot_ids", ""),
        }

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

    # token_counter=len → 메시지 개수 기준 (문자수 아님). 최근 20개 메시지만 전달.
    trimmed_messages = trim_messages(condensed, max_tokens=20, strategy="last", token_counter=len)
    # ── 수동 JSON 파싱 방식 (with_structured_output 사용 금지) ──
    # 이유: gpt-oss-120b는 function calling을 지원하지만, OpenRouter 프록시가
    #       structured output 파라미터를 제대로 전달하지 못함.
    #       _model.with_structured_output(RouteResponse).invoke() 사용 시
    #       예외 발생 → fallback "요청을 이해하지 못했습니다" 반환되는 버그 발생.
    # 해결: LLM에게 raw JSON 출력을 요청하고, regex로 추출 후 Pydantic 검증.
    # 참고: with_structured_output 재도입 시 OpenRouter 호환성 먼저 확인할 것.
    try:
        # ── stream() 루프: <think> 구간은 실시간 전송, 나머지는 누적 ──
        raw_text = ""
        thinking_buf = ""
        in_think = False

        for chunk in _model.stream(
            [{"role": "system", "content": prompt}, *trimmed_messages],
            config={"callbacks": _lf_callbacks()},
            max_tokens=2048,
        ):
            token = chunk.content or ""
            if not token:
                continue
            raw_text += token

            # <think> 태그 파싱 — 토큰 단위로 처리
            if not in_think and "<think>" in raw_text and "</think>" not in raw_text:
                in_think = True
                # <think> 이후 부분만 thinking_buf에
                thinking_buf = raw_text.split("<think>", 1)[1]
                stream_event("thinking", ThinkingEvent(
                    content=thinking_buf, agent="supervisor", node="supervisor",
                ))
                continue

            if in_think:
                if "</think>" in raw_text:
                    # thinking 종료
                    in_think = False
                    think_content = raw_text.split("<think>", 1)[1].split("</think>", 1)[0]
                    # 마지막 thinking 청크 전송
                    remaining = think_content[len(thinking_buf):]
                    if remaining:
                        stream_event("thinking", ThinkingEvent(
                            content=remaining, agent="supervisor", node="supervisor",
                        ))
                else:
                    # thinking 진행 중 — 새 토큰만 전송
                    current_think = raw_text.split("<think>", 1)[1]
                    new_part = current_think[len(thinking_buf):]
                    if new_part:
                        stream_event("thinking", ThinkingEvent(
                            content=new_part, agent="supervisor", node="supervisor",
                        ))
                        thinking_buf = current_think

        raw_text = raw_text.strip()
        logger.debug("[Supervisor] raw LLM response: %s", raw_text[:500])

        decision = extract_json_from_llm(raw_text, RouteResponse)
    except (ConnectionError, TimeoutError, OSError):
        raise  # Tier 1: RetryPolicy가 재시도
    except Exception as e:
        logger.error("Supervisor JSON 파싱 실패: %s", e)
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
            curr_params = {
                "lot_id": decision.map_lot_id, "lot_ids": decision.map_lot_ids,
                "wf_ids": decision.map_wf_ids, "groupkey": decision.map_groupkey,
                "ref_date": decision.ref_date, "filter_params": decision.filter_params,
                "yield_lot_ids": decision.yield_lot_ids,
                "yield_groupkey": decision.yield_groupkey,
                "dh_query": decision.dh_query,
                "dh_fail_type": decision.dh_fail_type,
                "dh_cause_oper": decision.dh_cause_oper,
            }
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
        "_last_agent_params": {
            "lot_id": decision.map_lot_id, "lot_ids": decision.map_lot_ids,
            "wf_ids": decision.map_wf_ids, "groupkey": decision.map_groupkey,
            "ref_date": decision.ref_date, "filter_params": decision.filter_params,
            "yield_lot_ids": decision.yield_lot_ids,
            "yield_groupkey": decision.yield_groupkey,
            "dh_query": decision.dh_query,
            "dh_fail_type": decision.dh_fail_type,
            "dh_cause_oper": decision.dh_cause_oper,
            "lh_lot_ids": decision.lh_lot_ids,
        },
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


# ── 그래프 조립 (순환 import 방지: yield_query_agent/wads_agent/map_agent는 supervisor를 import하지 않음)
from yield_query_agent import yield_agent_node  # noqa: E402
from wads_agent import wads_agent_node  # noqa: E402
from map_agent import map_agent_node  # noqa: E402
from fail_history_agent import fail_history_agent_node  # noqa: E402
from ppt_export_agent import ppt_export_node  # noqa: E402
from lot_history_agent import lot_history_agent_node  # noqa: E402

# 에이전트 노드 재시도 정책 (Oracle/LLM 일시적 오류 자동 재시도)
_retry = RetryPolicy(max_attempts=3, initial_interval=1.0)

workflow = StateGraph(YieldQueryState)
workflow.add_node("rewrite", rewrite_node, retry_policy=_retry)
workflow.add_node("planner", planner_node, retry_policy=_retry)
workflow.add_node("supervisor", supervisor_node, retry_policy=_retry)
workflow.add_node("yield_agent", yield_agent_node, retry_policy=_retry)
workflow.add_node("wads_agent", wads_agent_node, retry_policy=_retry)
workflow.add_node("map_agent", map_agent_node, retry_policy=_retry)
workflow.add_node("fail_history_agent", fail_history_agent_node, retry_policy=_retry)
workflow.add_node("ppt_export", ppt_export_node, retry_policy=_retry)
workflow.add_node("lot_history_agent", lot_history_agent_node, retry_policy=_retry)

workflow.add_edge(START, "rewrite")
workflow.add_edge("rewrite", "planner")        # rewrite → planner
workflow.add_edge("planner", "supervisor")      # planner → supervisor
# supervisor → agent: Command(goto=...)가 처리 (conditional_edges 불필요)
# agent → supervisor: 정적 엣지로 루프 복귀
workflow.add_edge("yield_agent", "supervisor")
workflow.add_edge("wads_agent", "supervisor")
workflow.add_edge("map_agent", "supervisor")
workflow.add_edge("fail_history_agent", "supervisor")
# ppt_export → supervisor (planner 큐의 후속 task 실행을 위해 supervisor로 복귀)
workflow.add_edge("ppt_export", "supervisor")
workflow.add_edge("lot_history_agent", "supervisor")

# workflow는 빌더(StateGraph)로 export — agent_server.py에서 checkpointer와 함께 compile
# 로컬 테스트:
if __name__ == "__main__":
    yield_supervisor = workflow.compile()
    print("OK — yield_supervisor compiled for local test")

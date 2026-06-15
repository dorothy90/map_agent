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
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Dict, Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    trim_messages,
)
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables import RunnableConfig
from langfuse import observe
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.types import Command, RetryPolicy, interrupt
from pydantic import BaseModel, Field

from common import stream_event, get_llm, extract_json_from_llm, is_transient_error
from canonical_request import (
    AGENT_SLOT_SCHEMAS,
    build_task_from_canonical_request,
    build_tasks_from_canonical_requests,
    canonical_requests_from_tasks,
    normalize_canonical_request,
)
from lf_utils import lf_callbacks as _lf_callbacks  # noqa: E402
from models import StatusEvent
from prompts import (
    CANONICAL_PLANNER_SYSTEM_PROMPT,
    REPLANNER_SYSTEM_PROMPT,
)
from result_contracts import (
    ResultContractError,
    build_recent_result_index_entry,
    prune_recent_results,
)
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


# ── Pydantic 라우팅 결정 모델 ────────────────────────────────
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
    ambiguous_slots: list[dict] = Field(
        default_factory=list,
        description=(
            "진짜 모호해서(여러 해석이 모두 그럴듯해 하나로 확신 불가) 사용자 확인이 필요한 슬롯만. "
            "각 항목: {\"slot\":슬롯명, \"candidates\":[후보값,...], \"reason\":한국어 질문}. "
            "해당 슬롯은 slots에서 비워두고 여기 적는다. 기본값이 있는 미지정은 모호가 아니다 → 빈 리스트."
        ),
    )


class CanonicalPlanResponse(BaseModel):
    """LLM canonicalizer output schema."""

    requests: list[CanonicalRequestItem] = Field(
        default_factory=list, description="정규화된 요청 목록"
    )
    answer: str = Field(
        default="",
        description="도구 실행 없이 제공 context만으로 답할 수 있을 때의 사용자 응답",
    )


# ── yield 조회 기간: 라벨 기반 time_range → ref_date/periods/unit 변환 ──────────
# LLM이 YYYYMMDD/periods 산술을 직접 하면 "16-17주차" 같은 특정 범위가 "최근 N주"로
# 떨어지는 silent-wrong이 생긴다. planner는 자연 라벨(time_range)만 뱉고, supervisor가
# dispatch 직전 코드로 ref_date/periods/unit으로 변환한다(yield_agent_node는 무수정).
class TimeRange(BaseModel):
    """yield_agent 조회 기간을 라벨 기반으로 표현.

    라벨 포맷:
      weekly:  "YYYY-Www"   (예: "2026-W17") — ISO 주차
      monthly: "YYYY-MM"    (예: "2026-02")
      daily:   "YYYY-MM-DD" (예: "2026-05-06")
    단일 시점이면 start == end."""

    unit: Literal["weekly", "monthly", "daily"] = Field(description="시간 단위")
    start: str = Field(description="시작 라벨 (포함)")
    end: str = Field(description="끝 라벨 (포함)")


def _parse_iso_week_label(label: str) -> tuple[int, int]:
    """'2026-W17' / '2026-w17' → (2026, 17)"""
    year_s, week_s = label.strip().replace("w", "W").split("-W", 1)
    return int(year_s), int(week_s)


def _parse_year_month_label(label: str) -> tuple[int, int]:
    """'2026-02' → (2026, 2)"""
    year_s, month_s = label.strip().split("-", 1)
    return int(year_s), int(month_s)


def resolve_time_range(tr: TimeRange) -> tuple[str, int, str]:
    """TimeRange 라벨 → (ref_date YYYYMMDD, periods, unit). end 시점이 기준일."""
    unit = tr.unit
    start, end = (tr.start or "").strip(), (tr.end or "").strip()
    if not start or not end:
        raise ValueError(f"time_range start/end empty: start={start!r} end={end!r}")

    if unit == "weekly":
        sy, sw = _parse_iso_week_label(start)
        ey, ew = _parse_iso_week_label(end)
        start_monday = date.fromisocalendar(sy, sw, 1)
        end_monday = date.fromisocalendar(ey, ew, 1)
        weeks = ((end_monday - start_monday).days // 7) + 1
        return end_monday.strftime("%Y%m%d"), max(1, weeks), "weekly"

    if unit == "monthly":
        sy, sm = _parse_year_month_label(start)
        ey, em = _parse_year_month_label(end)
        months = (ey - sy) * 12 + (em - sm) + 1
        return f"{ey:04d}{em:02d}01", max(1, months), "monthly"

    if unit == "daily":
        sd = datetime.strptime(start, "%Y-%m-%d").date()
        ed = datetime.strptime(end, "%Y-%m-%d").date()
        days = (ed - sd).days + 1
        return ed.strftime("%Y%m%d"), max(1, days), "daily"

    raise ValueError(f"unknown time_range.unit: {unit!r}")


def _apply_time_range_dict(params: dict) -> None:
    """task_params 안의 time_range를 ref_date/periods/unit으로 변환·치환(in-place).
    변환 성공 시 time_range 키는 제거한다(yield_agent_node는 ref_date/periods/unit만 읽음).
    변환 실패/부재 시 기존 슬롯을 건드리지 않아 backward-compatible."""
    tr_data = params.get("time_range")
    if not isinstance(tr_data, dict):
        return
    try:
        ref, periods, unit = resolve_time_range(TimeRange(**tr_data))
    except Exception as e:
        logger.warning("[Supervisor] task_params.time_range 변환 실패: %s | data=%r", e, tr_data)
        params.pop("time_range", None)
        return
    params["ref_date"] = ref
    params["periods"] = periods
    params["unit"] = unit
    params.pop("time_range", None)


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
    "wt_resp_agent",
    "mining_agent",
}


_MAX_CONTEXT_TOKENS = 30_000

# Planner referent window: how many recent raw conversation turns the planner sees ONLY
# to resolve follow-up referents ("그거/처음 거/아까 그"). Kept tight on purpose — recent
# referents are near, and older raw turns mislead resolution; cross-turn DATA depth is
# carried by recent_results (K=10, shown in full via _recent_results_prompt_context), not
# raw turns. _MAX_CONTEXT_TOKENS still trims this further as a safety budget.
_PLANNER_REFERENT_TURNS = 3


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


# Full result blocks are emitted newest-first until this CHARACTER budget is hit;
# older results then fall back to a 1-line summary so the planner still knows they
# exist. A resource budget governs how much detail is shown — not a magic turn count
# (#2: the planner was capped to the last 3 of K=10 accumulated results, so it was
# blind to older referenceable results even though resolution covered all K).
_RECENT_CONTEXT_FULL_BUDGET_CHARS = 6000

_RECENT_CONTEXT_PREFERRED_KEYS = (
    "parameter", "param", "fail_type", "cnt", "count", "detection_count",
    "lot_id", "lot_ids", "wf_ids", "lotcd", "groupkey", "groupkeys",
    "map_oper", "category", "end_tm",
)


def _recent_result_full_block(result: dict[str, Any]) -> list[str]:
    """Full detail for one result: summary + per-report tags + up to 5 compact rows."""
    rows = result.get("rows") or []
    columns = [
        {"name": column.get("name"), "semantic": column.get("semantic")}
        for column in (result.get("columns") or [])
        if isinstance(column, dict)
    ][:8]
    block = [
        "result: "
        f"result_id={result.get('result_id', '')} "
        f"source_agent={result.get('source_agent', '')} "
        f"kind={result.get('kind', '')} "
        f"title={preview_text(result.get('title', ''), max_chars=80)} "
        f"row_count={len(rows)} "
        f"columns={json.dumps(columns, ensure_ascii=False)}"
    ]
    # carry-both: expose per-report ordinal/parameter/oper so the planner is aware of
    # "N번째 리포트" targets, not just the per-wafer rows.
    reports = result.get("reports") or []
    report_tags = [
        f"{rp.get('report_index', i)}:{rp.get('parameter', '')}/{rp.get('map_oper', '')}"
        for i, rp in enumerate(reports, start=1)
        if isinstance(rp, dict)
    ]
    if report_tags:
        block.append(f"reports: [{', '.join(report_tags)}]")
    for index, row in enumerate(rows[:5], start=1):
        if not isinstance(row, dict):
            continue
        compact_row = {
            key: row.get(key)
            for key in _RECENT_CONTEXT_PREFERRED_KEYS
            if row.get(key) not in (None, "")
        }
        if compact_row:
            block.append(f"row_{index}: {json.dumps(compact_row, ensure_ascii=False)}")
    return block


def _recent_result_condensed_line(result: dict[str, Any]) -> str:
    """One-line summary for older results kept for awareness (beyond the full budget)."""
    rows = result.get("rows") or []
    reports = result.get("reports") or []
    params = _unique_texts(
        [str(rp.get("parameter") or "") for rp in reports if isinstance(rp, dict)]
        or [str(r.get("parameter") or r.get("fail_type") or "") for r in rows if isinstance(r, dict)]
    )[:6]
    return (
        "result(condensed): "
        f"result_id={result.get('result_id', '')} "
        f"source_agent={result.get('source_agent', '')} "
        f"kind={result.get('kind', '')} "
        f"row_count={len(rows)} reports={len(reports)} "
        f"params={json.dumps(params, ensure_ascii=False)}"
    )


def _recent_results_prompt_context(recent_results: list[dict[str, Any]]) -> str:
    """Compact structured result context for follow-up planning.

    Exposes result metadata and row order (not raw assistant prose) so follow-ups can
    refer to prior tables. #2: shows ALL accumulated results so the planner is aware of
    every result it can reference — full detail for the most recent within a character
    budget, a 1-line summary for older ones (no magic turn cap; budget governs).
    """
    if not recent_results:
        return ""
    lines = [
        "Recent structured results are ordered as displayed to the user. Follow-up references to ranks, rows, or prior items refer to that displayed order.",
    ]
    # Newest-first: keep emitting full blocks until the character budget is exhausted;
    # always keep at least the newest result full.
    budget = _RECENT_CONTEXT_FULL_BUDGET_CHARS
    full_from = len(recent_results)
    for i in range(len(recent_results) - 1, -1, -1):
        cost = sum(len(x) for x in _recent_result_full_block(recent_results[i]))
        if i != len(recent_results) - 1 and cost > budget:
            break
        budget -= cost
        full_from = i
    for i, result in enumerate(recent_results):
        if i >= full_from:
            lines.extend(_recent_result_full_block(result))
        else:
            lines.append(_recent_result_condensed_line(result))
    return "\n".join(lines)


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
    _iy, _iw, _ = today.isocalendar()
    prompt = CANONICAL_PLANNER_SYSTEM_PROMPT.format(
        today=today.strftime("%Y년 %m월 %d일 (%A)"),
        today_yyyymmdd=today.strftime("%Y%m%d"),
        today_yyyy_mm_dd=today.strftime("%Y-%m-%d"),
        week_ago_yyyy_mm_dd=(today - timedelta(days=6)).strftime("%Y-%m-%d"),
        year=today.year,
        today_iso_week=f"{_iy}-W{_iw:02d}",
        today_year_month=today.strftime("%Y-%m"),
    )

    meta_parts: list[str] = []
    # Reference resolution is planner-owned (reference_resolver removed): build the
    # recent-results context here each turn so follow-up references resolve from the
    # displayed prior results. Also returned to state below for downstream chaining.
    # 축3: accumulate (K=10) across turns so ordinals survive beyond the last 3 results.
    recent_results = _accumulate_recent_results(state.get("recent_results"), messages)
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
    # 축2: inject the recent N=3 conversation turns (raw user/assistant text, token-
    # trimmed) so the planner can resolve follow-up REFERENTS ("그거/처음 거/아까 그").
    # Slot VALUES still come only from the latest request + structured context (prompt).
    recent_turns = _get_recent_turns(messages, max_turns=_PLANNER_REFERENT_TURNS, exclude_last=last_human)
    invoke_messages.extend(recent_turns)
    invoke_messages.append({"role": "user", "content": last_human.content})
    emit_runtime_detail(
        "planner.input",
        {
            "last_human": last_human.content,
            "meta": meta,
            "recent_turns": recent_turns,
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

    # 모호 슬롯 수집 (시나리오 2): plan.requests(intent+agent 필터)는 build_tasks의
    # task_{i} 순서와 1:1 → task_id로 태깅해 supervisor의 missing_param HITL이 options로 묻는다.
    ambiguous_slots: list[dict] = []
    for _i, _req in enumerate(
        (r for r in plan.requests if r.intent and r.agent), start=1
    ):
        for _amb in getattr(_req, "ambiguous_slots", None) or []:
            if isinstance(_amb, dict) and _amb.get("slot"):
                ambiguous_slots.append(
                    {"task_id": f"task_{_i}", "agent": _req.agent, **_amb}
                )

    update = {
        "canonical_request": canonical_requests[0],
        "canonical_requests": canonical_requests,
        "ambiguous_slots": ambiguous_slots,
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

    if result.action == "cancel":
        return Command(
            goto="supervisor",
            update={
                "response": "사용자가 분석 계획을 취소했습니다.",
                "canonical_request": {},
                "canonical_requests": [],
                "task_plan": [],
                "pending_tasks": [],
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
        update=_plan_review_commit(result_requests, new_task_plan),
    )


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


def _latest_wads_reports(state: dict) -> list[dict]:
    """최신 wads_agent 결과 envelope의 per-report 목록을 읽는다.
    각 report = {parameter, map_oper, groupkeys, ...} (wads_tools가 이미 구성해 envelope에 첨부).
    없으면 []. report별 cummap fan-out 입력으로 사용."""
    for message in reversed(state.get("messages", []) or []):
        if isinstance(message, AIMessage) and getattr(message, "name", "") == "wads_agent":
            reports = (
                ((getattr(message, "additional_kwargs", None) or {}).get("result") or {})
                .get("extensions", {})
                .get("wads_agent", {})
                .get("reports")
            )
            if isinstance(reports, list) and reports:
                return [r for r in reports if isinstance(r, dict)]
    return []


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


def _report_rows_of(result: dict) -> list:
    """Per-report rows for #RN ordinal resolution. carry-both (i): prefer the dedicated
    `reports` channel (carried even when displayed `rows` are per-wafer); fall back to
    legacy report-shaped rows for results produced before the channel existed."""
    reports = result.get("reports")
    if isinstance(reports, list) and any(_is_report_row(r) for r in reports):
        return [r for r in reports if _is_report_row(r)]
    return [r for r in (result.get("rows") or []) if _is_report_row(r)]


def _latest_report_result(state: dict) -> dict | None:
    """Most-recent wads result carrying per-report rows (reports channel or legacy rows)."""
    for result in reversed(state.get("recent_results", []) or []):
        if not isinstance(result, dict) or result.get("source_agent") != "wads_agent":
            continue
        if _report_rows_of(result):
            return result
    return None


def _resolve_report_ordinal(state: dict, ordinal: int) -> tuple[list[str], str, str] | None:
    """row[ordinal-1] of the latest report result -> (groupkeys, map_oper, parameter).

    None if there is no report-structured result or the ordinal is out of range —
    the caller marks it unresolved so the dispatch missing-param backstop asks
    (never silently substitutes another report / all lots).
    """
    result = _latest_report_result(state)
    if not result:
        return None
    rows = _report_rows_of(result)
    idx = ordinal - 1
    if idx < 0 or idx >= len(rows):
        return None
    row = rows[idx]
    groupkeys = _unique_texts(_groupkey_list(row.get("groupkeys") or row.get("groupkey")))
    if not groupkeys:
        return None
    return groupkeys, _map_oper_from_wads_row(row), str(row.get("parameter") or "")


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
        groupkeys, oper, parameter = resolved
        params["groupkey"] = ",".join(groupkeys)
        if oper and _is_placeholder_or_empty(params.get("map_oper")):
            params["map_oper"] = oper
        if parameter:  # carry the report's parameter as the cummap display label
            params["map_label"] = parameter
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


def _accumulate_recent_results(current: list | None, messages: list) -> list:
    """축3: accumulate the resolver index across turns, decoupled from message prune.

    Merge the carried index (current) with the results currently in messages, dedup by
    result_id (prune_recent_results keeps the newest per id), and cap to
    MAX_RECENT_RESULTS (K). A result stays referenceable for K results even after its
    message is pruned (30-msg cap), so ordinal refs survive far longer than the last 3.
    Source of truth stays messages; the index is an accumulated projection. Lifetime:
    agent_server seeds recent_results=[] on a new session, so it clears per session.
    """
    return prune_recent_results((current or []) + _build_recent_results_index(messages))


def _recent_results_update_from_messages(
    messages: list, current_recent_results: list | None
) -> dict:
    """Return an overwrite update for the accumulated recent_results index."""

    recent_results = _accumulate_recent_results(current_recent_results, messages)
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
            # TEMP(relation_tree fail_type): required param is now fail_type (was cause_oper).
            if _is_placeholder_or_empty(params.get("fail_type")):
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

    # ── 결정론적 후속 제안 (B안): yield 이상감지 시 confirm 필요한 WADS task를 큐에 추가 ──
    # 완료판정보다 먼저 실행 — pending이 비어도 후속을 넣어 턴이 조기 종료되지 않게 한다.
    # task_plan에도 append해 완료판정(아래)·max_steps 정합을 맞춘다.
    followup = _maybe_propose_wads_followup(state)
    if followup:
        return {**scratchpad_update, **followup}

    # ── Step 5: wads 검출 후속 선택(map/...) sentinel 제안 (wads followup과 같은 결정론 경로) ──
    # wads followup 이후에 호출 — wads가 실행돼 검출 결과가 있을 때만 발동(detected_count>0).
    postwads = _maybe_propose_postwads_choice(state)
    if postwads:
        return {**scratchpad_update, **postwads}

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
    elif agent == "wt_resp_agent":
        # main_oper 선택 후속: lotcd 상속(없으면 state). fail_type/cause_oper은 공통 블록이 채움.
        proj["lotcd"] = task_params.get("lotcd") or state.get("lotcd", "")

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
                # 공정 필터 ("PT1H"/"PT1C") — yield 열화 fan-out 시 공정을 CATEGORY로 전달
                "wads_category": task_params.get("wads_category", ""),
            }
        )

    elif agent == "map_agent":
        proj.update(
            {
                "map_type": task_params.get("map_type", "binmap"),
                "map_oper": task_params.get("map_oper") or state.get("map_oper", ""),
                # wafer-number pattern filter (LLM fills wf_mod/wf_rem; SQL applies MOD).
                "wf_mod": task_params.get("wf_mod") or 0,
                "wf_rem": task_params.get("wf_rem") or 0,
                # cummap display label (backend-resolved, e.g. #RN report parameter).
                "map_label": task_params.get("map_label") or "",
                # WADS report별 cummap fan-out 입력 [{parameter, map_oper, groupkeys}, …].
                "map_groups": task_params.get("map_groups") or [],
            }
        )

    elif agent == "fail_history_agent":
        proj["dh_query"] = task_params.get("dh_query", "")
        # WADS report별 불량이력 fan-out 입력 [{lotcd, parameter, lot_ids}, …].
        proj["fail_groups"] = task_params.get("fail_groups") or []

    elif agent == "relation_tree_agent":
        # lotcd(3자)는 rt_lot_code와 동일 개념 — 구 필드 fallback 포함
        if not proj.get("lotcd"):
            proj["lotcd"] = task_params.get("rt_lot_code") or state.get("lotcd", "")
        # WADS report별 연관분석 fan-out 입력 [{lotcd, parameter, lot_ids}, …].
        proj["rt_groups"] = task_params.get("rt_groups") or []

    elif agent == "mining_agent":
        # 상류(wads→wt_resp) 공유키 상속: lotcd, fail_type, wads_category(=mode), cause_oper(main_oper).
        proj["lotcd"] = task_params.get("lotcd") or state.get("lotcd", "")
        proj["fail_type"] = _parse_fail_type(task_params) or state.get("fail_type", "")
        proj["cause_oper"] = _parse_cause_oper(task_params) or state.get("cause_oper", "")
        proj["wads_category"] = task_params.get("wads_category") or state.get("wads_category", "")
        # group_good/group_bad: 사용자 직접 입력 또는 상류 결과 상속 (chained-input).
        proj["group_good"] = task_params.get("group_good") or state.get("group_good") or []
        proj["group_bad"] = task_params.get("group_bad") or state.get("group_bad") or []
        # mining 고유 슬롯
        proj["tech"] = task_params.get("tech") or state.get("tech", "")
        proj["user_id"] = task_params.get("user_id") or state.get("user_id", "")
        proj["rank_limit"] = task_params.get("rank_limit") or state.get("rank_limit") or 10

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
        # TEMP(relation_tree fail_type): required is now lotcd + fail_type (was cause_oper).
        if not update_dict.get("fail_type"):
            fields.append({
                "slot": "fail_type",
                "label": "연관 분석할 파라미터(불량유형)를 입력해주세요. (예: VTH 또는 VTH,IDSAT)",
                "type": "fail_type",
            })
    elif agent == "wt_resp_agent":
        if not update_dict.get("lotcd"):
            fields.append({
                "slot": "lotcd",
                "label": "WT Resp 분석할 LOT 코드를 입력해주세요. (예: 4SS2DPD)",
                "type": "lotcd",
            })
        if not update_dict.get("fail_type"):
            fields.append({
                "slot": "fail_type",
                "label": "WT Resp 분석할 파라미터(불량유형)를 입력해주세요. (예: VTH)",
                "type": "fail_type",
            })
        if not update_dict.get("cause_oper"):
            fields.append({
                "slot": "cause_oper",
                "label": "기준 공정(main_oper)을 입력해주세요.",
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


def _ambiguous_fields(
    agent: str, task_id: str, update_dict: dict, state: dict
) -> list[dict]:
    """모호 슬롯(시나리오 2)을 choice 필드로 변환 — 누락이 아니라 '값이 모호'한 케이스.

    planner가 state["ambiguous_slots"]에 적은 항목 중 이 task의, 아직 안 채워진,
    이 agent가 받는 슬롯만 options 필드로 만든다. 이미 값이 있으면(=resume로 선택됨) skip."""
    out: list[dict] = []
    allowed = AGENT_SLOT_SCHEMAS.get(agent, set())
    for amb in state.get("ambiguous_slots") or []:
        if not isinstance(amb, dict) or amb.get("task_id") != task_id:
            continue
        slot = amb.get("slot")
        if not slot or slot not in allowed:
            continue
        if str(update_dict.get(slot) or "").strip():
            continue  # 이미 채워짐 (resume 선택값) → 다시 묻지 않음
        candidates = [str(c).strip() for c in (amb.get("candidates") or []) if str(c).strip()]
        if not candidates:
            continue
        out.append({
            "slot": slot,
            "label": amb.get("reason") or f"'{slot}' 값이 모호합니다. 선택해주세요.",
            "type": "choice",
            "options": candidates,
        })
    return out


def _merge_fields(*field_lists: list[dict]) -> list[dict]:
    """슬롯 기준 dedup — 같은 슬롯이 누락+모호로 겹치면 options가 있는 쪽(모호)을 우선한다."""
    by_slot: dict[str, dict] = {}
    for fields in field_lists:
        for field in fields:
            slot = field.get("slot")
            if slot not in by_slot or (field.get("options") and not by_slot[slot].get("options")):
                by_slot[slot] = field
    return list(by_slot.values())


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
    task_id = str(current_task.get("task_id") or "")
    while True:
        fields = _merge_fields(
            _missing_required_fields(agent, update_dict, rejections),
            _ambiguous_fields(agent, task_id, update_dict, state),
        )
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
        "supervisor",  # confirm 거절 후 다음 pending task를 dispatch하러 재진입
        "yield_agent",
        "wads_agent",
        "map_agent",
        "fail_history_agent",
        "lot_history_agent",
        "ppt_export",
        "relation_tree_agent",
        "wt_resp_agent",
        "mining_agent",
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
        # ── Step 5: WADS 검출 후속 선택 sentinel은 dispatch 직전에 3택1로 치환(신규 노드 0개) ──
        if current_task.get("agent") == "__postwads_choice__":
            return _choose_postwads_or_drop(current_task, remaining, state, step_count)
        # ── 실행 확인 (B안): missing_param과 같은 dispatch 직전 멱등 구간에서 interrupt ──
        # confirm_tasks에 이 task가 있으면 확인. 거절 → Command(드롭 후 다음 pending/END) 반환.
        # 승인/미해당 → confirm_tasks 정리 dict(아래 update_dict에 merge).
        confirm_cleanup = _confirm_or_drop(current_task, remaining, state, step_count)
        if isinstance(confirm_cleanup, Command):
            return confirm_cleanup
        # C1: 코드 기반 chained input 해소 — wads_sql_result 메시지에서 lot_ids 자동 주입 (LLM 불필요)
        task_params = _resolve_chained_params(current_task, state)
        # planner가 yield 기간을 라벨 time_range로 넘긴 경우 → ref_date/periods/unit으로 변환(in-place).
        _apply_time_range_dict(task_params)

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
        # confirm 승인 시 처리된 task_id를 confirm_tasks에서 제거 (미해당이면 {} → no-op).
        update_dict.update(confirm_cleanup)

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
                    "wf_mod",
                    "wf_rem",
                    "map_label",
                    "dh_query",
                    "ref_date",
                    "unit",
                    "periods",
                    "wads_start_tm",
                    "wads_end_tm",
                    "wads_category",
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


# ── HITL 확인 메커니즘 (task 속성 기반, B안) ──────────────────────────
# "확인 후 실행"은 그래프 노드가 아니라 task 속성으로 처리한다: replanner가 confirm이 필요한
# 후속 task를 pending에 넣으며 state["confirm_tasks"]에 메시지를 달면, supervisor가 dispatch
# 직전(무거운 부수효과 이전, missing_param과 같은 멱등 구간)에 interrupt로 확인한다. 체이닝이
# 늘어도 신규 노드 0개. yes/no 해석만 LLM이 하고(키워드 금지), 후속 task 생성은 결정론으로 둔다.
_CONFIRM_SYSTEM = (
    "사용자에게 후속 작업을 실행할지 물었고, 아래는 사용자의 자유 응답이다. "
    "응답이 긍정(예/진행/확인/보겠다)인지 부정(아니오/취소/거절/안 본다)인지 판단해라. "
    "오직 'yes' 또는 'no' 한 단어만 출력해라."
)


def _interpret_confirmation(answer: Any) -> bool:
    """사용자 자유응답의 yes/no를 LLM으로 해석 (키워드 매칭 금지, plan_review 패턴 재사용).

    빈 응답은 거절로 처리한다. LLM 실패 시에도 안전하게 거절(후속 미실행)한다."""
    if isinstance(answer, dict):
        text = answer.get("task_confirm") or next(iter(answer.values()), "")
    else:
        text = answer
    text = str(text or "").strip()
    if not text:
        return False
    try:
        verdict = (
            _model.invoke(
                [
                    {"role": "system", "content": _CONFIRM_SYSTEM},
                    {"role": "user", "content": text},
                ],
                config={"callbacks": _lf_callbacks()},
            ).content
            or ""
        ).strip().lower()
    except Exception as e:
        logger.warning("[Confirm] 응답 해석 LLM 실패 (%s) — 거절 처리", e)
        return False
    return verdict.startswith("y")


_RESUME_INTENT_SYSTEM = (
    "아래 JSON은 시스템이 사용자에게 띄운 HITL 질문(message)과 선택지(options), "
    "그리고 사용자의 다음 입력(input)이다. 입력이 그 질문에 대한 '답'(예/아니오·제시된 선택지·"
    "요청한 값 제공)인지, 아니면 그 질문과 무관한 '새로운 요청/질문'인지 판정해라. "
    "오직 'answer' 또는 'new' 한 단어만 출력해라."
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
        "message": pending_interrupt.get("message", ""),
        "options": [{"label": o.get("label"), "value": o.get("value")} for o in options],
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


def _build_wads_followup_task(state: Dict[str, Any]) -> dict:
    """yield 이상감지 → WADS follow-up task (replanner의 결정론 경로에서 호출).
    파라미터 필터(fail_type)는 넘기지 않는다 — 기간을 통째로 조회해 report별 map_oper/fail_type을
    확보하고, yield 열화감지 파라미터와의 교차 여부는 wads_agent가 anomaly_params로 사후 언급한다.
    시간창은 어제 하루(start=end=어제) 기본 세팅이라 confirm 1회로 바로 실행된다(별도 날짜 HITL 불필요)."""
    lotcd = state.get("lotcd", "")
    task_id = f"task_{len(state.get('task_plan') or []) + 1}_wads"
    yday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    return build_task_from_canonical_request(
        {
            "intent": "wads_report",
            "agent": "wads_agent",
            "slots": {
                "lotcd": lotcd,
                "wads_start_tm": yday,
                "wads_end_tm": yday,
            },
            "goal": f"{lotcd} WADS 열화 검출 리포트 조회",
        },
        task_id=task_id,
    )


_WADS_CONFIRM_MESSAGE = "WADS 열화 검출 리포트를 확인하시겠습니까?"


def _maybe_propose_wads_followup(state: Dict[str, Any]) -> dict:
    """결정론적 후속 제안: yield 이상감지 시 confirm 필요한 WADS task를 큐에 추가한다.
    이미 WADS가 계획/큐에 있으면(직접 요청 or 이미 제안됨) 재제안하지 않는다 — 이상감지 후
    매 replanner pass마다 재발동하는 무한 추가를 막는 가드. 반환: pending_tasks/task_plan
    (append)/confirm_tasks 업데이트 dict, 제안 없으면 {}."""
    anomaly_params = state.get("anomaly_params") or []
    if not anomaly_params:
        return {}
    pending = state.get("pending_tasks") or []
    task_plan = state.get("task_plan") or []
    if any(
        t.get("agent") == "wads_agent"
        for t in (pending + task_plan)
        if isinstance(t, dict)
    ):
        return {}
    wads_task = _build_wads_followup_task(state)
    task_id = wads_task["task_id"]
    logger.info("[Replanner] 이상감지 → WADS confirm task 제안: %s", task_id)
    return {
        # pending_tasks/task_plan은 overwrite 채널 — 기존 큐 보존 위해 반드시 append.
        "pending_tasks": pending + [wads_task],
        "task_plan": task_plan + [wads_task],
        "confirm_tasks": {
            **(state.get("confirm_tasks") or {}),
            task_id: _WADS_CONFIRM_MESSAGE,
        },
    }


def _confirm_or_drop(
    current_task: dict, remaining: list[dict], state: Dict[str, Any], step_count: int
):
    """dispatch 직전 confirm 처리 (missing_param과 같은 멱등 구간 — 무거운 부수효과 이전).
    confirm_tasks에 이 task가 없으면 {} 반환(그대로 진행).
    있으면 interrupt로 확인:
      - 승인 → {"confirm_tasks": <id 제거>} 반환(caller가 dispatch update에 merge).
      - 거절 → task 드롭. remaining 있으면 supervisor 재진입, 없으면 응답 set 후 END Command 반환."""
    confirm_tasks = state.get("confirm_tasks") or {}
    task_id = str(current_task.get("task_id") or "")
    message = confirm_tasks.get(task_id)
    if not message:
        return {}
    answer = interrupt({
        "type": "confirm",
        "interrupt_type": "task_confirm",
        "param": "task_confirm",
        "message": message,
        # InterruptEvent.options 스키마는 list[dict] — label/value 형태.
        "options": [{"label": "예", "value": "예"}, {"label": "아니오", "value": "아니오"}],
        "route": current_task.get("agent", ""),
    })
    new_confirm = {k: v for k, v in confirm_tasks.items() if k != task_id}
    if _interpret_confirmation(answer):
        return {"confirm_tasks": new_confirm}  # 승인 → dispatch 계속
    logger.info("[Confirm] task %s 거절 → 드롭 (remaining=%d)", task_id, len(remaining))
    if remaining:
        # 남은 task가 있으면 supervisor 재진입해 그걸 dispatch.
        return Command(
            update={
                "step_count": step_count,
                "pending_tasks": remaining,
                "confirm_tasks": new_confirm,
            },
            goto="supervisor",
        )
    # 남은 task 없음 → 직전 agent 결과로 턴 정상 종료 (replanner 완료판정과 동일 메시지 규칙).
    last_agent_msg = next(
        (
            m.content
            for m in reversed(state.get("messages", []))
            if isinstance(m, AIMessage)
            and getattr(m, "name", "") in _AGENT_NAMES
            and isinstance(m.content, str)
            and m.content.strip()
        ),
        "분석을 완료했습니다.",
    )
    return Command(
        update={
            "step_count": step_count,
            "pending_tasks": [],
            "confirm_tasks": new_confirm,
            "response": last_agent_msg,
        },
        goto=END,
    )


# ── Step 5: WADS 검출 후속 선택(map/fail_history/relation_tree) ──────────────
# yield→wads와 동일한 결정론 제안 패턴의 확장. wads가 파라미터를 검출하면 후속 선택 sentinel
# task를 큐에 넣고(replanner), supervisor dispatch 멱등 구간에서 3택1 interrupt로 확정한다.
# 신규 그래프 노드 0개 — sentinel은 dispatch 직전에 구체 task로 치환된다.
_POSTWADS_CHOICE_MESSAGE = "WADS 검출 결과로 이어서 무엇을 조회할까요?"


def _build_postwads_map_task(state: Dict[str, Any], task_plan: list, reports=None) -> dict:
    """WADS 검출 wafer의 cummap을 report(parameter+map_oper)별로 그리는 단일 map task.
    per-report 데이터는 wads 결과 envelope(_latest_wads_reports)에서 가져와 map_groups로 운반하고,
    map_agent가 group별로 cummap을 루프 렌더한다(report별로 따로 → binmap 1장 뭉침 방지).
    reports=선택된 1개 report 리스트면 그것만, None이면 전체(기존 동작=회귀안전).
    reports가 없으면 빈 chained 슬롯으로 두고 _resolve_chained_params가 단일 cummap을 채운다(fallback)."""
    lotcd = state.get("lotcd", "")
    task_id = f"task_{len(task_plan) + 1}_map"
    map_groups: list[dict] = []
    for r in (reports if reports is not None else _latest_wads_reports(state)):
        gks = [str(g).strip() for g in (r.get("groupkeys") or []) if str(g).strip()]
        if not gks:
            continue
        map_groups.append({
            "parameter": str(r.get("parameter") or ""),
            "map_oper": _normalize_map_oper(str(r.get("map_oper") or "")),
            "groupkeys": gks,
        })
    slots: dict[str, Any] = {"lotcd": lotcd, "map_type": "cummap"}
    if map_groups:
        # required(map_oper + lot_ids|groupkey) 충족용 top-level 값 + 렌더 주도용 map_groups.
        union: list[str] = []
        for g in map_groups:
            for gk in g["groupkeys"]:
                if gk not in union:
                    union.append(gk)
        slots.update({
            "map_groups": map_groups,
            "groupkey": ",".join(union),
            "map_oper": map_groups[0]["map_oper"],
        })
    return build_task_from_canonical_request(
        {
            "intent": "map",
            "agent": "map_agent",
            "slots": slots,
            "goal": f"{lotcd} WADS 검출 wafer cummap 조회",
        },
        task_id=task_id,
    )


def _postwads_detected_params(state: Dict[str, Any], reports=None) -> str:
    """WADS 검출 report들의 parameter를 distinct join → fail_type 슬롯 값.
    reports=선택된 1개 report 리스트면 그것만, None이면 전체(기존 동작=회귀안전).
    fail_history/relation_tree 체이닝의 검출 파라미터 입력. 없으면 ""(빈 값이면 dispatch
    missing_param HITL이 보완)."""
    params: list[str] = []
    for r in (reports if reports is not None else _latest_wads_reports(state)):
        p = str(r.get("parameter") or "").strip()
        if p and p not in params:
            params.append(p)
    return ",".join(params)


def _postwads_report_groups(state: Dict[str, Any], reports=None) -> list[dict]:
    """WADS report별 후속(fail_history/relation_tree) fan-out 입력.
    각 group = {lotcd, parameter, lot_ids}. cummap의 map_groups와 대칭 — 에이전트가
    group마다 따로 분석/검색한다(파라미터 뭉치기 금지). parameter 없는 report는 제외.
    reports=선택된 1개 report 리스트면 그것만, None이면 전체(기존 동작=회귀안전)."""
    groups: list[dict] = []
    for r in (reports if reports is not None else _latest_wads_reports(state)):
        param = str(r.get("parameter") or "").strip()
        if not param:
            continue
        groups.append({
            "lotcd": str(r.get("lotcd") or state.get("lotcd", "")).strip(),
            "parameter": param,
            "lot_ids": [str(x).strip() for x in (r.get("lot_ids") or []) if str(x).strip()],
        })
    return groups


def _build_postwads_fail_history_task(state: Dict[str, Any], task_plan: list, reports=None) -> dict:
    """WADS 검출을 report별로 불량이력 검색하는 단일 task (_build_postwads_map_task 대칭).
    report별 데이터는 fail_groups로 운반하고 fail_history_agent가 group마다 검색한다.
    reports=선택된 1개 report 리스트면 그것만, None이면 전체(기존 동작=회귀안전).
    top-level lotcd/fail_type은 dispatch 가드/요약용·fallback(groups 없으면 단일 검색)."""
    lotcd = state.get("lotcd", "")
    fail_type = _postwads_detected_params(state, reports)
    groups = _postwads_report_groups(state, reports)
    slots: dict[str, Any] = {"lotcd": lotcd, "fail_type": fail_type}
    if groups:
        slots["fail_groups"] = groups
    return build_task_from_canonical_request(
        {
            "intent": "fail_history_search",
            "agent": "fail_history_agent",
            "slots": slots,
            "goal": f"{lotcd} {fail_type or '검출 파라미터'} 불량이력 검색".strip(),
        },
        task_id=f"task_{len(task_plan) + 1}_fail_history",
    )


def _build_postwads_relation_tree_task(state: Dict[str, Any], task_plan: list, reports=None) -> dict:
    """WADS 검출을 report별로 Inline-WT 연관 분석하는 단일 task (_build_postwads_map_task 대칭).
    report별 데이터는 rt_groups로 운반하고 relation_tree_agent가 group마다 분석한다.
    reports=선택된 1개 report 리스트면 그것만, None이면 전체(기존 동작=회귀안전).
    top-level lotcd/fail_type은 dispatch required(map_oper 외 lotcd+fail_type) 가드 충족용."""
    lotcd = state.get("lotcd", "")
    fail_type = _postwads_detected_params(state, reports)
    groups = _postwads_report_groups(state, reports)
    slots: dict[str, Any] = {"lotcd": lotcd, "fail_type": fail_type}
    if groups:
        slots["rt_groups"] = groups
    return build_task_from_canonical_request(
        {
            "intent": "relation_tree",
            "agent": "relation_tree_agent",
            "slots": slots,
            "goal": f"{lotcd} {fail_type or '검출 파라미터'} Inline-WT 연관 분석".strip(),
        },
        task_id=f"task_{len(task_plan) + 1}_relation_tree",
    )


# 후속 라우팅 테이블 — value(선택지)→구체 task 빌더. 검출 파라미터(fail_type)/lotcd는
# state에서 빌더가 채우고, 부족분은 dispatch missing_param HITL이 보완한다.
_POSTWADS_ROUTES = {
    "map_agent": _build_postwads_map_task,
    "fail_history_agent": _build_postwads_fail_history_task,
    "relation_tree_agent": _build_postwads_relation_tree_task,
}

# interrupt 선택지. value는 _POSTWADS_ROUTES 키 또는 'none'(안 함).
_POSTWADS_OPTIONS = [
    {"label": "cummap/binmap 맵 조회", "value": "map_agent"},
    {"label": "불량이력 조회", "value": "fail_history_agent"},
    {"label": "LOTCD 연계 분석", "value": "relation_tree_agent"},
    {"label": "안 함", "value": "none"},
]

_POSTWADS_CHOICE_SYSTEM = (
    "WADS 검출 결과로 이어서 실행할 후속 작업 선택지를 사용자에게 제시했다. 아래 JSON은 제시된 "
    "선택지(options)와 사용자의 자유 응답(answer)이다. 사용자가 고른 선택지의 value를 정확히 하나만 "
    "출력해라. 아무것도 고르지 않거나 거절(안 함/취소/그만)이면 'none'을 출력해라. value 문자열 하나만 출력."
)


def _interpret_postwads_choice(answer: Any) -> str:
    """사용자 응답 → 선택된 route 키('map_agent' 등) 또는 ''(미선택/거절). 키워드 매칭 금지.
    UI 버튼 클릭(정확히 일치하는 value/label)은 LLM 없이 즉시 매핑하고, 자유응답만 LLM이 해석한다."""
    if isinstance(answer, dict):
        text = answer.get("postwads_choice") or next(iter(answer.values()), "")
    else:
        text = answer
    text = str(text or "").strip()
    if not text:
        return ""
    for opt in _POSTWADS_OPTIONS:  # UI 클릭 경로 — LLM 불필요
        if text == opt.get("value") or text == opt.get("label"):
            v = str(opt.get("value") or "")
            return v if v in _POSTWADS_ROUTES else ""
    try:  # 자유응답 → LLM 분류 (_interpret_confirmation과 동일 패턴, 키워드 금지)
        verdict = (
            _model.invoke(
                [
                    {"role": "system", "content": _POSTWADS_CHOICE_SYSTEM},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"options": _POSTWADS_OPTIONS, "answer": text},
                            ensure_ascii=False,
                        ),
                    },
                ],
                config={"callbacks": _lf_callbacks()},
            ).content
            or ""
        ).strip()
    except Exception as e:
        logger.warning("[PostWADS] 선택 해석 LLM 실패 (%s) — 미선택 처리", e)
        return ""
    return verdict if verdict in _POSTWADS_ROUTES else ""


# ── fail_type 1차 선택 (2-step HITL의 첫 단계) ───────────────────────────────
# wads 검출 후 어떤 fail_type(=report)을 후속 분석할지 먼저 고른다. 선택값은 report 인덱스
# 문자열만 sentinel params(selected_idx)에 실어 운반하고, 재진입 시 _latest_wads_reports로 복원
# 한다 (messages가 single source of truth — probe로 두 interrupt 사이 인덱스 안정성 입증).
_POSTWADS_FAILTYPE_MESSAGE = "어느 fail_type의 후속을 분석할까요?"

_POSTWADS_FAILTYPE_SYSTEM = (
    "WADS 검출 결과의 fail_type 후속 선택지를 사용자에게 제시했다. 아래 JSON은 제시된 "
    "선택지(options: label/value)와 사용자의 자유 응답(answer)이다. 사용자가 고른 선택지의 "
    "value를 정확히 하나만 출력해라. 아무것도 고르지 않거나 거절(안 함/취소/그만)이면 'none'을 "
    "출력해라. value 문자열 하나만 출력."
)


def _postwads_failtype_options(state: Dict[str, Any]) -> list[dict]:
    """WADS report별 fail_type 선택지. label=`parameter @ map_oper`, value=report 인덱스 문자열.
    + 마지막에 '안 함'(value='none'). report 1개여도 항상 제시한다."""
    options: list[dict] = []
    for idx, r in enumerate(_latest_wads_reports(state)):
        param = str(r.get("parameter") or "").strip() or "(unknown)"
        oper = _normalize_map_oper(str(r.get("map_oper") or "")).strip()
        options.append({"label": f"{param} @ {oper}" if oper else param, "value": str(idx)})
    options.append({"label": "안 함", "value": "none"})
    return options


def _interpret_postwads_failtype(answer: Any, state: Dict[str, Any]) -> str:
    """사용자 응답 → 선택된 report 인덱스 문자열, 미선택/거절이면 ''. 키워드 매칭 금지.
    UI 클릭(value/label 정확일치)은 LLM 없이 즉시, 자유응답만 LLM이 인덱스 분류한다
    (_interpret_postwads_choice와 동일 패턴)."""
    options = _postwads_failtype_options(state)
    n_reports = len(_latest_wads_reports(state))
    if isinstance(answer, dict):
        text = answer.get("postwads_failtype") or next(iter(answer.values()), "")
    else:
        text = answer
    text = str(text or "").strip()
    if not text:
        return ""
    chosen = ""
    for opt in options:  # UI 클릭 경로 — LLM 불필요
        if text == opt.get("value") or text == opt.get("label"):
            chosen = str(opt.get("value") or "")
            break
    if not chosen:  # 자유응답 → LLM 분류
        try:
            chosen = (
                _model.invoke(
                    [
                        {"role": "system", "content": _POSTWADS_FAILTYPE_SYSTEM},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"options": options, "answer": text},
                                ensure_ascii=False,
                            ),
                        },
                    ],
                    config={"callbacks": _lf_callbacks()},
                ).content
                or ""
            ).strip()
        except Exception as e:
            logger.warning("[PostWADS] fail_type 해석 LLM 실패 (%s) — 미선택 처리", e)
            return ""
    if chosen == "none":
        return ""
    try:
        idx = int(chosen)
    except (TypeError, ValueError):
        return ""
    return chosen if 0 <= idx < n_reports else ""


def _maybe_propose_postwads_choice(state: Dict[str, Any]) -> dict:
    """결정론적 후속 제안: wads가 파라미터를 검출하면 후속 선택 sentinel task를 큐에 추가한다.
    턴당 1회(postwads_offered) + 다운스트림(map/fail_history/relation_tree)이 이미 계획/큐에 있으면
    재제안하지 않는다. 실제 선택 interrupt는 supervisor가 dispatch 직전 _choose_postwads_or_drop로 한다.
    반환: pending_tasks/task_plan(append) + postwads_offered 업데이트 dict, 제안 없으면 {}."""
    if state.get("postwads_offered"):
        return {}
    wads_data = _latest_wads_result(state)
    detected = (
        int(wads_data.get("detected_count") or 0)
        or len(wads_data.get("lot_ids") or [])
        or len(wads_data.get("groupkeys") or [])
    )
    if not detected:
        return {}
    pending = state.get("pending_tasks") or []
    task_plan = state.get("task_plan") or []
    downstream = {"map_agent", "fail_history_agent", "relation_tree_agent"}
    if any(
        isinstance(t, dict) and t.get("agent") in downstream
        for t in (pending + task_plan)
    ):
        return {}
    task_id = f"task_{len(task_plan) + 1}_postwads_choice"
    sentinel = {
        "task_id": task_id,
        "agent": "__postwads_choice__",
        "goal": "WADS 검출 후속 선택",
        "params": {},
    }
    logger.info("[Replanner] WADS 검출 → 후속 선택 sentinel 제안: %s", task_id)
    return {
        "pending_tasks": pending + [sentinel],
        "task_plan": task_plan + [sentinel],
        "postwads_offered": True,
    }


def _drop_postwads_sentinel(
    remaining: list[dict], state: Dict[str, Any], step_count: int
) -> Command:
    """후속 미선택/거절 → sentinel 드롭. remaining 있으면 supervisor 재진입, 없으면 직전 결과로 턴 종료.
    sentinel(+selected_idx)은 remaining에 없으므로 pending에서 빠지며 selected_idx도 함께 소멸한다."""
    logger.info("[PostWADS] 후속 미선택 → sentinel 드롭 (remaining=%d)", len(remaining))
    if remaining:
        return Command(
            update={
                "step_count": step_count,
                "pending_tasks": remaining,
                "postwads_offered": True,
            },
            goto="supervisor",
        )
    last_agent_msg = next(
        (
            m.content
            for m in reversed(state.get("messages", []))
            if isinstance(m, AIMessage)
            and getattr(m, "name", "") in _AGENT_NAMES
            and isinstance(m.content, str)
            and m.content.strip()
        ),
        "분석을 완료했습니다.",
    )
    return Command(
        update={
            "step_count": step_count,
            "pending_tasks": [],
            "postwads_offered": True,
            "response": last_agent_msg,
        },
        goto=END,
    )


def _choose_postwads_or_drop(
    current_task: dict, remaining: list[dict], state: Dict[str, Any], step_count: int
) -> Command:
    """dispatch 직전 후속 선택 처리 (_confirm_or_drop과 같은 멱등 구간 — 무거운 부수효과 이전).
    2-step HITL을 super-step 분리로 처리한다 (한 노드 1 interrupt + 1 LLM = replay-safe):
      - 1차: fail_type 선택. 선택 시 인덱스만 sentinel params(selected_idx)에 실어 commit 후 재진입.
      - 2차: selected_idx가 있으면 분석종류 선택 → 그 1개 report로만 구체 task 빌드(치환).
      - 어느 단계든 미선택/거절 → sentinel 드롭."""
    task_plan = state.get("task_plan") or []
    params = current_task.get("params") or {}
    selected_idx = params.get("selected_idx")
    reports_all = _latest_wads_reports(state)

    # ── super-step 1: fail_type 선택 (selected_idx 미확정 & 고를 report가 있을 때만) ──
    # report breakdown이 없으면(detected는 lot_ids/groupkeys로만) 고를 게 없으므로 건너뛰고
    # 바로 분석종류 선택으로 간다(reports=None fallback = 기존 동작, 회귀안전).
    if selected_idx is None and reports_all:
        ft_answer = interrupt({
            "type": "confirm",
            "interrupt_type": "postwads_choice",  # 기존 타입 재사용 → agent_server 가드/resume 그대로
            "param": "postwads_failtype",
            "message": _POSTWADS_FAILTYPE_MESSAGE,
            "options": _postwads_failtype_options(state),
            "route": "",
        })
        chosen_idx = _interpret_postwads_failtype(ft_answer, state)
        if not chosen_idx:
            return _drop_postwads_sentinel(remaining, state, step_count)
        # commit 후 재진입 (super-step 분리). sentinel엔 인덱스만 운반, task_plan은 안 건드림.
        requeued = {**current_task, "params": {**params, "selected_idx": chosen_idx}}
        logger.info("[PostWADS] fail_type 선택 idx=%s → 재진입", chosen_idx)
        return Command(
            update={
                "step_count": step_count,
                "pending_tasks": [requeued] + remaining,
            },
            goto="supervisor",
        )

    # ── super-step 2: 분석종류 선택 (selected_idx 확정, 또는 report 없어 1차 생략) ──
    # selected_idx로 report 복원. None(생략)/복원 실패 → reports=None 전체 fallback(회귀안전).
    selected = None
    if selected_idx is not None:
        try:
            selected = [reports_all[int(selected_idx)]]
        except (TypeError, ValueError, IndexError):
            selected = None
    answer = interrupt({
        "type": "confirm",
        "interrupt_type": "postwads_choice",
        "param": "postwads_choice",
        "message": _POSTWADS_CHOICE_MESSAGE,
        "options": _POSTWADS_OPTIONS,
        "route": "",
    })
    chosen = _interpret_postwads_choice(answer)
    if chosen in _POSTWADS_ROUTES:
        concrete = _POSTWADS_ROUTES[chosen](state, task_plan, selected)
        logger.info("[PostWADS] 선택=%s(idx=%s) → %s 큐 추가(치환)", chosen, selected_idx, concrete.get("task_id"))
        return Command(
            update={
                "step_count": step_count,
                "pending_tasks": [concrete] + remaining,
                "task_plan": task_plan + [concrete],
                "postwads_offered": True,
            },
            goto="supervisor",
        )
    return _drop_postwads_sentinel(remaining, state, step_count)


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
    wads_category: str  # 공정 필터 ("PT1H"/"PT1C") → CATEGORY(PT1H_TEST/PT1C_TEST)
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
    wf_mod: int  # wafer-number pattern divisor (짝수=2, 3배수=3 …); 0/absent = no filter
    wf_rem: int  # remainder for the pattern (짝수=0, 홀수=1, N배수=0)
    map_label: str  # display label for the cummap (e.g. #RN report parameter "JUNCTION")
    map_groups: list  # WADS report별 cummap fan-out [{parameter, map_oper, groupkeys}, …] (overwrite)

    # Fail History 파라미터
    dh_query: str
    fail_groups: list  # WADS report별 불량이력 fan-out [{lotcd, parameter, lot_ids}, …] (overwrite)

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

    # Relation Tree (Inline-WT 연관 분석)
    rt_groups: list  # WADS report별 연관분석 fan-out [{lotcd, parameter, lot_ids}, …] (overwrite)
    # Relation Tree 결과
    relation_tree_artifacts: Annotated[list, operator.add]

    # Mining (gini 기반 기여 파라미터 마이닝) — 상류 공유키 재사용:
    # lot_cd=lotcd, fail_name=fail_type, mode=wads_category (별도 키 없음).
    group_good: list  # 양품 그룹 식별자 (사용자 직접/상류 상속, overwrite)
    group_bad: list  # 불량 그룹 식별자 (사용자 직접/상류 상속, overwrite)
    tech: str  # 기술/공정 세대 코드
    user_id: str  # 요청 사용자 ID
    rank_limit: int  # 상위 N개 제한 (0/absent = 기본 10)

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

    # 모호 슬롯 (시나리오 2, overwrite): planner가 채우고 supervisor missing_param HITL이 소비.
    # 각 항목 {task_id, agent, slot, candidates:[...], reason}.
    ambiguous_slots: list[dict]

    # 실행 확인 대기 task (B안, overwrite): replanner가 {task_id: 확인메시지}를 채우고
    # supervisor가 dispatch 직전 interrupt로 확인 → 처리된 id는 제거. 신규 노드 없이 체이닝 확인.
    confirm_tasks: dict

    # Step 5: WADS 검출 후속 선택을 이번 turn에 이미 제안했는지 (overwrite, turn별 리셋).
    # replanner가 sentinel 제안 시 True로 세팅 → 같은 turn 재제안/무한 추가 방지.
    postwads_offered: bool

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
    pending_tasks: list[dict]  # 아직 실행 안 된 task dict들 (overwrite)
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

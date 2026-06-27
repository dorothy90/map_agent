"""node_planner — supervisor.py에서 분리(노드/응집 헬퍼). 자동 분할 codemod 생성."""
from __future__ import annotations


from datetime import date, timedelta
from typing import Any, Dict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langfuse import observe

from common import stream_event, extract_json_from_llm
from canonical_request import build_tasks_from_canonical_requests, normalize_canonical_request
from lf_utils import lf_callbacks as _lf_callbacks  # noqa: E402
from models import StatusEvent
from prompts import CANONICAL_PLANNER_SYSTEM_PROMPT
from task_normalizer_validator import apply_ordinal_ref
from local_trace import emit_runtime_detail, emit_trace_event, preview_text, summarize_tasks, task_flow

load_dotenv(override=True)

from orch_utils import _model, logger
from query_state import CanonicalPlanResponse
from recent_results import _accumulate_recent_results, _get_recent_turns, _recent_results_prompt_context




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

# Planner referent window: how many recent raw conversation turns the planner sees ONLY
# to resolve follow-up referents ("그거/처음 거/아까 그"). Kept tight on purpose — recent
# referents are near, and older raw turns mislead resolution; cross-turn DATA depth is
# carried by recent_results (K=10, shown in full via _recent_results_prompt_context), not
# raw turns. _MAX_CONTEXT_TOKENS still trims this further as a safety budget.
_PLANNER_REFERENT_TURNS = 3


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
    # S1-b: god-state lot_ids 제거. reference 해소는 위 recent_results(K=10 윈도우)가 단일 경로이며,
    # 별도 "이전 lot" 힌트(무윈도우 envelope)는 window 경계를 깨므로(beyond-window가 해소돼버림) 두지 않는다.
    # ("그 lot들" 결과-파생 체이닝은 _resolve_chained_params가 envelope에서 따로 처리한다.)
    if state.get("cause_oper"):
        meta_parts.append(f"이전 main_oper: {state['cause_oper']}")
    if state.get("selected_fail_type"):
        meta_parts.append(f"직전 선택 파라미터(fail_type): {state['selected_fail_type']}")
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

"""node_replanner — supervisor.py에서 분리(노드/응집 헬퍼). 자동 분할 codemod 생성."""
from __future__ import annotations


import json
from datetime import date
from typing import Any, Dict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langfuse import observe

from common import stream_event, extract_json_from_llm
from canonical_request import build_task_from_canonical_request, build_tasks_from_canonical_requests, canonical_requests_from_tasks, normalize_canonical_request
from lf_utils import lf_callbacks as _lf_callbacks  # noqa: E402
from models import StatusEvent
from prompts import REPLANNER_SYSTEM_PROMPT
from local_trace import emit_trace_event, summarize_result_envelope

load_dotenv(override=True)

from orch_utils import _AGENT_NAMES, _is_placeholder_or_empty, _model, logger
from query_state import CanonicalPlanResponse
from recent_results import _latest_result_envelope_for_task, _recent_results_update
from wads_context import _latest_wads_result, _resolve_chained_params, _wads_groupkeys_by_map_oper




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

    # ── 선언적 후속 제안 (S2): 결과 envelope.followups를 읽어 후속을 큐에 추가 ──
    # 완료판정보다 먼저 실행 — pending이 비어도 후속을 넣어 턴이 조기 종료되지 않게 한다.
    # task_plan에도 append해 완료판정(아래)·max_steps 정합을 맞춘다.
    # 모든 후속(yield→wads / postwads / relation→main_oper / wt_resp→mining)이 선언적으로
    # 통합됨 — 각각 wads/relation_tree/wt_resp envelope이 followups로 선언한다.
    declared = _propose_followups(state)
    if declared:
        return {**scratchpad_update, **declared}

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


# ── S2: 선언적 followup (envelope.followups → propose/resolve) ─────────────────
# 후속 트리거 판단을 supervisor에서 결과 생성 에이전트로 옮긴다. 에이전트가 자기 결과
# envelope.followups에 후속을 선언하면(이상감지/검출수/main_oper/group 산출 여부는 에이전트가
# 안다), replanner가 _propose_followups로 enqueue, supervisor가 dispatch 직전
# _resolve_followup_or_drop로 confirm/choice interrupt를 해소한다. 모든 후속(yield→wads /
# postwads 2-step / relation→main_oper / wt_resp→mining)이 이 선언적 경로로 통합됐다
# (confirm 해소는 _confirm_or_drop, 1-step/2-step 택1은 _resolve_single_choice 재사용).
def _latest_result_followups(state: Dict[str, Any]) -> list[dict]:
    """가장 최근 에이전트 결과 envelope의 followups를 읽는다(없으면 []).
    replanner는 직전 에이전트 완료 직후 돌므로 latest agent message가 방금 끝난 에이전트다."""
    for message in reversed(state.get("messages", []) or []):
        if isinstance(message, AIMessage) and getattr(message, "name", "") in _AGENT_NAMES:
            result = (getattr(message, "additional_kwargs", None) or {}).get("result")
            if isinstance(result, dict) and isinstance(result.get("followups"), list):
                return [f for f in result["followups"] if isinstance(f, dict)]
            return []
    return []


def _propose_followups(state: Dict[str, Any]) -> dict:
    """선언적 후속 제안(모든 후속 체인 공통). 최신 결과 envelope의 followups를 읽어
    guard로 중복 차단 후 pending/task_plan에 추가한다:
      - confirm: agent+default_slots로 구체 task를 만들고 confirm_tasks 등록(_confirm_or_drop이 해소).
      - choice : __choice__ sentinel(followup 스펙을 params로 운반)로 추가(_resolve_followup_or_drop이 해소).
    guard: guard_key(턴당 1회 state 플래그) + guard_agents(이미 plan에 있으면 제안 안 함=중복 차단).
    반환: pending_tasks/task_plan(append)/confirm_tasks/guard_key 업데이트 dict, 제안 없으면 {}."""
    followups = _latest_result_followups(state)
    if not followups:
        return {}
    pending = list(state.get("pending_tasks") or [])
    task_plan = list(state.get("task_plan") or [])
    confirm_tasks = dict(state.get("confirm_tasks") or {})
    planned_agents = {t.get("agent") for t in (pending + task_plan) if isinstance(t, dict)}
    extra_updates: dict = {}
    added = False
    for fu in followups:
        guard_key = str(fu.get("guard_key") or "")
        if guard_key and state.get(guard_key):
            continue
        guard_agents = [str(a) for a in (fu.get("guard_agents") or [])]
        if guard_agents and any(a in planned_agents for a in guard_agents):
            continue
        agent = str(fu.get("agent") or "")
        if not agent:
            continue
        if agent == "__choice__":
            task_id = f"task_{len(task_plan) + 1}_choice"
            task = {
                "task_id": task_id,
                "agent": "__choice__",
                "goal": str(fu.get("goal") or "후속 선택"),
                "params": {"followup": fu},
            }
        else:
            task = build_task_from_canonical_request(
                {
                    "agent": agent,
                    "slots": dict(fu.get("default_slots") or {}),
                    "goal": str(fu.get("goal") or ""),
                },
                task_id=f"task_{len(task_plan) + 1}_{agent}",
            )
            task_id = task["task_id"]
            if fu.get("confirm"):
                confirm_tasks[task_id] = str(
                    fu.get("confirm_message") or "후속 작업을 실행하시겠습니까?"
                )
        pending.append(task)
        task_plan.append(task)
        planned_agents.add(agent)
        if guard_key:
            extra_updates[guard_key] = True
        logger.info(
            "[Followup] 제안: %s agent=%s confirm=%s", task_id, agent, bool(fu.get("confirm"))
        )
        added = True
    if not added:
        return {}
    return {
        "pending_tasks": pending,
        "task_plan": task_plan,
        "confirm_tasks": confirm_tasks,
        **extra_updates,
    }

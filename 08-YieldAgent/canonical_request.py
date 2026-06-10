from __future__ import annotations

from typing import Any


AGENT_SLOT_RULES: dict[str, dict[str, Any]] = {
    "yield_agent": {
        "allowed": {"lotcd", "ref_date", "unit", "periods"},
        "required": ("lotcd",),
    },
    "wads_agent": {
        "allowed": {"lotcd", "wads_start_tm", "wads_end_tm", "fail_type"},
        "required": (),
    },
    "map_agent": {
        "allowed": {"lot_ids", "wf_ids", "groupkey", "map_type", "map_oper", "wf_mod", "wf_rem"},
        "required": ("map_oper",),
        "required_any": (("lot_ids", "groupkey"),),
    },
    "fail_history_agent": {
        "allowed": {"lotcd", "fail_type", "cause_oper", "dh_query"},
        "required": (),
    },
    "lot_history_agent": {
        "allowed": {"lot_ids"},
        "required": ("lot_ids",),
    },
    "relation_tree_agent": {
        # TEMP(relation_tree fail_type): fail_type is now the analyzed parameter (required
        # with lotcd). Without it in `allowed`, _clean_slots dropped a "#N" ordinal token
        # before apply_ordinal_ref could resolve it -> silent interrupt. cause_oper stays optional.
        "allowed": {"lotcd", "fail_type", "cause_oper"},
        "required": ("lotcd", "fail_type"),
    },
    "ppt_export": {
        "allowed": set(),
        "required": (),
    },
}
AGENT_SLOT_SCHEMAS: dict[str, set[str]] = {
    agent: set(rules["allowed"])
    for agent, rules in AGENT_SLOT_RULES.items()
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _clean_slots(agent: str, slots: dict[str, Any]) -> dict[str, Any]:
    allowed = AGENT_SLOT_SCHEMAS.get(agent, set())
    return {key: value for key, value in slots.items() if key in allowed and value not in (None, "", [])}


def normalize_canonical_request(canonical_request: dict[str, Any]) -> dict[str, Any]:
    agent = _clean_text(canonical_request.get("agent"))
    intent = _clean_text(canonical_request.get("intent"))
    slots = _clean_slots(agent, dict(canonical_request.get("slots") or {}))
    goal = _clean_text(canonical_request.get("goal")) or f"{intent} 실행"
    if goal == f"{intent} 실행":
        goal = _canonical_goal(intent, agent, slots)
    normalized = {
        "intent": intent,
        "agent": agent,
        "slots": slots,
        "goal": goal,
    }
    source = canonical_request.get("source")
    if isinstance(source, dict) and source:
        normalized["source"] = source
    return normalized


def build_task_from_canonical_request(
    canonical_request: dict[str, Any],
    *,
    task_id: str = "task_1",
) -> dict[str, Any]:
    normalized = normalize_canonical_request(canonical_request)
    return {
        "task_id": task_id,
        "agent": normalized["agent"],
        "params": normalized["slots"],
        "goal": normalized["goal"],
    }


def build_tasks_from_canonical_requests(
    canonical_requests: list[dict[str, Any]],
    *,
    task_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for index, canonical_request in enumerate(canonical_requests or [], start=1):
        normalized = normalize_canonical_request(canonical_request)
        if not normalized["agent"] or not normalized["intent"]:
            continue
        task_id = task_ids[index - 1] if task_ids and index <= len(task_ids) else f"task_{index}"
        tasks.append(build_task_from_canonical_request(normalized, task_id=task_id))
    return tasks


def _default_intent_for_agent(agent: str) -> str:
    if agent == "yield_agent":
        return "yield_query"
    if agent == "wads_agent":
        return "wads_report"
    if agent == "map_agent":
        return "map"
    if agent == "fail_history_agent":
        return "fail_history_search"
    if agent == "lot_history_agent":
        return "lot_history"
    if agent == "relation_tree_agent":
        return "relation_tree"
    if agent == "ppt_export":
        return "ppt_export"
    return ""


def canonical_request_from_task(task: dict[str, Any]) -> dict[str, Any]:
    agent = _clean_text(task.get("agent"))
    goal = _clean_text(task.get("goal"))
    return normalize_canonical_request({
        "intent": _clean_text(task.get("intent")) or _default_intent_for_agent(agent),
        "agent": agent,
        "slots": dict(task.get("params") or {}),
        "goal": goal,
        "source": {
            "type": "task_contract",
            "task_id": _clean_text(task.get("task_id")),
        },
    })


def canonical_requests_from_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        canonical_request_from_task(task)
        for task in tasks or []
        if isinstance(task, dict)
    ]


def _canonical_goal(intent: str, agent: str, slots: dict[str, Any]) -> str:
    if agent == "yield_agent":
        product = _clean_text(slots.get("lotcd")) or "지정 제품"
        return f"{product} 수율 조회"
    if agent == "wads_agent":
        product = _clean_text(slots.get("lotcd")) or "전체 제품"
        parameter = _clean_text(slots.get("fail_type")) or "전체 파라미터"
        start = _clean_text(slots.get("wads_start_tm"))
        end = _clean_text(slots.get("wads_end_tm"))
        period = f"{start}~{end} " if start and end else ""
        suffix = "리포트 조회" if intent == "wads_report" else "파라미터별 검출 건수 목록 조회"
        return f"{period}{product} {parameter} WADS {suffix}".strip()
    if agent == "fail_history_agent":
        product = _clean_text(slots.get("lotcd"))
        parameter = _clean_text(slots.get("fail_type")) or "지정 파라미터"
        return f"{product + ' ' if product else ''}{parameter} 불량이력 검색".strip()
    if agent == "map_agent":
        lot_ids = ",".join(_clean_text(v) for v in _as_list(slots.get("lot_ids")) if _clean_text(v))
        groupkey = _clean_text(slots.get("groupkey"))
        target = lot_ids or groupkey or "지정 wafer"
        map_type = _clean_text(slots.get("map_type")) or "map"
        map_oper = _clean_text(slots.get("map_oper"))
        return f"{target} {map_oper + ' ' if map_oper else ''}{map_type} 조회".strip()
    if agent == "lot_history_agent":
        lot_ids = ",".join(_clean_text(v) for v in _as_list(slots.get("lot_ids")) if _clean_text(v))
        return f"{lot_ids} LOT 이력 조회".strip()
    if agent == "relation_tree_agent":
        lotcd = _clean_text(slots.get("lotcd")) or "지정 LOT"
        cause_oper = _clean_text(slots.get("cause_oper")) or "지정 공정"
        return f"{lotcd} {cause_oper} Inline-WT 연관 분석".strip()
    if agent == "ppt_export":
        return "분석 결과 PPT 생성"
    return f"{intent} 실행"

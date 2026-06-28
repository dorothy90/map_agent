"""wads_context — supervisor.py에서 분리(노드/응집 헬퍼). 자동 분할 codemod 생성."""
from __future__ import annotations


import re
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import AIMessage

from canonical_request import build_tasks_from_canonical_requests, canonical_requests_from_tasks
from task_normalizer_validator import UNRESOLVED_REF

load_dotenv(override=True)

from orch_utils import _groupkey_list, _is_placeholder_or_empty, _normalize_map_oper, _unique_texts, logger




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

    # 활성 fail_type(이번 턴 map task에 실린 단일 파라미터)이 있으면 그 parameter가 속한
    # map_oper로 fan-out을 스코핑한다. 예) FMAX(X)는 PT1C_TEST 전용 → cummap을 PT1C 1개로 한정
    # (선택 안 했거나 매칭 report 없으면 기존 전체 fan-out 유지). reports의 parameter→map_oper
    # 데이터 매핑만 사용(키워드 매핑 아님). god-state 폐기: 글로벌이 아닌 task.params에서 읽는다.
    fail_type = ""
    for _t in tasks:
        _fp = str((_t.get("params") or {}).get("fail_type") or "").strip()
        if _fp:
            fail_type = _fp
            break
    if fail_type and "," not in fail_type:
        allowed_opers = {
            _normalize_map_oper(str(r.get("map_oper") or "")) or _map_oper_from_wads_row(r)
            for r in _latest_wads_reports(state)
            if str(r.get("parameter") or "").strip().upper() == fail_type.upper()
        }
        allowed_opers.discard("")
        scoped = {o: gk for o, gk in groups.items() if o in allowed_opers}
        if scoped and scoped != groups:
            trace.append({
                "event": "recent_wads_map_oper_scoped_by_fail_type",
                "fail_type": fail_type,
                "map_opers": list(scoped.keys()),
                "dropped": [o for o in groups if o not in scoped],
            })
            groups = scoped

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

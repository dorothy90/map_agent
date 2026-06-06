"""Task normalization and validation for planner output.

Phase 5 keeps this layer deliberately narrow:
- normalizer: field-name aliases only
- validator: allowed/required params, resolved_refs application, issues

No HITL, no semantic guessing, and no agent business logic lives here.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from canonical_request import (
    AGENT_SLOT_RULES,
    build_tasks_from_canonical_requests,
    canonical_request_from_task,
)


FIELD_ALIASES = {
    "map_lot_ids": "lot_ids",
    "dh_fail_type": "fail_type",
}


AGENT_PARAM_RULES: dict[str, dict[str, Any]] = AGENT_SLOT_RULES


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def normalize_task_fields(tasks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize planner task field aliases.

    The normalizer only renames fields. It does not coerce values, default
    missing params, validate allowed params, or infer semantics.
    """

    normalized_tasks: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []

    for task in tasks or []:
        normalized_task = deepcopy(task)
        params = dict(normalized_task.get("params") or {})
        normalized_params: dict[str, Any] = {}

        for key, value in params.items():
            canonical = FIELD_ALIASES.get(key)
            if not canonical:
                normalized_params[key] = value
                continue

            if canonical in params or canonical in normalized_params:
                normalized_params[key] = value
                trace.append({
                    "event": "field_alias_conflict",
                    "task_id": normalized_task.get("task_id", ""),
                    "agent": normalized_task.get("agent", ""),
                    "from": key,
                    "to": canonical,
                })
                continue

            normalized_params[canonical] = value
            trace.append({
                "event": "field_alias_normalized",
                "task_id": normalized_task.get("task_id", ""),
                "agent": normalized_task.get("agent", ""),
                "from": key,
                "to": canonical,
            })

        normalized_task["params"] = normalized_params
        normalized_tasks.append(normalized_task)

    return normalized_tasks, trace


def _report_ref_params(report: dict[str, Any], base_params: dict[str, Any]) -> dict[str, Any]:
    params = dict(base_params)
    groupkeys = [str(v).strip() for v in _as_list(report.get("groupkeys")) if str(v).strip()]
    if groupkeys:
        params["groupkey"] = ",".join(groupkeys)
        params.pop("lot_ids", None)
        params.pop("wf_ids", None)

    map_oper = str(report.get("map_oper") or "").strip().upper()
    if map_oper and _is_empty(params.get("map_oper")):
        params["map_oper"] = map_oper

    return params


def _remove_redundant_wads_tasks_for_report_refs(
    tasks: list[dict[str, Any]],
    trace: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not any(task.get("agent") == "map_agent" for task in tasks):
        return tasks

    retained: list[dict[str, Any]] = []
    for task in tasks:
        if task.get("agent") == "wads_agent":
            trace.append({
                "event": "resolved_report_ref_removed_redundant_source_task",
                "task_id": task.get("task_id", ""),
                "agent": "wads_agent",
                "reason": "report_refs_already_resolved",
            })
            continue
        retained.append(task)
    return retained


def apply_report_refs_to_map_tasks(
    tasks: list[dict[str, Any]],
    resolved_refs: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply deterministic report ordinal refs to map tasks.

    This only acts on explicit ReferenceResolver output such as "1,2번째 리포트".
    It does not infer report meaning from free text or choose results by recency.
    """

    reports = (resolved_refs or {}).get("reports") or []
    if not reports:
        return tasks, []

    updated_tasks = deepcopy(tasks or [])
    trace: list[dict[str, Any]] = []
    map_indexes = [
        index
        for index, task in enumerate(updated_tasks)
        if task.get("agent") == "map_agent"
    ]
    if not map_indexes:
        return updated_tasks, trace

    if len(reports) > 1 and len(map_indexes) == 1:
        index = map_indexes[0]
        base_task = updated_tasks[index]
        base_request = canonical_request_from_task(base_task)
        expanded_requests: list[dict[str, Any]] = []
        task_ids: list[str] = []
        for report in reports:
            ordinal = report.get("ordinal")
            map_type = str((base_task.get("params") or {}).get("map_type") or "map")
            goal = f"리포트 {ordinal} wafer {map_type}"
            expanded_requests.append({
                **base_request,
                "slots": _report_ref_params(report, dict(base_task.get("params") or {})),
                "goal": goal,
                "source": {
                    **dict(base_request.get("source") or {}),
                    "type": "resolved_report_ref",
                    "report_ordinal": ordinal,
                },
            })
            task_ids.append(f"{base_task.get('task_id', 'task_map')}_r{ordinal}")
            trace.append({
                "event": "resolved_report_ref_fanout",
                "task_id": task_ids[-1],
                "agent": "map_agent",
                "report_ordinal": ordinal,
                "groupkey_count": len(report.get("groupkeys") or []),
            })
        expanded = build_tasks_from_canonical_requests(expanded_requests, task_ids=task_ids)
        updated_tasks = updated_tasks[:index] + expanded + updated_tasks[index + 1:]
        updated_tasks = _remove_redundant_wads_tasks_for_report_refs(updated_tasks, trace)
        return updated_tasks, trace

    for task_index, report in zip(map_indexes, reports):
        task = updated_tasks[task_index]
        previous = dict(task.get("params") or {})
        request = canonical_request_from_task(task)
        request["slots"] = _report_ref_params(report, previous)
        updated_tasks[task_index] = build_tasks_from_canonical_requests(
            [request],
            task_ids=[str(task.get("task_id") or f"task_{task_index + 1}")],
        )[0]
        trace.append({
            "event": "resolved_report_ref_applied",
            "task_id": task.get("task_id", ""),
            "agent": "map_agent",
            "report_ordinal": report.get("ordinal"),
            "groupkey_count": len(report.get("groupkeys") or []),
            "previous": previous,
        })

    updated_tasks = _remove_redundant_wads_tasks_for_report_refs(updated_tasks, trace)
    return updated_tasks, trace


def _apply_resolved_refs(
    task: dict[str, Any],
    params: dict[str, Any],
    resolved_refs: dict[str, Any],
) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    agent = task.get("agent", "")

    if agent in {"map_agent", "lot_history_agent"}:
        lot_ids = [str(v).strip() for v in _as_list(resolved_refs.get("lot_ids")) if str(v).strip()]
        if lot_ids:
            previous = params.get("lot_ids")
            if agent == "map_agent" and not _is_empty(params.get("groupkey")):
                trace.append({
                    "event": "resolved_ref_skipped",
                    "task_id": task.get("task_id", ""),
                    "agent": agent,
                    "param": "lot_ids",
                    "source": "resolved_refs.lot_ids",
                    "reason": "groupkey_is_more_specific_selector",
                })
                return trace
            params["lot_ids"] = lot_ids
            trace.append({
                "event": "resolved_ref_applied" if _is_empty(previous) else "resolved_ref_overrode",
                "task_id": task.get("task_id", ""),
                "agent": agent,
                "param": "lot_ids",
                "source": "resolved_refs.lot_ids",
                "previous": previous,
            })

    if agent in {"wads_agent", "fail_history_agent"}:
        parameters = [str(v).strip() for v in _as_list(resolved_refs.get("parameters")) if str(v).strip()]
        if parameters:
            previous = params.get("fail_type")
            params["fail_type"] = parameters[0]
            trace.append({
                "event": "resolved_ref_applied" if _is_empty(previous) else "resolved_ref_overrode",
                "task_id": task.get("task_id", ""),
                "agent": agent,
                "param": "fail_type",
                "source": "resolved_refs.parameters",
                "previous": previous,
            })

    row = {}
    for key in ("parameter", "lot"):
        ref = resolved_refs.get(key)
        if isinstance(ref, dict) and isinstance(ref.get("row"), dict):
            row = ref["row"]
            break
    if row and agent in {"wads_agent", "fail_history_agent", "yield_agent", "relation_tree_agent"} and _is_empty(params.get("lotcd")):
        for row_key in ("lotcd", "lot_cd", "product"):
            value = row.get(row_key)
            if value not in (None, ""):
                params["lotcd"] = str(value).strip()
                trace.append({
                    "event": "resolved_ref_applied",
                    "task_id": task.get("task_id", ""),
                    "agent": agent,
                    "param": "lotcd",
                    "source": f"resolved_refs.row.{row_key}",
                })
                break

    if row and agent == "wads_agent":
        for param, row_keys in {
            "wads_end_tm": ("end_tm", "date", "created_at"),
            "wads_start_tm": ("start_tm",),
        }.items():
            if not _is_empty(params.get(param)):
                continue
            for row_key in row_keys:
                value = row.get(row_key)
                if value not in (None, ""):
                    params[param] = str(value).strip()
                    trace.append({
                        "event": "resolved_ref_applied",
                        "task_id": task.get("task_id", ""),
                        "agent": agent,
                        "param": param,
                        "source": f"resolved_refs.row.{row_key}",
                    })
                    break

    return trace


def validate_tasks(
    tasks: list[dict[str, Any]],
    *,
    resolved_refs: dict[str, Any] | None = None,
    reference_issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply deterministic resolved_refs to tasks; surface unresolved references.

    Step 3 removed the deterministic gates that lived here:
    - allow-list param dropping / value blanking — agents ignore unknown params,
      and the supervisor validates required params at dispatch (no double work).
    - missing_param gating — same dispatch-time supervisor guard covers it.
    - cost/fanout gating — the planner already caps plans at _MAX_TASKS.
    Reference application (_apply_resolved_refs) stays until reference_resolver is
    retired (Step 5); unresolved references are still surfaced as blocking issues.
    """

    resolved_refs = resolved_refs or {}
    issues: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []

    for ref_issue in reference_issues or []:
        issues.append({
            "type": "ambiguous_reference",
            "severity": "error",
            "blocking": True,
            "reason": ref_issue.get("reason", "reference_resolution_failed"),
            "reference": ref_issue.get("reference", ""),
            "candidates": ref_issue.get("candidates", []),
            "candidate_options": ref_issue.get("candidate_options", []),
        })

    validated_tasks: list[dict[str, Any]] = []
    for task in tasks or []:
        validated_task = deepcopy(task)
        params = dict(validated_task.get("params") or {})
        trace.extend(_apply_resolved_refs(validated_task, params, resolved_refs))
        validated_task["params"] = params
        validated_tasks.append(validated_task)

    return {
        "tasks": validated_tasks,
        "issues": issues,
        "trace": trace,
    }


def has_blocking_issues(issues: list[dict[str, Any]]) -> bool:
    return any(issue.get("blocking", issue.get("severity", "error") == "error") for issue in issues or [])


def format_task_issues_for_user(issues: list[dict[str, Any]]) -> str:
    blocking = [issue for issue in issues if issue.get("severity", "error") == "error"]
    if not blocking:
        return ""

    lines = ["작업 계획을 실행하기 전에 필요한 입력을 확인해야 합니다."]
    for issue in blocking:
        task = issue.get("task_id", "")
        agent = issue.get("agent", "")
        param = issue.get("param", "")
        reason = issue.get("reason", "")
        label = f"{task}/{agent}" if task or agent else "reference"
        if issue.get("type") == "missing_param":
            lines.append(f"- {label}: `{param}` 누락 ({reason})")
        elif issue.get("type") == "ambiguous_reference":
            reference = issue.get("reference", "")
            options = issue.get("candidate_options") or []
            if options:
                labels = [
                    f"{option.get('index')}. {option.get('label')}"
                    for option in options
                    if isinstance(option, dict) and option.get("label")
                ]
                suffix = f" 후보: {'; '.join(labels)}" if labels else ""
                lines.append(f"- 참조 표현 `{reference}` 확인 필요 ({reason}).{suffix}")
            else:
                lines.append(f"- 참조 표현 `{reference}` 확인 필요 ({reason})")
        else:
            lines.append(f"- {label}: `{param}` 오류 ({reason})")
    return "\n".join(lines)

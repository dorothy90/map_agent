"""node_normalizer — supervisor.py에서 분리(노드/응집 헬퍼). 자동 분할 codemod 생성."""
from __future__ import annotations


import json
from typing import Any, Dict

from dotenv import load_dotenv
from langchain_core.runnables import RunnableConfig
from langfuse import observe

from canonical_request import canonical_requests_from_tasks
from task_normalizer_validator import normalize_task_fields, validate_tasks
from local_trace import emit_runtime_detail, emit_trace_event, summarize_trace_value

load_dotenv(override=True)

from orch_utils import logger
from wads_context import _apply_recent_wads_to_map_tasks


def _trace_tasks(tasks: list[dict[str, Any]], *, redact: bool) -> list[dict[str, Any]]:
    if not redact:
        return tasks
    return [
        {
            "task_id": str(task.get("task_id") or ""),
            "agent": str(task.get("agent") or ""),
            "param_keys": sorted(str(key) for key in (task.get("params") or {})),
            "params": summarize_trace_value(task.get("params") or {}),
            "goal": summarize_trace_value(task.get("goal") or ""),
        }
        for task in tasks
        if isinstance(task, dict)
    ]




@observe(
    name="task_normalizer_validator_node",
    capture_input=False,
    capture_output=False,
)
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
    redact_trace = bool(state.get("wiki_context"))

    normalized_tasks, normalization_trace = normalize_task_fields(tasks)
    normalized_tasks, recent_wads_trace = _apply_recent_wads_to_map_tasks(
        normalized_tasks,
        state,
    )
    normalization_trace.extend(recent_wads_trace)
    trace_tasks = _trace_tasks(tasks, redact=redact_trace)
    emit_runtime_detail("normalizer.input", {"tasks": trace_tasks})
    validation = validate_tasks(normalized_tasks)
    trace_normalized_tasks = _trace_tasks(
        normalized_tasks, redact=redact_trace
    )
    trace_normalization = (
        [summarize_trace_value(event) for event in normalization_trace]
        if redact_trace
        else normalization_trace
    )
    trace_validation = (
        [summarize_trace_value(event) for event in validation.get("trace", [])]
        if redact_trace
        else validation.get("trace", [])
    )
    trace_issues = (
        [summarize_trace_value(issue) for issue in validation.get("issues", [])]
        if redact_trace
        else validation.get("issues", [])
    )
    emit_runtime_detail(
        "normalizer.output",
        {
            "normalized_tasks": trace_normalized_tasks,
            "normalization_trace": trace_normalization,
            "validation_trace": trace_validation,
            "issues": trace_issues,
        },
    )
    trace = list(state.get("task_normalization_trace", []) or [])
    trace.extend(normalization_trace)
    trace.extend(validation.get("trace", []))

    for event, trace_event in zip(normalization_trace, trace_normalization):
        logger.info("[TaskNormalizer] %s", trace_event)
        emit_trace_event(
            "normalization_applied",
            source="task_normalizer",
            task_id=str(event.get("task_id") or ""),
            payload=trace_event,
        )
    for event, trace_event in zip(validation.get("trace", []), trace_validation):
        logger.info("[TaskValidator] %s", trace_event)
        emit_trace_event(
            "normalization_applied",
            source="task_validator",
            task_id=str(event.get("task_id") or ""),
            payload=trace_event,
        )

    seen_issue_keys = set()
    issues = []
    for issue in validation.get("issues", []):
        key = json.dumps(issue, sort_keys=True, ensure_ascii=False)
        if key not in seen_issue_keys:
            seen_issue_keys.add(key)
            issues.append(issue)
    if issues:
        logger.info(
            "[TaskValidator] issues=%s",
            summarize_trace_value(issues) if redact_trace else issues,
        )
    for issue in issues:
        emit_trace_event(
            "validation_issue",
            source="task_validator",
            severity=str(issue.get("severity") or "warning"),
            task_id=str(issue.get("task_id") or ""),
            payload=(summarize_trace_value(issue) if redact_trace else issue),
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

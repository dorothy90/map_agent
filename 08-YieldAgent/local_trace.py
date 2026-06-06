"""Local JSONL observability for supervisor multi-agent runs.

This module is intentionally self-contained and does not depend on LangSmith,
Langfuse, or any external SaaS. It writes structured, redacted trace events to
JSONL in development and stdout in production by default.
"""

from __future__ import annotations

import contextvars
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
from threading import Lock
from typing import Any, Protocol
import uuid


TRACE_SCHEMA_VERSION = "local-trace/v1"
TRACE_EVENT_TYPES = frozenset({
    "user_turn_started",
    "rewrite_output",
    "planner_output",
    "task_builder_output",
    "reference_resolved",
    "normalization_applied",
    "validation_issue",
    "hitl_triggered",
    "workflow_node",
    "supervisor_dispatch",
    "agent_started",
    "agent_result_enveloped",
    "result_consistency_warning",
    "result_pruned",
    "task_completed",
    "task_failed",
})

_MAX_SAFE_STRING_CHARS = 160
_MAX_DICT_KEYS = 80
_MAX_LIST_ITEMS = 40
_MAX_VERBOSE_LIST_ITEMS = 30
_TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar("yield_trace_id", default="")
_TURN_ID: contextvars.ContextVar[str] = contextvars.ContextVar("yield_turn_id", default="")

_SENSITIVE_EXACT_KEYS = frozenset({
    "answer",
    "api_key",
    "apikey",
    "authorization",
    "base64",
    "bytes",
    "content",
    "cookie",
    "data",
    "goal",
    "html",
    "image",
    "message",
    "passwd",
    "password",
    "payload",
    "prompt",
    "query",
    "raw",
    "rows",
    "secret",
    "sql",
    "summary",
    "table_result",
    "token",
    "value",
})
_SENSITIVE_KEY_PARTS = ("base64", "bytes", "html", "image", "payload", "query", "raw", "sql")
_PAYLOAD_LIKE_RE = re.compile(
    r"(<html|<!doctype html|<body|<svg|data:image/|data:application/|select\s+.+\s+from\s+)",
    re.IGNORECASE | re.DOTALL,
)

logger = logging.getLogger("yield_agent.local_trace")
runtime_logger = logging.getLogger("yield_agent.runtime")
runtime_logger.propagate = False
_TERMINAL_CONFIGURED = False


class TraceSink(Protocol):
    def emit(self, event: dict[str, Any]) -> None:
        ...


class JsonlFileTraceSink:
    def __init__(self, path: Path):
        self.path = path
        self._lock = Lock()

    def emit(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fp:
                fp.write(line + "\n")


class StdoutTraceSink:
    def emit(self, event: dict[str, Any]) -> None:
        print(json.dumps(event, ensure_ascii=False, sort_keys=True), file=sys.stdout, flush=True)


class NoopTraceSink:
    def emit(self, event: dict[str, Any]) -> None:
        return None


_SINK: TraceSink | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _stable_hash(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    return _sha256_text(text)


def make_trace_id(seed: str | None = None) -> str:
    if seed:
        return f"trace_{_sha256_text(seed)[:16]}"
    return f"trace_{uuid.uuid4().hex}"


def new_turn_id() -> str:
    return f"turn_{uuid.uuid4().hex}"


def set_trace_context(trace_id: str, turn_id: str) -> tuple[contextvars.Token[str], contextvars.Token[str]]:
    return (_TRACE_ID.set(trace_id or make_trace_id()), _TURN_ID.set(turn_id or new_turn_id()))


def reset_trace_context(tokens: tuple[contextvars.Token[str], contextvars.Token[str]] | None) -> None:
    if not tokens:
        return
    trace_token, turn_token = tokens
    _TRACE_ID.reset(trace_token)
    _TURN_ID.reset(turn_token)


def current_trace_context() -> tuple[str, str]:
    return _TRACE_ID.get(), _TURN_ID.get()


def _short_id(value: str, prefix: str = "") -> str:
    text = str(value or "")
    if prefix and text.startswith(prefix):
        text = text[len(prefix):]
    return text[:8] if text else "-"


def preview_text(value: Any, *, max_chars: int = 120) -> str:
    """Short single-line preview for local debugging logs."""

    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}..."


def _preview_value(value: Any, *, max_items: int = 5, max_chars: int = 80) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        previews = [preview_text(item, max_chars=max_chars) for item in items[:max_items]]
        return "[" + ",".join(previews) + (",..." if len(items) > max_items else "") + "]"
    if isinstance(value, dict):
        keys = _dict_keys(value, limit=max_items)
        return "{" + ",".join(keys) + (",..." if len(value) > max_items else "") + "}"
    return preview_text(value, max_chars=max_chars)


def _preview_params(params: dict[str, Any] | None, *, max_items: int = 8) -> str:
    if not params:
        return "{}"
    parts: list[str] = []
    for key in sorted(params):
        value = params[key]
        if value in (None, "", [], {}):
            continue
        preview = _preview_value(value)
        if preview:
            parts.append(f"{key}={preview}")
    return "{" + ", ".join(parts[:max_items]) + (" ..." if len(parts) > max_items else "") + "}"


def task_flow(tasks: list[dict[str, Any]] | None, *, max_tasks: int = 5) -> str:
    parts: list[str] = []
    for task in (tasks or [])[:max_tasks]:
        if not isinstance(task, dict):
            continue
        task_id = task.get("task_id") or "-"
        agent = task.get("agent") or "-"
        params = _preview_params(task.get("params") or {}, max_items=4)
        parts.append(f"{task_id}:{agent}{params}")
    suffix = " -> ..." if tasks and len(tasks) > max_tasks else ""
    return " -> ".join(parts) + suffix


def _terminal_enabled() -> bool:
    return os.getenv("LOCAL_RUNTIME_LOG", "1").strip().lower() not in {"0", "false", "off", "none"}


def verbose_runtime_enabled() -> bool:
    if os.getenv("LOCAL_RUNTIME_VERBOSE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return os.getenv("LOCAL_RUNTIME_LOG_LEVEL", "").strip().upper() == "DEBUG"


def raw_verbose_runtime_enabled() -> bool:
    return os.getenv("LOCAL_RUNTIME_ALLOW_SENSITIVE_LOGS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _runtime_detail_mode() -> str:
    raw = os.getenv("LOCAL_RUNTIME_DETAIL", "compact").strip().lower()
    if raw in {"raw", "full", "json"}:
        return "raw"
    return "compact"


def _terminal_level() -> int:
    raw = os.getenv("LOCAL_RUNTIME_LOG_LEVEL", os.getenv("LOG_LEVEL", "INFO")).strip().upper()
    return getattr(logging, raw, logging.INFO)


def configure_runtime_terminal_logger() -> None:
    global _TERMINAL_CONFIGURED
    if _TERMINAL_CONFIGURED:
        return
    runtime_logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    runtime_logger.addHandler(handler)
    runtime_logger.setLevel(_terminal_level())
    _TERMINAL_CONFIGURED = True


def _compact_param_summary(params: dict[str, Any] | None) -> str:
    if not params:
        return "{}"
    parts: list[str] = []
    for key in sorted(params):
        value = params[key]
        if not isinstance(value, dict):
            parts.append(f"{key}:?")
            continue
        if value.get("present") is False:
            continue
        preview = value.get("preview")
        if preview:
            parts.append(f"{key}={preview}")
            continue
        if value.get("type") == "list":
            parts.append(f"{key}:{value.get('count', 0)}")
        elif value.get("type") == "dict":
            parts.append(f"{key}:dict")
        else:
            parts.append(f"{key}:set")
    return "{" + ", ".join(parts[:8]) + (" ..." if len(parts) > 8 else "") + "}"


def _terminal_prefix(event: dict[str, Any]) -> str:
    trace = _short_id(event.get("trace_id", ""), "trace_")
    turn = _short_id(event.get("turn_id", ""), "turn_")
    task = str(event.get("task_id") or "")
    if task:
        return f"[turn={turn} trace={trace} task={task}]"
    return f"[turn={turn} trace={trace}]"


def _format_terminal_event(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    event_type = event.get("event_type", "")
    prefix = _terminal_prefix(event)

    if event_type == "user_turn_started":
        if payload.get("resume"):
            pending = payload.get("pending_interrupt") or {}
            answer = payload.get("resume_preview") or payload.get("question_preview") or ""
            pending_bits = []
            if isinstance(pending, dict):
                for key in ("interrupt_type", "type", "param", "route"):
                    value = pending.get(key)
                    if value:
                        pending_bits.append(f"{key}={value}")
            pending_text = f" pending={{{', '.join(pending_bits)}}}" if pending_bits else ""
            answer_text = f" answer=\"{answer}\"" if answer else ""
            return f"{prefix} HITL resume{answer_text}{pending_text}"
        question = payload.get("question_preview") or ""
        detail = f" question=\"{question}\"" if question else ""
        return f"{prefix} user turn started{detail}"

    if event_type == "rewrite_output":
        before = payload.get("input_preview") or ""
        after = payload.get("rewritten_preview") or ""
        if before or after:
            return (
                f"{prefix} rewrite changed={bool(payload.get('changed'))}"
                f" \"{before}\" -> \"{after}\""
            )
        return f"{prefix} rewrite changed={bool(payload.get('changed'))}"

    if event_type == "planner_output":
        status = payload.get("status", "")
        task_count = payload.get("task_count", 0)
        flow = payload.get("task_flow") or ""
        if status == "ok":
            return f"{prefix} planner -> {task_count} tasks {flow}".rstrip()
        return f"{prefix} planner {status or 'output'} tasks={task_count} {flow}".rstrip()

    if event_type == "reference_resolved":
        status = payload.get("status", "")
        keys = payload.get("resolved_keys") or []
        if status == "issue":
            return f"{prefix} reference issue count={payload.get('issue_count', 0)}"
        return f"{prefix} reference {status or 'resolved'} keys={','.join(keys) if keys else '-'}"

    if event_type == "normalization_applied":
        name = payload.get("event", "normalization")
        agent = payload.get("agent", "")
        param = payload.get("param") or payload.get("to") or ""
        return f"{prefix} normalize {name} agent={agent or '-'} param={param or '-'}"

    if event_type == "validation_issue":
        issue_type = payload.get("type", "issue")
        param = payload.get("param", "")
        reason = payload.get("reason", "")
        return f"{prefix} issue {issue_type} param={param or '-'} reason={reason or '-'}"

    if event_type == "hitl_triggered":
        issue_type = payload.get("issue_type", "hitl")
        param = payload.get("param", "")
        route = payload.get("agent", "")
        message = payload.get("message_preview", "")
        message_text = f" msg=\"{message}\"" if message else ""
        return f"{prefix} HITL {issue_type} param={param or '-'} route={route or '-'}{message_text}"

    if event_type == "workflow_node":
        keys = payload.get("keys") or []
        key_text = ",".join(keys[:8]) if isinstance(keys, list) else "-"
        return (
            f"{prefix} node {payload.get('node', event.get('source', '-'))} done"
            f" step={payload.get('step', '-')}"
            f" keys={key_text or '-'}"
            f" plan={payload.get('task_plan_count', 0)}"
            f" pending={payload.get('pending_task_count', 0)}"
        )

    if event_type == "supervisor_dispatch":
        target = payload.get("target", "")
        reason = payload.get("reason", "")
        params = _compact_param_summary(payload.get("params"))
        if target == "__end__":
            return f"{prefix} supervisor -> END reason={reason or '-'}"
        goal = payload.get("task_goal_preview") or ""
        goal_text = f" goal=\"{goal}\"" if goal else ""
        return f"{prefix} dispatch {target or '-'}{goal_text} params={params}"

    if event_type == "agent_started":
        agent = payload.get("agent", payload.get("target", ""))
        params = _compact_param_summary(payload.get("params"))
        goal = payload.get("task_goal_preview") or ""
        goal_text = f" goal=\"{goal}\"" if goal else ""
        return f"{prefix} agent started {agent or '-'}{goal_text} params={params}"

    if event_type == "agent_result_enveloped":
        agent = payload.get("source_agent") or event.get("source", "")
        return (
            f"{prefix} result {agent or '-'} kind={payload.get('kind', '-')}"
            f" status={payload.get('status', '-')} rows={payload.get('row_count', 0)}"
            f" artifacts={payload.get('artifact_ref_count', 0)}"
            f" result={_short_id(event.get('result_id', ''), 'result_')}"
        )

    if event_type == "result_pruned":
        return (
            f"{prefix} result pruned input={payload.get('input_count', 0)}"
            f" output={payload.get('output_count', 0)} dropped={payload.get('dropped_count', 0)}"
        )

    if event_type in {"task_completed", "task_failed"}:
        status = "failed" if event_type == "task_failed" else "completed"
        agent = payload.get("source_agent") or event.get("source", "")
        return (
            f"{prefix} {status} {agent or '-'} kind={payload.get('kind', '-')}"
            f" rows={payload.get('row_count', 0)} artifacts={payload.get('artifact_ref_count', 0)}"
            f" result={_short_id(event.get('result_id', ''), 'result_')}"
        )

    return f"{prefix} {event_type}"


def emit_terminal_runtime_log(event: dict[str, Any]) -> None:
    if not _terminal_enabled():
        return
    configure_runtime_terminal_logger()
    level = logging.ERROR if event.get("severity") == "error" else logging.INFO
    if level < runtime_logger.getEffectiveLevel():
        return
    runtime_logger.log(level, _format_terminal_event(event))


def _verbose_max_chars() -> int:
    raw = os.getenv("LOCAL_RUNTIME_VERBOSE_MAX_CHARS", "4000").strip()
    try:
        return max(200, int(raw))
    except ValueError:
        return 4000


def _clip_verbose_text(text: str) -> str:
    limit = _verbose_max_chars()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... <truncated chars={len(text) - limit}>"


def _verbose_json_string(text: str, *, depth: int) -> Any:
    clipped = _clip_verbose_text(text)
    stripped = text.strip()
    if not stripped.startswith(("{", "[")) or len(text) > _verbose_max_chars():
        return clipped
    try:
        parsed = json.loads(text)
    except Exception:
        return clipped
    return {
        "raw": clipped,
        "parsed_json": _verbose_jsonable(parsed, depth=depth + 1),
    }


def _verbose_jsonable(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "<max-depth>"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, bytes):
        return f"<bytes len={len(value)}>"
    if isinstance(value, str):
        return _verbose_json_string(value, depth=depth)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:_MAX_DICT_KEYS]:
            result[str(key)] = _verbose_jsonable(item, depth=depth + 1)
        if len(value) > _MAX_DICT_KEYS:
            result["_truncated_keys"] = len(value) - _MAX_DICT_KEYS
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [_verbose_jsonable(item, depth=depth + 1) for item in items[:_MAX_VERBOSE_LIST_ITEMS]]
        if len(items) > _MAX_VERBOSE_LIST_ITEMS:
            result.append(f"<truncated items={len(items) - _MAX_VERBOSE_LIST_ITEMS}>")
        return result
    content = getattr(value, "content", None)
    if content is not None:
        return {
            "type": type(value).__name__,
            "name": getattr(value, "name", ""),
            "content": _verbose_jsonable(content, depth=depth + 1),
            "additional_kwargs": _verbose_jsonable(getattr(value, "additional_kwargs", {}) or {}, depth=depth + 1),
        }
    return _clip_verbose_text(str(value))


def _len_text(value: Any) -> int:
    if value is None:
        return 0
    return len(value if isinstance(value, str) else str(value))


def _list_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _dict_keys(value: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(value, dict):
        return []
    return sorted(str(key) for key in value.keys())[:limit]


def _safe_display(value: Any, *, max_chars: int = 80) -> str:
    if value in (None, "", [], {}):
        return "-"
    if isinstance(value, list):
        return _preview_value(value, max_chars=max_chars)
    if isinstance(value, dict):
        return _preview_value(value, max_chars=max_chars)
    return preview_text(value, max_chars=max_chars)


def _compact_task(task: dict[str, Any]) -> str:
    task_id = task.get("task_id") or "-"
    agent = task.get("agent") or "-"
    params = task.get("params") or {}
    param_bits = []
    for key in sorted(params):
        param_bits.append(f"{key}={_safe_display(params[key])}")
    return f"{task_id}:{agent} params={{{', '.join(param_bits[:6])}}}"


def _compact_result(result: dict[str, Any] | None) -> str:
    if not isinstance(result, dict):
        return "result=-"
    rid = _short_id(str(result.get("result_id") or ""), "result_")
    return (
        f"result={rid} kind={result.get('kind', '-')}"
        f" status={result.get('status', '-')}"
        f" rows={_list_len(result.get('rows'))}"
        f" artifacts={_list_len(result.get('artifact_refs'))}"
    )


def _compact_issue(issue: dict[str, Any] | None) -> str:
    if not isinstance(issue, dict):
        return "issue=-"
    return (
        f"type={issue.get('type', '-')}"
        f" task={issue.get('task_id', '-')}"
        f" agent={issue.get('agent', '-')}"
        f" param={issue.get('param', '-')}"
        f" reason={issue.get('reason', '-')}"
    )


def _parse_planner_raw_summary(raw_text: Any) -> str:
    if not isinstance(raw_text, str) or not raw_text.strip():
        return "raw_len=0"
    summary = f"raw_len={len(raw_text)}"
    try:
        match = re.search(r"(\{.*\})", raw_text, flags=re.DOTALL)
        parsed = json.loads(match.group(1) if match else raw_text)
        tasks = parsed.get("tasks", []) if isinstance(parsed, dict) else []
        if isinstance(tasks, list):
            agents = [str(task.get("agent", "-")) for task in tasks if isinstance(task, dict)]
            summary += f" tasks={len(tasks)} agents={','.join(agents[:5]) if agents else '-'}"
    except Exception:
        pass
    return summary


def _compact_runtime_detail(label: str, payload: Any) -> str:
    if not isinstance(payload, dict):
        return f"{label} value_type={type(payload).__name__}"

    if label == "user_turn.input":
        if payload.get("resume_value") is not None:
            pending = payload.get("pending_interrupt") or {}
            return (
                f"{label} resume=True"
                f" answer=\"{preview_text(payload.get('resume_value'))}\""
                f" pending_type={pending.get('interrupt_type', pending.get('type', '-')) if isinstance(pending, dict) else '-'}"
                f" param={pending.get('param', '-') if isinstance(pending, dict) else '-'}"
                f" route={pending.get('route', '-') if isinstance(pending, dict) else '-'}"
            )
        return (
            f"{label} query_len={_len_text(payload.get('query'))}"
            f" resume={payload.get('resume_value') is not None}"
            f" query=\"{preview_text(payload.get('query'))}\""
            f" input={payload.get('stream_input_type', '-')}"
        )

    if label == "stream.custom":
        data = payload.get("data") or {}
        message = preview_text(data.get("message"), max_chars=100) if isinstance(data, dict) else ""
        node = data.get("node", "") if isinstance(data, dict) else ""
        message_text = f" msg=\"{message}\"" if message else ""
        node_text = f" node={node}" if node else ""
        return (
            f"{label} kind={payload.get('kind', '-')}{node_text}{message_text}"
            f" data_keys={','.join(_dict_keys(data)) or '-'}"
        )

    if label == "graph.update":
        return (
            f"{label} node={payload.get('node', '-')}"
            f" delta_keys={len(payload.get('keys') or [])}"
            f" delta_plan={_list_len(payload.get('task_plan'))}"
            f" delta_pending={_list_len(payload.get('pending_tasks'))}"
            f" delta_issues={_list_len(payload.get('validation_issues'))}"
        )

    if label == "message":
        additional = payload.get("additional_kwargs") or {}
        result = additional.get("result") if isinstance(additional, dict) else None
        return (
            f"{label} node={payload.get('node', '-')}"
            f" agent={payload.get('agent', '-')}"
            f" content_len={_len_text(payload.get('content'))}"
            f" kwargs={','.join(_dict_keys(additional)) or '-'}"
            f" {_compact_result(result)}"
        )

    if label in {"artifact.raw", "artifact.data"}:
        data = payload.get("data")
        text = str(data or "")
        data_kind = "file_ref" if text.startswith("file://") else "payload"
        return (
            f"{label} node={payload.get('node', '-')}"
            f" agent={payload.get('agent', '-')}"
            f" title={payload.get('title', '-')}"
            f" type={payload.get('type', '-') or '-'}"
            f" mime={payload.get('mime', '-') or '-'}"
            f" data={data_kind}:{len(text)}"
        )

    if label == "analysis_result":
        return f"{label} node={payload.get('node', '-')} len={_len_text(payload.get('analysis'))}"

    if label == "suggestion":
        return f"{label} node={payload.get('node', '-')} len={_len_text(payload.get('suggestion'))}"

    if label == "interrupt":
        message = preview_text(payload.get("message"), max_chars=80)
        message_text = f" msg=\"{message}\"" if message else ""
        return (
            f"{label} type={payload.get('type', payload.get('interrupt_type', '-'))}"
            f" param={payload.get('param', '-')}"
            f" route={payload.get('route', '-')}"
            f"{message_text}"
        )

    if label == "rewrite.input":
        return (
            f"{label} user_len={_len_text(payload.get('last_human'))}"
            f" meta={bool(payload.get('meta'))}"
            f" recent={_list_len(payload.get('recent_turns'))}"
            f" messages={_list_len(payload.get('invoke_messages'))}"
        )

    if label == "rewrite.tool":
        args = payload.get("args") or {}
        return f"{label} name={payload.get('name', '-')} args={','.join(_dict_keys(args)) or '-'} result_len={_len_text(payload.get('result'))}"

    if label == "rewrite.output":
        return (
            f"{label} input_len={_len_text(payload.get('input'))}"
            f" output_len={_len_text(payload.get('rewritten'))}"
            f" changed={payload.get('input') != payload.get('rewritten')}"
        )

    if label == "planner.input":
        return (
            f"{label} user_len={_len_text(payload.get('last_human'))}"
            f" meta={bool(payload.get('meta'))}"
            f" recent={_list_len(payload.get('recent_turns'))}"
            f" messages={_list_len(payload.get('invoke_messages'))}"
        )

    if label == "planner.raw":
        return f"{label} {_parse_planner_raw_summary(payload.get('raw_text'))}"

    if label == "planner.tasks":
        tasks = payload.get("tasks") or []
        compact = " | ".join(_compact_task(task) for task in tasks[:5] if isinstance(task, dict))
        return f"{label} count={len(tasks)} {compact}".rstrip()

    if label == "planner.empty_response":
        return (
            f"{label} user_len={_len_text(payload.get('user_text'))}"
            f" planner_len={_len_text(payload.get('planner_text'))}"
            f" response_len={_len_text(payload.get('response'))}"
        )

    if label == "normalizer.input":
        tasks = payload.get("tasks") or []
        agents = [str(task.get("agent", "-")) for task in tasks if isinstance(task, dict)]
        return f"{label} tasks={len(tasks)} agents={','.join(agents[:5]) if agents else '-'}"

    if label == "normalizer.output":
        issues = payload.get("issues") or []
        issue_types = [str(issue.get("type", "-")) for issue in issues if isinstance(issue, dict)]
        return (
            f"{label} tasks={_list_len(payload.get('normalized_tasks'))}"
            f" normalized={_list_len(payload.get('normalization_trace'))}"
            f" validation={_list_len(payload.get('validation_trace'))}"
            f" issues={len(issues)}"
            f" issue_types={','.join(issue_types[:5]) if issue_types else '-'}"
        )

    if label == "hitl.issue":
        payload_message = ""
        interrupt_payload = payload.get("payload") or {}
        if isinstance(interrupt_payload, dict):
            payload_message = preview_text(interrupt_payload.get("message"), max_chars=100)
        message_text = f" msg=\"{payload_message}\"" if payload_message else ""
        return f"{label} {_compact_issue(payload.get('issue'))}{message_text}"

    if label == "hitl.response":
        record = payload.get("record") or {}
        selected = record.get("selected_result_id", "") if isinstance(record, dict) else ""
        selected_text = f" selected={_short_id(str(selected), 'result_')}" if selected else ""
        return (
            f"{label} {_compact_issue(payload.get('issue'))}"
            f" answer=\"{preview_text(payload.get('user_response'))}\""
            f"{selected_text}"
        )

    if label == "supervisor.current_task":
        current = payload.get("current_task") or {}
        params = payload.get("resolved_task_params") or {}
        return (
            f"{label} {_compact_task(current)}"
            f" remaining={_list_len(payload.get('remaining_tasks'))}"
            f" resolved_params={_preview_params(params)}"
        )

    if label == "supervisor.dispatch_state":
        update = payload.get("update") or {}
        param_bits = []
        for key in (
            "lotcd", "lot_ids", "wf_ids", "groupkey", "fail_type", "cause_oper",
            "map_type", "map_oper", "dh_query", "wads_start_tm", "wads_end_tm",
            "ref_date", "unit", "periods",
        ):
            if key in update:
                param_bits.append(f"{key}={_safe_display(update.get(key))}")
        return (
            f"{label} goto={payload.get('goto', '-')}"
            f" update_keys={','.join(_dict_keys(update, limit=12)) or '-'}"
            f" params={{{', '.join(param_bits)}}}"
        )

    if label == "param.invalid":
        return (
            f"{label} agent={payload.get('agent', '-')}"
            f" param={payload.get('param', '-')}"
            f" value={_safe_display(payload.get('value'))}"
            f" reason={payload.get('reason', '-')}"
            f" action={payload.get('action', '-')}"
        )

    if label == "wads.query_context":
        return (
            f"{label} lotcd={_safe_display(payload.get('lotcd'))}"
            f" start={_safe_display(payload.get('start_tm'))}"
            f" end={_safe_display(payload.get('end_tm'))}"
            f" parameter={_safe_display(payload.get('parameter'))}"
            f" goal=\"{preview_text(payload.get('task_goal'))}\""
        )

    if label == "wads.sql":
        return (
            f"{label} context={payload.get('context', '-')}"
            f" bind_keys={_preview_value(payload.get('bind_keys'))}"
            f" binds={_preview_params(payload.get('binds') or {})}"
            f" join_wafers={payload.get('join_wafers', '-')}"
            f" sql=\"{preview_text(payload.get('sql'), max_chars=500)}\""
        )

    if label == "wads.query_data":
        filters = payload.get("filters") or {}
        join = payload.get("join_coverage") or {}
        join_text = ""
        if isinstance(join, dict) and join:
            join_text = (
                f" reports={join.get('report_rows', '-')}"
                f" joined={join.get('joined_rows', '-')}"
                f" wafer_rows={join.get('wafer_rows', '-')}"
                f" groupkeys={join.get('unique_groupkeys', '-')}"
                f" missing_groupkey_rows={join.get('missing_groupkey_rows', '-')}"
                f" join_missing={join.get('join_missing', False)}"
            )
        return (
            f"{label} rows={payload.get('row_count', '-')}"
            f"{join_text}"
            f" filters={_preview_params(filters)}"
            f" columns={','.join(payload.get('columns') or []) or '-'}"
            f" sample={_preview_value(payload.get('sample'))}"
        )

    if label == "wads.report_data":
        filters = payload.get("filters") or {}
        join = payload.get("join_coverage") or {}
        join_text = ""
        if isinstance(join, dict) and join:
            join_text = (
                f" reports={join.get('report_rows', '-')}"
                f" joined={join.get('joined_rows', '-')}"
                f" wafer_rows={join.get('wafer_rows', '-')}"
                f" groupkeys={join.get('unique_groupkeys', '-')}"
                f" missing_groupkey_rows={join.get('missing_groupkey_rows', '-')}"
                f" join_missing={join.get('join_missing', False)}"
            )
        return (
            f"{label} rows={payload.get('row_count', '-')}"
            f"{join_text}"
            f" filters={_preview_params(filters)}"
            f" columns={','.join(payload.get('columns') or []) or '-'}"
            f" sample={_preview_value(payload.get('sample'))}"
        )

    if label == "wads.tool_calls":
        calls = payload.get("tool_calls") or []
        parts = []
        for call in calls[:5]:
            if not isinstance(call, dict):
                continue
            parts.append(f"{call.get('name', '-')}({_preview_params(call.get('args') or {})})")
        return f"{label} count={len(calls)} calls={'; '.join(parts) or '-'}"

    if label == "wads.result_payload":
        return (
            f"{label} status={payload.get('status', '-')}"
            f" rows={payload.get('row_count', '-')}"
            f" artifacts={payload.get('artifact_count', '-')}"
            f" reports={payload.get('report_row_count', '-')}"
            f" joined={payload.get('joined_row_count', '-')}"
            f" wafer_groupkeys={payload.get('wafer_groupkey_count', '-')}"
            f" missing_groupkey_rows={payload.get('missing_groupkey_rows', '-')}"
            f" join_missing={payload.get('join_missing', False)}"
            f" lots={_preview_value(payload.get('lot_ids'))}"
            f" groupkeys={_preview_value(payload.get('groupkeys'))}"
        )

    return f"{label} keys={','.join(_dict_keys(payload, limit=12)) or '-'}"


def emit_runtime_detail(
    label: str,
    payload: Any,
    *,
    task_id: str = "",
    result_id: str = "",
    level: int = logging.INFO,
) -> None:
    """Emit local terminal details for development debugging only.

    This intentionally does not write to JSONL. Enable with
    LOCAL_RUNTIME_VERBOSE=1 or LOCAL_RUNTIME_LOG_LEVEL=DEBUG. Raw sensitive
    values require LOCAL_RUNTIME_ALLOW_SENSITIVE_LOGS=1.
    """

    if not _terminal_enabled() or not verbose_runtime_enabled():
        return
    configure_runtime_terminal_logger()
    if level < runtime_logger.getEffectiveLevel():
        return
    trace_id, turn_id = current_trace_context()
    event = {
        "trace_id": trace_id or "trace_unknown",
        "turn_id": turn_id or "turn_unknown",
        "task_id": task_id or "",
        "result_id": result_id or "",
    }
    if _runtime_detail_mode() == "raw":
        detail_payload = payload if raw_verbose_runtime_enabled() else redact_trace_payload(payload)
        body = json.dumps(_verbose_jsonable(detail_payload), ensure_ascii=False, indent=2, default=str)
        runtime_logger.log(level, "%s %s\n%s", _terminal_prefix(event), label, body)
        return

    runtime_logger.log(level, "%s %s", _terminal_prefix(event), _compact_runtime_detail(label, payload))


def _is_sensitive_key(key_path: str) -> bool:
    if not key_path:
        return False
    key = key_path.rsplit(".", 1)[-1].strip().lower().replace("-", "_")
    if key in _SENSITIVE_EXACT_KEYS:
        return True
    return any(part in key for part in _SENSITIVE_KEY_PARTS)


def _redacted_marker(value: Any) -> dict[str, Any]:
    text_hash = _stable_hash(value)
    length = len(value) if isinstance(value, (str, bytes, list, tuple, dict)) else None
    marker = {"redacted": True, "sha256": text_hash}
    if length is not None:
        marker["length"] = length
    return marker


def redact_trace_payload(value: Any, *, key_path: str = "", depth: int = 0) -> Any:
    """Return a JSON-safe payload with sensitive raw values removed."""

    if depth > 8:
        return {"truncated": True, "reason": "max_depth"}

    if _is_sensitive_key(key_path):
        return _redacted_marker(value)

    if value is None or isinstance(value, bool | int | float):
        return value

    if isinstance(value, bytes):
        return _redacted_marker(value.decode("utf-8", errors="replace"))

    if isinstance(value, str):
        if len(value) > _MAX_SAFE_STRING_CHARS or _PAYLOAD_LIKE_RE.search(value):
            return _redacted_marker(value)
        return value

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_DICT_KEYS:
                result["_truncated_keys"] = len(value) - _MAX_DICT_KEYS
                break
            key_str = str(key)
            next_path = f"{key_path}.{key_str}" if key_path else key_str
            result[key_str] = redact_trace_payload(item, key_path=next_path, depth=depth + 1)
        return result

    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [
            redact_trace_payload(item, key_path=key_path, depth=depth + 1)
            for item in items[:_MAX_LIST_ITEMS]
        ]
        if len(items) > _MAX_LIST_ITEMS:
            result.append({"truncated_items": len(items) - _MAX_LIST_ITEMS})
        return result

    return redact_trace_payload(str(value), key_path=key_path, depth=depth + 1)


def summarize_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """Summarize parameter presence with short local-debug previews."""

    summary: dict[str, Any] = {}
    for key, value in (params or {}).items():
        if value is None or value == "":
            summary[key] = {"type": "empty", "present": False}
        elif isinstance(value, list):
            summary[key] = {
                "type": "list",
                "present": True,
                "count": len(value),
                "sha256": _stable_hash(value),
                "preview": _preview_value(value),
            }
        elif isinstance(value, dict):
            summary[key] = {
                "type": "dict",
                "present": True,
                "keys": sorted(str(k) for k in value.keys())[:_MAX_LIST_ITEMS],
                "sha256": _stable_hash(value),
                "preview": _preview_value(value),
            }
        else:
            text = str(value)
            summary[key] = {
                "type": type(value).__name__,
                "present": True,
                "length": len(text),
                "sha256": _sha256_text(text),
                "preview": _preview_value(value),
            }
    return summary


def summarize_tasks(tasks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for task in tasks or []:
        params = task.get("params") or {}
        result.append({
            "task_id": task.get("task_id", ""),
            "agent": task.get("agent", ""),
            "goal_preview": preview_text(task.get("goal", "")),
            "param_keys": sorted(str(k) for k in params.keys()),
            "params": summarize_params(params),
        })
    return result


def summarize_result_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    entities = envelope.get("entities") or {}
    return {
        "schema_version": envelope.get("schema_version", ""),
        "source_agent": envelope.get("source_agent", ""),
        "kind": envelope.get("kind", ""),
        "status": envelope.get("status", ""),
        "row_count": len(envelope.get("rows") or []),
        "column_count": len(envelope.get("columns") or []),
        "artifact_ref_count": len(envelope.get("artifact_refs") or []),
        "entity_counts": {
            key: len(value) if isinstance(value, list) else 1
            for key, value in entities.items()
            if value
        },
    }


def _trace_sink_kind() -> str:
    configured = os.getenv("LOCAL_TRACE_SINK", "").strip().lower()
    if configured:
        return configured
    env = (
        os.getenv("APP_ENV")
        or os.getenv("ENV")
        or os.getenv("ENVIRONMENT")
        or os.getenv("YIELD_AGENT_ENV")
        or "development"
    ).strip().lower()
    return "stdout" if env in {"prod", "production"} else "file"


def _trace_path() -> Path:
    if os.getenv("LOCAL_TRACE_PATH"):
        return Path(os.environ["LOCAL_TRACE_PATH"]).expanduser()
    trace_dir = Path(os.getenv("LOCAL_TRACE_DIR", str(Path(__file__).resolve().parent / "traces"))).expanduser()
    filename = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
    return trace_dir / filename


def get_trace_sink() -> TraceSink:
    global _SINK
    if _SINK is not None:
        return _SINK

    kind = _trace_sink_kind()
    if kind in {"0", "false", "off", "none", "noop"}:
        _SINK = NoopTraceSink()
    elif kind == "stdout":
        _SINK = StdoutTraceSink()
    elif kind == "file":
        _SINK = JsonlFileTraceSink(_trace_path())
    else:
        logger.warning("Unknown LOCAL_TRACE_SINK=%r; using stdout", kind)
        _SINK = StdoutTraceSink()
    return _SINK


def reset_trace_sink_for_tests() -> None:
    global _SINK
    _SINK = None


def reset_runtime_terminal_logger_for_tests() -> None:
    global _TERMINAL_CONFIGURED
    runtime_logger.handlers.clear()
    _TERMINAL_CONFIGURED = False


def emit_trace_event(
    event_type: str,
    *,
    source: str = "",
    payload: dict[str, Any] | None = None,
    severity: str = "info",
    trace_id: str = "",
    turn_id: str = "",
    task_id: str = "",
    result_id: str = "",
) -> dict[str, Any]:
    if event_type not in TRACE_EVENT_TYPES:
        raise ValueError(f"Unsupported local trace event type: {event_type}")

    current_trace_id, current_turn_id = current_trace_context()
    event = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "event_id": f"event_{uuid.uuid4().hex}",
        "timestamp": _utc_now(),
        "event_type": event_type,
        "severity": severity,
        "trace_id": trace_id or current_trace_id or "trace_unknown",
        "turn_id": turn_id or current_turn_id or "turn_unknown",
        "task_id": task_id or "",
        "result_id": result_id or "",
        "source": source,
        "payload": redact_trace_payload(payload or {}),
    }

    try:
        get_trace_sink().emit(event)
    except Exception as exc:
        if os.getenv("LOCAL_TRACE_RAISE_ERRORS") == "1":
            raise
        logger.warning("[LocalTrace] failed to emit %s: %s", event_type, exc)
    emit_terminal_runtime_log(event)
    return event

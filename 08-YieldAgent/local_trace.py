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

from langchain_core.callbacks import BaseCallbackHandler


TRACE_SCHEMA_VERSION = "local-trace/v1"
TRACE_EVENT_TYPES = frozenset({
    "user_turn_started",
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


def llm_io_enabled() -> bool:
    """Whether to render LLM multi-turn input / AI content under the tree.

    Heavy (full message previews + SQL), so off unless verbose or explicitly on.
    """
    if os.getenv("LOCAL_RUNTIME_LLM_IO", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return verbose_runtime_enabled()


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


# ── Human-readable tree renderer for the terminal runtime log ──────────
#
# The JSONL sink keeps the full, redacted event stream for machines. This
# renderer turns that same stream into an indented per-turn tree so a human can
# read parsing, fan-out/fan-in, and timing at a glance. It is intentionally
# stateful: durations and tree nesting need a little cross-event memory. The
# raw JSONL is untouched — this only changes what humans see on the terminal.

_RENDER_STATE: dict[str, Any] = {"turn_id": "", "turn_start": 0.0, "tasks": {}}

# Nodes that already emit their own semantic line — their workflow_node echo is
# pure duplication, so we skip it (the plumbing nodes still render as a spine).
_SEMANTIC_NODES = frozenset({"reference_resolver", "planner", "task_builder", "supervisor"})

_ANSI = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "green": "\033[32m", "red": "\033[31m", "yellow": "\033[33m",
    "cyan": "\033[36m", "blue": "\033[34m",
}


def _supports_color() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("LOCAL_RUNTIME_COLOR", "").strip().lower() in {"0", "false", "off"}:
        return False
    try:
        return sys.stderr.isatty()
    except Exception:
        return False


def _c(text: str, *codes: str) -> str:
    if not codes or not _supports_color():
        return text
    return "".join(_ANSI[c] for c in codes) + text + _ANSI["reset"]


def _tag(name: str) -> str:
    return name.ljust(8)


def _parse_ts(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return 0.0


def _dur(start: float, end: float) -> str:
    if not start or not end or end < start:
        return ""
    return f"{end - start:.1f}s"


def _reset_turn(event: dict[str, Any]) -> None:
    _RENDER_STATE["turn_id"] = event.get("turn_id", "")
    _RENDER_STATE["turn_start"] = _parse_ts(event.get("timestamp", ""))
    _RENDER_STATE["tasks"] = {}


def _format_terminal_event(event: dict[str, Any]) -> str | None:
    """Render one trace event as a terminal tree line, or None to skip it."""

    payload = event.get("payload") or {}
    event_type = event.get("event_type", "")
    now = _parse_ts(event.get("timestamp", ""))
    task = str(event.get("task_id") or "")

    if event_type == "user_turn_started":
        _reset_turn(event)
        trace = _short_id(event.get("trace_id", ""), "trace_")
        turn = _short_id(event.get("turn_id", ""), "turn_")
        tag = _c(f"[{turn}/{trace}]", "dim")
        if payload.get("resume"):
            answer = payload.get("resume_preview") or payload.get("question_preview") or ""
            ans = f' answer="{answer}"' if answer else ""
            return _c("\n▸ ", "bold", "cyan") + _c(f"HITL resume{ans}", "bold") + f"  {tag}"
        question = payload.get("question_preview") or ""
        return _c("\n▸ ", "bold", "cyan") + _c(f'"{question}"', "bold") + f"  {tag}"

    # Joined a turn mid-stream (e.g. after a server restart) — adopt it silently.
    if event.get("turn_id") and event["turn_id"] != _RENDER_STATE["turn_id"]:
        _reset_turn(event)

    if event_type == "reference_resolved":
        status = payload.get("status", "")
        if status == "issue":
            return "  " + _c(_tag("ref") + f"issue×{payload.get('issue_count', 0)}", "yellow")
        if status == "no_reference":
            return None  # nothing referenced — no signal worth a line
        keys = payload.get("resolved_keys") or []
        return "  " + _c(_tag("ref"), "dim") + f"resolved {','.join(keys) or '-'}"

    if event_type == "planner_output":
        status = payload.get("status", "")
        n = payload.get("request_count", payload.get("task_count", 0))
        if status and status != "ok":
            return "  " + _c(_tag("planner") + f"{status} requests={n}", "yellow")
        return "  " + _c(_tag("planner") + f"{n} request(s)", "dim")

    if event_type == "task_builder_output":
        n = payload.get("task_count", 0)
        flow = payload.get("task_flow") or ""
        return "  " + _c(_tag("plan"), "bold") + f"{n} task" + (f"  {flow}" if flow else "")

    if event_type == "normalization_applied":
        name = payload.get("event", "normalization")
        param = payload.get("param") or payload.get("to") or ""
        body = f"{name}" + (f" param={param}" if param else "")
        return "  " + _c(_tag("norm") + body, "dim")

    if event_type == "validation_issue":
        body = (
            f"{payload.get('type', 'issue')} param={payload.get('param', '-') or '-'}"
            f" {payload.get('reason', '') or ''}"
        ).rstrip()
        return "  " + _c(_tag("issue") + body, "yellow")

    if event_type == "hitl_triggered":
        msg = payload.get("message_preview", "")
        body = (
            f"{payload.get('issue_type', 'hitl')} param={payload.get('param', '-') or '-'}"
            f" route={payload.get('agent', '-') or '-'}" + (f' "{msg}"' if msg else "")
        )
        return "  " + _c("⚠ HITL ", "yellow", "bold") + _c(body, "yellow")

    if event_type == "supervisor_dispatch":
        target = payload.get("target", "")
        if target == "__end__":
            total = _dur(_RENDER_STATE["turn_start"], now)
            reason = payload.get("reason", "") or "-"
            return "  " + _c("■ END", "bold") + _c(f"  total={total or '?'}  reason={reason}", "dim")
        if task:
            _RENDER_STATE["tasks"][task] = {"agent": target, "start": now}
        goal = payload.get("task_goal_preview") or ""
        params = _compact_param_summary(payload.get("params"))
        goal_t = f' goal="{goal}"' if goal else ""
        return (
            "  " + _c("▶ ", "blue", "bold") + _c(f"{task or '?'} {target or '-'}", "blue", "bold")
            + _c(f"{goal_t}  {params}", "dim")
        )

    if event_type == "agent_started":
        return None  # redundant with supervisor_dispatch; timing tracked there

    if event_type == "agent_result_enveloped":
        agent = payload.get("source_agent") or event.get("source", "-")
        info = _RENDER_STATE["tasks"].get(task, {})
        dur = _dur(info.get("start", 0.0), now)
        status = payload.get("status", "-")
        ok = status in ("ok", "success", "completed", "-")
        mark = _c("✓", "green") if ok else _c("✗", "red")
        body = (
            (f"{dur}  " if dur else "")
            + f"{payload.get('kind', '-')} rows={payload.get('row_count', 0)}"
            f" cols={payload.get('column_count', 0)} artifacts={payload.get('artifact_ref_count', 0)}"
        )
        return "    " + mark + f" {agent}  " + _c(body, "dim")

    if event_type == "result_pruned":
        body = (
            f"{payload.get('input_count', 0)}→{payload.get('output_count', 0)}"
            f" (dropped {payload.get('dropped_count', 0)})"
        )
        return "  " + _c(_tag("prune") + body, "dim")

    if event_type in {"task_completed", "task_failed"}:
        failed = event_type == "task_failed"
        agent = payload.get("source_agent") or event.get("source", "-")
        mark = _c("✗", "red", "bold") if failed else _c("✓", "green", "bold")
        word = "failed" if failed else "done"
        head = _c(f" {task or '-'} {word} {agent}", "red" if failed else "green")
        tail = _c(
            f"  rows={payload.get('row_count', 0)} artifacts={payload.get('artifact_ref_count', 0)}",
            "dim",
        )
        return "  " + mark + head + tail

    if event_type == "workflow_node":
        node = payload.get("node", event.get("source", "-"))
        if (
            node in _SEMANTIC_NODES
            or any(t.get("agent") == node for t in _RENDER_STATE["tasks"].values())
        ):
            return None  # echo of a node that already printed its own line
        elapsed = payload.get("elapsed")
        et = f" +{elapsed:.1f}s" if isinstance(elapsed, (int, float)) and elapsed else ""
        return "  " + _c(f"·  {node}{et}", "dim")

    return "  " + _c(event_type, "dim")


def emit_terminal_runtime_log(event: dict[str, Any]) -> None:
    if not _terminal_enabled():
        return
    configure_runtime_terminal_logger()
    level = logging.ERROR if event.get("severity") == "error" else logging.INFO
    if level < runtime_logger.getEffectiveLevel():
        return
    line = _format_terminal_event(event)
    if line is None:
        return
    runtime_logger.log(level, line)


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

    if label == "history.excluded":
        items = payload.get("excluded") or []
        previews = " | ".join(
            f"{it.get('role', '?')}:\"{it.get('preview', '')}\""
            for it in items if isinstance(it, dict)
        )
        return (
            f"history  {payload.get('kept_for_llm', '-')}/{payload.get('eligible', '-')} turns → LLM,"
            f" 제외 {len(items)}: {previews or '-'}"
        )

    return f"{label} keys={','.join(_dict_keys(payload, limit=12)) or '-'}"


# ── Single-turn debug capture → standalone HTML ────────────────────────
#
# For debugging we don't need the whole history — just the LATEST turn, in full
# (un-redacted) detail, as one readable HTML page. This recorder keeps an
# in-memory buffer for the current turn only (reset each new turn) and writes
# traces/last_turn.html at each milestone, so even a crashed turn leaves a
# report. Independent of JSONL redaction and the verbose terminal gate.

_TURNS: list[dict[str, Any]] = []  # recent turns, oldest→newest; newest is active


def _last_turn_enabled() -> bool:
    return os.getenv("LOCAL_LAST_TURN_HTML", "1").strip().lower() not in {"0", "false", "off", "none"}


def _last_turn_keep() -> int:
    try:
        return max(1, int(os.getenv("LOCAL_LAST_TURN_KEEP", "3")))
    except ValueError:
        return 3


def _last_turn_path() -> Path:
    return _trace_path().parent / "last_turns.html"


def _last_turns_json_path() -> Path:
    return _trace_path().parent / "last_turns.json"


_TURNS_LOADED = False


def _load_turns_once() -> None:
    """Reload the recent-turns buffer from disk so it survives a restart/reload."""
    global _TURNS_LOADED
    if _TURNS_LOADED:
        return
    _TURNS_LOADED = True
    try:
        p = _last_turns_json_path()
        if not _TURNS and p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for turn in data[-_last_turn_keep():]:
                    if isinstance(turn, dict):
                        turn.setdefault("_llm_index", {})  # live-pairing only; empty for loaded turns
                        _TURNS.append(turn)
    except Exception:
        pass


def _persist_turns() -> None:
    try:
        data = [{k: v for k, v in t.items() if k != "_llm_index"} for t in _TURNS]
        _last_turns_json_path().write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        pass


def _clip(text: Any, limit: int = 6000) -> str:
    s = text if isinstance(text, str) else str(text)
    return s if len(s) <= limit else f"{s[:limit]}… <+{len(s) - limit} chars>"


def _current_turn() -> dict[str, Any] | None:
    return _TURNS[-1] if _TURNS else None


def _start_turn(event: dict[str, Any], payload: dict[str, Any]) -> None:
    prev = _current_turn()
    if prev is not None and prev.get("status") == "running":
        prev["status"] = "done"  # a new turn began → the previous one finished
    _TURNS.append({
        "turn_id": event.get("turn_id", ""),
        "trace_id": event.get("trace_id", ""),
        "question": payload.get("question_preview") or "",
        "started_at": event.get("timestamp", ""),
        "ended_at": "",
        "status": "running",
        "seq": 0,
        "events": [],
        "details": [],
        "llm_calls": [],
        "_llm_index": {},
    })
    keep = _last_turn_keep()
    if len(_TURNS) > keep:
        del _TURNS[:-keep]


def _seq() -> int:
    t = _current_turn()
    if t is None:
        return 0
    t["seq"] = t.get("seq", 0) + 1
    return t["seq"]


def record_trace_event(event: dict[str, Any], raw_payload: dict[str, Any]) -> None:
    if not _last_turn_enabled():
        return
    try:
        _load_turns_once()
        etype = event.get("event_type", "")
        cur = _current_turn()
        if etype == "user_turn_started" or cur is None or (
            cur.get("turn_id") and event.get("turn_id") != cur["turn_id"]
        ):
            _start_turn(event, raw_payload)
            cur = _current_turn()
        is_end = etype == "supervisor_dispatch" and (raw_payload or {}).get("target") == "__end__"
        if etype != "workflow_node":  # plumbing — keep the report clean
            cur["events"].append({
                "seq": _seq(),
                "ts": event.get("timestamp", ""),
                "type": etype,
                "source": event.get("source", ""),
                "severity": event.get("severity", "info"),
                "task_id": event.get("task_id", ""),
                "payload": _verbose_jsonable(raw_payload or {}),
            })
        if etype == "task_failed":
            cur["status"] = "failed"
        if is_end:
            if cur.get("status") != "failed":
                cur["status"] = "ok"
            cur["ended_at"] = event.get("timestamp", "")
        if etype in ("task_failed", "task_completed") or is_end:
            _write_last_turn_html()
    except Exception:
        pass


def record_detail(label: str, payload: Any) -> None:
    if not _last_turn_enabled():
        return
    cur = _current_turn()
    if cur is None:
        return
    try:
        cur["details"].append({
            "seq": _seq(),
            "label": label,
            "payload": _verbose_jsonable(payload),
        })
    except Exception:
        pass


def _msg_capture(message: Any) -> dict[str, Any]:
    text = _msg_text(message)
    cap: dict[str, Any] = {"role": getattr(message, "type", "?") or "?", "content": _clip(text), "chars": len(text)}
    tcs = getattr(message, "tool_calls", None)
    if tcs:
        cap["tool_calls"] = [{"name": tc.get("name"), "args": tc.get("args")} for tc in tcs]
    return cap


def _llm_out_repr(response: Any) -> dict[str, Any]:
    try:
        gen = response.generations[0][0]
        msg = getattr(gen, "message", None)
        tcs = getattr(msg, "tool_calls", None) if msg is not None else None
        if tcs:
            return {"kind": "tool_calls", "tool_calls": [{"name": tc.get("name"), "args": tc.get("args")} for tc in tcs]}
        text = _msg_text(msg) if msg is not None else (getattr(gen, "text", "") or "")
        return {"kind": "text", "text": _clip(text)}
    except Exception:
        return {"kind": "text", "text": ""}


def record_llm_start(run_id: Any, node: str, messages: list) -> None:
    if not _last_turn_enabled():
        return
    cur = _current_turn()
    if cur is None:
        return
    try:
        entry = {"seq": _seq(), "node": node, "in": [_msg_capture(m) for m in messages], "out": None}
        cur["llm_calls"].append(entry)
        cur["_llm_index"][str(run_id)] = entry
    except Exception:
        pass


def record_llm_end(run_id: Any, out: dict[str, Any]) -> None:
    if not _last_turn_enabled():
        return
    cur = _current_turn()
    if cur is None:
        return
    try:
        entry = cur.get("_llm_index", {}).get(str(run_id))
        if entry is None and cur.get("llm_calls"):
            entry = cur["llm_calls"][-1]
        if entry is not None:
            entry["out"] = out
    except Exception:
        pass


# -- HTML rendering -----------------------------------------------------

def _esc(value: Any) -> str:
    s = value if isinstance(value, str) else str(value)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _json_pre(obj: Any) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        text = str(obj)
    return f"<pre>{_esc(text)}</pre>"


def _event_hint(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("task_flow", "question_preview", "target", "reason", "status", "row_count", "resolved_keys"):
        val = payload.get(key)
        if val not in (None, "", [], {}):
            return f"{key}={_esc(_clip(val, 200))}"
    return ""


_LAST_TURN_CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;background:#f6f7f9;color:#1c2128}
.wrap{max-width:1040px;margin:0 auto;padding:24px}
h1{font-size:19px;margin:0 0 4px} .meta{color:#57606a;font-size:13px;margin-bottom:18px}
.badge{display:inline-block;padding:2px 9px;border-radius:10px;font-size:12px;font-weight:700}
.ok{background:#dafbe1;color:#1a7f37}.failed{background:#ffebe9;color:#cf222e}.running{background:#fff8c5;color:#9a6700}.done{background:#eaeef2;color:#57606a}
section{background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:14px 16px;margin-bottom:14px}
section>h2{font-size:12px;margin:0 0 10px;color:#57606a;text-transform:uppercase;letter-spacing:.05em}
.row{border-bottom:1px solid #eef1f4;padding:7px 0}.row:last-child{border:0}
.etype{font-weight:600}.src{color:#8c959f;font-size:12px;margin-left:6px}
.hint{color:#0a3069;font-size:12px;margin-left:6px}
.err{background:#fff8f8;border-color:#ffcecb}.err h2{color:#cf222e}
pre{background:#f6f8fa;border-radius:6px;padding:10px;overflow:auto;font-size:12px;line-height:1.45;margin:6px 0;white-space:pre-wrap;word-break:break-word}
.call{border:1px solid #e3e8ee;border-radius:6px;padding:10px;margin-bottom:10px}
.call>.node{font-weight:700;color:#0550ae;font-size:13px}
.msg{border-left:3px solid #d8dee4;padding:2px 0 2px 10px;margin:6px 0}
.role{font-weight:700;font-size:12px}.r-system{color:#8250df}.r-human{color:#1a7f37}.r-ai{color:#bc4c00}.r-tool{color:#57606a}
.out{border-left:3px solid #1a7f37;padding-left:10px;margin-top:8px}
details>summary{cursor:pointer;font-size:12px;color:#57606a;user-select:none}
.label{font-weight:600;color:#0a3069}
.turn{margin-bottom:18px}
.turn-cur{border-left:4px solid #0969da;padding-left:12px}
.turn-old{border-left:4px solid #d0d7de;padding-left:12px}
.turn-old>summary{font-size:14px;padding:8px 0}
.turn-head{font-size:14px;padding:8px 0;border-bottom:1px solid #eaeef2;margin-bottom:10px}
.q{font-weight:600;color:#1c2128}
"""


def _render_msg(msg: dict[str, Any]) -> str:
    role = str(msg.get("role", "?"))
    content = msg.get("content", "")
    head = f'<span class="role r-{_esc(role)}">{_esc(role)}</span> <span class="src">{msg.get("chars", 0)} chars</span>'
    body = f"<pre>{_esc(content)}</pre>" if content else ""
    tcs = msg.get("tool_calls")
    if tcs:
        body += _json_pre(tcs)
    return f'<div class="msg">{head}{body}</div>'


def _render_llm_call(call: dict[str, Any]) -> str:
    parts = [f'<div class="node">↳ llm · {_esc(call.get("node", "llm"))}'
             f' <span class="src">in {len(call.get("in") or [])} msgs</span></div>']
    for m in call.get("in") or []:
        parts.append(_render_msg(m))
    out = call.get("out") or {}
    if out.get("kind") == "tool_calls":
        parts.append(f'<div class="out"><span class="role r-ai">out → tool_calls</span>{_json_pre(out.get("tool_calls"))}</div>')
    else:
        parts.append(f'<div class="out"><span class="role r-ai">out →</span><pre>{_esc(out.get("text", ""))}</pre></div>')
    return f'<div class="call">{"".join(parts)}</div>'


def _turn_header_line(t: dict[str, Any], pos: int, total: int) -> str:
    status = t.get("status", "running")
    dur = _dur(_parse_ts(t.get("started_at", "")), _parse_ts(t.get("ended_at", ""))) or "—"
    return (
        f'<span class="badge {status}">{status}</span> '
        f'<b>turn {pos}/{total}</b> · <span class="q">{_esc(t.get("question") or "(no question)")}</span> '
        f'<span class="src">{dur} · turn={_esc(_short_id(t.get("turn_id", ""), "turn_"))}</span>'
    )


def _render_one_turn_body(t: dict[str, Any]) -> str:
    # error box (if any task_failed captured)
    err_html = ""
    for ev in t.get("events", []):
        if ev.get("type") == "task_failed":
            p = ev.get("payload") or {}
            err_html = (
                '<section class="err"><h2>error</h2>'
                f'<div><b>{_esc(p.get("error_type", "Error"))}</b>: {_esc(p.get("error_message", "(no message)"))}</div>'
                f'<pre>{_esc(p.get("traceback", "(no traceback captured)"))}</pre></section>'
            )
            break

    # pipeline events
    rows = []
    for ev in t.get("events", []):
        p = ev.get("payload") or {}
        hint = _event_hint(p)
        rows.append(
            f'<div class="row"><span class="etype">{_esc(ev.get("type"))}</span>'
            f'<span class="src">{_esc(ev.get("source", ""))}'
            f'{(" · " + _esc(ev.get("task_id"))) if ev.get("task_id") else ""}</span>'
            f'{(" <span class=hint>" + hint + "</span>") if hint else ""}'
            f'<details><summary>payload</summary>{_json_pre(p)}</details></div>'
        )
    pipeline = '<section><h2>pipeline</h2>' + ("".join(rows) or "(none)") + '</section>'

    # llm calls
    calls = "".join(_render_llm_call(c) for c in t.get("llm_calls", []))
    llm = f'<section><h2>llm calls ({len(t.get("llm_calls", []))})</h2>{calls or "(none — LLM I/O capture needs a model run)"}</section>'

    # tool / sql / other details
    dets = []
    for d in t.get("details", []):
        dets.append(
            f'<div class="row"><span class="label">{_esc(d.get("label"))}</span>'
            f'<details><summary>detail</summary>{_json_pre(d.get("payload"))}</details></div>'
        )
    details_sec = f'<section><h2>tool / sql details ({len(dets)})</h2>{"".join(dets) or "(none)"}</section>'

    return err_html + pipeline + llm + details_sec


def _render_all_turns_html() -> str:
    total = len(_TURNS)
    blocks = []
    for i in range(total - 1, -1, -1):  # newest first
        t = _TURNS[i]
        hdr = _turn_header_line(t, i + 1, total)
        body = _render_one_turn_body(t)
        if i == total - 1:  # newest — expanded
            blocks.append(f'<div class="turn turn-cur"><div class="turn-head">{hdr}</div>{body}</div>')
        else:  # older — collapsed thread
            blocks.append(f'<details class="turn turn-old"><summary>{hdr}</summary>{body}</details>')
    inner = "".join(blocks) or "<section>(no turn captured yet)</section>"
    title = _esc((_current_turn() or {}).get("question") or "")[:40]
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>last {total} turns · {title}</title>'
        f'<style>{_LAST_TURN_CSS}</style></head><body><div class="wrap">'
        f'<h1>최근 {total}턴 · 디버그</h1>'
        '<div class="meta">최신 턴이 위 (펼침). 이전 턴은 접힘 — 클릭해서 멀티턴 맥락 확인.</div>'
        f'{inner}</div></body></html>'
    )


def _write_last_turn_html() -> None:
    try:
        path = _last_turn_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_all_turns_html(), encoding="utf-8")
        _persist_turns()
    except Exception as exc:
        logger.warning("[LastTurn] failed to write html: %s", exc)


def dump_last_turn_html() -> str:
    """Render the recent-turns buffer to HTML and return the file path."""
    _write_last_turn_html()
    return str(_last_turn_path())


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

    record_detail(label, payload)  # single-turn HTML capture (independent of verbose gate)
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
        indented = "\n".join("        " + ln for ln in body.splitlines())
        runtime_logger.log(level, "%s%s\n%s", _c("      ↳ ", "dim"), _c(label, "dim"), indented)
        return

    runtime_logger.log(level, "%s%s", _c("      ↳ ", "dim"), _c(_compact_runtime_detail(label, payload), "dim"))


# ── LLM multi-turn I/O renderer (callback handler) ─────────────────────
#
# Attached to every model.invoke via lf_utils.lf_callbacks(). It is the single
# ground-truth point for "what actually went into the LLM": on_chat_model_start
# receives the final message list (already trimmed / isolated by each node), and
# on_llm_end receives the AI content. Gated by llm_io_enabled() so the default
# tree stays clean. Never raises into the model call.

def _approx_tokens(chars: int) -> str:
    return f"{round(chars / 4 / 1000, 1)}k" if chars >= 1000 else str(chars // 4)


def _msg_text(message: Any) -> str:
    content = getattr(message, "content", message)
    return content if isinstance(content, str) else str(content)


def _msg_oneline(message: Any) -> str:
    role = getattr(message, "type", "?") or "?"
    text = _msg_text(message)
    tool_calls = getattr(message, "tool_calls", None)
    extra = ""
    if tool_calls:
        names = ",".join(str(tc.get("name", "?")) for tc in tool_calls[:3])
        extra = f"  →tool_calls[{names}]"
    return f'{role:<6} "{preview_text(text, max_chars=120)}" ({len(text)} chars){extra}'


class _LLMTraceHandler(BaseCallbackHandler):
    """Render LLM multi-turn input + AI output as children of the current task."""

    raise_error = False

    def _node_label(self, metadata: dict | None) -> str:
        if metadata:
            node = metadata.get("langgraph_node") or metadata.get("agent")
            if node:
                return str(node)
        agents = [t.get("agent") for t in (_RENDER_STATE.get("tasks") or {}).values() if t.get("agent")]
        return agents[-1] if agents else "llm"

    def on_chat_model_start(self, serialized, messages, *, metadata=None, run_id=None, **kwargs):
        msgs = messages[0] if messages else []
        node = self._node_label(metadata)
        record_llm_start(run_id, node, msgs)  # single-turn HTML capture (always)
        if not (_terminal_enabled() and llm_io_enabled()):
            return
        try:
            total_chars = sum(len(_msg_text(m)) for m in msgs)
            header = (
                _c("      ↳ ", "dim") + _c("llm ", "cyan") + _c(node, "cyan")
                + _c(f"  in {len(msgs)} msgs (~{_approx_tokens(total_chars)} tok)", "dim")
            )
            runtime_logger.log(logging.INFO, header)
            for m in msgs:
                runtime_logger.log(logging.INFO, _c("          · " + _msg_oneline(m), "dim"))
        except Exception:
            pass

    def on_llm_end(self, response, *, run_id=None, **kwargs):
        out = _llm_out_repr(response)
        record_llm_end(run_id, out)  # single-turn HTML capture (always)
        if not (_terminal_enabled() and llm_io_enabled()):
            return
        try:
            if out.get("kind") == "tool_calls":
                names = ", ".join(
                    f"{tc.get('name', '?')}({_preview_params(tc.get('args') or {}, max_items=4)})"
                    for tc in (out.get("tool_calls") or [])[:3]
                )
                body = f"tool_calls: {names}"
            else:
                body = f'"{preview_text(out.get("text", ""), max_chars=160)}"'
            runtime_logger.log(logging.INFO, _c("        out → ", "green") + _c(body, "dim"))
        except Exception:
            pass


_LLM_HANDLER: _LLMTraceHandler | None = None


def llm_trace_handler() -> _LLMTraceHandler:
    """Process-wide singleton handler to attach to model callbacks."""
    global _LLM_HANDLER
    if _LLM_HANDLER is None:
        _LLM_HANDLER = _LLMTraceHandler()
    return _LLM_HANDLER


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
    raw_payload = payload or {}
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
        "payload": redact_trace_payload(raw_payload),
    }

    try:
        get_trace_sink().emit(event)
    except Exception as exc:
        if os.getenv("LOCAL_TRACE_RAISE_ERRORS") == "1":
            raise
        logger.warning("[LocalTrace] failed to emit %s: %s", event_type, exc)
    emit_terminal_runtime_log(event)
    record_trace_event(event, raw_payload)  # single-turn HTML capture (un-redacted)
    return event

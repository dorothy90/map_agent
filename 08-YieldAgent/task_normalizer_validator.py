"""Task normalization for planner output.

Narrow layer: field-name alias normalization only. The deterministic gates that
once lived here (allow-list dropping, value blanking, missing-param, cost/fanout)
were removed in Step 3; reference (resolved_refs) application was removed in
Step 5② — the planner now resolves references and the supervisor dispatch guard
validates required params.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


FIELD_ALIASES = {
    "map_lot_ids": "lot_ids",
    "dh_fail_type": "fail_type",
}

# ── Deterministic ordinal-reference resolution (Step 5②-a) ──────────────
# The planner judges the SEMANTICS ("this slot is the N-th item of a prior
# result") and emits an ordinal token like "#1" / "#last". The code then does the
# pure-mechanical part: pick row[N-1] of the relevant recent result and copy the
# value. This replaces reference_resolver's keyword/regex phrase matching (dropped)
# while keeping "첫번째 = row[0]" a 100% deterministic code operation, not an LLM guess.

_ORDINAL_TOKEN_RE = re.compile(r"^#(\d+|last|latest)$")
# "#RN" = N번째 WADS 리포트 참조. #N(행 ordinal)과 별개의 토큰으로, 리포트 줄을
# 직접 인덱싱한다(report N = report_rows[N-1]).
_REPORT_TOKEN_RE = re.compile(r"^#R(\d+)$")

# Sentinel left in a slot when an ordinal reference could not be resolved (out of
# range / no source). It is non-empty on purpose so wads chaining skips it; the
# dispatch path strips it back to "" so the missing-param backstop fires (②-b).
UNRESOLVED_REF = "#unresolved"

# (agent, slot) -> recent-result column semantic to read the value from.
_ORDINAL_SLOT_SEMANTIC = {
    ("lot_history_agent", "lot_ids"): "lot_id",
    ("map_agent", "lot_ids"): "lot_id",
    ("fail_history_agent", "fail_type"): "parameter",
    ("wads_agent", "fail_type"): "parameter",
    # TEMP(relation_tree fail_type): relation_tree now takes the detected parameter via
    # fail_type, so "N번째 parameter relation tree" -> fail_type="#N" must resolve here.
    ("relation_tree_agent", "fail_type"): "parameter",
}

# Fallback row keys per semantic, used when a result has no semantic-tagged column.
_SEMANTIC_ROW_KEYS = {
    "lot_id": ("lot_id", "lot_ids", "lot", "groupkey"),
    "parameter": ("parameter", "param", "fail_type"),
}


def _semantic_column_name(result: dict[str, Any], semantic: str) -> str | None:
    cols = [
        c for c in (result.get("columns") or [])
        if isinstance(c, dict) and c.get("semantic") == semantic
    ]
    return cols[0].get("name") if len(cols) == 1 else None


def _row_semantic_value(row: dict[str, Any], semantic: str, col: str | None) -> str | None:
    for key in ([col] if col else []) + list(_SEMANTIC_ROW_KEYS.get(semantic, ())):
        if not key:
            continue
        value = row.get(key)
        if isinstance(value, list):
            value = value[0] if value else None
        if value in (None, ""):
            continue
        text = str(value).strip()
        if semantic == "lot_id" and "." in text:   # lot.wf groupkey -> lot id
            text = text.split(".", 1)[0]
        return text or None
    return None


def _resolve_ordinal_value(recent_results: list[dict[str, Any]] | None, semantic: str, token: str) -> str | None:
    """Ordinal indexes the DISTINCT semantic values in displayed order, not raw rows.

    Result rows are per-wafer, so one lot repeats across consecutive rows; the user
    sees a deduplicated lot list, so "두번째 lot" = 2nd distinct lot, not row[1].
    Returns None if out of range / no such result (caller clears the slot -> backstop).
    """
    for result in reversed(recent_results or []):
        rows = result.get("rows") or []
        if not rows:
            continue
        col = _semantic_column_name(result, semantic)
        if not col and not any(k in (rows[0] or {}) for k in _SEMANTIC_ROW_KEYS.get(semantic, ())):
            continue
        ordered: list[str] = []
        seen: set[str] = set()
        for row in rows:
            val = _row_semantic_value(row, semantic, col)
            if val and val not in seen:
                seen.add(val)
                ordered.append(val)
        if not ordered:
            continue
        idx = (len(ordered) - 1) if token in ("last", "latest") else int(token) - 1
        if idx < 0 or idx >= len(ordered):
            return None
        return ordered[idx]
    return None


def _is_report_row(row: Any) -> bool:
    """A report row = one degradation report (one parameter×lot with its wafers).
    Mirror of supervisor._is_report_row (supervisor imports this module, not vice versa)."""
    return isinstance(row, dict) and (
        row.get("report_index") not in (None, "")
        or (row.get("groupkeys") and row.get("parameter"))
    )


def _report_rows_of(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-report rows for #RN ordinal resolution. Prefer the dedicated `reports`
    channel (carried even when displayed `rows` are per-wafer); fall back to legacy
    report-shaped rows. Mirror of supervisor._report_rows_of."""
    reports = result.get("reports")
    if isinstance(reports, list) and any(_is_report_row(r) for r in reports):
        return [r for r in reports if _is_report_row(r)]
    return [r for r in (result.get("rows") or []) if _is_report_row(r)]


def _resolve_report_ordinal_value(
    recent_results: list[dict[str, Any]] | None, semantic: str, ordinal: int
) -> str | None:
    """N번째 리포트(report_rows[N-1])에서 semantic 값을 읽는다.

    #N(_resolve_ordinal_value)은 per-wafer 행의 DISTINCT 값을 인덱싱하지만, #RN은
    리포트 줄을 직접 인덱싱한다(report N = row[N-1]) — 인덱싱 체계가 다르다.
    lot 추출('groupkey=lot.wf')은 _row_semantic_value가 그대로 처리한다.
    범위 밖/소스 없음이면 None (caller가 UNRESOLVED_REF로 마크 → dispatch backstop이 물음)."""
    for result in reversed(recent_results or []):
        rows = _report_rows_of(result)
        if not rows:
            continue
        idx = ordinal - 1
        if idx < 0 or idx >= len(rows):
            return None
        return _row_semantic_value(rows[idx], semantic, None)
    return None


def apply_ordinal_ref(agent: str, slots: dict[str, Any], recent_results: list[dict[str, Any]] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Resolve planner ordinal tokens ("#N"/"#last") in slots into concrete values.

    Pure mechanical: the planner already decided this is an ordinal reference; here
    we deterministically read row[N-1]. Unresolvable tokens are cleared (slot left
    empty) so the supervisor dispatch guard can ask — never substitute a wrong value.
    """
    resolved = dict(slots or {})
    trace: list[dict[str, Any]] = []
    for slot, value in list(resolved.items()):
        if not isinstance(value, str):
            continue

        # "#RN" (report ordinal) — resolve from the Nth WADS report row. map_agent의
        # groupkey="#RN"은 (map_agent,groupkey)가 semantic 테이블에 없어 토큰 그대로
        # 두고 supervisor map 경로가 처리한다.
        report_match = _REPORT_TOKEN_RE.match(value.strip())
        if report_match:
            semantic = _ORDINAL_SLOT_SEMANTIC.get((agent, slot))
            if not semantic:
                continue
            out = _resolve_report_ordinal_value(
                recent_results, semantic, int(report_match.group(1))
            )
            if out is None:
                resolved[slot] = UNRESOLVED_REF
                trace.append({"event": "report_ordinal_ref_unresolved", "agent": agent, "slot": slot, "token": value})
            else:
                resolved[slot] = out
                trace.append({"event": "report_ordinal_ref_resolved", "agent": agent, "slot": slot, "token": value, "value": out})
            continue

        match = _ORDINAL_TOKEN_RE.match(value.strip())
        if not match:
            continue
        semantic = _ORDINAL_SLOT_SEMANTIC.get((agent, slot))
        if not semantic:
            resolved[slot] = UNRESOLVED_REF
            trace.append({"event": "ordinal_ref_unmappable", "agent": agent, "slot": slot, "token": value})
            continue
        out = _resolve_ordinal_value(recent_results, semantic, match.group(1))
        if out is None:
            # Reference intended but not resolvable (out of range / no source). Mark it
            # so chaining does NOT silently fill it (Step 5②-b) — the dispatch guard
            # asks instead. UNRESOLVED_REF is non-empty, so _is_placeholder_or_empty is
            # False and the wads chaining skips it; _resolve_chained_params strips it
            # back to "" at the very end so the guard sees an empty required slot.
            resolved[slot] = UNRESOLVED_REF
            trace.append({"event": "ordinal_ref_unresolved", "agent": agent, "slot": slot, "token": value})
        else:
            resolved[slot] = out
            trace.append({"event": "ordinal_ref_resolved", "agent": agent, "slot": slot, "token": value, "value": out})
    return resolved, trace


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


def validate_tasks(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Pass normalized tasks through unchanged.

    Kept as the validator node's contract point. All deterministic gating
    (Step 3) and reference/resolved_refs application (Step 5②) were removed —
    the planner resolves references and the supervisor dispatch guard validates
    required params, so nothing is dropped, blanked, or blocked here.
    """
    return {
        "tasks": [deepcopy(task) for task in tasks or []],
        "issues": [],
        "trace": [],
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
        else:
            lines.append(f"- {label}: `{param}` 오류 ({reason})")
    return "\n".join(lines)

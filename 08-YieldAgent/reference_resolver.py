"""Deterministic ReferenceResolver v1.

The resolver only reads the bounded recent_results index. It does not call an
LLM, does not fuzzy-match semantics, and does not choose a target result by
recency except for the explicit phrase "방금 결과".
"""

from __future__ import annotations

import re
from typing import Any


_RESULT_ID_RE = re.compile(r"\bresult_[A-Za-z0-9]+\b")
_ORDINALS = {
    "첫번째": 1,
    "첫 번째": 1,
    "두번째": 2,
    "두 번째": 2,
}
_ORDINAL_RE = r"(?P<ordinal>첫\s*번째|첫번째|두\s*번째|두번째)"
_LOT_RE = re.compile(fr"{_ORDINAL_RE}\s*(?:lot|LOT|Lot)")
_PARAM_RE = re.compile(fr"{_ORDINAL_RE}\s*(?:파라미터|parameter|PARAMETER|Parameter)")
_MAP_RE = re.compile(fr"{_ORDINAL_RE}\s*(?:map|MAP|Map|맵)")
_REPORT_WORD_RE = re.compile(fr"{_ORDINAL_RE}\s*(?:리포트|report|REPORT|Report)")
_REPORT_NUM_LIST_RE = re.compile(
    r"(?P<numbers>\d+(?:\s*,\s*\d+)*)\s*(?:번째|번)?\s*(?:리포트|report|REPORT|Report)"
)


def _issue(reference: str, reason: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "type": "ambiguous_reference",
        "reference": reference,
        "reason": reason,
    }
    payload.update({k: v for k, v in extra.items() if v not in (None, [], {})})
    return payload


def _ordinal_value(raw: str) -> int:
    return _ORDINALS[re.sub(r"\s+", " ", raw.strip())]


def _find_result(recent_results: list[dict[str, Any]], result_id: str) -> dict[str, Any] | None:
    return next((r for r in recent_results if r.get("result_id") == result_id), None)


def _display_result_id(result_id: Any) -> str:
    text = str(result_id or "").strip()
    if text.startswith("result_") and len(text) > 18:
        return f"result_{text.removeprefix('result_')[:8]}"
    return text


def _display_agent(source_agent: Any) -> str:
    labels = {
        "wads_agent": "WADS 결과",
        "map_agent": "Map 결과",
        "fail_history_agent": "Fail history 결과",
        "lot_history_agent": "Lot history 결과",
    }
    text = str(source_agent or "").strip()
    return labels.get(text, text or "이전 결과")


def _display_kind(kind: Any) -> str:
    labels = {
        "table": "테이블",
        "summary": "요약",
        "document": "문서",
        "image": "이미지",
        "report": "리포트",
        "artifact": "아티팩트",
    }
    text = str(kind or "").strip()
    return labels.get(text, text or "결과")


def _row_preview(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    preferred = (
        "lot_id",
        "lotid",
        "lot_cd",
        "lotcd",
        "groupkey",
        "wf_id",
        "parameter",
        "fail_type",
        "category",
        "map_oper",
        "cause_oper",
    )
    parts: list[str] = []
    for key in preferred:
        value = row.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
        if len(parts) >= 3:
            break
    if not parts:
        for key, value in row.items():
            if value not in (None, ""):
                parts.append(f"{key}={value}")
            if len(parts) >= 3:
                break
    preview = ", ".join(parts)
    return preview[:120]


def _candidate_label(result: dict[str, Any]) -> str:
    rows = result.get("rows", []) or []
    refs = result.get("artifact_refs", []) or []
    title = str(result.get("title") or "").strip()
    parts = [
        _display_agent(result.get("source_agent")),
        _display_kind(result.get("kind")),
    ]
    if rows:
        parts.append(f"{len(rows)}행")
        preview = _row_preview(rows[0])
        if preview:
            parts.append(preview)
    if refs:
        parts.append(f"아티팩트 {len(refs)}개")
    if title:
        parts.append(title[:80])
    display_id = _display_result_id(result.get("result_id"))
    if display_id:
        parts.append(f"id={display_id}")
    return " · ".join(parts)


def _candidate_options(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for index, result in enumerate(results, start=1):
        result_id = str(result.get("result_id") or "").strip()
        if not result_id:
            continue
        rows = result.get("rows", []) or []
        refs = result.get("artifact_refs", []) or []
        options.append(
            {
                "index": index,
                "result_id": result_id,
                "display_id": _display_result_id(result_id),
                "label": _candidate_label(result),
                "source_agent": str(result.get("source_agent") or ""),
                "kind": str(result.get("kind") or ""),
                "title": str(result.get("title") or ""),
                "row_count": len(rows),
                "artifact_count": len(refs),
                "created_at": str(result.get("created_at") or ""),
            }
        )
    return options


def _explicit_result_ids(text: str) -> list[str]:
    return list(dict.fromkeys(_RESULT_ID_RE.findall(text or "")))


def _semantic_columns(result: dict[str, Any], semantic: str) -> list[dict[str, Any]]:
    return [
        column
        for column in result.get("columns", []) or []
        if isinstance(column, dict) and column.get("semantic") == semantic
    ]


def _results_with_semantic_rows(recent_results: list[dict[str, Any]], semantic: str) -> list[dict[str, Any]]:
    return [
        result
        for result in recent_results
        if _semantic_columns(result, semantic) and result.get("rows")
    ]


def _has_report_rows(result: dict[str, Any]) -> bool:
    for row in result.get("rows", []) or []:
        if not isinstance(row, dict):
            continue
        if row.get("report_index") not in (None, ""):
            return True
        if row.get("groupkeys") and row.get("category") and row.get("parameter"):
            return True
    return False


def _results_with_report_rows(recent_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [result for result in recent_results if _has_report_rows(result)]


def _target_result(
    text: str,
    recent_results: list[dict[str, Any]],
    *,
    needs_target: bool,
    semantic_target: str = "",
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    resolved_refs: dict[str, Any] = {}
    explicit_ids = _explicit_result_ids(text)

    if len(explicit_ids) > 1:
        issues.append(_issue("result_id", "multiple_explicit_result_ids", candidates=explicit_ids))
        return None, issues, resolved_refs

    if explicit_ids:
        target = _find_result(recent_results, explicit_ids[0])
        if not target:
            issues.append(_issue("result_id", "unknown_explicit_result_id", result_id=explicit_ids[0]))
            return None, issues, resolved_refs
        if "방금 결과" in text and recent_results and target.get("result_id") != recent_results[-1].get("result_id"):
            issues.append(_issue("방금 결과", "conflicting_explicit_and_recent_result", result_id=explicit_ids[0]))
            return None, issues, resolved_refs
        resolved_refs["result_id"] = target.get("result_id")
        return target, issues, resolved_refs

    if "이 결과" in text:
        issues.append(_issue("이 결과", "explicit_result_id_required"))
        return None, issues, resolved_refs

    if "방금 결과" in text:
        if not recent_results:
            issues.append(_issue("방금 결과", "no_recent_results"))
            return None, issues, resolved_refs
        target = recent_results[-1]
        resolved_refs["result_id"] = target.get("result_id")
        return target, issues, resolved_refs

    if needs_target:
        if len(recent_results) == 1:
            target = recent_results[0]
            resolved_refs["result_id"] = target.get("result_id")
            return target, issues, resolved_refs
        if semantic_target:
            semantic_candidates = (
                _results_with_report_rows(recent_results)
                if semantic_target == "report"
                else _results_with_semantic_rows(recent_results, semantic_target)
            )
            if len(semantic_candidates) == 1:
                target = semantic_candidates[0]
                resolved_refs["result_id"] = target.get("result_id")
                return target, issues, resolved_refs
        issues.append(
            _issue(
                "result",
                "target_result_required",
                candidates=[r.get("result_id") for r in recent_results if r.get("result_id")],
                candidate_options=_candidate_options(recent_results),
            )
        )
    return None, issues, resolved_refs


def _report_ordinals(text: str) -> list[int]:
    ordinals: list[int] = []
    seen: set[int] = set()

    def add(value: int) -> None:
        if value > 0 and value not in seen:
            seen.add(value)
            ordinals.append(value)

    for match in _REPORT_WORD_RE.finditer(text or ""):
        add(_ordinal_value(match.group("ordinal")))

    for match in _REPORT_NUM_LIST_RE.finditer(text or ""):
        for raw in str(match.group("numbers") or "").split(","):
            raw = raw.strip()
            if raw.isdigit():
                add(int(raw))

    return ordinals


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _lotid_from_groupkey(value: Any) -> str:
    text = str(value or "").strip()
    if "." in text:
        return text.rsplit(".", 1)[0]
    return text


def _wf_id_from_groupkey(value: Any) -> str:
    text = str(value or "").strip()
    if "." in text:
        return text.rsplit(".", 1)[1]
    return ""


def _map_oper_from_report_row(row: dict[str, Any]) -> str:
    explicit = str(row.get("map_oper") or "").strip().upper()
    if explicit in {"PT1H", "PT1C"}:
        return explicit
    category = str(row.get("category") or "").upper()
    if "PT1C" in category:
        return "PT1C"
    if "PT1H" in category:
        return "PT1H"
    return ""


def _resolve_report_refs(
    result: dict[str, Any],
    *,
    ordinals: list[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    resolved: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    rows = result.get("rows", []) or []

    for ordinal in ordinals:
        row_index = ordinal - 1
        if row_index < 0 or row_index >= len(rows):
            issues.append(_issue("리포트", "ordinal_out_of_range", result_id=result.get("result_id"), ordinal=ordinal))
            continue
        row = rows[row_index]
        if not isinstance(row, dict):
            issues.append(_issue("리포트", "row_not_object", result_id=result.get("result_id"), ordinal=ordinal))
            continue
        groupkeys = [str(v).strip() for v in _as_list(row.get("groupkeys") or row.get("groupkey")) if str(v).strip()]
        if not groupkeys:
            issues.append(_issue("리포트", "report_groupkeys_missing", result_id=result.get("result_id"), ordinal=ordinal))
            continue
        lot_ids = [str(v).strip() for v in _as_list(row.get("lot_ids")) if str(v).strip()]
        if not lot_ids:
            lot_ids = list(dict.fromkeys(_lotid_from_groupkey(groupkey) for groupkey in groupkeys if _lotid_from_groupkey(groupkey)))
        wf_ids = [str(v).strip() for v in _as_list(row.get("wf_ids")) if str(v).strip()]
        if not wf_ids:
            wf_ids = list(dict.fromkeys(_wf_id_from_groupkey(groupkey) for groupkey in groupkeys if _wf_id_from_groupkey(groupkey)))
        resolved.append(
            {
                "result_id": result.get("result_id"),
                "ordinal": ordinal,
                "row_index": row_index,
                "row": row,
                "groupkeys": groupkeys,
                "lot_ids": lot_ids,
                "wf_ids": wf_ids,
                "map_oper": _map_oper_from_report_row(row),
            }
        )
    return resolved, issues


def _resolve_row_value(
    result: dict[str, Any],
    *,
    reference: str,
    ordinal: int,
    semantic: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    columns = _semantic_columns(result, semantic)
    if not columns:
        return None, _issue(reference, "missing_semantic_column", result_id=result.get("result_id"), semantic=semantic)
    if len(columns) > 1:
        return None, _issue(
            reference,
            "multiple_semantic_columns",
            result_id=result.get("result_id"),
            semantic=semantic,
            candidates=[c.get("name") for c in columns],
        )

    row_index = ordinal - 1
    rows = result.get("rows", []) or []
    if row_index >= len(rows):
        return None, _issue(reference, "ordinal_out_of_range", result_id=result.get("result_id"), ordinal=ordinal)

    column_name = columns[0].get("name")
    value = rows[row_index].get(column_name)
    if value in (None, ""):
        return None, _issue(
            reference,
            "semantic_value_missing",
            result_id=result.get("result_id"),
            row_index=row_index,
            column=column_name,
        )

    return {
        "result_id": result.get("result_id"),
        "row_index": row_index,
        "column": column_name,
        "semantic": semantic,
        "value": str(value),
        "row": rows[row_index],
    }, None


def _map_candidates(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        ref
        for ref in result.get("artifact_refs", []) or []
        if isinstance(ref, dict) and ref.get("semantic") == "map"
    ]


def _resolve_map_ref(
    recent_results: list[dict[str, Any]],
    target: dict[str, Any] | None,
    *,
    reference: str,
    ordinal: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if target is None:
        map_results = [r for r in recent_results if _map_candidates(r)]
        if len(map_results) != 1:
            return None, _issue(
                reference,
                "target_result_required",
                candidates=[r.get("result_id") for r in map_results if r.get("result_id")],
                candidate_options=_candidate_options(map_results),
            )
        target = map_results[0]

    candidates = _map_candidates(target)
    index = ordinal - 1
    if index >= len(candidates):
        return None, _issue(reference, "ordinal_out_of_range", result_id=target.get("result_id"), ordinal=ordinal)
    ref = candidates[index]
    return {
        "result_id": target.get("result_id"),
        "artifact_ref": ref.get("artifact_ref"),
        "title": ref.get("title", ""),
        "kind": ref.get("kind", ""),
        "semantic": ref.get("semantic", ""),
    }, None


def resolve_references(text: str, recent_results: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Resolve supported reference expressions deterministically.

    Returns either {"resolved_refs": {...}} or {"issues": [...]}.
    If the text contains no supported reference expression, resolved_refs is an
    empty dict.
    """

    recent_results = recent_results or []
    lot_match = _LOT_RE.search(text or "")
    param_match = _PARAM_RE.search(text or "")
    map_match = _MAP_RE.search(text or "")
    report_ordinals = _report_ordinals(text or "")
    has_result_ref = "방금 결과" in (text or "") or "이 결과" in (text or "") or bool(_explicit_result_ids(text or ""))
    needs_target = bool(lot_match or param_match or report_ordinals)
    semantic_target = ""
    if param_match:
        semantic_target = "parameter"
    elif lot_match:
        semantic_target = "lot_id"
    elif report_ordinals:
        semantic_target = "report"

    target, issues, resolved_refs = _target_result(
        text or "",
        recent_results,
        needs_target=needs_target,
        semantic_target=semantic_target,
    )

    if lot_match and not issues:
        ordinal = _ordinal_value(lot_match.group("ordinal"))
        resolved, issue = _resolve_row_value(
            target or {},
            reference=lot_match.group(0),
            ordinal=ordinal,
            semantic="lot_id",
        )
        if issue:
            issues.append(issue)
        elif resolved:
            resolved_refs["lot"] = resolved
            resolved_refs["lot_ids"] = [resolved["value"]]

    if param_match and not issues:
        ordinal = _ordinal_value(param_match.group("ordinal"))
        resolved, issue = _resolve_row_value(
            target or {},
            reference=param_match.group(0),
            ordinal=ordinal,
            semantic="parameter",
        )
        if issue:
            issues.append(issue)
        elif resolved:
            resolved_refs["parameter"] = resolved
            resolved_refs["parameters"] = [resolved["value"]]

    if report_ordinals and not issues:
        reports, report_issues = _resolve_report_refs(target or {}, ordinals=report_ordinals)
        if report_issues:
            issues.extend(report_issues)
        elif reports:
            resolved_refs["reports"] = reports
            resolved_refs["report_groupkeys"] = [
                {
                    "ordinal": report["ordinal"],
                    "groupkeys": report["groupkeys"],
                    "map_oper": report.get("map_oper", ""),
                    "lot_ids": report.get("lot_ids", []),
                    "wf_ids": report.get("wf_ids", []),
                }
                for report in reports
            ]
            resolved_refs.setdefault("result_id", reports[0].get("result_id"))

    if map_match and not issues:
        ordinal = _ordinal_value(map_match.group("ordinal"))
        resolved, issue = _resolve_map_ref(
            recent_results,
            target,
            reference=map_match.group(0),
            ordinal=ordinal,
        )
        if issue:
            issues.append(issue)
        elif resolved:
            resolved_refs["map"] = resolved
            resolved_refs["maps"] = [resolved]
            resolved_refs.setdefault("result_id", resolved["result_id"])

    if issues:
        return {"issues": issues}
    if has_result_ref or lot_match or param_match or map_match or report_ordinals:
        return {"resolved_refs": resolved_refs}
    return {"resolved_refs": {}}

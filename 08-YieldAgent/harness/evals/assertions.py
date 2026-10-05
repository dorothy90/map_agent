"""Deterministic release assertions; no model wording is used as an oracle."""
from __future__ import annotations

import hashlib
from pathlib import Path


SUCCESS_TOOL_STATUSES = {"success", "empty"}


def _evidence(run: dict, kind: str, check_id: str) -> dict:
    return (run.get(f"{kind}_evidence") or {}).get(check_id) or {}


def evaluate_case(case: dict, run: dict) -> dict:
    reasons: list[str] = []
    outcome = case.get("expected_outcome", "success")
    status = run.get("status")
    if outcome != "success":
        expected = set(case.get("expected_statuses") or case.get("expected") or [])
        if expected and status not in expected:
            reasons.append(f"unexpected_status:{status}")
        return {"passed": not reasons, "reasons": reasons, "outcome": outcome}

    if status != "completed":
        reasons.append("status_not_completed")
    calls = run.get("calls") or []
    observations = run.get("observations") or []
    statuses: dict[str, list[str]] = {}
    for item in [*calls, *observations]:
        name = item.get("tool_name")
        if name:
            statuses.setdefault(name, []).append(item.get("status", ""))
    for name in case.get("requires_tools", []):
        seen = statuses.get(name, [])
        if not seen:
            reasons.append(f"required_tool_missing:{name}")
        elif not any(value in SUCCESS_TOOL_STATUSES for value in seen):
            reasons.append(f"required_tool_failed:{name}")
    if case.get("no_data_tools") and statuses:
        reasons.append("unexpected_tool_call")
    if case.get("requires_live_result"):
        own = [o for o in observations if o.get("run_id") == run.get("run_id")]
        if not own or not any(o.get("status") in SUCCESS_TOOL_STATUSES for o in own):
            reasons.append("missing_successful_live_result")
        elif not all((o.get("provenance") or {}).get("data_origin") == "live" for o in own):
            reasons.append("non_live_result")
    for check in case.get("numeric_checks", []):
        check_id = check["id"]
        evidence = _evidence(run, "numeric", check_id)
        if evidence.get("passed") is not True:
            reasons.append(f"numeric_check_failed:{check_id}")
        if not evidence.get("sha256"):
            reasons.append(f"numeric_evidence_unbound:{check_id}")
    for check in case.get("artifact_checks", []):
        check_id = check["id"]
        evidence = _evidence(run, "artifact", check_id)
        if evidence.get("passed") is not True:
            reasons.append(f"artifact_check_failed:{check_id}")
        if not evidence.get("sha256"):
            reasons.append(f"artifact_evidence_unbound:{check_id}")
    return {"passed": not reasons, "reasons": reasons, "outcome": outcome}


def validate_review_manifest(review: dict, required, build: dict, run_ids: set[str],
                             numeric_hashes: set[str], artifact_hashes: set[str]):
    reasons: list[str] = []
    for name in required:
        record = review.get(name) or {}
        if record.get("passed") is not True:
            reasons.append(f"review_not_passed:{name}")
            continue
        evidence = record.get("evidence") or []
        if not evidence:
            reasons.append(f"review_evidence_missing:{name}")
        for item in evidence:
            path = Path(item.get("path", ""))
            if not path.is_file():
                reasons.append(f"evidence_file_missing:{name}")
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if item.get("sha256") != digest:
                reasons.append(f"evidence_hash_mismatch:{name}")
            if item.get("code_hash") != build.get("code_hash") or item.get("model_hash") != build.get("model_hash"):
                reasons.append(f"evidence_build_mismatch:{name}")
            if not set(item.get("run_ids") or []) <= run_ids or not item.get("run_ids"):
                reasons.append(f"evidence_run_unbound:{name}")
            bound = set(item.get("numeric_hashes") or []) | set(item.get("artifact_hashes") or [])
            known = numeric_hashes | artifact_hashes
            if not bound or not bound <= known:
                reasons.append(f"evidence_result_unbound:{name}")
    return not reasons, reasons

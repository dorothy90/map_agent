"""Human-in-the-loop gate for validated task issues.

The gate turns validator issues into interrupt questions. It does not edit
tasks; supervisor may consume collected responses when projecting state.
"""

from __future__ import annotations

import re
from typing import Any


SUPPORTED_HITL_ISSUE_TYPES = {
    "missing_param",
    "ambiguous_reference",
    "high_cost",
    "excessive_fanout",
}
CONFIRMATION_ISSUE_TYPES = {"high_cost", "excessive_fanout"}

PARAM_LABELS = {
    "lotcd": "제품코드",
    "lot_ids": "LOT ID",
    "lot_ids|groupkey": "LOT ID 또는 lot.wf",
    "groupkey": "lot.wf",
    "map_oper": "맵 공정",
    "cause_oper": "원인 공정",
    "fail_type": "불량/파라미터",
}


def _display_result_id(result_id: Any) -> str:
    text = str(result_id or "").strip()
    if text.startswith("result_") and len(text) > 18:
        return f"result_{text.removeprefix('result_')[:8]}"
    return text


def _candidate_options(issue: dict[str, Any]) -> list[dict[str, Any]]:
    raw_options = issue.get("candidate_options") or []
    options: list[dict[str, Any]] = []
    if isinstance(raw_options, list):
        for fallback_index, raw in enumerate(raw_options, start=1):
            if not isinstance(raw, dict):
                continue
            result_id = str(raw.get("result_id") or "").strip()
            label = str(raw.get("label") or "").strip()
            index = raw.get("index") or fallback_index
            display_id = str(raw.get("display_id") or _display_result_id(result_id)).strip()
            if not label:
                label = display_id or result_id or f"후보 {index}"
            option = dict(raw)
            option.update(
                {
                    "index": index,
                    "label": label,
                    "value": result_id,
                    "display_id": display_id,
                }
            )
            options.append(option)
    if options:
        return options

    candidates = issue.get("candidates") or []
    if not isinstance(candidates, list):
        return []
    for index, result_id in enumerate(candidates, start=1):
        display_id = _display_result_id(result_id)
        options.append(
            {
                "index": index,
                "label": f"이전 결과 id={display_id}",
                "value": str(result_id or ""),
                "display_id": display_id,
            }
        )
    return options


def _candidate_lines(issue: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for option in _candidate_options(issue):
        lines.append(f"{option.get('index')}. {option.get('label')}")
    return lines


def select_hitl_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only blocking issues that require user confirmation."""

    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for issue in issues or []:
        if issue.get("type") not in SUPPORTED_HITL_ISSUE_TYPES:
            continue
        if not issue.get("blocking", issue.get("severity") == "error"):
            continue
        key = (
            issue.get("type"),
            issue.get("task_id", ""),
            issue.get("agent", ""),
            issue.get("param", ""),
            issue.get("reference", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


def hitl_question_for_issue(issue: dict[str, Any]) -> str:
    issue_type = issue.get("type", "")
    task_id = issue.get("task_id", "")
    agent = issue.get("agent", "")
    label = f"{task_id}/{agent}" if task_id or agent else "현재 요청"

    if issue_type == "missing_param":
        param = issue.get("param", "")
        param_label = PARAM_LABELS.get(param, param or "필수 값")
        if param == "map_oper":
            return f"{label} 실행 전에 {param_label}이 필요합니다. PT1H 또는 PT1C 중 하나를 입력해주세요."
        if param in {"lot_ids", "lot_ids|groupkey"}:
            return f"{label} 실행 전에 {param_label}가 필요합니다. 예: 4SS2DPD 또는 4SS2DPD.03"
        return f"{label} 실행 전에 {param_label} 값을 입력해주세요."

    if issue_type == "ambiguous_reference":
        reference = issue.get("reference", "이전 결과")
        candidate_lines = _candidate_lines(issue)
        if candidate_lines:
            return (
                f"`{reference}` 참조가 모호합니다. 아래 후보 중 번호로 답해주세요.\n"
                + "\n".join(candidate_lines)
            )
        return f"`{reference}` 참조가 모호합니다. 어떤 결과를 사용할지 명확히 입력해주세요."

    if issue_type == "high_cost":
        cost = issue.get("cost_units", "")
        return f"예상 실행 비용이 높은 계획입니다(cost={cost}). 진행할지 답해주세요."

    if issue_type == "excessive_fanout":
        count = issue.get("task_count", "")
        return f"작업이 {count}개로 많이 생성되었습니다. 모두 진행할지 답해주세요."

    return "실행 전에 사용자 확인이 필요합니다. 진행할지 답해주세요."


def hitl_selected_result_id(issue: dict[str, Any], response: Any) -> str:
    if issue.get("type") != "ambiguous_reference":
        return ""
    text = str(response or "").strip()
    if not text:
        return ""
    compact = re.sub(r"\s+", "", text).lower()
    for option in _candidate_options(issue):
        result_id = str(option.get("value") or option.get("result_id") or "").strip()
        display_id = str(option.get("display_id") or _display_result_id(result_id)).strip()
        index = str(option.get("index") or "").strip()
        if index and compact in {index, f"{index}번", f"후보{index}", f"{index}번째"}:
            return result_id
        if result_id and result_id.lower() in text.lower():
            return result_id
        if display_id and display_id.lower() in text.lower():
            return result_id
    return ""


def should_abort_after_response(issue: dict[str, Any], response: Any) -> bool:
    issue_type = issue.get("type", "")
    if issue_type == "ambiguous_reference":
        return not hitl_selected_result_id(issue, response)
    return False


def hitl_abort_message(issue: dict[str, Any]) -> str:
    issue_type = issue.get("type", "")
    if issue_type == "ambiguous_reference":
        return "참조가 모호해 실행하지 않았습니다. 후보 번호나 결과 설명을 포함해 다시 요청해주세요."
    if issue_type in CONFIRMATION_ISSUE_TYPES:
        return "사용자 확인이 없어 작업 실행을 중단했습니다."
    return "사용자 확인이 필요해 작업 실행을 중단했습니다."


def hitl_interrupt_payload(issue: dict[str, Any]) -> dict[str, Any]:
    question = hitl_question_for_issue(issue)
    payload = {
        "type": "hitl_gate",
        "interrupt_type": issue.get("type", "hitl_gate"),
        "issue": issue,
        "param": issue.get("param", ""),
        "message": question,
        "route": issue.get("agent", ""),
    }
    options = _candidate_options(issue)
    if options:
        payload["options"] = options
    return payload


def hitl_response_record(issue: dict[str, Any], response: Any) -> dict[str, Any]:
    return {
        "issue_type": issue.get("type", ""),
        "task_id": issue.get("task_id", ""),
        "agent": issue.get("agent", ""),
        "param": issue.get("param", ""),
        "reference": issue.get("reference", ""),
        "response": "" if response is None else str(response).strip(),
        "selected_result_id": hitl_selected_result_id(issue, response),
    }

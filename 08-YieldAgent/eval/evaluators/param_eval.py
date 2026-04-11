"""
파라미터 추출 평가 — RouteResponse 필드별 비교
=============================================
- exact match: lotcd, unit, map_type, map_oper, ref_date, wads_start_tm, wads_end_tm
- F1 score: filter_params (list)
- contains match: yield_lot_ids, map_lot_ids, lh_lot_ids (순서 무관 쉼표 구분)
"""
from __future__ import annotations

from typing import Any


def _f1_score(actual: list[str], expected: list[str]) -> float:
    """두 리스트의 F1 score (순서 무관)."""
    actual_set = set(actual)
    expected_set = set(expected)
    if not actual_set and not expected_set:
        return 1.0
    if not actual_set or not expected_set:
        return 0.0
    tp = len(actual_set & expected_set)
    precision = tp / len(actual_set)
    recall = tp / len(expected_set)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _comma_set_match(actual: str, expected: str) -> float:
    """쉼표 구분 문자열을 set으로 변환 후 비교. 순서 무관."""
    actual_set = set(s.strip() for s in actual.split(",") if s.strip()) if actual else set()
    expected_set = set(s.strip() for s in expected.split(",") if s.strip()) if expected else set()
    if not actual_set and not expected_set:
        return 1.0
    if actual_set == expected_set:
        return 1.0
    return 0.0


# exact match 대상 필드들
EXACT_FIELDS = ["lotcd", "unit", "map_type", "map_oper", "ref_date",
                "wads_start_tm", "wads_end_tm", "dh_fail_type", "dh_cause_oper"]

# 리스트 F1 대상
LIST_FIELDS = ["filter_params"]

# 쉼표 구분 set match 대상
COMMA_SET_FIELDS = ["yield_lot_ids", "yield_groupkey", "map_lot_id", "map_lot_ids",
                    "map_groupkey", "map_wf_ids", "lh_lot_ids"]


def score_params(actual, expected: dict[str, Any]) -> dict[str, Any]:
    """RouteResponse 파라미터 추출 스코어링.

    Args:
        actual: RouteResponse Pydantic 모델 인스턴스
        expected: 골든 데이터의 expected dict (next 제외)

    Returns:
        {field_name: {"score": float, "actual": Any, "expected": Any}, ...,
         "overall": float}
    """
    scores = {}
    checked = 0
    total_score = 0.0

    for field in EXACT_FIELDS:
        if field not in expected:
            continue
        actual_val = getattr(actual, field, "")
        expected_val = expected[field]
        match = 1.0 if actual_val == expected_val else 0.0
        scores[field] = {"score": match, "actual": actual_val, "expected": expected_val}
        checked += 1
        total_score += match

    for field in LIST_FIELDS:
        if field not in expected:
            continue
        actual_val = getattr(actual, field, [])
        expected_val = expected[field]
        f1 = _f1_score(actual_val, expected_val)
        scores[field] = {"score": f1, "actual": actual_val, "expected": expected_val}
        checked += 1
        total_score += f1

    for field in COMMA_SET_FIELDS:
        if field not in expected:
            continue
        actual_val = getattr(actual, field, "")
        expected_val = expected[field]
        match = _comma_set_match(actual_val, expected_val)
        scores[field] = {"score": match, "actual": actual_val, "expected": expected_val}
        checked += 1
        total_score += match

    scores["overall"] = total_score / checked if checked > 0 else 1.0
    return scores


def param_accuracy(all_scores: list[dict]) -> dict[str, float]:
    """필드별 평균 정확도 집계.

    Returns:
        {"overall": float, "lotcd": float, "filter_params": float, ...}
    """
    field_totals: dict[str, list[float]] = {}
    for case_scores in all_scores:
        for field, detail in case_scores.items():
            if field == "overall":
                field_totals.setdefault("overall", []).append(detail)
            elif isinstance(detail, dict) and "score" in detail:
                field_totals.setdefault(field, []).append(detail["score"])

    return {
        field: sum(vals) / len(vals) if vals else 0.0
        for field, vals in field_totals.items()
    }

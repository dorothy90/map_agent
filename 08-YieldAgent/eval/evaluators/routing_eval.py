"""
라우팅 평가 — RouteResponse.next 필드 exact match
=================================================
"""
from __future__ import annotations

from typing import Any


def score_routing(actual_next: str, expected_next: str) -> dict[str, Any]:
    """라우팅 결정의 정확도 스코어링.

    Returns:
        {"match": bool, "actual": str, "expected": str}
    """
    return {
        "match": actual_next == expected_next,
        "actual": actual_next,
        "expected": expected_next,
    }


def routing_accuracy(results: list[dict]) -> float:
    """전체 라우팅 정확도 (0.0 ~ 1.0)."""
    if not results:
        return 0.0
    correct = sum(1 for r in results if r["match"])
    return correct / len(results)

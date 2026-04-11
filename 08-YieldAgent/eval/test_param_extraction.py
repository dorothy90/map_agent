"""
파라미터 추출 정확도 테스트
==========================
supervisor LLM 응답의 RouteResponse 필드들이 골든 기대값과 일치하는지 검증.
routing_golden.json의 expected 필드 중 next 이외 모든 파라미터를 비교.

실행:
    cd map_agent/08-YieldAgent
    python -m pytest eval/test_param_extraction.py -v -m slow --tb=short
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from common import extract_json_from_llm, get_llm
from prompts import SUPERVISOR_SYSTEM_PROMPT
from supervisor import RouteResponse

from eval.conftest import load_golden
from eval.evaluators.param_eval import param_accuracy, score_params


def _run_supervisor_routing(llm, query: str, date_strs: dict, max_retries: int = 2) -> RouteResponse:
    prompt = SUPERVISOR_SYSTEM_PROMPT.format(**date_strs)
    last_error = None
    for _ in range(max_retries + 1):
        response = llm.invoke(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": query},
            ],
            max_tokens=2048,
        )
        try:
            return extract_json_from_llm(response.content.strip(), RouteResponse)
        except (ValueError, Exception) as e:
            last_error = e
    raise last_error


# 골든 데이터 중 파라미터 검증 대상 (next 외에 1개 이상 기대값이 있는 케이스)
_GOLDEN = load_golden("routing_golden.json")
_PARAM_CASES = [
    c for c in _GOLDEN
    if len([k for k in c["expected"] if k != "next"]) > 0
]
_PARAM_IDS = [c["id"] for c in _PARAM_CASES]


@pytest.fixture(scope="module")
def llm():
    return get_llm()


@pytest.mark.slow
@pytest.mark.parametrize("case", _PARAM_CASES, ids=_PARAM_IDS)
def test_param_extraction(llm, frozen_date_strs, case):
    """각 골든 케이스의 파라미터 추출 정확도."""
    decision = _run_supervisor_routing(llm, case["input_query"], frozen_date_strs)

    # next 필드 제외하고 파라미터만 비교
    expected_params = {k: v for k, v in case["expected"].items() if k != "next"}
    scores = score_params(decision, expected_params)

    # 개별 필드 실패 리포트
    failures = []
    for field, detail in scores.items():
        if field == "overall":
            continue
        if isinstance(detail, dict) and detail["score"] < 1.0:
            failures.append(
                f"  {field}: expected={detail['expected']}, actual={detail['actual']}"
            )

    assert scores["overall"] >= 0.8, (
        f"[{case['id']}] 파라미터 추출 점수 {scores['overall']:.2f} < 0.8\n"
        f"query=\"{case['input_query']}\"\n"
        f"실패 필드:\n" + "\n".join(failures)
    )


@pytest.mark.slow
def test_param_accuracy_summary(llm, frozen_date_strs):
    """전체 파라미터 추출 정확도 요약 (>= 90% 목표)."""
    all_scores = []
    for case in _PARAM_CASES:
        try:
            decision = _run_supervisor_routing(llm, case["input_query"], frozen_date_strs)
            expected_params = {k: v for k, v in case["expected"].items() if k != "next"}
            all_scores.append(score_params(decision, expected_params))
        except Exception:
            # 파싱 실패 시 모든 필드 0점
            expected_params = {k: v for k, v in case["expected"].items() if k != "next"}
            zero_scores = {
                field: {"score": 0.0, "actual": "ERROR", "expected": v}
                for field, v in expected_params.items()
            }
            zero_scores["overall"] = 0.0
            all_scores.append(zero_scores)

    field_acc = param_accuracy(all_scores)
    print("\n파라미터별 정확도:")
    for field, acc in sorted(field_acc.items()):
        print(f"  {field}: {acc:.1%}")
    assert field_acc.get("overall", 0) >= 0.90, (
        f"전체 파라미터 정확도 {field_acc.get('overall', 0):.1%} < 90% 목표"
    )

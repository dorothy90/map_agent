"""
라우팅 정확도 테스트
===================
supervisor prompt + LLM → extract_json_from_llm → RouteResponse.next 비교.
노드 격리 테스트: 전체 그래프 / DB 불필요, LLM 호출만 필요.

실행:
    cd map_agent/08-YieldAgent
    python -m pytest eval/test_routing.py -v -m slow --tb=short
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
from eval.evaluators.routing_eval import routing_accuracy, score_routing


def _format_supervisor_prompt(date_strs: dict) -> str:
    """SUPERVISOR_SYSTEM_PROMPT에 날짜 변수를 주입."""
    return SUPERVISOR_SYSTEM_PROMPT.format(**date_strs)


def _run_supervisor_routing(llm, query: str, date_strs: dict, max_retries: int = 2) -> RouteResponse:
    """supervisor prompt로 LLM 호출 → RouteResponse 파싱.

    LLM이 가끔 malformed 응답을 반환하므로 최대 max_retries회 재시도.
    """
    prompt = _format_supervisor_prompt(date_strs)
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


# ── 골든 데이터셋 로드 ──────────────────────────────────────
_GOLDEN = load_golden("routing_golden.json")
_GOLDEN_IDS = [c["id"] for c in _GOLDEN]


@pytest.fixture(scope="module")
def llm():
    return get_llm()


@pytest.mark.slow
@pytest.mark.parametrize("case", _GOLDEN, ids=_GOLDEN_IDS)
def test_routing_next(llm, frozen_date_strs, case):
    """각 골든 케이스에 대해 라우팅 next 값이 기대와 일치하는지."""
    decision = _run_supervisor_routing(llm, case["input_query"], frozen_date_strs)
    expected_next = case["expected"]["next"]
    result = score_routing(decision.next, expected_next)
    assert result["match"], (
        f"[{case['id']}] 라우팅 불일치: "
        f"expected={expected_next}, actual={decision.next}, "
        f"query=\"{case['input_query']}\""
    )


@pytest.mark.slow
def test_routing_accuracy_summary(llm, frozen_date_strs):
    """전체 골든셋 라우팅 정확도 요약 (>= 95% 목표)."""
    results = []
    for case in _GOLDEN:
        try:
            decision = _run_supervisor_routing(llm, case["input_query"], frozen_date_strs)
            results.append(score_routing(decision.next, case["expected"]["next"]))
        except Exception as e:
            results.append({"match": False, "actual": f"ERROR: {e}", "expected": case["expected"]["next"]})

    acc = routing_accuracy(results)
    failures = [
        f"  {_GOLDEN[i]['id']}: expected={r['expected']}, actual={r['actual']}"
        for i, r in enumerate(results)
        if not r["match"]
    ]
    failure_report = "\n".join(failures) if failures else "  (none)"
    print(f"\n라우팅 정확도: {acc:.1%} ({sum(r['match'] for r in results)}/{len(results)})")
    print(f"실패 케이스:\n{failure_report}")
    assert acc >= 0.95, f"라우팅 정확도 {acc:.1%} < 95% 목표"

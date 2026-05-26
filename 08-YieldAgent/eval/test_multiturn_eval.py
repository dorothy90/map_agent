"""
Planner 멀티턴 평가
===================
conversation_history + new query → PlanResponse.
체이닝·컨텍스트 carry-over·팬아웃 검증.

실행:
    cd map_agent/08-YieldAgent
    python -m pytest eval/test_multiturn_eval.py -v -m "slow and multi_turn" --tb=short -s

전체 요약만:
    python -m pytest eval/test_multiturn_eval.py::test_multiturn_accuracy_summary -v -m slow -s
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import sys

import pytest

_EVAL_DIR = str(Path(__file__).resolve().parent)
if _EVAL_DIR not in sys.path:
    sys.path.insert(0, _EVAL_DIR)

from test_planner_eval import (
    _is_empty,
    _run_planner,
    _score_chaining,
    _score_params,
)

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"
RESULTS_DIR = Path(__file__).resolve().parent / "results"


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def llm():
    from common import get_llm
    return get_llm()


@pytest.fixture(scope="module")
def mt_goldset() -> list[dict]:
    with open(DATASETS_DIR / "multiturn_goldset.json", encoding="utf-8") as f:
        return json.load(f)


def _build_messages(case: dict) -> list[dict]:
    history = case.get("conversation_history", [])
    return list(history) + [{"role": "user", "content": case["query"]}]


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.multi_turn
def test_multiturn_routing(llm, mt_goldset, frozen_date_strs):
    results = []
    for case in mt_goldset:
        msgs = _build_messages(case)
        plan = _run_planner(llm, msgs, frozen_date_strs)
        actual_agents = {t.agent for t in plan.tasks}
        expected_agents = set(case["expected_agents"])
        ok = expected_agents.issubset(actual_agents)
        results.append(ok)
        print(
            f"[{case['id']}] {case['category']} routing={'OK' if ok else 'FAIL'} "
            f"expected={expected_agents} actual={actual_agents}"
        )

    passed = sum(results)
    total = len(results)
    pct = passed / total * 100
    print(f"\n=== Multi-Turn Routing: {passed}/{total} ({pct:.1f}%) ===")
    assert pct >= 65, f"Multi-turn routing {pct:.1f}% below 65% threshold"


@pytest.mark.slow
@pytest.mark.multi_turn
def test_multiturn_context_params(llm, mt_goldset, frozen_date_strs):
    """컨텍스트 carry-over: conversation_history에서 파라미터가 추론되는지 검증."""
    results = []
    for case in mt_goldset:
        if not case.get("expected_params"):
            continue
        msgs = _build_messages(case)
        plan = _run_planner(llm, msgs, frozen_date_strs)
        tasks_dicts = [t.model_dump() for t in plan.tasks]
        scores = _score_params(tasks_dicts, case["expected_params"])
        ok = all(scores.values()) if scores else True
        results.append(ok)
        print(f"[{case['id']}] {case['category']} context_params={scores}")

    if not results:
        pytest.skip("No cases with expected_params")

    passed = sum(results)
    total = len(results)
    pct = passed / total * 100
    print(f"\n=== Context Carry-Over: {passed}/{total} ({pct:.1f}%) ===")
    assert pct >= 65, f"Context carry-over {pct:.1f}% below 65% threshold"


@pytest.mark.slow
@pytest.mark.multi_turn
def test_multiturn_chaining(llm, mt_goldset, frozen_date_strs):
    """체이닝 의도: 어시스턴트 결과 참조 시 lot_ids를 비워야 하는 케이스 검증."""
    chaining_cases = [c for c in mt_goldset if c.get("expected_chaining")]
    if not chaining_cases:
        pytest.skip("No chaining cases")

    results = []
    for case in chaining_cases:
        msgs = _build_messages(case)
        plan = _run_planner(llm, msgs, frozen_date_strs)
        tasks_dicts = [t.model_dump() for t in plan.tasks]
        chain_scores = _score_chaining(tasks_dicts, case["expected_chaining"])
        ok = all(chain_scores.values())
        results.append(ok)
        print(f"[{case['id']}] {case['category']} chaining={chain_scores}")

    passed = sum(results)
    total = len(results)
    pct = passed / total * 100
    print(f"\n=== Chaining Accuracy: {passed}/{total} ({pct:.1f}%) ===")
    assert pct >= 50, f"Chaining accuracy {pct:.1f}% below 50% threshold"


@pytest.mark.slow
@pytest.mark.multi_turn
def test_multiturn_accuracy_summary(llm, mt_goldset, frozen_date_strs):
    """전체 멀티턴 정확도 요약 리포트 (결과 파일 저장)."""
    RESULTS_DIR.mkdir(exist_ok=True)

    per_case = []
    for case in mt_goldset:
        msgs = _build_messages(case)
        plan = _run_planner(llm, msgs, frozen_date_strs)
        tasks_dicts = [t.model_dump() for t in plan.tasks]
        actual_agents = {t.agent for t in plan.tasks}

        routing_ok = set(case["expected_agents"]).issubset(actual_agents)
        p_scores = _score_params(tasks_dicts, case.get("expected_params", {}))
        param_ok = all(p_scores.values()) if p_scores else True
        chain_ok = None
        if case.get("expected_chaining"):
            c_scores = _score_chaining(tasks_dicts, case["expected_chaining"])
            chain_ok = all(c_scores.values())

        per_case.append({
            "id": case["id"],
            "category": case["category"],
            "routing_ok": routing_ok,
            "param_ok": param_ok,
            "chain_ok": chain_ok,
            "actual_agents": list(actual_agents),
            "tasks": tasks_dicts,
        })
        print(
            f"[{case['id']}] {case['category']} "
            f"routing={'OK' if routing_ok else 'FAIL'} "
            f"params={'OK' if param_ok else 'FAIL'} "
            f"chain={'OK' if chain_ok else ('FAIL' if chain_ok is False else '-')}"
        )

    routing_pct = sum(c["routing_ok"] for c in per_case) / len(per_case) * 100
    param_pct = sum(c["param_ok"] for c in per_case) / len(per_case) * 100
    chain_cases = [c for c in per_case if c["chain_ok"] is not None]
    chain_pct = sum(c["chain_ok"] for c in chain_cases) / len(chain_cases) * 100 if chain_cases else 0

    summary = {
        "total_cases": len(per_case),
        "routing_accuracy": f"{routing_pct:.1f}%",
        "param_accuracy": f"{param_pct:.1f}%",
        "chaining_accuracy": f"{chain_pct:.1f}%" if chain_cases else "N/A",
        "per_case": per_case,
    }

    print("\n=== Multi-Turn Planner Summary ===")
    print(f"  Routing:  {routing_pct:.1f}%")
    print(f"  Params:   {param_pct:.1f}%")
    print(f"  Chaining: {chain_pct:.1f}%" if chain_cases else "  Chaining: N/A")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"multiturn_eval_{ts}.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Results saved → {out_path}")

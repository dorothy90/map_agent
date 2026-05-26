"""
Planner 단일턴 평가
===================
PLANNER_SYSTEM_PROMPT + LLM → PlanResponse 파싱 → 라우팅·파라미터·체이닝 검증.
서버·DB 불필요. LLM API key 필요.

실행:
    cd map_agent/08-YieldAgent
    python -m pytest eval/test_planner_eval.py -v -m slow --tb=short -s
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from common import extract_json_from_llm, get_llm
from prompts import PLANNER_SYSTEM_PROMPT
from supervisor import PlanResponse

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

SET_FIELDS = {"lot_ids", "wf_ids"}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _run_planner(llm, messages: list[dict], date_strs: dict, max_retries: int = 2) -> PlanResponse:
    full_date_strs = {**date_strs, "year": date_strs["today_yyyymmdd"][:4]}
    prompt = PLANNER_SYSTEM_PROMPT.format(**full_date_strs)
    full_msgs = [{"role": "system", "content": prompt}] + messages
    last_error: Exception | None = None
    for _ in range(max_retries + 1):
        resp = llm.invoke(full_msgs, max_tokens=4096)
        try:
            return extract_json_from_llm(resp.content.strip(), PlanResponse)
        except Exception as e:
            last_error = e
    raise last_error  # type: ignore[misc]


def _normalize_fail_type(v: str) -> str:
    return v.split("(", 1)[0].strip() if "(" in v else v


def _is_empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _score_params(actual_tasks: list[dict], expected_params: dict) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for field, expected in expected_params.items():
        matched = False
        for task in actual_tasks:
            params = task.get("params") or {}
            actual = params.get(field)
            if actual is None:
                continue
            if field in SET_FIELDS:
                exp_set = set(expected) if isinstance(expected, list) else {expected}
                act_set = set(actual) if isinstance(actual, list) else {actual}
                if exp_set == act_set:
                    matched = True
                    break
            elif field == "fail_type":
                if _normalize_fail_type(str(actual)) == _normalize_fail_type(str(expected)):
                    matched = True
                    break
            else:
                if str(actual) == str(expected):
                    matched = True
                    break
        results[field] = matched
    return results


def _score_chaining(actual_tasks: list[dict], expected_chaining: dict) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for agent, expected_fields in expected_chaining.items():
        agent_tasks = [t for t in actual_tasks if t.get("agent") == agent]
        for field, marker in expected_fields.items():
            if marker == "<<empty>>":
                ok = any(_is_empty(t.get("params", {}).get(field)) for t in agent_tasks)
                results[f"{agent}.{field}"] = ok
    return results


def _build_single_messages(query: str) -> list[dict]:
    return [{"role": "user", "content": query}]


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def llm():
    return get_llm()


@pytest.fixture(scope="module")
def goldset() -> list[dict]:
    with open(DATASETS_DIR / "planner_goldset.json", encoding="utf-8") as f:
        return json.load(f)


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_planner_routing_all(llm, goldset, frozen_date_strs):
    results = []
    for case in goldset:
        msgs = _build_single_messages(case["query"])
        plan = _run_planner(llm, msgs, frozen_date_strs)
        actual_agents = {t.agent for t in plan.tasks}
        expected_agents = set(case["expected_agents"])
        ok = expected_agents.issubset(actual_agents)
        results.append({
            "id": case["id"],
            "query": case["query"],
            "expected_agents": list(expected_agents),
            "actual_agents": list(actual_agents),
            "routing_ok": ok,
        })
        print(f"[{case['id']}] routing={'OK' if ok else 'FAIL'} expected={expected_agents} actual={actual_agents}")

    total = len(results)
    passed = sum(1 for r in results if r["routing_ok"])
    pct = passed / total * 100
    print(f"\n=== Routing Accuracy: {passed}/{total} ({pct:.1f}%) ===")
    assert pct >= 70, f"Routing accuracy {pct:.1f}% below 70% threshold"


@pytest.mark.slow
def test_planner_params_all(llm, goldset, frozen_date_strs):
    field_scores: dict[str, list[bool]] = {}
    case_results = []

    for case in goldset:
        msgs = _build_single_messages(case["query"])
        plan = _run_planner(llm, msgs, frozen_date_strs)
        tasks_dicts = [t.model_dump() for t in plan.tasks]
        scores = _score_params(tasks_dicts, case.get("expected_params", {}))
        for field, ok in scores.items():
            field_scores.setdefault(field, []).append(ok)
        all_ok = all(scores.values()) if scores else True
        case_results.append({"id": case["id"], "param_scores": scores, "all_ok": all_ok})
        print(f"[{case['id']}] params={scores}")

    total = len(case_results)
    passed = sum(1 for r in case_results if r["all_ok"])
    pct = passed / total * 100 if total else 100
    print(f"\n=== Param Accuracy: {passed}/{total} ({pct:.1f}%) ===")
    for field, vals in field_scores.items():
        fp = sum(vals) / len(vals) * 100
        print(f"  {field}: {sum(vals)}/{len(vals)} ({fp:.1f}%)")
    assert pct >= 65, f"Param accuracy {pct:.1f}% below 65% threshold"


@pytest.mark.slow
def test_planner_chaining(llm, goldset, frozen_date_strs):
    chaining_cases = [c for c in goldset if c.get("expected_chaining")]
    if not chaining_cases:
        pytest.skip("No chaining cases in goldset")

    results = []
    for case in chaining_cases:
        msgs = _build_single_messages(case["query"])
        plan = _run_planner(llm, msgs, frozen_date_strs)
        tasks_dicts = [t.model_dump() for t in plan.tasks]
        chain_scores = _score_chaining(tasks_dicts, case["expected_chaining"])
        ok = all(chain_scores.values())
        results.append({"id": case["id"], "chain_scores": chain_scores, "ok": ok})
        print(f"[{case['id']}] chaining={chain_scores}")

    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    pct = passed / total * 100
    print(f"\n=== Chaining Accuracy: {passed}/{total} ({pct:.1f}%) ===")
    assert pct >= 60, f"Chaining accuracy {pct:.1f}% below 60% threshold"


@pytest.mark.slow
def test_planner_accuracy_summary(llm, goldset, frozen_date_strs):
    """전체 정확도 요약 리포트 (결과 파일 저장)."""
    RESULTS_DIR.mkdir(exist_ok=True)

    routing_results = []
    param_results = []
    chaining_results = []

    for case in goldset:
        msgs = _build_single_messages(case["query"])
        plan = _run_planner(llm, msgs, frozen_date_strs)
        tasks_dicts = [t.model_dump() for t in plan.tasks]
        actual_agents = {t.agent for t in plan.tasks}

        routing_ok = set(case["expected_agents"]).issubset(actual_agents)
        routing_results.append(routing_ok)

        p_scores = _score_params(tasks_dicts, case.get("expected_params", {}))
        param_ok = all(p_scores.values()) if p_scores else True
        param_results.append(param_ok)

        if case.get("expected_chaining"):
            c_scores = _score_chaining(tasks_dicts, case["expected_chaining"])
            chaining_results.append(all(c_scores.values()))

        print(
            f"[{case['id']}] routing={'OK' if routing_ok else 'FAIL'} "
            f"params={'OK' if param_ok else 'FAIL'} "
            f"agents={actual_agents}"
        )

    r_pct = sum(routing_results) / len(routing_results) * 100
    p_pct = sum(param_results) / len(param_results) * 100
    c_pct = sum(chaining_results) / len(chaining_results) * 100 if chaining_results else 0

    summary = {
        "routing_accuracy": f"{r_pct:.1f}%",
        "param_accuracy": f"{p_pct:.1f}%",
        "chaining_accuracy": f"{c_pct:.1f}%",
        "routing_passed": f"{sum(routing_results)}/{len(routing_results)}",
        "param_passed": f"{sum(param_results)}/{len(param_results)}",
        "chaining_passed": f"{sum(chaining_results)}/{len(chaining_results)}" if chaining_results else "N/A",
    }
    print("\n=== Single-Turn Planner Summary ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"planner_eval_{ts}.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Results saved → {out_path}")

"""E2E regression baseline for the yield-agent graph.

These fixtures pin the behavior that must keep passing through the step-by-step
node-consolidation refactor. The headline case is the "4SS 못잡음" regression:
the planner must put "4SS" into the `lotcd` slot (NOT `unit`) and the turn must run
yield_agent without a missing-제품코드 HITL block.

Each CASE is asserted against the live server's `traces/*.jsonl` (structured outcome)
plus the SSE output (user-facing success/error text). Run:

    uvicorn agent_server:app --port 8001     # in another terminal
    pytest tests/test_e2e_regression.py -v
    # or, without pytest:
    python tests/test_e2e_regression.py
"""

from __future__ import annotations

from e2e_client import (
    VALID_UNITS,
    TurnResult,
    coerce_periods,
    run_turn,
    server_is_up,
)

DATA_AGENTS = {
    "yield_agent", "wads_agent", "map_agent", "fail_history_agent",
    "lot_history_agent", "relation_tree_agent", "ppt_export",
}

# Markers that indicate the yield result actually rendered (vs the old missing-param block).
YIELD_SUCCESS_MARKERS = ("주간", "pt1h", "수율 데이터", "Δ", "이상 감지")
MISSING_PRODUCT_MARKER = "제품코드"          # "...제품코드 값을 입력해주세요." == the regression symptom
AGENT_ERROR_MARKER = "에이전트 실행 오류"      # hard runtime error surfaced to the user


# ─────────────────────────────────────────────────────────────
# Fixtures: the failure case that was fixed + the cases that always worked.
# ─────────────────────────────────────────────────────────────
CASES: list[dict] = [
    # THE regression: "4SS" must land in lotcd, period must parse, yield must run.
    {
        "id": "yield_4ss_3w_regression",
        "query": "최근 3주간 4SS 수율 알려줘",
        "kind": "yield",
        "expect": {"lotcd": "4SS", "periods": 3},
    },
    # Period-phrasing variants — guard the unit/periods slot class across wordings.
    {
        "id": "yield_4ss_2w",
        "query": "지난 2주 4SS 수율 보여줘",
        "kind": "yield",
        "expect": {"lotcd": "4SS", "periods": 2},
    },
    {
        "id": "yield_4ss_6month",
        "query": "4SS 최근 6개월 수율 추세 알려줘",
        "kind": "yield",
        "expect": {"lotcd": "4SS", "periods": 6, "unit": "monthly"},
    },
    # Normal cases that always worked — baseline must not regress.
    {
        "id": "greeting_no_agent",
        "query": "안녕",
        "kind": "no_agent",
    },
    {
        "id": "wads_detect_list",
        "query": "최근 1주일 4SS 검출 lot 알려줘",
        "kind": "wads",
    },
]


# ─────────────────────────────────────────────────────────────
# Checks — return a list of failure strings ([] == pass). Shared by pytest + __main__.
# ─────────────────────────────────────────────────────────────
def check_case(case: dict, r: TurnResult) -> list[str]:
    kind = case["kind"]
    if kind == "yield":
        return _check_yield(case, r)
    if kind == "wads":
        return _check_wads(case, r)
    if kind == "no_agent":
        return _check_no_agent(case, r)
    return [f"unknown case kind: {kind}"]


def _check_yield(case: dict, r: TurnResult) -> list[str]:
    fails: list[str] = []
    expect = case.get("expect", {})
    agents = r.planned_agents()
    if "yield_agent" not in agents:
        fails.append(f"planner did not route to yield_agent (planned={agents})")
        return fails  # nothing else meaningful to check

    slots = r.slots_for("yield_agent")

    # lotcd — the core "4SS 못잡음" assertion
    lotcd = str(slots.get("lotcd", ""))
    if lotcd != expect["lotcd"]:
        fails.append(f"lotcd expected {expect['lotcd']!r}, got {lotcd!r} (slots={slots})")

    # unit must never be a product code; if present it must be a real unit
    if "unit" in slots:
        unit = str(slots["unit"]).lower()
        if unit not in VALID_UNITS:
            fails.append(f"unit slot is not a valid unit: {slots['unit']!r} (slots={slots})")
        if unit == expect["lotcd"].lower():
            fails.append(f"product code leaked into unit slot: {slots['unit']!r}")
    if "unit" in expect and str(slots.get("unit", "")).lower() != expect["unit"]:
        fails.append(f"unit expected {expect['unit']!r}, got {slots.get('unit')!r}")

    # periods must parse to the requested count
    if "periods" in expect:
        got = coerce_periods(slots.get("periods"))
        if got != expect["periods"]:
            fails.append(f"periods expected {expect['periods']}, got {slots.get('periods')!r} -> {got}")

    # no missing-제품코드 HITL block
    blocked = [b for b in r.missing_param_blocks() if b.get("param") == "lotcd"]
    if blocked:
        fails.append(f"yield blocked by missing-lotcd HITL: {blocked}")

    # user-facing outcome: yield rendered, not the missing-제품코드 prompt
    if r.sse_contains(MISSING_PRODUCT_MARKER):
        fails.append("SSE output asked for 제품코드 (missing-param prompt) — regression symptom")
    if not any(r.sse_contains(m) for m in YIELD_SUCCESS_MARKERS):
        fails.append(f"SSE output has no yield-success marker {YIELD_SUCCESS_MARKERS}")
    return fails


def _check_wads(case: dict, r: TurnResult) -> list[str]:
    fails: list[str] = []
    if "wads_agent" not in r.planned_agents():
        fails.append(f"planner did not route to wads_agent (planned={r.planned_agents()})")
    if r.sse_contains(AGENT_ERROR_MARKER):
        fails.append("SSE output contains a runtime agent error")
    return fails


def _check_no_agent(case: dict, r: TurnResult) -> list[str]:
    fails: list[str] = []
    planned = [a for a in r.planned_agents() if a in DATA_AGENTS]
    if planned:
        fails.append(f"greeting unexpectedly planned data agents: {planned}")
    return fails


# ─────────────────────────────────────────────────────────────
# pytest entrypoint
# ─────────────────────────────────────────────────────────────
try:
    import pytest

    @pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
    def test_regression_case(case):
        r = run_turn(case["query"])
        assert r.events_of("planner_output"), (
            f"no planner_output trace captured for {case['id']} "
            f"(trace_id={r.trace_id}); is LOCAL_TRACE writing to traces/*.jsonl?"
        )
        fails = check_case(case, r)
        assert not fails, "\n".join([f"[{case['id']}] {f}" for f in fails])
except ImportError:
    pass


# ─────────────────────────────────────────────────────────────
# standalone runner (no pytest needed)
# ─────────────────────────────────────────────────────────────
def _main() -> int:
    if not server_is_up():
        print("SKIP: agent server not up — start `uvicorn agent_server:app --port 8001`")
        return 2
    total_fail = 0
    for case in CASES:
        r = run_turn(case["query"])
        if not r.events_of("planner_output"):
            print(f"FAIL {case['id']}: no planner_output trace (trace_id={r.trace_id})")
            total_fail += 1
            continue
        fails = check_case(case, r)
        if fails:
            total_fail += 1
            print(f"FAIL {case['id']} ({case['query']!r})")
            for f in fails:
                print(f"     - {f}")
        else:
            print(f"PASS {case['id']} ({case['query']!r})")
    print(f"\n{len(CASES) - total_fail}/{len(CASES)} passed")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(_main())

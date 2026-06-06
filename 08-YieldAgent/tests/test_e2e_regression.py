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
    Session,
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
    # Safety nets — guards that replaced the removed hitl_gate must actually fire.
    # (1) Missing required param must BLOCK at supervisor dispatch (not silently run).
    {
        "id": "missing_lotcd_blocks_dispatch",
        "query": "최근 3주간 수율 알려줘",
        "kind": "required_param_block",
        "expect": {"agent": "yield_agent", "param": "lotcd"},
    },
    # (2) planner _MAX_TASKS=5 cap must hold (it is now the only fan-out guard).
    {
        "id": "max_tasks_cap_enforced",
        "query": "4SS 수율 보여주고, 4SS WADS 검출 리스트, 그 wafer map, "
                 "4SS 불량이력, 4SS lot 이력, 4SS relation tree까지 전부 분석해줘",
        "kind": "task_cap",
        "expect": {"max_tasks": 5},
    },
    # Reference follow-ups (baseline for Step 5: resolution moves to the planner).
    # Resolves: an ordinal ref to a prior result must fill the downstream slot.
    {
        "id": "reference_resolves_followup",
        "turns": [
            "최근 1주일 4SS 검출 lot 알려줘",
            "첫번째 lot 이력 보여줘",
        ],
        "kind": "reference_resolves",
        "expect": {"agent": "lot_history_agent", "param": "lot_ids", "ordinal": 1},
    },
    # Ordinal N (not just 1): "두번째" must deterministically map to row[1].
    {
        "id": "reference_resolves_2nd",
        "turns": [
            "최근 1주일 4SS 검출 lot 알려줘",
            "두번째 lot 이력 보여줘",
        ],
        "kind": "reference_resolves",
        "expect": {"agent": "lot_history_agent", "param": "lot_ids", "ordinal": 2},
    },
    # Can't resolve: a ref with no resolvable target must hit the dispatch
    # missing-param backstop (not silently run). (no prior result to reference)
    {
        "id": "reference_unresolved_blocks",
        "query": "첫번째 결과 lot 이력 보여줘",
        "kind": "reference_unresolved_block",
        "expect": {"param": "lot_ids"},
    },
    # Step 5②-b: an OUT-OF-RANGE ordinal ("백번째" with far fewer lots) can't be
    # resolved -> slot marked unresolved -> chaining must NOT silently fill all wads
    # lots; the dispatch missing-param backstop asks instead. (Was an xfail until ②-b.)
    {
        "id": "reference_out_of_range_blocks",
        "turns": [
            "최근 1주일 4SS 검출 lot 알려줘",
            "백번째 lot 이력 보여줘",
        ],
        "kind": "reference_unresolved_block",
        "expect": {"param": "lot_ids"},
    },
    # Step 5②-b guard: a PLAIN-empty chained slot (no ordinal token — "그 lot들" =
    # all wads lots) must still be filled from the wads result, NOT mistaken for a
    # failed reference. Proves the unresolved-ref sentinel didn't break real chaining.
    {
        "id": "wads_map_chaining_intact",
        "turns": [
            "최근 1주일 4SS 검출 lot 알려줘",
            "그 lot들 wafer map 보여줘",
        ],
        "kind": "wads_map_chain",
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
    if kind == "required_param_block":
        return _check_required_param_block(case, r)
    if kind == "task_cap":
        return _check_task_cap(case, r)
    if kind == "reference_resolves":
        return _check_reference_resolves(case, r)
    if kind == "reference_unresolved_block":
        return _check_reference_unresolved_block(case, r)
    if kind == "wads_map_chain":
        return _check_wads_map_chain(case, r)
    return [f"unknown case kind: {kind}"]


def _check_wads_map_chain(case: dict, r: TurnResult) -> list[str]:
    """A plain-empty chained slot (no ordinal token) must still be filled from the
    prior wads result — the unresolved-ref sentinel must not block real chaining."""
    fails: list[str] = []
    if "map_agent" not in r.planned_agents():
        fails.append(f"follow-up did not route to map_agent (planned={r.planned_agents()})")
    if r.sse_interrupts("missing_param"):
        fails.append(
            f"plain chaining wrongly hit a missing-param backstop: "
            f"{[i.get('param') for i in r.sse_interrupts('missing_param')]} "
            f"(sentinel must not affect non-reference empty slots)"
        )
    if r.sse_contains(AGENT_ERROR_MARKER):
        fails.append("runtime agent error on chained wads->map")
    return fails


def _as_lot_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [s.strip() for s in str(value or "").split(",") if s.strip()]


def _check_reference_resolves(case: dict, r: TurnResult) -> list[str]:
    """A follow-up ordinal reference must resolve to the EXACT prior item — value
    check, not just non-empty. "첫번째" must give the first displayed lot, alone.
    This catches both a wrong lot and the _resolve_chained_params all-lots fallback
    (which would silently fill every wads lot). (today via reference_resolver;
    Step 5 via planner — comparable because we assert the outcome, not the path.)"""
    fails: list[str] = []
    expect = case.get("expect", {})
    agent, param = expect["agent"], expect["param"]

    ordinal = expect.get("ordinal", 1)
    t1 = r.turns[0] if r.turns else r
    lots = t1.displayed_lots()
    if len(lots) < ordinal:
        return [f"T1 produced {len(lots)} lots (<{ordinal}) — cannot baseline ordinal {ordinal}"]
    want_lot = lots[ordinal - 1]

    if agent not in r.planned_agents():
        fails.append(f"follow-up did not route to {agent} (planned={r.planned_agents()})")
        return fails

    got = _as_lot_list(r.slots_for(agent).get(param))
    if got != [want_lot]:
        reason = "chained all-lots fallback" if len(got) != 1 else "wrong/missing lot"
        fails.append(
            f"ordinal {ordinal} must resolve to exactly [{want_lot}] (displayed #{ordinal}), "
            f"got {got!r} ({reason}); slots={r.slots_for(agent)}"
        )
    if r.sse_interrupts("missing_param"):
        fails.append("unexpected missing_param block on a resolvable reference")
    if r.sse_contains(AGENT_ERROR_MARKER):
        fails.append("runtime agent error on resolved follow-up")
    return fails


def _check_reference_unresolved_block(case: dict, r: TurnResult) -> list[str]:
    """An unresolvable reference must hit the supervisor dispatch missing-param
    backstop (the guard that catches what reference resolution couldn't fill)."""
    fails: list[str] = []
    expect = case.get("expect", {})
    blocks = r.sse_interrupts("missing_param")
    if not blocks:
        fails.append(
            f"no missing_param backstop fired for an unresolvable reference "
            f"(interrupts: {[i.get('interrupt_type') for i in r.sse_interrupts()]}, "
            f"agents: {r.planned_agents()})"
        )
        return fails
    if expect.get("param") and expect["param"] not in [b.get("param") for b in blocks]:
        fails.append(f"backstop param {[b.get('param') for b in blocks]}, expected {expect['param']!r}")
    return fails


def _check_required_param_block(case: dict, r: TurnResult) -> list[str]:
    """A required slot left empty must BLOCK at supervisor dispatch via a
    missing_param interrupt — the guard that replaced hitl_gate."""
    fails: list[str] = []
    expect = case.get("expect", {})
    blocks = r.sse_interrupts("missing_param")
    if not blocks:
        fails.append(
            f"no missing_param interrupt — dispatch did not block on missing "
            f"required param (interrupts seen: {[i.get('interrupt_type') for i in r.sse_interrupts()]})"
        )
        return fails
    params = [b.get("param") for b in blocks]
    if expect.get("param") and expect["param"] not in params:
        fails.append(f"missing_param interrupt for {params}, expected param {expect['param']!r}")
    # the blocked agent must not have produced a yield success artifact
    if r.sse_contains("주간 (최근"):
        fails.append("agent appears to have run despite missing required param")
    return fails


def _check_task_cap(case: dict, r: TurnResult) -> list[str]:
    """planner _MAX_TASKS cap is the only fan-out guard now; it must hold."""
    fails: list[str] = []
    cap = case.get("expect", {}).get("max_tasks", 5)
    n = len(r.planner_requests())
    if n > cap:
        fails.append(f"planner produced {n} requests, exceeds cap {cap}")
    # this query enumerates >cap tasks, so the cap must have actually fired
    if not r.cap_status_fired():
        fails.append(
            f"cap did not fire (planned {n}); expected a '처음 N개만' drop notice "
            f"for an over-cap request"
        )
    return fails


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


def run_case(case: dict) -> TurnResult:
    """Run a case's turn(s); return the last turn's result. Multi-turn cases use a
    shared Session so follow-up references can see prior-turn results."""
    if "turns" in case:
        session = Session()
        results = [session.turn(query) for query in case["turns"]]
        last = results[-1]
        last.turns = results
        return last
    r = run_turn(case["query"])
    r.turns = [r]
    return r


def case_label(case: dict) -> str:
    return case.get("query") or " ⟶ ".join(case.get("turns", []))


# ─────────────────────────────────────────────────────────────
# pytest entrypoint
# ─────────────────────────────────────────────────────────────
try:
    import pytest

    def _params():
        out = []
        for c in CASES:
            marks = [pytest.mark.xfail(reason=c["xfail"], strict=False)] if c.get("xfail") else []
            out.append(pytest.param(c, id=c["id"], marks=marks))
        return out

    @pytest.mark.parametrize("case", _params())
    def test_regression_case(case):
        r = run_case(case)
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
        r = run_case(case)
        if not r.events_of("planner_output"):
            print(f"FAIL {case['id']}: no planner_output trace (trace_id={r.trace_id})")
            total_fail += 1
            continue
        fails = check_case(case, r)
        if case.get("xfail"):
            # expected-failure case: failing now is correct (bug reproduced)
            if fails:
                print(f"XFAIL {case['id']} (expected — {fails[0]})")
            else:
                print(f"XPASS {case['id']} — no longer fails (bug fixed/changed? review)")
            continue
        if fails:
            total_fail += 1
            print(f"FAIL {case['id']} ({case_label(case)!r})")
            for f in fails:
                print(f"     - {f}")
        else:
            print(f"PASS {case['id']} ({case_label(case)!r})")
    n_real = sum(1 for c in CASES if not c.get("xfail"))
    print(f"\n{n_real - total_fail}/{n_real} passed (+ {len(CASES) - n_real} xfail-tracked)")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(_main())

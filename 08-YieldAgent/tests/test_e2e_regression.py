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

import re

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
        # message_contains pins the MISSING-path prompt, distinct from the INVALID-path
        # re-prompt asserted by yield_invalid_lotcd_reprompt_6d_anchor.
        "expect": {
            "agent": "yield_agent", "param": "lotcd",
            "message_contains": "입력해주세요",
        },
    },
    # ── Structured-HITL batch contract (harness redesign 축 1) ──
    # Both missing required slots arrive in ONE interrupt (fields[]), answered by a
    # single {slot: value} dict resume — no second interrupt. (Behavior change from the
    # earlier per-slot sequential asks: map/relation_tree were 2 interrupts, now 1.)
    {
        "id": "map_interrupt_order",
        "steps": [
            {"query": "wafer map 보여줘", "expect_fields": ["lot_ids", "map_oper"]},
            {"resume": {"lot_ids": "4SS2DPD", "map_oper": "PT1H"}, "expect_fields": []},
        ],
        "kind": "interrupt_sequence",
    },
    {
        "id": "relation_tree_interrupt_order",
        "steps": [
            {"query": "relation tree 분석해줘", "expect_fields": ["lotcd", "cause_oper"]},
            {"resume": {"lotcd": "4SS2DPD", "cause_oper": "STEP07"}, "expect_fields": []},
        ],
        "kind": "interrupt_sequence",
    },
    # lot_ids format guard (축1 보강): a malformed answer must NOT be queried silently —
    # it is rejected at apply and re-asked (backstop) with the reason. Pinned on BOTH
    # resume paths (str fallback AND dict) because both bypassed the guard before; and
    # the all-or-nothing multi-lot case (one bad token rejects the whole answer — no
    # partial fill that would silently drop a lot).
    {
        "id": "lot_ids_invalid_reask_str",
        "steps": [
            {"query": "wafer map 보여줘", "expect_fields": ["lot_ids", "map_oper"]},
            # str fallback: whole sentence into first slot -> rejected -> re-ask (with reason)
            {"resume": "4SSWWK6 로 pt1h 그려줘",
             "expect_fields": ["lot_ids", "map_oper"], "message_contains": "형식이 맞지 않"},
            {"resume": {"lot_ids": "4SS2DPD", "map_oper": "PT1H"}, "expect_fields": []},
        ],
        "kind": "interrupt_sequence",
    },
    {
        "id": "lot_ids_invalid_reask_dict",
        "steps": [
            {"query": "wafer map 보여줘", "expect_fields": ["lot_ids", "map_oper"]},
            # dict path also bypassed the guard before — must reject + re-ask lot_ids only
            {"resume": {"lot_ids": "이건 lot 아님", "map_oper": "PT1H"},
             "expect_fields": ["lot_ids"], "message_contains": "형식이 맞지 않"},
            {"resume": {"lot_ids": "4SS2DPD"}, "expect_fields": []},
        ],
        "kind": "interrupt_sequence",
    },
    {
        "id": "lot_ids_multi_partial_reask",
        "steps": [
            {"query": "wafer map 보여줘", "expect_fields": ["lot_ids", "map_oper"]},
            # one bad token rejects the WHOLE answer (all-or-nothing); reason names it
            {"resume": {"lot_ids": "4SS2DPD,NOTALOT99", "map_oper": "PT1H"},
             "expect_fields": ["lot_ids"], "message_contains": "NOTALOT99"},
            {"resume": {"lot_ids": "4SS2DPD,4SSXCEW"}, "expect_fields": []},
        ],
        "kind": "interrupt_sequence",
    },
    # ── 축2: planner deep follow-up reference resolution (N-turn context) ──
    # S1 (closed by 축2): conversational back-reference to the FIRST product two turns
    # ago ("처음 거"). The planner now sees the recent turns and resolves it to the
    # literal product (4SS), not an ordinal token. PASS = routed to wads_agent AND
    # wads.lotcd == "4SS" (asserted separately).
    {
        "id": "deep_followup_product_backref",
        "turns": [
            "4SS 수율 보여줘",
            "5NA 수율도 보여줘",
            "처음 거 검출 lot 보여줘",
        ],
        "kind": "deep_followup",
        "expect": {"agent": "wads_agent", "slot": "lotcd", "value": "4SS"},
    },
    # S2 regression guard: modify-prior-request resolved via state.lotcd — must keep 4SS.
    {
        "id": "deep_followup_modify_keeps_lotcd",
        "turns": [
            "4SS 최근 3주 수율 보여줘",
            "그거 6개월로 바꿔서 보여줘",
        ],
        "kind": "deep_followup",
        "expect": {"agent": "yield_agent", "slot": "lotcd", "value": "4SS"},
    },
    # S3 regression guard (control): explicit product name after a switch — must stay 5NA.
    {
        "id": "deep_followup_explicit_product",
        "turns": [
            "5NA 수율 보여줘",
            "4SS 수율 보여줘",
            "5NA 검출 lot 보여줘",
        ],
        "kind": "deep_followup",
        "expect": {"agent": "wads_agent", "slot": "lotcd", "value": "5NA"},
    },
    # S4 stale-bleed guard: T3 explicitly names the 2nd product (5NA) — 축2's injected
    # history must NOT bleed the 1st product (4SS) back in. Slot values come from the
    # latest request, not past turns.
    {
        "id": "deep_followup_stale_bleed_guard",
        "turns": [
            "4SS 수율 보여줘",
            "5NA 수율도 보여줘",
            "5NA 6개월로 바꿔서 보여줘",
        ],
        "kind": "deep_followup",
        "expect": {"agent": "yield_agent", "slot": "lotcd", "value": "5NA"},
    },
    # (b) 6d ANCHOR — an invalid (non-product-code) lotcd must hit the validation
    # re-prompt via _normalize_product_lotcd / _PRODUCT_LOTCD_RE. Dual purpose:
    #   1. fixes current invalid-lotcd behavior in place;
    #   2. is the comparison anchor for Step 6d (alias chains / _PRODUCT_LOTCD_RE,
    #      reopened as a separate analysis). When 6d removes the regex guard this
    #      assertion breaks, forcing an explicit "is dropping this validation
    #      intended?" decision — same role as the ②-b silent-all-lots xfail.
    {
        "id": "yield_invalid_lotcd_reprompt_6d_anchor",
        "query": "ABCD 수율 알려줘",
        "kind": "invalid_param_block",
        "expect": {"param": "lotcd", "message_contains": "형식이 아닙니다"},
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
    # Step 5②-c: report ordinal -> the Nth report's exact groupkeys (#RN), no bleed.
    {
        "id": "report_ordinal_resolves_1st",
        "turns": [
            "4SS 열화 리포트 보여줘",
            "첫번째 리포트 cummap 보여줘",
        ],
        "kind": "report_resolves",
        "expect": {"ordinal": 1},
    },
    {
        "id": "report_ordinal_resolves_2nd",
        "turns": [
            "4SS 열화 리포트 보여줘",
            "두번째 리포트 cummap 보여줘",
        ],
        "kind": "report_resolves",
        "expect": {"ordinal": 2},
    },
    # Step 5② finalization: a MULTI-task report-map fanout ("1,2번째 리포트 cummap"
    # -> two map tasks) must go through plan_review (HITL), not a deterministic
    # auto-approve. (The auto-approve shortcut depended on resolved_refs, which the
    # reference_resolver removal emptied; this asserts the post-removal HITL path so
    # the now-inert _should_auto_approve_report_map_plan can be deleted safely.)
    {
        "id": "report_map_multi_plan_review",
        "turns": [
            "4SS 열화 리포트 보여줘",
            "1,2번째 리포트 cummap 보여줘",
        ],
        "kind": "plan_review_required",
    },
    # plan_review modify must ACTUALLY modify: a "각각 불량이력도" follow-up adds
    # fail_history_agent tasks (not drop to 0/unchanged). Regression pin for the
    # _PLAN_REVIEW_SYSTEM field/shape + valid-agent fix.
    {
        "id": "plan_review_modify_adds_tasks",
        "steps": [
            {"query": "4SS 열화 리포트 보여줘"},
            {"query": "1,2번째 리포트 cummap 보여줘"},
            {"resume": "각각 불량이력도 조사해줘"},
        ],
        "kind": "plan_modify",
        "expect": {"added_agent": "fail_history_agent"},
    },
    # Step 5②-c: SEQUENTIAL chaining — report-ordinal (#RN) narrows to a report's
    # wafers, then a wafer-ordinal (#N) must pick from THAT narrowed pool, not the
    # earlier full detection. Uses the 2nd report so its lot differs from the full
    # pool's first lot — that's what makes a broken chain (silently re-reading the
    # full prior result) detectable by value.
    {
        "id": "sequential_report_then_wafer_chain",
        "turns": [
            "4SS 열화 리포트 보여줘",
            "두번째 리포트에서 검출된 wafer들 보여줘",
            "그중 첫번째 wafer 이력 확인해줘",
        ],
        "kind": "sequential_chain",
        "expect": {"agent": "lot_history_agent", "param": "lot_ids", "report_ordinal": 2},
    },
    # An OUT-OF-RANGE report ordinal must also hit the dispatch backstop (#R100 ->
    # no such report row -> unresolved -> ask), never silently slice another report.
    {
        "id": "report_ordinal_out_of_range_blocks",
        "turns": [
            "4SS 열화 리포트 보여줘",
            "백번째 리포트 cummap 보여줘",
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
    if kind == "report_resolves":
        return _check_report_resolves(case, r)
    if kind == "sequential_chain":
        return _check_sequential_chain(case, r)
    if kind == "plan_review_required":
        return _check_plan_review_required(case, r)
    if kind == "plan_modify":
        return _check_plan_modify(case, r)
    if kind == "deep_followup":
        return _check_deep_followup(case, r)
    if kind == "interrupt_sequence":
        return _check_interrupt_sequence(case, r)
    if kind == "invalid_param_block":
        return _check_invalid_param_block(case, r)
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


def _check_report_resolves(case: dict, r: TurnResult) -> list[str]:
    """A "N번째 리포트 cummap" follow-up (#RN) must resolve to the Nth prior REPORT's
    EXACT groupkeys — the slice happens in the wads->map chaining (validator), so the
    resolved value shows up in the DISPATCHED groupkey, not the planner slot. Boundary
    check: every dispatched groupkey belongs to report-N's lot, with no bleed from
    another report's lot."""
    fails: list[str] = []
    ordinal = case.get("expect", {}).get("ordinal", 1)
    t1 = r.turns[0] if r.turns else r
    lots = t1.displayed_lots()
    if len(lots) < ordinal:
        return [f"T1 produced {len(lots)} report lots (<{ordinal}) — cannot baseline report {ordinal}"]
    want_lot = lots[ordinal - 1]

    if "map_agent" not in r.planned_agents():
        return [f"report ref did not route to map_agent (planned={r.planned_agents()})"]
    if r.sse_interrupts("missing_param"):
        fails.append("unexpected missing_param block on a resolvable report reference")

    preview = r.dispatched_param("map_agent", "groupkey").get("preview", "") or ""
    groupkeys = re.findall(r"4SS[A-Z0-9]{4}\.\w+", preview)
    if not groupkeys:
        fails.append(f"no groupkeys dispatched for report {ordinal} (groupkey preview={preview!r})")
        return fails
    lots_in = sorted({g.split(".")[0] for g in groupkeys})
    if lots_in != [want_lot]:
        fails.append(
            f"report {ordinal} must slice exactly report-{ordinal} lot [{want_lot}], "
            f"got groupkey lots {lots_in} (wrong report / bleed); preview={preview!r}"
        )
    if r.sse_contains(AGENT_ERROR_MARKER):
        fails.append("runtime agent error on resolved report follow-up")
    return fails


def _check_interrupt_sequence(case: dict, r: TurnResult) -> list[str]:
    """Pin the structured-HITL batch contract across a query+resume sequence.

    Each step with `expect_fields` must pause at exactly ONE missing_param interrupt
    whose `fields` SLOT SET equals expect_fields (set, not order — fields is an
    unordered collection the form renders together). A step with expect_fields=[] must
    run with NO interrupt. This proves batching: all missing slots arrive in one
    interrupt, and a single dict resume clears them (no second interrupt)."""
    fails: list[str] = []
    steps = case["steps"]
    results = r.turns or [r]
    if len(results) != len(steps):
        return [f"ran {len(results)} steps, expected {len(steps)}"]
    for i, (step, res) in enumerate(zip(steps, results)):
        label = step.get("query") or f"resume={step.get('resume')!r}"
        missing = res.sse_interrupts("missing_param")
        expect = set(step.get("expect_fields") or [])
        if not expect:
            if missing:
                got = [x.get("param") for x in missing]
                fails.append(f"step{i} ({label}): expected NO interrupt (run), got {got}")
            continue
        if len(missing) != 1:
            params = [x.get("param") for x in missing]
            fails.append(
                f"step{i} ({label}): expected exactly ONE batched interrupt for "
                f"{sorted(expect)}, got {len(missing)} ({params})"
            )
            continue
        got_slots = {f.get("slot") for f in (missing[0].get("fields") or [])}
        if got_slots != expect:
            fails.append(
                f"step{i} ({label}): interrupt fields {sorted(got_slots)} != "
                f"expected {sorted(expect)}"
            )
        want_msg = step.get("message_contains")
        if want_msg and want_msg not in (missing[0].get("message") or ""):
            fails.append(
                f"step{i} ({label}): interrupt message missing {want_msg!r}: "
                f"{missing[0].get('message')!r}"
            )
    return fails


def _check_invalid_param_block(case: dict, r: TurnResult) -> list[str]:
    """An INVALID (not just missing) param must block at dispatch with the validation
    re-prompt. 6d ANCHOR: pins the _normalize_product_lotcd / _PRODUCT_LOTCD_RE reject
    behavior so removing that guard in 6d forces an explicit decision (the assertion
    breaks and asks "is dropping this validation intended?")."""
    fails: list[str] = []
    expect = case.get("expect", {})
    blocks = r.sse_interrupts("missing_param")
    if not blocks:
        fails.append(
            f"no missing_param interrupt for an invalid param "
            f"(interrupts: {[i.get('interrupt_type') for i in r.sse_interrupts()]})"
        )
        return fails
    params = [b.get("param") for b in blocks]
    if expect.get("param") and expect["param"] not in params:
        fails.append(f"missing_param for {params}, expected {expect['param']!r}")
    want_msg = expect.get("message_contains")
    if want_msg and not any(want_msg in (b.get("message") or "") for b in blocks):
        fails.append(
            f"interrupt message missing {want_msg!r} (the invalid-format re-prompt): "
            f"{[b.get('message') for b in blocks]}"
        )
    return fails


def _check_deep_followup(case: dict, r: TurnResult) -> list[str]:
    """A deep follow-up must (1) route to the expected agent AND (2) resolve the
    expected slot value — asserted SEPARATELY so a routing-ok/value-unresolved gap
    (the 축2 symptom) is distinguishable from a misroute."""
    fails: list[str] = []
    expect = case.get("expect", {})
    agent, slot, value = expect["agent"], expect["slot"], expect["value"]
    if agent not in r.planned_agents():
        fails.append(f"not routed to {agent} (planned={r.planned_agents()})")
    got = r.slots_for(agent).get(slot)
    if got != value:
        fails.append(
            f"{agent}.{slot} expected {value!r}, got {got!r} "
            f"(deep follow-up reference unresolved; slots={r.slots_for(agent)})"
        )
    return fails


def _check_plan_modify(case: dict, r: TurnResult) -> list[str]:
    """A plan_review 'modify' follow-up must actually apply: re-show plan_review with
    the requested addition present (not drop to 0 tasks / leave it unchanged)."""
    fails: list[str] = []
    expect = case.get("expect", {})
    last = r.turns[-1] if r.turns else r
    if not last.sse_interrupts("plan_review"):
        fails.append(
            f"modify did not re-show plan_review "
            f"(interrupts={[i.get('interrupt_type') for i in last.sse_interrupts()]})"
        )
    added = expect.get("added_agent")
    if added and added not in last.sse_blob:
        fails.append(f"modify did not add {added!r} to the plan (dropped to 0 / unchanged?)")
    return fails


def _check_plan_review_required(case: dict, r: TurnResult) -> list[str]:
    """A multi-task report-map fanout must pause for plan_review (HITL), not be
    auto-approved. Asserts the path that remains after the deterministic
    auto-approve shortcut is removed."""
    fails: list[str] = []
    n_map = sum(1 for a in r.planned_agents() if a == "map_agent")
    if n_map < 2:
        fails.append(
            f"expected a >=2 map_agent fanout (report ordinals), got {n_map} "
            f"(agents={r.planned_agents()})"
        )
    if not r.sse_interrupts("plan_review"):
        fails.append(
            f"multi-task report-map must go through plan_review (HITL), not auto-approve; "
            f"interrupts={[i.get('interrupt_type') for i in r.sse_interrupts()]}"
        )
    return fails


def _check_sequential_chain(case: dict, r: TurnResult) -> list[str]:
    """report-ordinal (#RN) -> wafer-ordinal (#N) sequential narrowing. Turn-2 narrows
    to report-N's wafers; turn-3's "그중 첫번째 wafer" must resolve from THAT narrowed
    pool. Value check: turn-3 picks report-N's lot, NOT the full prior detection's
    first lot (which is what a broken chain — re-reading the full result — would give)."""
    fails: list[str] = []
    expect = case.get("expect", {})
    agent, param = expect["agent"], expect["param"]
    rep = expect.get("report_ordinal", 2)

    t1 = r.turns[0] if r.turns else r
    lots = t1.displayed_lots()
    if len(lots) < rep:
        return [f"T1 produced {len(lots)} lots (<{rep}) — cannot baseline report {rep}"]
    want_lot = lots[rep - 1]   # report-N's lot = the narrowed pool the chain must keep
    full_first = lots[0]       # full prior pool's first lot = the silent-wrong value
    if want_lot == full_first:
        return [f"non-discriminating fixture: report-{rep} lot == full-pool first ({want_lot})"]

    if agent not in r.planned_agents():
        return [f"turn-3 did not route to {agent} (planned={r.planned_agents()})"]

    # The resolved value may be a wafer ("4SSHQSK.11") or its lot ("4SSHQSK") — both
    # prove continuity as long as the LOT belongs to report-N's narrowed pool. What we
    # reject: the full prior pool's lot (chain broke) or empty (no reference formed).
    got = _as_lot_list(r.slots_for(agent).get(param))
    got_lots = sorted({g.split(".")[0] for g in got})
    if got_lots != [want_lot]:
        reason = (
            "chain broke: picked from the full prior detection, not the narrowed report"
            if got_lots == [full_first]
            else "no reference formed (lot_ids empty)"
            if not got_lots
            else "wrong wafer pool"
        )
        fails.append(
            f"#N must pick from report-{rep}'s narrowed pool (lot {want_lot}), "
            f"got {got!r} ({reason}); slots={r.slots_for(agent)}"
        )
    if r.sse_interrupts("missing_param"):
        fails.append("unexpected missing_param block on a resolvable sequential chain")
    if r.sse_contains(AGENT_ERROR_MARKER):
        fails.append("runtime agent error on sequential chain")
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
    # message_contains pins the MISSING-path prompt, distinct from the INVALID-path
    # re-prompt — so 6d can't conflate "missing" and "invalid" lotcd handling.
    want_msg = expect.get("message_contains")
    if want_msg and not any(want_msg in (b.get("message") or "") for b in blocks):
        fails.append(
            f"interrupt message missing {want_msg!r}: {[b.get('message') for b in blocks]}"
        )
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
    if "steps" in case:
        # interrupt_sequence: one Session driven by query / resume_value steps. Each
        # step does its own SSE drain, so each step's interrupts are isolated to that
        # step's TurnResult. We return the FIRST step (the only one with a
        # planner_output — resume steps continue mid-graph) as primary so the shared
        # planner_output guard holds; all step results live in .turns for the check.
        session = Session()
        results = []
        for step in case["steps"]:
            if "resume" in step:
                results.append(session.turn("", resume_value=step["resume"]))
            else:
                results.append(session.turn(step["query"]))
        primary = results[0]
        primary.turns = results
        return primary
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
    if "steps" in case:
        return " ⟶ ".join(
            s.get("query") or f"↩{s.get('resume')!r}" for s in case["steps"]
        )
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
def _run_one(case: dict) -> tuple[dict, "TurnResult", list[str]]:
    r = run_case(case)
    if not r.events_of("planner_output"):
        return case, r, [f"no planner_output trace (trace_id={r.trace_id})"]
    return case, r, check_case(case, r)


def _main() -> int:
    """Run the suite concurrently — each case is an independent session/trace, so the
    only ceiling is the LLM backend's concurrency. Cuts the serial ~4-5min to ~1min.
      python tests/test_e2e_regression.py              # all cases
      python tests/test_e2e_regression.py report seq   # only ids matching a substring
      E2E_WORKERS=4 python tests/test_e2e_regression.py
    """
    import concurrent.futures
    import os
    import sys

    if not server_is_up():
        print("SKIP: agent server not up — start `uvicorn agent_server:app --port 8001`")
        return 2

    selectors = [a for a in sys.argv[1:] if not a.startswith("-")]
    cases = [c for c in CASES if not selectors or any(s in c["id"] for s in selectors)]
    if not cases:
        print(f"no cases match {selectors}")
        return 2
    workers = max(1, int(os.getenv("E2E_WORKERS", "8")))

    # interrupt_sequence cases are resume-based multi-step flows whose query→resume
    # timing is sensitive to concurrent server load (a busy backend can delay the
    # interrupt SSE past the drain). They are the Step 6c order safety net, so they
    # must be deterministic — run them SERIALLY, isolated from the parallel pool, so a
    # failure means a real interrupt-order break, not a load flake. The rest run
    # concurrently for speed.
    _serial_kinds = ("interrupt_sequence", "plan_modify")  # resume-based — concurrency-sensitive
    serial_cases = [c for c in cases if c.get("kind") in _serial_kinds]
    parallel_cases = [c for c in cases if c.get("kind") not in _serial_kinds]

    results: dict[str, tuple[dict, "TurnResult", list[str]]] = {}
    for c in serial_cases:
        case, r, fails = _run_one(c)
        results[case["id"]] = (case, r, fails)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_run_one, c): c["id"] for c in parallel_cases}
        for fut in concurrent.futures.as_completed(futures):
            case, r, fails = fut.result()
            results[case["id"]] = (case, r, fails)

    total_fail = 0
    for c in cases:  # stable, source-ordered output
        case, r, fails = results[c["id"]]
        if case.get("xfail"):
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
    n_real = sum(1 for c in cases if not c.get("xfail"))
    print(f"\n{n_real - total_fail}/{n_real} passed (+ {len(cases) - n_real} xfail-tracked)")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(_main())

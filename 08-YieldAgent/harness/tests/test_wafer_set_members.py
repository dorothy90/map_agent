from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


def atom(column, op, value=None, **extra):
    from harness.wafer_set_contracts import PredicateAtom
    return PredicateAtom(column=column, op=op, value=value, **extra)


def group(*items, mode="all"):
    from harness.wafer_set_contracts import PredicateGroup
    return PredicateGroup(mode=mode, items=list(items))


def test_predicate_null_and_type_semantics():
    from harness.wafer_set_predicates import evaluate_predicate

    rows = [{"n": 3, "x": None}, {"n": 4, "x": "a"}, {"n": None, "x": "b"}]
    assert evaluate_predicate(rows, group(atom("n", "mod_eq", divisor=2, remainder=0)), "exclude") == [False, True, False]
    assert evaluate_predicate(rows, group(atom("x", "eq", "a")), "include") == [True, True, False]
    with pytest.raises(ValueError, match="null"):
        evaluate_predicate(rows, group(atom("x", "eq", "a")), "error")
    with pytest.raises(ValueError, match="type"):
        evaluate_predicate(rows, group(atom("n", "eq", "3")), "exclude")


def test_filter_operations_order_deduplication_and_conflicts():
    from harness.wafer_set_predicates import filter_rows

    left = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}, {"id": 2, "v": "b"}]
    right = [{"id": 2, "v": "b"}, {"id": 3, "v": "c"}]
    assert [r["id"] for r in filter_rows(left, operation="union", key_columns=["id"],
                                           null_policy="exclude", other=right).rows] == [1, 2, 3]
    assert [r["id"] for r in filter_rows(left, operation="intersection", key_columns=["id"],
                                           null_policy="exclude", other=right).rows] == [2]
    assert [r["id"] for r in filter_rows(left, operation="difference", key_columns=["id"],
                                           null_policy="exclude", other=right).rows] == [1]
    with pytest.raises(ValueError, match="conflicting"):
        filter_rows(left, operation="union", key_columns=["id"], null_policy="exclude",
                    other=[{"id": 2, "v": "different"}])


def test_make_cohorts_preserves_overlap_deduplicates_within_and_keeps_empty_rule():
    from harness.wafer_set_predicates import make_cohorts

    rows = [{"wf_id": 12, "member_id": "m12", "measurement_ref": "r12"},
            {"wf_id": 12, "member_id": "m12", "measurement_ref": "r12"},
            {"wf_id": 9, "member_id": "m9", "measurement_ref": "r9"}]
    rules = [
        {"name": "three", "predicate": group(atom("wf_id", "mod_eq", divisor=3, remainder=0))},
        {"name": "four", "predicate": group(atom("wf_id", "mod_eq", divisor=4, remainder=0))},
        {"name": "none", "predicate": group(atom("wf_id", "eq", 99))},
    ]
    result = make_cohorts(rows, key_columns=["wf_id"], group_by=[], rules=rules,
                          combine="independent", null_group="include",
                          max_cohorts=100, max_memberships=100)
    counts = {row["label"]: row["member_count"] for row in result.cohorts}
    assert counts == {"three": 2, "four": 1, "none": 0}
    twelve = [row for row in result.memberships if row["member_id"] == "m12"]
    assert len(twelve) == 2 and len({row["cohort_id"] for row in twelve}) == 2
    assert result.audit["overlapping_members"] == 1


def test_make_cohorts_crosses_groups_and_rules_and_rejects_limits():
    from harness.wafer_set_predicates import make_cohorts

    rows = [{"wf_id": 1, "product": "A", "member_id": "a", "measurement_ref": "ra"},
            {"wf_id": 2, "product": "B", "member_id": "b", "measurement_ref": "rb"}]
    rules = [{"name": "all", "predicate": group(atom("wf_id", "ge", 0))}]
    result = make_cohorts(rows, key_columns=["wf_id"], group_by=["product"], rules=rules,
                          combine="cross", null_group="include", max_cohorts=2, max_memberships=2)
    assert len(result.cohorts) == 2 and len(result.memberships) == 2
    with pytest.raises(ValueError, match="max_cohorts"):
        make_cohorts(rows, key_columns=["wf_id"], group_by=["product"], rules=rules,
                     combine="independent", null_group="include", max_cohorts=1, max_memberships=10)


def test_member_descriptions_are_general():
    from harness.tools import wafer_set_members
    from harness.tools.registry import ToolRegistry

    registry = ToolRegistry()
    wafer_set_members.register(registry)
    bad = ("PT1H", "PT1C", "?", "LANGGRAPH", "MAP.", "DF_")
    for spec in registry.tools.values():
        payload = json.dumps(spec.schema.model_json_schema(), ensure_ascii=False)
        assert all(token not in spec.description and token not in payload for token in bad)

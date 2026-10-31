from __future__ import annotations

import pytest


def test_join_one_to_many_preserves_left_measurement_and_audits_expansion():
    from harness.wafer_set_relations import join_tables

    left = [{"fact_id": "l1", "lot_id": "L", "wf_id": 1,
             "measurement_ref": "measurement-a", "value": 90.0}]
    right = [
        {"fact_id": "r1", "lot_id": "L", "wf_id": 1, "step": "A"},
        {"fact_id": "r2", "lot_id": "L", "wf_id": 1, "step": "B"},
    ]
    result = join_tables(left, right, relation_id="wafer_history", how="left",
                         expected_cardinality="one_to_many", time_policy="none",
                         left_time=None, right_time=None, tolerance_seconds=None,
                         allow_approximate=False, max_expansion=3, max_rows=10)
    assert len(result.joined) == 2
    assert {row["measurement_ref"] for row in result.joined} == {"measurement-a"}
    assert {row["right__step"] for row in result.joined} == {"A", "B"}
    assert result.audit["actual_cardinality"] == "one_to_many"
    assert result.audit["expansion_ratio"] == 2.0


def test_join_rejects_many_to_many_null_keys_and_preflight_overflow():
    from harness.wafer_set_relations import join_tables

    left = [{"fact_id": "l1", "lot_id": "L", "wf_id": 1},
            {"fact_id": "l2", "lot_id": "L", "wf_id": 1},
            {"fact_id": "ln", "lot_id": None, "wf_id": 1}]
    right = [{"fact_id": "r1", "lot_id": "L", "wf_id": 1},
             {"fact_id": "r2", "lot_id": "L", "wf_id": 1}]
    with pytest.raises(ValueError, match="many-to-many"):
        join_tables(left, right, relation_id="wafer_identity", how="left",
                    expected_cardinality="one_to_many", time_policy="none",
                    left_time=None, right_time=None, tolerance_seconds=None,
                    allow_approximate=False, max_expansion=20, max_rows=20)

    with pytest.raises(ValueError, match="max_rows"):
        join_tables(left[:1], right, relation_id="wafer_history", how="left",
                    expected_cardinality="one_to_many", time_policy="none",
                    left_time=None, right_time=None, tolerance_seconds=None,
                    allow_approximate=False, max_expansion=20, max_rows=1)


def test_latest_before_selects_one_row_and_rejects_same_time_conflict():
    from harness.wafer_set_relations import join_tables

    left = [{"fact_id": "l1", "lot_id": "L", "wf_id": 1, "at": "2026-01-02 00:00:00"}]
    right = [
        {"fact_id": "r1", "lot_id": "L", "wf_id": 1, "at": "2026-01-01 00:00:00", "v": 1},
        {"fact_id": "r2", "lot_id": "L", "wf_id": 1, "at": "2026-01-01 12:00:00", "v": 2},
    ]
    result = join_tables(left, right, relation_id="wafer_identity", how="left",
                         expected_cardinality="one_to_one", time_policy="latest_before",
                         left_time="at", right_time="at", tolerance_seconds=None,
                         allow_approximate=False, max_expansion=2, max_rows=10)
    assert result.joined[0]["right__v"] == 2

    conflicting = right + [{**right[-1], "fact_id": "r3", "v": 3}]
    with pytest.raises(ValueError, match="same-time"):
        join_tables(left, conflicting, relation_id="wafer_identity", how="left",
                    expected_cardinality="one_to_one", time_policy="latest_before",
                    left_time="at", right_time="at", tolerance_seconds=None,
                    allow_approximate=False, max_expansion=2, max_rows=10)


def test_approximate_relation_requires_opt_in():
    from harness.wafer_set_relations import join_tables

    with pytest.raises(ValueError, match="approximate"):
        join_tables([], [], relation_id="wads_report_day", how="left",
                    expected_cardinality="one_to_many", time_policy="none",
                    left_time=None, right_time=None, tolerance_seconds=None,
                    allow_approximate=False, max_expansion=2, max_rows=10)

    result = join_tables(
        [{"fact_id": "l", "lot_cd": "P", "category": "C", "parameter": "A",
          "end_tm": "2026-01-01 03:00:00"}],
        [{"fact_id": "wrong", "lot_cd": "P", "oper_para": "C_B", "end_tm": "2026-01-01 04:00:00"},
         {"fact_id": "right", "lot_cd": "P", "oper_para": "C_A", "end_tm": "2026-01-01 05:00:00"}],
        relation_id="wads_report_day", how="left", expected_cardinality="one_to_many",
        time_policy="none", left_time=None, right_time=None, tolerance_seconds=None,
        allow_approximate=True, max_expansion=2, max_rows=10)
    assert [row["right_fact_id"] for row in result.joined] == ["right"]

    by_day = join_tables(
        [{"fact_id": "l1", "lot_cd": "P", "category": "C", "parameter": "A",
          "end_tm": "2026-01-01 03:00:00"},
         {"fact_id": "l2", "lot_cd": "P", "category": "C", "parameter": "A",
          "end_tm": "2026-01-02 03:00:00"}],
        [{"fact_id": "r1", "lot_cd": "P", "oper_para": "C_A", "end_tm": "2026-01-01 04:00:00"},
         {"fact_id": "r2", "lot_cd": "P", "oper_para": "C_A", "end_tm": "2026-01-02 04:00:00"}],
        relation_id="wads_report_day", how="left", expected_cardinality="one_to_one",
        time_policy="none", left_time=None, right_time=None, tolerance_seconds=None,
        allow_approximate=True, max_expansion=2, max_rows=10)
    assert [row["right_fact_id"] for row in by_day.joined] == ["r1", "r2"]


def test_exact_image_identity_maps_registered_side_specific_keys_and_nulls_do_not_match():
    from harness.wafer_set_relations import join_tables

    left = [{"fact_id": "l1", "lot_id": "L", "wf_id": 1},
            {"fact_id": "ln", "lot_id": None, "wf_id": 1}]
    right = [{"fact_id": "r1", "alias_lot_id": "L", "wf_id": 1}]
    result = join_tables(left, right, relation_id="bmw_exact_identity", how="left",
                         expected_cardinality="one_to_many", time_policy="none",
                         left_time=None, right_time=None, tolerance_seconds=None,
                         allow_approximate=False, max_expansion=2, max_rows=10)
    assert result.joined[0]["right__fact_id"] == "r1"
    assert result.joined[1]["match_status"] == "left_only"


def test_temporal_many_to_one_and_parameter_process_guard():
    from harness.wafer_set_relations import join_tables

    left = [{"fact_id": "l1", "lot_id": "L", "wf_id": 1, "at": "2026-01-02 00:00:00"},
            {"fact_id": "l2", "lot_id": "L", "wf_id": 1, "at": "2026-01-03 00:00:00"}]
    right = [{"fact_id": "r1", "lot_id": "L", "wf_id": 1, "at": "2026-01-01 00:00:00"}]
    result = join_tables(left, right, relation_id="wafer_identity", how="left",
                         expected_cardinality="many_to_one", time_policy="latest_before",
                         left_time="at", right_time="at", tolerance_seconds=None,
                         allow_approximate=False, max_expansion=2, max_rows=10)
    assert result.audit["actual_cardinality"] == "many_to_one"

    parameter_left = [{"fact_id": "l", "lot_id": "L", "wf_id": 1, "process": "A"}]
    parameter_right = [{"fact_id": "r", "lot_id": "L", "wf_id": 1, "process": "B"}]
    guarded = join_tables(parameter_left, parameter_right, relation_id="wafer_parameter", how="left",
                          expected_cardinality="one_to_many", time_policy="none",
                          left_time=None, right_time=None, tolerance_seconds=None,
                          allow_approximate=False, max_expansion=2, max_rows=10)
    assert guarded.joined[0]["match_status"] == "left_only"

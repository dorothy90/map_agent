from __future__ import annotations

import json
import hashlib
from types import SimpleNamespace

import pytest


class Store:
    def __init__(self, observations): self.observations = observations
    async def observation(self, principal, session, result_id):
        assert (principal, session) == ("p", "s")
        return self.observations[result_id]
    async def rows(self, principal, session, result_id, table_id=None):
        observation = await self.observation(principal, session, result_id)
        table = next(item for item in observation.tables if item.table_id == table_id)
        return json.loads(json.dumps(table.rows))


def observation(result_id, tables):
    from harness.tools.wafer_set_tools import _version
    return SimpleNamespace(result_id=result_id, tables=tables, status="success",
        scope={"handle_contract": "wafer-table/v1",
               "table_versions": {table.table_id: _version(table.rows) for table in tables}},
        provenance={"data_origin": "fixture"})


def payload(entries):
    body = json.dumps({"MAP": entries}, separators=(",", ":"))
    full = hashlib.sha256(body.encode()).hexdigest()
    return {"payload_hash": full[:16], "payload_sha256": full, "map_val_json": body}


def test_parse_cache_is_namespaced_and_immutable():
    from harness.wafer_set_arrays import clear_parse_cache, parse_maps

    clear_parse_cache()
    rows = [payload(["0,0,A,A", "0,1,B,A"])]
    first = parse_maps(rows, namespace=("p", "s", "r", 1))
    second = parse_maps(rows, namespace=("p", "s", "r", 1))
    other_epoch = parse_maps(rows, namespace=("p", "s", "r", 2))
    key = rows[0]["payload_hash"]
    assert first[key] is second[key]
    assert first[key] is not other_epoch[key]
    assert not first[key].rows.flags.writeable
    assert not first[key].left_codes.flags.writeable


def test_bin_and_symbol_separate_union_numeric_answers():
    from harness.wafer_set_arrays import compute_map_metrics, parse_maps

    parsed = parse_maps([payload(["0,0,A,A", "0,1,B,A", "0,2,A,B", "0,3,B,B"])],
                        namespace=("p", "s", "r", 1))
    parsed = next(iter(parsed.values()))
    bin_rows, incomplete = compute_map_metrics(parsed, [{"metric_id": "pass", "mode": "bin",
        "quantity": "pass_rate", "bin_side": "left_bin", "pass_bin": "A",
        "parameters": [], "combine": "separate", "better_when": "higher"}], [])
    assert not incomplete
    assert bin_rows[0]["numerator"] == 2 and bin_rows[0]["denominator"] == 4
    assert bin_rows[0]["value"] == 50

    definitions = [
        {"definition_id": "left", "parameter": "left", "regex_pattern": "^B.$",
         "unit": "fraction", "better_when": "higher"},
        {"definition_id": "right", "parameter": "right", "regex_pattern": "^.B$",
         "unit": "volt", "better_when": "neutral"},
    ]
    specs = [{"metric_id": "symbols", "mode": "symbol", "quantity": "fail_rate",
              "parameters": ["left", "right"], "combine": "separate", "better_when": "lower"},
             {"metric_id": "union", "mode": "symbol", "quantity": "fail_rate",
              "parameters": ["left", "right"], "combine": "union", "better_when": "lower"}]
    rows, incomplete = compute_map_metrics(parsed, specs, definitions)
    assert not incomplete
    assert [(row["definition_id"], row["numerator"]) for row in rows[:2]] == [("left", 2), ("right", 2)]
    assert rows[2]["numerator"] == 3  # one die matches both definitions, counted once
    assert {row["unit"] for row in rows} == {"percent"}
    assert {row["better_when"] for row in rows} == {"lower"}


def test_definition_selection_uses_stored_process_link():
    from harness.tools.wafer_set_metrics import MetricSpec, _definition_subset

    definitions = [{"definition_id": "a", "lot_cd": "P", "process": "A", "parameter": "x"},
                   {"definition_id": "b", "lot_cd": "P", "process": "B", "parameter": "x"}]
    links = [{"source_process": "SOURCE", "definition_process": "B"}]
    spec = MetricSpec(metric_id="m", mode="symbol", quantity="fail_rate", parameters=["x"])
    assert _definition_subset(definitions, {"lot_cd": "P", "process": "SOURCE"}, spec, links)[0]["definition_id"] == "b"


def test_definition_selection_never_falls_back_to_other_lot_or_process():
    from harness.tools.wafer_set_metrics import MetricSpec, _definition_subset

    definitions = [
        {"definition_id": "other-lot", "lot_cd": "Q", "process": "X", "parameter": "failure"},
        {"definition_id": "other-process", "lot_cd": "P", "process": "Y", "parameter": "failure"},
    ]
    spec = MetricSpec(metric_id="m", mode="symbol", quantity="fail_rate",
                      parameters=["failure"])

    with pytest.raises(ValueError, match="missing or ambiguous"):
        _definition_subset(definitions, {"lot_cd": "P", "process": "X"}, spec)


def test_symbol_metrics_fullmatch_pair_and_reject_one_side_patterns():
    from harness.wafer_set_arrays import compute_map_metrics, parse_maps

    parsed = next(iter(parse_maps([payload([
        "0,0,A,B", "0,1,A,C", "0,2,C,B", "0,3,C,D",
    ])], namespace=("pair", "scope", "run", 1)).values()))
    spec = [{"metric_id": "fail", "mode": "symbol", "quantity": "fail_rate",
             "parameters": ["failure"], "combine": "separate", "better_when": "lower"}]

    rows, incomplete = compute_map_metrics(parsed, spec, [{
        "definition_id": "pair", "parameter": "failure", "regex_pattern": "AB"}])
    assert not incomplete and rows[0]["fail_die_count"] == 1
    for pattern in ("A", "B"):
        rows, _ = compute_map_metrics(parsed, spec, [{
            "definition_id": pattern, "parameter": "failure", "regex_pattern": pattern}])
        assert rows[0]["fail_die_count"] == 0


def test_fail_yield_remains_the_complement_of_symbol_fail_rate():
    from harness.wafer_set_arrays import compute_map_metrics, parse_maps
    from harness.tools.wafer_set_metrics import MetricSpec, _stored_metric

    parsed = next(iter(parse_maps([payload([
        "0,0,A,B", "0,1,A,C", "0,2,C,B", "0,3,C,D",
    ])], namespace=("yield", "scope", "run", 1)).values()))
    definition = [{"definition_id": "pair", "parameter": "failure", "regex_pattern": "AB"}]
    specs = [
        {"metric_id": "fail", "mode": "symbol", "quantity": "fail_rate",
         "parameters": ["failure"], "combine": "separate", "better_when": "lower"},
        {"metric_id": "yield", "mode": "symbol", "quantity": "fail_yield",
         "parameters": ["failure"], "combine": "separate", "better_when": "higher"},
    ]

    rows, _ = compute_map_metrics(parsed, specs, definition)
    assert [row["value"] for row in rows] == [25, 75]
    stored = _stored_metric(
        {"die_count": 4, "pass_die_count": 3, "calc_mode": "symbol",
         "calc_symbol_parameters": "failure"},
        MetricSpec(metric_id="yield", mode="stored_selection", quantity="fail_yield"),
    )
    assert stored["numerator"] == 3 and stored["value"] == 75


def test_malformed_pair_and_zero_denominator_are_partial():
    from harness.wafer_set_arrays import compute_map_metrics, parse_maps

    parsed = next(iter(parse_maps([payload(["0,0,TOO_LONG,A"])], namespace=("p", "s", "r", 1)).values()))
    rows, incomplete = compute_map_metrics(parsed, [{"metric_id": "m", "mode": "bin",
        "quantity": "pass_rate", "bin_side": "left_bin", "pass_bin": "A",
        "parameters": [], "combine": "separate", "better_when": "higher"}], [])
    assert incomplete and rows[0]["unmatched_cells"] == 1

    empty = next(iter(parse_maps([payload([])], namespace=("p", "s", "r", 1)).values()))
    rows, incomplete = compute_map_metrics(empty, [{"metric_id": "m", "mode": "bin",
        "quantity": "pass_rate", "bin_side": "left_bin", "pass_bin": "A",
        "parameters": [], "combine": "separate", "better_when": "higher"}], [])
    assert incomplete and rows[0]["value"] is None


def test_bin_pass_metric_does_not_invent_parameter_failure_count():
    from harness.wafer_set_arrays import compute_map_metrics, parse_maps

    parsed = next(iter(parse_maps([payload(["0,0,A,B", "0,1,C,D"])],
                                  namespace=("bin", "scope", "run", 1)).values()))
    rows, incomplete = compute_map_metrics(parsed, [{"metric_id": "pass", "mode": "bin",
        "quantity": "pass_rate", "bin_side": "left_bin", "pass_bin": "A",
        "parameters": [], "combine": "separate", "better_when": "higher"}], [])

    assert not incomplete
    assert rows[0]["pass_die_count"] == 1
    assert rows[0]["fail_die_count"] is None

    malformed = next(iter(parse_maps([payload(["0,0,A,"])],
                                     namespace=("bin", "malformed", "run", 1)).values()))
    rows, incomplete = compute_map_metrics(malformed, [{"metric_id": "pass", "mode": "bin",
        "quantity": "pass_rate", "bin_side": "left_bin", "pass_bin": "A",
        "parameters": [], "combine": "separate", "better_when": "higher"}], [])
    assert incomplete and rows[0]["unmatched_cells"] == 1


@pytest.mark.parametrize("entry", ["0,0,A", "0,0,A,", "0,0,AB,B", "0,0,A,BC"])
def test_malformed_symbol_pairs_are_unmatched_not_failed(entry):
    from harness.wafer_set_arrays import compute_map_metrics, parse_maps

    parsed = next(iter(parse_maps([payload([entry])],
                                  namespace=("malformed", entry, "run", 1)).values()))
    rows, incomplete = compute_map_metrics(parsed, [{"metric_id": "fail", "mode": "symbol",
        "quantity": "fail_rate", "parameters": ["failure"], "combine": "separate",
        "better_when": "lower"}], [{"definition_id": "pair", "parameter": "failure",
                                      "regex_pattern": "AB"}])

    assert incomplete
    assert rows[0]["unmatched_cells"] == 1
    assert rows[0]["fail_die_count"] == 0


def test_metric_input_validation_and_general_descriptions():
    from harness.tools import wafer_set_metrics
    from harness.tools.registry import ToolRegistry

    with pytest.raises(ValueError):
        wafer_set_metrics.MetricSpec(metric_id="x", mode="symbol", quantity="fail_rate")
    with pytest.raises(ValueError):
        wafer_set_metrics.MetricSpec(metric_id="x", mode="bin", quantity="pass_rate")
    with pytest.raises(ValueError):
        wafer_set_metrics.MetricSpec(metric_id="x", mode="bin", quantity="pass_rate",
                                     bin_side="left_bin", pass_bin="A", value_column="value")
    with pytest.raises(ValueError):
        wafer_set_metrics.MetricSpec(metric_id="x", mode="stored_selection", quantity="pass_rate",
                                     combine="union")
    registry = ToolRegistry()
    wafer_set_metrics.register(registry)
    schema = json.dumps(registry.tools["compute_metrics"].schema.model_json_schema(), ensure_ascii=False)
    assert all(token not in registry.tools["compute_metrics"].description + schema
               for token in ("PT1H", "PT1C", "?", "LANGGRAPH", "MAP.", "DF_"))
    spec = wafer_set_metrics.MetricSpec(metric_id="v", mode="source_value", quantity="value",
                                        value_column="value")
    with pytest.raises(ValueError, match="finite"):
        wafer_set_metrics._source_metric({"value": float("nan")}, spec, {})


@pytest.mark.asyncio
async def test_compute_metrics_handler_uses_handle_and_preserves_provenance():
    from harness.types import ResultTable
    from harness.tools.wafer_set_metrics import ComputeMetricsInput, compute_metrics

    rows = [{"member_id": "w", "measurement_ref": "m", "lot_id": "L", "wf_id": 1,
             "lot_cd": "P", "process": "X", "time_value": "2026-01-01 00:00:00",
             "yield_percent": 50, "pass_die_count": 1, "die_count": 2,
             "unmatched_cells": 0, "calc_mode": "bin", "calc_pass_bin": "A",
             "calc_bin_side": "left_bin", "calc_symbol_parameters": None}]
    table = ResultTable(table_id="wafers", title="Wafers", rows=rows, columns=list(rows[0]),
                        complete=True, missing_reason=None)
    obs = observation("source", [table])
    ctx = SimpleNamespace(store=Store({"source": obs}),
                          run={"principal_id": "p", "session_id": "s", "run_id": "r", "epoch": 1})
    args = ComputeMetricsInput(source={"result_id": "source", "table_id": "wafers"},
        metrics=[{"metric_id": "stored", "mode": "stored_selection", "quantity": "pass_rate"}])
    result = await compute_metrics(args, ctx)
    assert result.source_result_ids == ["source"]
    assert result.tables[0].rows[0]["value"] == 50
    assert result.scope["handle_contract"] == "wafer-table/v1"


def test_stored_bin_selection_cannot_supply_fail_metric():
    from harness.tools.wafer_set_metrics import MetricSpec, _stored_metric

    row = {"die_count": 2, "pass_die_count": 1, "calc_mode": "bin",
           "calc_bin_side": "left_bin", "calc_pass_bin": "A"}
    spec = MetricSpec(metric_id="fail", mode="stored_selection", quantity="fail_rate")

    with pytest.raises(ValueError, match="symbol_bin_parameters"):
        _stored_metric(row, spec)


@pytest.mark.asyncio
async def test_compute_symbol_metrics_uses_stored_merged_definitions_without_changing_units():
    from harness.types import ResultTable
    from harness.tools.wafer_set_metrics import ComputeMetricsInput, compute_metrics

    payload_row = payload(["0,0,A,A", "0,1,B,A"])
    measurements = [{"member_id": "w", "measurement_ref": "m", "lot_cd": "P",
        "process": "PT1H TEST", "payload_hash": payload_row["payload_hash"],
        "payload_sha256": payload_row["payload_sha256"]}]
    definition_rows = [{"definition_id": "symbol:legacy", "source_id": "wafer_parameter",
        "lot_cd": "P", "process": "PT1H", "parameter": "failure", "mode": "symbol",
        "regex_pattern": "^B.$", "param_kind": "bin", "unit": "fraction",
        "value_basis": "stored fraction", "better_when": "higher",
        "definition_version": "legacy"}]
    links = [{"source_id": "symbol_definition", "source_process": "PT1H TEST",
        "definition_process": "PT1H", "mapping_basis": "validated_normalization",
        "mapping_version": "link"}]
    source_tables = [
        ResultTable(table_id="map_measurements", title="Measurements", rows=measurements,
                    columns=list(measurements[0]), complete=True, missing_reason=None),
        ResultTable(table_id="map_payloads", title="Payloads", rows=[payload_row],
                    columns=list(payload_row), complete=True, missing_reason=None),
    ]
    definition_tables = [
        ResultTable(table_id="metric_definitions", title="Definitions", rows=definition_rows,
                    columns=list(definition_rows[0]), complete=True, missing_reason=None),
        ResultTable(table_id="process_links", title="Links", rows=links, columns=list(links[0]),
                    complete=True, missing_reason=None),
    ]
    ctx = SimpleNamespace(store=Store({
        "source": observation("source", source_tables),
        "definitions": observation("definitions", definition_tables),
    }), run={"principal_id": "p", "session_id": "s", "run_id": "r", "epoch": 1})
    args = ComputeMetricsInput(
        source={"result_id": "source", "table_id": "map_measurements"},
        definitions={"result_id": "definitions", "table_id": "metric_definitions"},
        metrics=[
            {"metric_id": "separate", "mode": "symbol", "quantity": "fail_rate",
             "parameters": ["failure"], "better_when": "lower"},
            {"metric_id": "union", "mode": "symbol", "quantity": "fail_rate",
             "parameters": ["failure"], "combine": "union", "better_when": "lower"},
        ])

    result = await compute_metrics(args, ctx)

    rows = result.tables[0].rows
    assert rows[0]["definition_id"] == "symbol:legacy"
    assert rows[1]["definition_id"].startswith("symbol-union:")
    assert {row["unit"] for row in rows} == {"percent"}
    assert {row["better_when"] for row in rows} == {"lower"}
    assert {row["unit"] for row in result.tables[1].rows} == {"percent"}


@pytest.mark.asyncio
async def test_compute_symbol_metrics_reports_unmatched_limitation_count():
    from harness.types import ResultTable
    from harness.tools.wafer_set_metrics import ComputeMetricsInput, compute_metrics

    payload_row = payload(["0,0,A,"])
    measurements = [{"measurement_ref": "m", "lot_cd": "P", "process": "X",
        "payload_hash": payload_row["payload_hash"], "payload_sha256": payload_row["payload_sha256"]}]
    definitions = [{"definition_id": "pair", "lot_cd": "P", "process": "X",
        "parameter": "failure", "regex_pattern": "AB"}]
    source_tables = [
        ResultTable(table_id="map_measurements", title="Measurements", rows=measurements,
                    columns=list(measurements[0]), complete=True, missing_reason=None),
        ResultTable(table_id="map_payloads", title="Payloads", rows=[payload_row],
                    columns=list(payload_row), complete=True, missing_reason=None),
    ]
    definition_table = ResultTable(table_id="metric_definitions", title="Definitions",
        rows=definitions, columns=list(definitions[0]), complete=True, missing_reason=None)
    ctx = SimpleNamespace(store=Store({
        "source": observation("source", source_tables),
        "definitions": observation("definitions", [definition_table]),
    }), run={"principal_id": "p", "session_id": "s", "run_id": "r", "epoch": 1})
    args = ComputeMetricsInput(
        source={"result_id": "source", "table_id": "map_measurements"},
        definitions={"result_id": "definitions", "table_id": "metric_definitions"},
        metrics=[{"metric_id": "fail", "mode": "symbol", "quantity": "fail_rate",
                  "parameters": ["failure"]}],
    )

    result = await compute_metrics(args, ctx)

    assert result.status == "partial"
    assert result.scope["limitations"][0]["code"] == "INVALID_BIN_PAIR"
    assert result.scope["limitations"][0]["affected_count"] == 1


@pytest.mark.asyncio
async def test_compute_metrics_matches_cohorts_by_preserved_key_columns():
    from harness.types import ResultTable
    from harness.tools.wafer_set_metrics import ComputeMetricsInput, compute_metrics
    from harness.wafer_set_predicates import make_cohorts

    rows = [
        {"lot_id": "LOT1", "wf_id": 1, "lot_cd": "P", "process": "X",
         "yield_percent": 50, "pass_die_count": 1, "die_count": 2,
         "unmatched_cells": 0, "calc_mode": "bin", "calc_pass_bin": "A",
         "calc_bin_side": "left_bin", "calc_symbol_parameters": None},
        {"lot_id": "LOT1", "wf_id": 2, "lot_cd": "P", "process": "X",
         "yield_percent": 100, "pass_die_count": 2, "die_count": 2,
         "unmatched_cells": 0, "calc_mode": "bin", "calc_pass_bin": "A",
         "calc_bin_side": "left_bin", "calc_symbol_parameters": None},
    ]
    cohort_output = make_cohorts(
        rows, key_columns=["lot_id", "wf_id"], group_by=["lot_id"], rules=[],
        combine="independent", null_group="include", max_cohorts=100,
        max_memberships=100,
    )
    source_table = ResultTable(table_id="wafers", title="Wafers", rows=rows,
                               columns=list(rows[0]), complete=True, missing_reason=None)
    membership_table = ResultTable(
        table_id="memberships", title="Memberships", rows=cohort_output.memberships,
        columns=["cohort_id", "member_id", "measurement_ref", "lot_id", "wf_id"],
        complete=True, missing_reason=None,
    )
    ctx = SimpleNamespace(
        store=Store({"source": observation("source", [source_table]),
                     "cohorts": observation("cohorts", [membership_table])}),
        run={"principal_id": "p", "session_id": "s", "run_id": "r", "epoch": 1},
    )
    args = ComputeMetricsInput(
        source={"result_id": "source", "table_id": "wafers"},
        cohorts={"result_id": "cohorts", "table_id": "memberships"},
        metrics=[{"metric_id": "stored", "mode": "stored_selection",
                  "quantity": "pass_rate"}],
    )

    result = await compute_metrics(args, ctx)

    assert [row["value"] for row in result.tables[0].rows] == [50, 100]
    assert len({row["cohort_id"] for row in result.tables[0].rows}) == 1
    assert result.scope["unmatched_members"] == 0


@pytest.mark.asyncio
async def test_compute_metrics_reports_when_no_cohort_members_match_source():
    from harness.types import ResultTable
    from harness.tools.wafer_set_metrics import ComputeMetricsInput, compute_metrics

    rows = [{"lot_id": "LOT1", "wf_id": 1, "pass_die_count": 1, "die_count": 2,
             "calc_mode": "bin", "calc_pass_bin": "A", "calc_bin_side": "left_bin"}]
    memberships = [{"cohort_id": "c", "member_id": '["OTHER", 9]',
                    "measurement_ref": None, "lot_id": "OTHER", "wf_id": 9}]
    source_table = ResultTable(table_id="wafers", title="Wafers", rows=rows,
                               columns=list(rows[0]), complete=True, missing_reason=None)
    membership_table = ResultTable(table_id="memberships", title="Memberships", rows=memberships,
                                   columns=list(memberships[0]), complete=True, missing_reason=None)
    ctx = SimpleNamespace(
        store=Store({"source": observation("source", [source_table]),
                     "cohorts": observation("cohorts", [membership_table])}),
        run={"principal_id": "p", "session_id": "s", "run_id": "r", "epoch": 1},
    )
    args = ComputeMetricsInput(
        source={"result_id": "source", "table_id": "wafers"},
        cohorts={"result_id": "cohorts", "table_id": "memberships"},
        metrics=[{"metric_id": "stored", "mode": "stored_selection",
                  "quantity": "pass_rate"}],
    )

    result = await compute_metrics(args, ctx)

    assert result.status == "partial"
    assert result.tables[0].rows == []
    assert result.tables[0].complete is False
    assert result.scope["unmatched_members"] == 1
    assert "match" in result.tables[0].missing_reason.lower()

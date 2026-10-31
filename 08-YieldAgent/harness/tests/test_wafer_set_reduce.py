from __future__ import annotations

import json
import hashlib
from types import SimpleNamespace

import pytest


def stored(entries):
    body = json.dumps({"MAP": entries})
    full = hashlib.sha256(body.encode()).hexdigest()
    return {"payload_hash": full[:16], "payload_sha256": full, "map_val_json": body}


def test_coordinate_denominators_sparse_grids_and_summary_difference():
    from harness.wafer_set_arrays import parse_maps, reduce_map_metrics

    payloads = [stored(["0,0,A,A", "0,1,B,A"]), stored(["0,0,A,A"])]
    parsed = parse_maps(payloads, namespace=("p", "s", "r", 1))
    measurements = [
        {"measurement_ref": "m1", "member_id": "w1", "payload_hash": payloads[0]["payload_hash"], "lot_cd": "P", "process": "X"},
        {"measurement_ref": "m2", "member_id": "w2", "payload_hash": payloads[1]["payload_hash"], "lot_cd": "P", "process": "X"},
    ]
    spec = {"metric_id": "pass", "mode": "bin", "quantity": "pass_rate",
            "bin_side": "left_bin", "pass_bin": "A", "parameters": [],
            "combine": "separate", "better_when": "higher"}
    cells, summaries, members, incomplete = reduce_map_metrics(
        measurements, parsed, [spec], [], [], coordinate_policy="same_product_process", grid_id=None)
    assert not incomplete and len(members) == 2
    by_coord = {(row["row"], row["col"]): row for row in cells}
    assert by_coord[(0, 0)]["denominator"] == 2 and by_coord[(0, 0)]["value"] == 100
    assert by_coord[(0, 1)]["denominator"] == 1 and by_coord[(0, 1)]["value"] == 0
    assert summaries[0]["pooled_value"] == pytest.approx(200 / 3)
    assert summaries[0]["mean_coordinate_value"] == 50


def test_symbol_reduction_keeps_percent_units_with_metadata_units():
    from harness.wafer_set_arrays import parse_maps, reduce_map_metrics

    payloads = [stored(["0,0,A,A", "0,1,B,A"])]
    parsed = parse_maps(payloads, namespace=("p", "s", "r", 1))
    measurements = [{"measurement_ref": "m", "member_id": "w",
        "payload_hash": payloads[0]["payload_hash"], "lot_cd": "P", "process": "X"}]
    definitions = [{"definition_id": "symbol:id", "lot_cd": "P", "process": "X",
        "parameter": "failure", "regex_pattern": "^B.$", "unit": "fraction",
        "better_when": "higher"}]
    spec = {"metric_id": "fail", "mode": "symbol", "quantity": "fail_rate",
        "parameters": ["failure"], "combine": "separate", "better_when": "lower"}

    cells, _, _, incomplete = reduce_map_metrics(
        measurements, parsed, [spec], definitions, [],
        coordinate_policy="same_product_process", grid_id=None)

    assert not incomplete
    assert {row["unit"] for row in cells} == {"percent"}


def test_symbol_reduction_fullmatches_pairs_and_requires_exact_scope():
    from harness.wafer_set_arrays import parse_maps, reduce_map_metrics

    payloads = [stored(["0,0,A,B", "0,1,A,C", "0,2,C,B", "0,3,C,D"])]
    parsed = parse_maps(payloads, namespace=("pair", "reduce", "run", 1))
    measurements = [{"measurement_ref": "m", "member_id": "w",
        "payload_hash": payloads[0]["payload_hash"], "lot_cd": "P", "process": "X"}]
    spec = {"metric_id": "fail", "mode": "symbol", "quantity": "fail_rate",
            "parameters": ["failure"], "combine": "separate", "better_when": "lower"}
    definitions = [{"definition_id": "pair", "lot_cd": "P", "process": "X",
                    "parameter": "failure", "regex_pattern": "AB"}]

    cells, summaries, _, incomplete = reduce_map_metrics(
        measurements, parsed, [spec], definitions, [],
        coordinate_policy="same_product_process", grid_id=None)
    assert not incomplete
    assert sum(row["numerator"] for row in cells) == 1
    assert summaries[0]["pooled_value"] == 25

    for wrong_scope in (("Q", "X"), ("P", "Y")):
        bad = [{**definitions[0], "lot_cd": wrong_scope[0], "process": wrong_scope[1]}]
        with pytest.raises(ValueError, match="missing or ambiguous"):
            reduce_map_metrics(measurements, parsed, [spec], bad, [],
                coordinate_policy="same_product_process", grid_id=None)


def test_reduce_input_rejects_bin_fail_metric_with_reselection_hint():
    from harness.tools.wafer_set_reduce import ReduceMapsInput

    with pytest.raises(ValueError, match="symbol_bin_parameters"):
        ReduceMapsInput(source={"result_id": "r", "table_id": "t"}, metrics=[{
            "metric_id": "fail", "mode": "bin", "quantity": "fail_rate",
            "bin_side": "left_bin", "pass_bin": "A"}])


def test_reduction_deduplicates_measurement_within_cohort_and_rejects_grid_mix():
    from harness.wafer_set_arrays import parse_maps, reduce_map_metrics

    payloads = [stored(["0,0,A,A"])]
    parsed = parse_maps(payloads, namespace=("p", "s", "r", 1))
    measurements = [{"measurement_ref": "m", "member_id": "w", "payload_hash": payloads[0]["payload_hash"],
                     "lot_cd": "P", "process": "X"}]
    memberships = [{"cohort_id": "c", "measurement_ref": "m", "member_id": "w"},
                   {"cohort_id": "c", "measurement_ref": "m", "member_id": "w"}]
    spec = {"metric_id": "pass", "mode": "bin", "quantity": "pass_rate",
            "bin_side": "left_bin", "pass_bin": "A", "parameters": [],
            "combine": "separate", "better_when": "higher"}
    _, summary, members, _ = reduce_map_metrics(measurements, parsed, [spec], [], memberships,
        coordinate_policy="same_product_process", grid_id=None)
    assert summary[0]["measurement_count"] == 1 and len(members) == 1
    cells, summary, members, _ = reduce_map_metrics(measurements, parsed, [spec], [],
        [{"cohort_id": "other", "measurement_ref": "not-m", "member_id": "not-w"}],
        coordinate_policy="same_product_process", grid_id=None)
    assert cells == [] and summary == [] and members == []
    with pytest.raises(ValueError, match="grid"):
        reduce_map_metrics(measurements, parsed, [spec], [], [],
                           coordinate_policy="explicit_same_grid", grid_id=None)


def test_reduction_matches_cohort_by_preserved_key_columns():
    from harness.wafer_set_arrays import parse_maps, reduce_map_metrics

    payloads = [stored(["0,0,A,A"])]
    parsed = parse_maps(payloads, namespace=("p", "s", "r", 1))
    measurements = [{"lot_id": "LOT1", "wf_id": 1,
                     "payload_hash": payloads[0]["payload_hash"],
                     "lot_cd": "P", "process": "X"}]
    memberships = [{"cohort_id": "c", "member_id": '["LOT1", 1]',
                    "measurement_ref": None, "lot_id": "LOT1", "wf_id": 1}]
    spec = {"metric_id": "pass", "mode": "bin", "quantity": "pass_rate",
            "bin_side": "left_bin", "pass_bin": "A", "parameters": [],
            "combine": "separate", "better_when": "higher"}

    cells, summary, members, incomplete = reduce_map_metrics(
        measurements, parsed, [spec], [], memberships,
        coordinate_policy="same_product_process", grid_id=None)

    assert not incomplete
    assert cells[0]["cohort_id"] == "c"
    assert summary[0]["measurement_count"] == 1
    assert members[0]["cohort_id"] == "c"


@pytest.mark.asyncio
async def test_reduce_handler_reads_only_stored_payloads(monkeypatch):
    from harness.types import ResultTable
    from harness.tools.wafer_set_reduce import ReduceMapsInput, reduce_maps
    from harness.tools.wafer_set_tools import _version

    payload_row = stored(["0,0,A,A", "0,1,B,A"])
    measurements = [{"measurement_ref": "m", "member_id": "w", "payload_hash": payload_row["payload_hash"],
                     "payload_sha256": payload_row["payload_sha256"], "lot_cd": "P", "process": "X"}]
    tables = [ResultTable(table_id="map_measurements", title="Measurements", rows=measurements,
                          columns=list(measurements[0]), complete=True, missing_reason=None),
              ResultTable(table_id="map_payloads", title="Payloads", rows=[payload_row],
                          columns=list(payload_row), complete=True, missing_reason=None)]
    obs = SimpleNamespace(result_id="source", tables=tables, status="success",
        scope={"handle_contract": "wafer-table/v1",
               "table_versions": {table.table_id: _version(table.rows) for table in tables}},
        provenance={"data_origin": "fixture"})
    class Store:
        async def observation(self, principal, session, result_id): return obs
        async def rows(self, principal, session, result_id, table_id=None):
            return json.loads(json.dumps(next(table.rows for table in tables if table.table_id == table_id)))
    ctx = SimpleNamespace(store=Store(), run={"principal_id": "p", "session_id": "s",
                                               "run_id": "r", "epoch": 1})
    args = ReduceMapsInput(source={"result_id": "source", "table_id": "map_measurements"},
        metrics=[{"metric_id": "pass", "mode": "bin", "quantity": "pass_rate",
                  "bin_side": "left_bin", "pass_bin": "A"}])
    result = await reduce_maps(args, ctx)
    assert result.tables[0].rows[0]["denominator"] == 1
    assert result.source_result_ids == ["source"]

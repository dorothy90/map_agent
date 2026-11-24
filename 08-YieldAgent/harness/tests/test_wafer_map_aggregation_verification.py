import asyncio

from harness.native_verification import (
    WAFER_MAP_REQUIRED_COLUMNS,
    enforce_wafer_map_contract_review,
    inspect_wafer_map_contracts,
    validate_wafer_map_contract,
)
from harness.types import TableRef, ToolObservation


SOURCE_ROWS = [
    {"measurement_id": "m1", "lot_id": "L1", "wf_id": 1, "row": -1, "col": 2,
     "bin": "AA", "fail_bin_name": "FAIL_A"},
    {"measurement_id": "m1", "lot_id": "L1", "wf_id": 1, "row": 0, "col": 5,
     "bin": "??", "fail_bin_name": None},
    {"measurement_id": "m2", "lot_id": "L1", "wf_id": 2, "row": -1, "col": 2,
     "bin": "??", "fail_bin_name": None},
    {"measurement_id": "m2", "lot_id": "L1", "wf_id": 2, "row": 3, "col": -4,
     "bin": "BB", "fail_bin_name": "FAIL_B"},
    {"measurement_id": "m3", "lot_id": "L2", "wf_id": 7, "row": -1, "col": 2,
     "bin": "BB", "fail_bin_name": "FAIL_B"},
    {"measurement_id": "m3", "lot_id": "L2", "wf_id": 7, "row": 0, "col": 5,
     "bin": "??", "fail_bin_name": None},
    {"measurement_id": "m3", "lot_id": "L2", "wf_id": 7, "row": 3, "col": -4,
     "bin": "??", "fail_bin_name": None},
    # A distinct measurement of the same lot/wafer is valid remeasurement evidence.
    {"measurement_id": "m4", "lot_id": "L2", "wf_id": 7, "row": -1, "col": 2,
     "bin": "??", "fail_bin_name": None},
    {"measurement_id": "m4", "lot_id": "L2", "wf_id": 7, "row": 3, "col": -4,
     "bin": "AA", "fail_bin_name": "FAIL_A"},
]


def manifest(group_id, member_count, *, grouping_mode="explicit_groups",
             numerator_mode="defined_fail_bins", numerator_bins_json="[]"):
    return {
        "contract_version": "wafer-map-evidence/v1", "map_type": "cummap",
        "selection_id": "selected", "grouping_mode": grouping_mode, "group_id": group_id,
        "member_count": member_count, "measurement_policy": "all selected measurements",
        "numerator_mode": numerator_mode, "numerator_bins_json": numerator_bins_json,
        "source_result_ids_json": '["source"]',
    }


def member(group_id, measurement_id, lot_id, wf_id):
    return {
        "contract_version": "wafer-map-evidence/v1", "selection_id": "selected",
        "group_id": group_id, "source_result_id": "source", "lot_id": lot_id,
        "wf_id": wf_id, "measurement_id": measurement_id,
    }


def cell(group_id, row, col, fail_count, observation_count, fail_rate):
    return {
        "contract_version": "wafer-map-evidence/v1", "selection_id": "selected",
        "group_id": group_id, "row": row, "col": col, "fail_count": fail_count,
        "observation_count": observation_count, "fail_rate": fail_rate,
    }


def valid_tables():
    return {
        "wafer_map_manifest": [manifest("range-1-2", 2), manifest("lot-2-retests", 2)],
        "wafer_map_members": [
            member("range-1-2", "m1", "L1", 1), member("range-1-2", "m2", "L1", 2),
            member("lot-2-retests", "m3", "L2", 7), member("lot-2-retests", "m4", "L2", 7),
        ],
        "wafer_map_cells": [
            cell("range-1-2", -1, 2, 1, 2, 0.5), cell("range-1-2", 0, 5, 0, 1, 0.0),
            cell("range-1-2", 3, -4, 1, 1, 1.0), cell("lot-2-retests", -1, 2, 1, 2, 0.5),
            cell("lot-2-retests", 0, 5, 0, 1, 0.0), cell("lot-2-retests", 3, -4, 1, 2, 0.5),
        ],
    }


def test_normal_cummap_contract_passes_independent_reaggregation():
    assert validate_wafer_map_contract(valid_tables(), {"source": SOURCE_ROWS}) == {
        "verdict": "pass", "issues": []}


def test_minimum_required_columns_form_valid_evidence_tables():
    tables = valid_tables()
    example = {
        table_id: [{column: row[column] for column in WAFER_MAP_REQUIRED_COLUMNS[table_id]} for row in rows]
        for table_id, rows in tables.items()
    }
    assert validate_wafer_map_contract(example, {"source": SOURCE_ROWS}) == {
        "verdict": "pass", "issues": []}


def test_fail_pass_and_missing_observation_has_hand_calculated_one_over_two():
    rows = [
        {"measurement_id": "a", "lot_id": "LA", "wf_id": 1, "row": 0, "col": 0,
         "bin": "AA", "fail_bin_name": "FAIL_A"},
        {"measurement_id": "b", "lot_id": "LB", "wf_id": 2, "row": 0, "col": 0,
         "bin": "??", "fail_bin_name": None},
        # Measurement c has no observation at (0, 0); it must not enter the denominator.
        {"measurement_id": "c", "lot_id": "LC", "wf_id": 3, "row": 9, "col": -2,
         "bin": "??", "fail_bin_name": None},
    ]
    tables = {
        "wafer_map_manifest": [manifest("arbitrary", 3, grouping_mode="selection_set")],
        "wafer_map_members": [member("arbitrary", "a", "LA", 1),
                              member("arbitrary", "b", "LB", 2),
                              member("arbitrary", "c", "LC", 3)],
        "wafer_map_cells": [cell("arbitrary", 0, 0, 1, 2, 0.5),
                            cell("arbitrary", 9, -2, 0, 1, 0.0)],
    }

    assert validate_wafer_map_contract(tables, {"source": rows})["verdict"] == "pass"


def test_single_wafer_is_zero_or_one_and_unobserved_coordinate_is_nan():
    tables = {
        "wafer_map_manifest": [manifest("selected-wafer", 1, grouping_mode="selection_set")],
        "wafer_map_members": [member("selected-wafer", "m1", "L1", 1)],
        "wafer_map_cells": [cell("selected-wafer", -1, 2, 1, 1, 1.0),
                            cell("selected-wafer", 0, 5, 0, 1, 0.0),
                            cell("selected-wafer", 3, -4, 0, 0, None)],
    }
    assert validate_wafer_map_contract(tables, {"source": SOURCE_ROWS})["verdict"] == "pass"


def test_selected_bin_changes_only_numerator_not_observation_count():
    tables = {
        "wafer_map_manifest": [manifest("all-four", 4, grouping_mode="selection_set",
            numerator_mode="selected_bins", numerator_bins_json='["AA"]')],
        "wafer_map_members": [member("all-four", "m1", "L1", 1), member("all-four", "m2", "L1", 2),
            member("all-four", "m3", "L2", 7), member("all-four", "m4", "L2", 7)],
        "wafer_map_cells": [cell("all-four", -1, 2, 1, 4, 0.25),
                            cell("all-four", 0, 5, 0, 2, 0.0),
                            cell("all-four", 3, -4, 1, 3, 1 / 3)],
    }
    assert validate_wafer_map_contract(tables, {"source": SOURCE_ROWS})["verdict"] == "pass"


def test_tampered_counts_rates_members_and_groups_are_rejected():
    mutations = []
    bad_count = valid_tables(); bad_count["wafer_map_cells"][0]["fail_count"] = 2; mutations.append(bad_count)
    bad_denominator = valid_tables(); bad_denominator["wafer_map_cells"][1].update(
        observation_count=2, fail_rate=0.0); mutations.append(bad_denominator)
    bad_rate = valid_tables(); bad_rate["wafer_map_cells"][2]["fail_rate"] = 0.5; mutations.append(bad_rate)
    unknown_member = valid_tables(); unknown_member["wafer_map_members"][0] = member(
        "range-1-2", "made-up", "L1", 1); mutations.append(unknown_member)
    duplicate_member = valid_tables(); duplicate_member["wafer_map_members"][1] = (
        duplicate_member["wafer_map_members"][0].copy()); mutations.append(duplicate_member)
    wrong_group_mode = valid_tables(); wrong_group_mode["wafer_map_manifest"] = [
        {**row, "grouping_mode": "selection_set"} for row in wrong_group_mode["wafer_map_manifest"]]
    mutations.append(wrong_group_mode)
    for tables in mutations:
        assert validate_wafer_map_contract(tables, {"source": SOURCE_ROWS})["verdict"] == "fail"


def test_duplicate_coordinate_within_measurement_is_rejected_but_remeasurement_is_not():
    assert validate_wafer_map_contract(valid_tables(), {
        "source": [*SOURCE_ROWS, SOURCE_ROWS[0].copy()]})["verdict"] == "fail"
    assert validate_wafer_map_contract(valid_tables(), {"source": SOURCE_ROWS})["verdict"] == "pass"


class MemoryStore:
    def __init__(self, rows): self._rows = rows
    async def rows(self, principal_id, session_id, result_id, table_id=None):
        return self._rows[(result_id, table_id)]


def table_ref(table_id, rows):
    return TableRef(table_id=table_id, title=table_id, columns=list(rows[0]) if rows else [],
                    total_rows=len(rows), preview_rows=rows[:5], complete=True, data_ref=table_id)


def observation(result_id, tool_name, *, source_ids=(), tables=(), artifacts=()):
    return ToolObservation(principal_id="p", session_id="s", run_id="r", invocation_id=result_id,
        result_id=result_id, tool_name=tool_name, status="success", source_result_ids=list(source_ids),
        tables=list(tables), artifact_refs=list(artifacts)).model_dump(mode="json")


def test_pr130_style_plot_without_structured_evidence_cannot_pass():
    source = observation("source", "load_wafer_maps", tables=[table_ref("wafer_map_dies", SOURCE_ROWS)])
    plot = observation("plot", "run_python", source_ids=["source"],
        tables=[table_ref("output", [{"stdout": "looks right"}])],
        artifacts=[{"artifact_id": "chart", "title": "chart", "mime": "text/html"}])
    store = MemoryStore({("source", "wafer_map_dies"): SOURCE_ROWS,
                         ("plot", "output"): [{"stdout": "looks right"}]})
    report = asyncio.run(inspect_wafer_map_contracts(store, "p", "s", [source, plot]))
    assert report["verdict"] == "fail"
    assert report["result_ids"] == ["plot", "source"]


def test_latest_corrected_map_for_same_source_replaces_rejected_draft():
    source = observation("source", "load_wafer_maps", tables=[table_ref("wafer_map_dies", SOURCE_ROWS)])
    draft = observation("draft", "run_python", source_ids=["source"],
        tables=[table_ref("output", [{"stdout": "bin pivots"}])],
        artifacts=[{"artifact_id": "draft-chart", "title": "chart", "mime": "text/html"}])
    tables = valid_tables()
    corrected = observation("corrected", "run_python", source_ids=["source"],
        tables=[table_ref(table_id, rows) for table_id, rows in tables.items()],
        artifacts=[{"artifact_id": "corrected-chart", "title": "chart", "mime": "text/html"}])
    stored = {("source", "wafer_map_dies"): SOURCE_ROWS, ("draft", "output"): [{"stdout": "bin pivots"}]}
    stored.update({("corrected", table_id): rows for table_id, rows in tables.items()})
    report = asyncio.run(inspect_wafer_map_contracts(MemoryStore(stored), "p", "s",
                                                     [source, draft, corrected]))
    assert report == {"verdict": "pass", "issues": [], "result_ids": ["corrected", "source"]}


def test_non_map_question_has_no_aggregation_contract_requirement():
    result = observation("yield", "query_db", tables=[table_ref("rows", [{"yield": 0.9}])])
    report = asyncio.run(inspect_wafer_map_contracts(
        MemoryStore({("yield", "rows"): [{"yield": 0.9}]}), "p", "s", [result]))
    assert report == {"verdict": "not_applicable", "issues": [], "result_ids": []}


def test_structural_failure_cannot_be_overridden_by_model_verifier_pass():
    report = enforce_wafer_map_contract_review(
        {"verdict": "pass", "issues": [], "result_ids": ["review-read"]},
        {"verdict": "fail", "issues": ["좌표별 분모 불일치"],
         "result_ids": ["plot", "source"]},
    )

    assert report == {
        "verdict": "fail", "issues": ["좌표별 분모 불일치"],
        "result_ids": ["review-read", "plot", "source"],
    }

from __future__ import annotations

import pytest
from types import SimpleNamespace
import json


def row(value, key="x", **extra):
    return {"group": key, "value": value, "metric_id": "m", "definition_id": "d",
            "unit": "percent", "better_when": "higher", "operation": "mean", **extra}


def test_percentage_point_relative_and_ratio_answers():
    from harness.wafer_set_statistics import compare_rows

    result = compare_rows([row(80)], [row(90)], keys=["group"], value_column="value",
                          methods=["difference", "relative_percent", "ratio"], paired=False,
                          pair_keys=[], missing="preserve")
    output = result.rows[0]
    assert output["delta"] == 10 and output["delta_unit"] == "percentage_points"
    assert output["relative_percent"] == 12.5 and output["ratio"] == 1.125
    assert output["direction"] == "improved"


def test_zero_baseline_and_missing_match_preserve_nulls():
    from harness.wafer_set_statistics import compare_rows

    result = compare_rows([row(0), row(10, "baseline-only")], [row(5), row(20, "current-only")],
                          keys=["group"], value_column="value",
                          methods=["difference", "relative_percent", "ratio"], paired=False,
                          pair_keys=[], missing="preserve")
    zero = next(item for item in result.rows if item["group"] == "x")
    assert zero["delta"] == 5 and zero["relative_percent"] is None and zero["ratio"] is None
    unmatched = [item for item in result.rows if item["match_status"] != "matched"]
    assert len(unmatched) == 2 and all(item["delta"] is None for item in unmatched)


def test_definition_unit_and_paired_validation():
    from harness.wafer_set_statistics import compare_rows

    with pytest.raises(ValueError, match="definition"):
        compare_rows([row(1)], [row(2, definition_id="other")], keys=["group"],
                     value_column="value", methods=["difference"], paired=False,
                     pair_keys=[], missing="preserve")
    with pytest.raises(ValueError, match="individual"):
        compare_rows([row(1)], [row(2)], keys=["group"], value_column="value",
                     methods=["difference"], paired=True, pair_keys=["member_id"], missing="preserve")


def test_baseline_only_coordinate_is_not_zero():
    from harness.wafer_set_statistics import compare_rows

    result = compare_rows([row(25, row=0, col=0)], [], keys=["row", "col"],
                          value_column="value", methods=["difference"], paired=False,
                          pair_keys=[], missing="preserve")
    assert result.rows[0]["current_value"] is None and result.rows[0]["delta"] is None


@pytest.mark.asyncio
async def test_compare_handler_uses_owned_handles_and_propagates_partial():
    from harness.types import ResultTable
    from harness.tools.wafer_set_compare import CompareMetricsInput, compare_metrics
    from harness.tools.wafer_set_tools import _version

    observations = {}
    for result_id, rows in (("baseline", [row(80)]), ("current", [row(90)])):
        table = ResultTable(table_id="aggregates", title="Aggregates", rows=rows,
                            columns=list(rows[0]), complete=True, missing_reason=None)
        observations[result_id] = SimpleNamespace(result_id=result_id, tables=[table], status="success",
            scope={"handle_contract": "wafer-table/v1", "table_versions": {"aggregates": _version(rows)}},
            provenance={"data_origin": "fixture"})
    class Store:
        async def observation(self, principal, session, result_id): return observations[result_id]
        async def rows(self, principal, session, result_id, table_id=None):
            return json.loads(json.dumps(observations[result_id].tables[0].rows))
    ctx = SimpleNamespace(store=Store(), run={"principal_id": "p", "session_id": "s"})
    args = CompareMetricsInput(baseline={"result_id": "baseline", "table_id": "aggregates"},
        current={"result_id": "current", "table_id": "aggregates"}, keys=["group"],
        methods=["difference", "relative_percent"])
    result = await compare_metrics(args, ctx)
    assert result.tables[0].rows[0]["delta"] == 10
    assert result.source_result_ids == ["baseline", "current"]

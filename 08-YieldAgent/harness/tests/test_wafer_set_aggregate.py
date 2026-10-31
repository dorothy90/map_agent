from __future__ import annotations

import pytest
from types import SimpleNamespace
import json


def metric(value, numerator, denominator, measurement, cohort=None, **extra):
    return {"metric_id": "yield", "definition_id": "d", "value": value,
            "numerator": numerator, "denominator": denominator, "unit": "percent",
            "measurement_ref": measurement, "member_id": measurement, "cohort_id": cohort,
            "lot_id": measurement, "wf_id": 1, "better_when": "higher",
            "aggregation_level": "measurement", **extra}


def test_mean_and_ratio_of_sums_distinguish_75_and_90():
    from harness.wafer_set_statistics import aggregate_rows

    rows = [metric(50, 1, 2, "a"), metric(100, 8, 8, "b")]
    mean = aggregate_rows(rows, group_by=[], operation="mean", period="none",
                          time_column=None, overlap="error", max_groups=100)
    weighted = aggregate_rows(rows, group_by=[], operation="ratio_of_sums", period="none",
                              time_column=None, overlap="error", max_groups=100)
    assert mean.rows[0]["value"] == 75
    assert weighted.rows[0]["value"] == 90
    assert weighted.rows[0]["numerator_sum"] == 9
    assert weighted.rows[0]["denominator_sum"] == 10


def test_duplicate_within_cohort_is_deduplicated_and_overlap_is_explicit():
    from harness.wafer_set_statistics import aggregate_rows

    duplicate = metric(50, 1, 2, "a", "c1")
    result = aggregate_rows([duplicate, dict(duplicate)], group_by=["cohort_id"], operation="mean",
                            period="none", time_column=None, overlap="error", max_groups=100)
    assert result.rows[0]["measurement_count"] == 1
    overlapping = [metric(50, 1, 2, "a", "c1"), metric(50, 1, 2, "a", "c2")]
    with pytest.raises(ValueError, match="overlap"):
        aggregate_rows(overlapping, group_by=[], operation="mean", period="none",
                       time_column=None, overlap="error", max_groups=100)


def test_period_boundaries_and_reaggregation_guard():
    from harness.wafer_set_statistics import aggregate_rows

    row = metric(50, 1, 2, "a", time_value="2026-09-20 23:59:59")
    result = aggregate_rows([row], group_by=[], operation="mean", period="weekly",
                            time_column="time_value", overlap="error", max_groups=100)
    assert result.rows[0]["period_start"] == "2026-09-14 00:00:00"
    assert result.rows[0]["period_end"] == "2026-09-21 00:00:00"
    row["aggregation_level"] = "cohort"
    with pytest.raises(ValueError, match="re-aggregate"):
        aggregate_rows([row], group_by=[], operation="mean", period="none",
                       time_column=None, overlap="error", max_groups=100)


def test_pivot_keeps_configured_missing_column():
    from harness.wafer_set_statistics import aggregate_rows, pivot_rows

    aggregate = aggregate_rows([metric(50, 1, 2, "a", process="P", parameter="X")],
                               group_by=["process", "parameter"], operation="mean",
                               period="none", time_column=None, overlap="error", max_groups=100)
    catalog = [{"source_id": "s", "process": "P", "parameter": "X", "included": True,
                "display_order": 1, "unit": "percent", "better_when": "higher"},
               {"source_id": "s", "process": "P", "parameter": "Y", "included": True,
                "display_order": 2, "unit": "percent", "better_when": "higher"}]
    pivot, columns = pivot_rows(aggregate.rows, catalog)
    assert len(columns) == 2
    missing_id = next(row["column_id"] for row in columns if row["parameter"] == "Y")
    assert pivot[0][missing_id] is None


@pytest.mark.asyncio
async def test_aggregate_handler_uses_stored_table_without_database():
    from harness.types import ResultTable
    from harness.tools.wafer_set_aggregate import AggregateMetricsInput, aggregate_metrics
    from harness.tools.wafer_set_tools import _version

    rows = [metric(50, 1, 2, "a"), metric(100, 8, 8, "b")]
    table = ResultTable(table_id="metrics", title="Metrics", rows=rows, columns=list(rows[0]),
                        complete=True, missing_reason=None)
    obs = SimpleNamespace(result_id="source", tables=[table], status="success",
        scope={"handle_contract": "wafer-table/v1", "table_versions": {"metrics": _version(rows)}},
        provenance={"data_origin": "fixture"})
    class Store:
        async def observation(self, principal, session, result_id): return obs
        async def rows(self, principal, session, result_id, table_id=None):
            return json.loads(json.dumps(rows))
    ctx = SimpleNamespace(store=Store(), run={"principal_id": "p", "session_id": "s"})
    result = await aggregate_metrics(AggregateMetricsInput(
        sources=[{"result_id": "source", "table_id": "metrics"}], operation="ratio_of_sums"), ctx)
    assert result.tables[0].rows[0]["value"] == 90
    assert result.source_result_ids == ["source"]


@pytest.mark.asyncio
async def test_aggregate_rejects_non_metric_source_with_clear_message():
    from harness.types import ResultTable
    from harness.tools.wafer_set_aggregate import AggregateMetricsInput, aggregate_metrics
    from harness.tools.wafer_set_tools import _version

    rows = [{"lot_id": "LOT1", "wf_id": 1, "yield_percent": 50}]
    table = ResultTable(table_id="wafers", title="Wafers", rows=rows, columns=list(rows[0]),
                        complete=True, missing_reason=None)
    obs = SimpleNamespace(result_id="source", tables=[table], status="success",
        scope={"handle_contract": "wafer-table/v1", "table_versions": {"wafers": _version(rows)}},
        provenance={"data_origin": "fixture"})
    class Store:
        async def observation(self, principal, session, result_id): return obs
        async def rows(self, principal, session, result_id, table_id=None):
            return json.loads(json.dumps(rows))
    ctx = SimpleNamespace(store=Store(), run={"principal_id": "p", "session_id": "s"})

    with pytest.raises(ValueError, match="compute_metrics.*필요 열"):
        await aggregate_metrics(AggregateMetricsInput(
            sources=[{"result_id": "source", "table_id": "wafers"}], operation="mean"), ctx)

from harness.tests.report_table_fakes import fake_report_metadata
import asyncio

import pytest
from pydantic import ValidationError

from harness.tools.registry import ToolRegistry
from harness.tools.table_artifact import YieldTableInput, register


REMOVED_FIELDS = {"mode", "columns", "column_groups", "column_formats", "delta_row", "period_kind", "rows", "periods", "product_label", "period_label",
                  "parameter_formats", "long_result_id", "counts_result_id", "gms_result_id",
                  "long_columns", "counts_columns", "gms_columns"}


def report_registry():
    registry = ToolRegistry()
    register(registry)
    return registry


def test_model_contract_only_exposes_report_inputs():
    tool = report_registry().model_tools()[0]["function"]
    schema = tool["parameters"]
    assert not REMOVED_FIELDS.intersection(schema["properties"])
    assert schema["additionalProperties"] is False
    assert {"lot_cd", "period", "report_scope_id", "grouping", "layout"} <= schema["properties"].keys()
    assert "mode=" not in tool["description"]


def test_explicit_week_renders_a_weekly_report(fake_period_report):
    result = asyncio.run(report_registry().invoke("render_yield_table", {
        "title": "weekly", "lot_cd": "4SS",
        "period": {"kind": "weeks", "weeks": ["2026-43", "2026-44", "2026-45", "2026-46"]},
    }, None))
    assert {k: result.scope[k] for k in ("title", "row_count", "column_count", "parameter_count")} == {"title": "weekly", "row_count": 4, "column_count": 11, "parameter_count": 3}
    assert 'th-section-pt1h' in result.artifacts[0]["data"]
    assert '<tr class="delta">' in result.artifacts[0]["data"]


@pytest.mark.parametrize("extra", [
    {"mode": "table"}, {"mode": "report"}, {"columns": []},
    {"column_groups": []}, {"column_formats": {}}, {"delta_row": False},
])
def test_removed_presentation_fields_are_rejected(extra):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        YieldTableInput(title="report", **extra)


def test_free_form_rows_cannot_fall_back_to_generic_table():
    with pytest.raises(ValidationError):
        YieldTableInput(title="custom table", rows=[{"a": 1, "b": 2}])


def test_group_report_keeps_105_metadata_parameters_including_zeroes(monkeypatch):
    params = [f"PARAM_{index:03d}" for index in range(105)]
    from harness.tools import table_artifact
    monkeypatch.setattr(table_artifact, '_fetch_parameter_metadata',
                        lambda lot: [('PT1H', param, i, 'P', 'o') for i, param in enumerate(params)])
    result = table_artifact._render_report(table_artifact._ReportInput(**{
        "title": "group report",
        "periods": [{"period": "chosen", "label": "선택"}, {"period": "rest", "label": "나머지"}],
        "rows": [{"period": group, "process": "pt1h", "param": param,
                  "value": 0 if index < 27 else index}
                 for group in ("chosen", "rest") for index, param in enumerate(params)],
        "comparison": {"current": "chosen", "baseline": "rest"},
    }), "4SS", table_artifact._fetch_parameter_metadata("4SS"), [])
    assert result.scope["row_count"] == 2
    assert result.scope["parameter_count"] == 105
    assert result.scope["column_count"] == 113
    html = result.artifacts[0]["data"]
    assert all(param in html for param in params)
    assert "Δ 선택 − 나머지" in html


@pytest.mark.parametrize("field,value", [
    ("rows", []), ("periods", []), ("product_label", "4SS"),
    ("parameter_formats", []), ("long_result_id", "saved"),
    ("counts_result_id", "saved"), ("gms_result_id", "saved"),
    ("long_columns", {}), ("counts_columns", {}), ("gms_columns", {}),
])
def test_public_api_rejects_removed_mapping_and_cell_inputs(field, value):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        YieldTableInput(lot_cd="4SS", period={"kind": "weeks", "weeks": ["2026-45"]},
                        **{field: value})


@pytest.fixture
def fake_period_report(monkeypatch):
    """Exercise the public query/render path with captured, offline evidence."""
    from harness.tools import table_artifact
    from harness.tools.yield_report_data import PeriodReportData, resolve_window

    def fetch(lot_cd, period):
        assert lot_cd == "4SS"
        window = resolve_window(period)
        weeks = window["weeks"]
        return PeriodReportData(
            lot_cd=lot_cd, window=window,
            metadata=[("PT1H", "VTH", 1, "P", "o"),
                      ("PT1H", "IDSAT", 2, "P", "o"),
                      ("PT1C", "PT1C", 1, "P", "o"),
                      ("PT1C", "EXCLUDED", 99, "P", "x")],
            yield_long=[{"grain": "week", "bucket": week, "week": week,
                         "process": "PT1H TEST", "param": "VTH", "value": 1.2 + i / 100}
                        for i, week in enumerate(weeks)],
            counts={"week": [{"week": week, "lot_count": 1, "wafer_count": 2,
                               "has_yield_data": True, "has_gms": True} for week in weeks]},
            period_lot_count=len(weeks), period_wafer_count=2 * len(weeks),
            gms={"week": [{"week": week, "period_date": week.replace("-", ""),
                            "fab": 97.1, "prb": 98.2, "cum0": 95.3} for week in weeks]},
            lot_population=[{"lot_id": f"LOT{i}", "wafer_count": 2,
                             "pt1h_mean": .9, "pt1h_value_count": 2} for i in range(len(weeks))],
            lot_parameter_stats=[], lot_wafer_members=[],
        )

    monkeypatch.setattr(table_artifact, "fetch_period_report", fetch)

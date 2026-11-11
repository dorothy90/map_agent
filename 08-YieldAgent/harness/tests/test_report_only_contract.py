from harness.tests.report_table_fakes import fake_report_metadata
import asyncio

import pytest
from pydantic import ValidationError

from harness.tools.registry import ToolRegistry
from harness.tools.table_artifact import YieldTableInput, register


REMOVED_FIELDS = {"mode", "columns", "column_groups", "column_formats", "delta_row"}


def report_registry():
    registry = ToolRegistry()
    register(registry)
    return registry


def test_model_contract_only_exposes_report_inputs():
    tool = report_registry().model_tools()[0]["function"]
    schema = tool["parameters"]
    assert not REMOVED_FIELDS.intersection(schema["properties"])
    assert schema["additionalProperties"] is False
    row = schema["properties"]["rows"]["items"]
    assert set(row["properties"]) == {"period", "process", "param", "value"}
    assert set(row["required"]) >= {"period", "process", "param"}
    assert "mode=" not in tool["description"]


def test_explicit_week_renders_a_weekly_report():
    result = asyncio.run(report_registry().invoke("render_yield_table", {
        "title": "weekly", "period_kind": "week",
        "rows": [
            {"period": "2026-W44", "process": "pt1h", "param": "VTH", "value": 1.2},
            {"period": "2026-W45", "process": "pt1h", "param": "VTH", "value": 1.3},
        ],
    }, None))
    assert {k: result.scope[k] for k in ("title", "row_count", "column_count", "parameter_count")} == {"title": "weekly", "row_count": 2, "column_count": 12, "parameter_count": 4}
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
    result = asyncio.run(report_registry().invoke("render_yield_table", {
        "title": "group report", "period_kind": "group",
        "periods": [{"period": "chosen", "label": "선택"}, {"period": "rest", "label": "나머지"}],
        "rows": [{"period": group, "process": "pt1h", "param": param,
                  "value": 0 if index < 27 else index}
                 for group in ("chosen", "rest") for index, param in enumerate(params)],
        "comparison": {"current": "chosen", "baseline": "rest"},
    }, None))
    assert result.scope["row_count"] == 2
    assert result.scope["parameter_count"] == 105
    assert result.scope["column_count"] == 113
    html = result.artifacts[0]["data"]
    assert all(param in html for param in params)
    assert "Δ 선택 − 나머지" in html

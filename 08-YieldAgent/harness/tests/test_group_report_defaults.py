from harness.tests.report_table_fakes import fake_report_metadata, group_html as _html

import pytest
from pydantic import ValidationError

from harness.tools.registry import ToolRegistry
from harness.tools.table_artifact import YieldTableInput, _ReportInput, register


def named_rows():
    return [
        {"period": name, "process": "pt1h", "param": "VTH", "value": value}
        for name, value in [("오늘(11/6)", 1.2), ("이번 주 나머지", 1.0)]
    ]


def test_arbitrary_names_render_by_default_without_period_metadata():
    args = _ReportInput(title="groups", rows=named_rows())
    html = _html(args)
    assert html.index("오늘(11/6)</td>") < html.index("이번 주 나머지</td>")
    assert '<tr class="delta">' not in html
    assert "th-section-pt1h" in html


def test_comparison_uses_inferred_group_names():
    html = _html(_ReportInput(title="groups", rows=named_rows(),
        comparison={"current": "오늘(11/6)", "baseline": "이번 주 나머지"}))
    assert "Δ 오늘(11/6) − 이번 주 나머지" in html
    assert ">+20.0</td>" in html


def test_week_shaped_group_names_are_not_normalized_or_sorted_by_default():
    rows = named_rows()
    rows[0]["period"], rows[1]["period"] = "2026-W99", "2026-01"
    html = _html(_ReportInput(title="groups", rows=rows))
    assert html.index("2026-W99</td>") < html.index("2026-01</td>")


def test_old_week_pass_in_contract_is_rejected():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        YieldTableInput(title="weekly", period_kind="week", rows=named_rows())


def test_old_wide_rows_are_still_rejected():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _ReportInput(title="groups", rows=[
            {"process": "pt1h", "param": "VTH", "today": 1.2, "rest": 1.0, "delta": .2},
        ])


def test_implicit_group_names_are_escaped():
    dangerous = '<img src=x onerror="alert(1)">'
    rows = named_rows()
    rows[0]["period"] = dangerous
    html = _html(_ReportInput(title="groups", rows=rows))
    assert "<img" not in html.lower()
    assert "&lt;img" in html


def test_explicit_metadata_still_controls_order_with_default_groups():
    rows = named_rows()
    html = _html(_ReportInput(title="groups", rows=rows, periods=[
        {"period": "이번 주 나머지", "label": "Other"},
        {"period": "오늘(11/6)", "label": "Selected"},
    ]))
    assert html.index("Other</td>") < html.index("Selected</td>")


def test_public_schema_uses_identifier_membership_and_rejects_pass_in_cells():
    registry = ToolRegistry()
    register(registry)
    schema = registry.model_tools()[0]["function"]["parameters"]
    fields = schema["properties"]
    assert {"lot_cd", "period", "report_scope_id", "grouping", "layout"} <= fields.keys()
    assert not {"rows", "periods", "product_label", "parameter_formats",
                "long_result_id", "counts_result_id", "gms_result_id",
                "long_columns", "counts_columns", "gms_columns"} & fields.keys()
    assert schema["additionalProperties"] is False

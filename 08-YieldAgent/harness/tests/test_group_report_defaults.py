from harness.tests.report_table_fakes import fake_report_metadata, group_html as _html
import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from harness.tools.registry import ToolRegistry
from harness.tools.table_artifact import YieldTableInput, register, render_yield_table


def named_rows():
    return [
        {"period": name, "process": "pt1h", "param": "VTH", "value": value}
        for name, value in [("오늘(11/6)", 1.2), ("이번 주 나머지", 1.0)]
    ]


def test_arbitrary_names_render_by_default_without_period_metadata():
    args = YieldTableInput(title="groups", rows=named_rows())
    html = _html(args)
    assert args.period_kind == "group"
    assert html.index("오늘(11/6)</td>") < html.index("이번 주 나머지</td>")
    assert '<tr class="delta">' not in html
    assert "th-section-pt1h" in html


def test_comparison_uses_inferred_group_names():
    html = _html(YieldTableInput(title="groups", rows=named_rows(),
        comparison={"current": "오늘(11/6)", "baseline": "이번 주 나머지"}))
    assert "Δ 오늘(11/6) − 이번 주 나머지" in html
    assert ">+20.0</td>" in html


def test_week_shaped_group_names_are_not_normalized_or_sorted_by_default():
    rows = named_rows()
    rows[0]["period"], rows[1]["period"] = "2026-W99", "2026-01"
    html = _html(YieldTableInput(title="groups", rows=rows))
    assert html.index("2026-W99</td>") < html.index("2026-01</td>")


def test_explicit_week_keeps_iso_validation():
    with pytest.raises(ValidationError, match="period must be"):
        YieldTableInput(title="weekly", period_kind="week", rows=named_rows())


def test_saved_results_infer_groups_order_counts_gms_and_lineage():
    saved = {
        "long": named_rows(),
        "counts": [{"period": name, "lot_count": 2, "wf_count": 6}
                   for name in ("이번 주 나머지", "오늘(11/6)")],
        "gms": [{"period": "오늘(11/6)", "fab": 97.1}],
    }

    class Store:
        async def read_result(self, principal, session, result_id, **kwargs):
            assert (principal, session) == ("owner", "session")
            return {"columns": list(saved[result_id][0]), "rows": saved[result_id], "complete": True,
                    "total_rows": len(saved[result_id])}

    ctx = SimpleNamespace(store=Store(), run={"principal_id": "owner", "session_id": "session"})
    result = asyncio.run(render_yield_table(YieldTableInput(
        title="groups", long_result_id="long", counts_result_id="counts",
        gms_result_id="gms", gms_columns={"period": "period", "fab": "fab"},
        comparison={"current": "오늘(11/6)", "baseline": "이번 주 나머지"},
    ), ctx))
    html = result.artifacts[0]["data"]
    assert result.scope["row_count"] == 2
    assert result.source_result_ids == ["long", "counts", "gms"]
    assert html.index("오늘(11/6)</td>") < html.index("이번 주 나머지</td>")
    assert "<td>2</td><td>6</td>" in html
    assert ">97.1</td>" in html
    assert "Δ 오늘(11/6) − 이번 주 나머지" in html


def test_model_schema_explains_group_default_optional_metadata_and_week_opt_in():
    registry = ToolRegistry()
    register(registry)
    schema = registry.model_tools()[0]["function"]["parameters"]["properties"]
    assert schema["period_kind"]["default"] == "group"
    assert "group name" in schema["rows"]["items"]["properties"]["period"]["description"]
    assert "first" in schema["periods"]["description"]
    assert "explicit" in schema["period_kind"]["description"]


def test_old_wide_rows_are_still_rejected():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        YieldTableInput(title="groups", rows=[
            {"process": "pt1h", "param": "VTH", "today": 1.2, "rest": 1.0, "delta": .2},
        ])


def test_implicit_group_names_are_escaped():
    dangerous = '<img src=x onerror="alert(1)">'
    rows = named_rows()
    rows[0]["period"] = dangerous
    html = _html(YieldTableInput(title="groups", rows=rows))
    assert "<img" not in html.lower()
    assert "&lt;img" in html


def test_counts_only_groups_support_comparison_without_metadata():
    class Store:
        async def read_result(self, *args, **kwargs):
            return {"rows": [
                {"period": "selected", "lot_count": 2, "wf_count": 6},
                {"period": "others", "lot_count": 3, "wf_count": 9},
            ]}

    ctx = SimpleNamespace(store=Store(), run={"principal_id": "owner", "session_id": "session"})
    result = asyncio.run(render_yield_table(YieldTableInput(
        title="groups", counts_result_id="counts",
        comparison={"current": "selected", "baseline": "others"},
    ), ctx))
    assert result.scope["row_count"] == 2
    assert "Δ selected − others" in result.artifacts[0]["data"]


def test_explicit_metadata_still_controls_order_with_default_groups():
    rows = named_rows()
    html = _html(YieldTableInput(title="groups", rows=rows, periods=[
        {"period": "이번 주 나머지", "label": "Other"},
        {"period": "오늘(11/6)", "label": "Selected"},
    ]))
    assert html.index("Other</td>") < html.index("Selected</td>")

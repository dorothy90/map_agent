from datetime import date

import pytest
from pydantic import ValidationError

from harness.tools import report_analysis, report_bin_maps, report_scatter, table_artifact
from harness.tools.load_wafer_maps import LoadWaferMapsInput
from harness.tests.test_period_report_data import period_db


RECIPE = dict(months=3, weeks=4, days=3, parameters_per_direction=3, bins_per_test=10)


def test_report_scope_cannot_mix_raw_limits_or_period_scope():
    for extra in ({"max_dies": 500}, {"period": {"kind": "recent", "weeks": 2}},
                  {"analysis": {**RECIPE, "max_dies": 500}}):
        with pytest.raises(ValidationError):
            table_artifact.YieldTableInput.model_validate({"analysis": RECIPE, "lot_cd": "P", **extra})
    assert "max_dies" not in LoadWaferMapsInput.model_json_schema()["properties"]
    with pytest.raises(ValidationError):
        LoadWaferMapsInput(aggregate={"group_by": "all"}, raw_page={"max_dies": 2})
    assert LoadWaferMapsInput(lot_ids=["L"], max_dies=24).max_dies == 24


def test_each_section_uses_same_product_tests_and_shared_calendar(monkeypatch, period_db):
    base = date(2025, 1, 1)
    tests = ["PT1H TEST", "PT1C TEST"]
    monkeypatch.setattr(report_analysis, "analysis_scope", lambda *a: (tests, base))
    periods, charts = [], []
    fetch_report = report_analysis.fetch_period_report

    def fetch(lot, period, **kw):
        periods.append((lot, period.model_dump(exclude_none=True), kw))
        return fetch_report(lot, period, **kw)

    def render_maps(lot, tests, start, split, end, top_n):
        charts.append((lot, tests, start, split, end, top_n))
        return [{"title": t} for t in tests], {}

    def render_scatter(*args, **kw):
        charts.append((*args, kw["top_n"]))
        return [{"title": "scatter"}], [], {}

    monkeypatch.setattr(report_analysis, "fetch_period_report", fetch)
    monkeypatch.setattr(report_scatter, "build_report_scatter", render_scatter)
    monkeypatch.setattr(report_bin_maps, "report_bin_maps", render_maps)
    result = report_analysis.build_report_analysis("P", report_analysis.ReportAnalysis(**RECIPE))
    assert all(lot == "P" and kw == {"base": base, "report_tests": tests} for lot, _, kw in periods)
    assert [p for _, p, _ in periods] == [dict(kind="recent", **{k: RECIPE[k]}) for k in ("months","weeks","days")]
    bounds = ("P", tests, date(2024,12,23), date(2024,12,30), date(2025,1,2))
    assert charts == [(*bounds, 3), (*bounds, 10)]
    assert result.status == "success" and len(result.artifacts) == 6
    assert result.scope["comparison"]["previous_week"] == "2024-W52"
    assert result.scope["comparison"]["current_week"] == "2025-W01"
    for grain in ("month", "week", "day"):
        summary = result.scope["report_summary"][grain]
        assert summary["period_totals"]["counts_process"] == ", ".join(tests)
        assert summary["period_totals"]["period_lot_count"] == 15
        assert {row["grain"] for row in summary["counts"]} == {grain}
        assert summary["displayed_rows"][-1]["row"] == "Δ"
        assert summary["displayed_rows"][-1]["values"]["PT1H TEST/VTH"] == "+1.0"


def test_missing_product_returns_database_choices_to_the_model(monkeypatch):
    import asyncio
    from harness.tools import yield_report_data
    from harness.tools.registry import domain_registry

    options = ["Product X", "Product Y"]
    monkeypatch.setattr(yield_report_data, "fetch_product_codes", lambda: options)
    arguments = {"analysis": RECIPE}
    result = asyncio.run(domain_registry().invoke("render_yield_table", arguments, None))
    assert result.scope["code"] == "REPORT_PRODUCT_REQUIRED"
    assert result.scope["details"]["choices"] == options
    assert arguments == {"analysis": RECIPE}

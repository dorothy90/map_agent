from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from harness.tools import report_analysis, report_bin_maps, report_scatter, table_artifact
from harness.tools.load_wafer_maps import LoadWaferMapsInput
from harness.tools.registry import ToolResult


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


def test_each_section_uses_same_product_tests_and_shared_calendar(monkeypatch):
    base = date(2025, 1, 1)
    tests = ["PT1H TEST", "PT1C TEST"]
    monkeypatch.setattr(report_analysis, "analysis_scope", lambda *a: (tests, base))
    periods, charts = [], []

    def fetch(lot, period, **kw):
        periods.append((lot, period.model_dump(exclude_none=True), kw))
        return SimpleNamespace(window={"end": base.isoformat()})

    def render_maps(lot, tests, start, split, end, top_n):
        charts.append((lot, tests, start, split, end, top_n))
        return [{"title": t} for t in tests], {}

    def render_scatter(*args, **kw):
        charts.append((*args, kw["top_n"]))
        return [{"title": "scatter"}], [], {}

    monkeypatch.setattr(report_analysis, "fetch_period_report", fetch)
    monkeypatch.setattr(table_artifact, "_render_period_report", lambda a, d, **kw:
                        ToolResult(status="success", artifacts=[{"title": a.title}]))
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


def test_missing_product_uses_catalog_selection_before_execution(monkeypatch):
    import asyncio
    from harness.tools import yield_report_data
    options = ["Product X", "Product Y"]
    monkeypatch.setattr(yield_report_data, "fetch_product_codes", lambda: options)
    received = []

    async def choose(questions):
        received.extend(questions)
        return {"outcome": "submitted", "answers": {"lot_cd": options[1]}}

    raw = {"analysis": RECIPE}
    effective = asyncio.run(table_artifact.select_analysis_product(raw, SimpleNamespace(request_choice=choose)))
    assert raw == {"analysis": RECIPE} and effective == {**raw, "lot_cd": options[1]}
    assert [q["choices"] for q in received] == [options]

    async def cancelled(questions):
        return {"outcome": "cancelled", "answers": {}}

    with pytest.raises(ValueError, match="제품 선택"):
        asyncio.run(table_artifact.select_analysis_product(raw, SimpleNamespace(request_choice=cancelled)))

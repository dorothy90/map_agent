import json

import pytest

from harness.tests.test_declarative_yield_report import inline, population_data, setup_report, table_rows
from harness.tests.test_period_report_data import period_db, run


@pytest.mark.parametrize("reverse", [True, False])
@pytest.mark.parametrize("layout", ["group", "group_period"])
def test_summary_uses_rendered_group_labels_counts_and_signed_delta(setup_report, reverse, layout):
    call, _, _, _ = setup_report
    grouping = inline(("A", ["L0", "L1", "L2"]), ("B", ["L20", "L21", "L22", "L23", "L24"]))
    grouping["groups"] = [{"id": "A", "label": "Low"}, {"id": "B", "label": "High"}]
    comparison = {"current": "A" if reverse else "B", "baseline": "B" if reverse else "A"}
    result = call(lot_cd="4SS", period={"kind": "recent", "weeks": 4}, layout=layout,
                  grouping=grouping, comparison=comparison)
    summary = result.scope["report_summary"]
    assert not summary["truncated"]
    assert summary["counts"] == table_rows(result, "group_counts")
    assert summary["comparison"] == comparison
    assert summary["groups"] == grouping["groups"]
    cells = {(r["grain"], r["bucket"], r["row"], r["column"]): r["shown"]
             for r in table_rows(result, "displayed_yield_table")}
    assert {(r["grain"], r["bucket"], r["row"], col): val
            for r in summary["displayed_rows"] for col, val in r["values"].items()} == cells
    if layout == "group":
        delta = summary["displayed_rows"][-1]
        assert delta["row"] == "Δ"
        assert delta["values"]["PT1H TEST/PT1H"].startswith("-" if reverse else "+")
    assert "report_summary" in result.summary


def test_large_period_summary_keeps_totals_and_latest_displayed_delta(period_db):
    result = run({"kind": "recent", "days": 92})
    summary = result.scope["report_summary"]
    assert summary["truncated"]
    assert summary["period_totals"]["period_lot_count"] == 15
    assert summary["period_totals"]["period_wafer_count"] == 150
    assert summary["comparisons"]["day"]["current"] == "2026-11-30"
    assert summary["displayed_rows"][-1]["values"]["PT1H TEST/VTH"] == "+1.0"
    assert len(json.dumps(summary, ensure_ascii=False).encode()) < 16000
    assert len(table_rows(result, "daily_counts")) == 92

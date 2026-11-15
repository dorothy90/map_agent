"""Sticky metadata columns and quieter group headers in the yield report HTML."""
import re

from yield_table_renderer import (
    REPORT_STYLE,
    ReportCell,
    ReportColumn,
    ReportGroup,
    ReportRow,
    render_report_html,
)


def _rendered_report():
    rows = [ReportRow(
        metadata=[ReportCell("W01", "td-week"), ReportCell("L1"), ReportCell("5")],
        values=[ReportCell("99.1"), ReportCell("98.0"), ReportCell("97.5")],
    )]
    groups = [
        ReportGroup("pt1h", "th-section-pt1h", [ReportColumn("VTH", "th-blue")]),
        ReportGroup("pt1c", "th-section-pt1c", [ReportColumn("PT1C", "th-blue-pt1c")]),
        ReportGroup("gms", "th-section-gms", [ReportColumn("GMS", "th-gms")]),
    ]
    delta = [
        ReportCell("Δ", "delta-week"),
        ReportCell(""),
        ReportCell(""),
        ReportCell("-0.2", "delta-neg"),
        ReportCell("+0.1", "delta-pos"),
        ReportCell("0.0"),
    ]
    return render_report_html(
        rows, groups, metadata_columns=["LOT", "WF"], period_label="WEEK", delta_row=delta,
    )


def _rule(css: str, selector: str) -> str:
    pattern = re.compile(re.escape(selector) + r"\s*\{([^}]+)\}", re.S)
    match = pattern.search(css)
    assert match, f"missing rule for {selector!r}"
    return match.group(1)


def test_report_html_freezes_week_lot_wf_on_horizontal_scroll():
    html = _rendered_report()
    assert 'class="th-week"' in html
    assert 'class="th-meta th-lot"' in html
    assert 'class="th-meta th-wf"' in html
    assert "td-week" in html and "td-lot" in html and "td-wf" in html
    week, lot, wf = (_rule(REPORT_STYLE, sel) for sel in (
        "th.th-week, td.td-week", "th.th-lot, td.td-lot", "th.th-wf, td.td-wf"))
    assert "position: sticky" in week and "left: 0" in week
    assert "width: max-content" in week and "min-width: max-content" in week
    assert "white-space: nowrap" in week
    assert "position: sticky" in lot and "left: var(--sticky-week-width)" in lot
    assert "width: max-content" in lot
    assert "position: sticky" in wf
    assert "left: calc(var(--sticky-week-width) + var(--sticky-lot-width))" in wf
    assert "width: max-content" in wf
    assert "box-shadow" in wf
    assert "70px" not in week + lot + wf and "106px" not in week + lot + wf
    assert "--sticky-week-width" in REPORT_STYLE
    assert "syncStickyOffsets" in html


def test_report_headers_keep_group_identity_with_neutral_tone():
    style = REPORT_STYLE
    pt1h, pt1c, gms = (_rule(style, f"th.th-section-{name}") for name in ("pt1h", "pt1c", "gms"))
    assert pt1h != pt1c != gms
    for old in ("#e6f7ff", "#f9f0ff", "#f6ffed", "#1890ff", "#722ed1", "#52c41a",
                "#0050b3", "#391085", "#237804"):
        assert old not in pt1h + pt1c + gms
    assert "#cf1322" in _rule(style, "td.delta-neg")
    assert "#389e0d" in _rule(style, "td.delta-pos")
    assert "#cf1322" in _rule(style, ".badge-d")
    assert "#389e0d" in _rule(style, ".badge-i")
    html = _rendered_report()
    assert "delta-week" in html and "td-week" in html
    assert "delta-neg" in html and "delta-pos" in html


def test_report_scrolls_inside_container_not_document():
    root = _rule(REPORT_STYLE, "html, body")
    assert "width: 100%" in root
    assert "overflow-x: hidden" in root
    container = _rule(REPORT_STYLE, ".table-container")
    assert "width: 100%" in container
    assert "width: max-content" not in container
    assert "overflow-x: auto" in container
    assert "width: max-content" in _rule(REPORT_STYLE, "table")
    html = _rendered_report()
    assert "document.body.getBoundingClientRect().height" in html
    assert "ResizeObserver" in html


def test_period_column_sizes_to_daily_and_weekly_labels():
    for label, value in (("WEEK", "2026-49"), ("MONTH", "2026-11"), ("DAY", "2026-11-15")):
        rows = [ReportRow(
            metadata=[ReportCell(value, "td-week"), ReportCell("L1"), ReportCell("5")],
            values=[ReportCell("99.1")],
        )]
        groups = [ReportGroup("pt1h", "th-section-pt1h", [ReportColumn("VTH", "th-blue")])]
        html = render_report_html(
            rows, groups, metadata_columns=["LOT", "WF"], period_label=label, delta_row=None,
        )
        assert f'>{label}</th>' in html
        assert f'>{value}</td>' in html
        week = _rule(REPORT_STYLE, "th.th-week, td.td-week")
        assert "max-content" in week and "white-space: nowrap" in week
        assert "width: 70px" not in week


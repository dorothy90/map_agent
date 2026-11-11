from harness.tests.report_table_fakes import fake_report_metadata, report_data, report_rows, report_html, COLUMNS
import asyncio
import hashlib
import importlib
import re
from html.parser import HTMLParser

import pytest
from pydantic import ValidationError

from yield_viz import GMS_COLUMNS, PARA_COLUMNS, PT1C_COLUMNS, _build_html_table


LEGACY_SHA256 = "566736a31943c3116f91d680c318534ad4f57b8e5893d8635c7eac5742d1225a"


def _legacy_rows():
    return [
        {
            "week": "2026-W43",
            "lotcount": 7,
            "wfCount": 14,
            "VTH": 1.234,
            "IDSAT": 2.345,
            "pt1c_VTH": 3.456,
            "gms_GM": 4.567,
        },
        {
            "week": "2026-W44",
            "lotcount": 8,
            "wfCount": 16,
            "VTH": 1.244,
            "IDSAT": None,
            "pt1c_VTH": 3.466,
            "gms_GM": 4.577,
        },
    ]


def _legacy_html(*, anomalies=True):
    anomaly_params = None
    if anomalies:
        anomaly_params = [
            {
                "key": "VTH",
                "direction": "개선",
                "prev_val": 1.23,
                "curr_val": 1.24,
                "change_pct": 0.8,
            },
            {
                "key": "pt1c_VTH",
                "direction": "열화",
                "prev_val": 3.46,
                "curr_val": 3.47,
                "change_pct": 0.3,
            },
        ]
    return _build_html_table(
        _legacy_rows(),
        "4SA",
        filter_params=["VTH", "IDSAT"],
        anomaly_params=anomaly_params,
    )


class _StructureParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.classes = []

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        if "class" in attr_map:
            self.classes.append((tag, attr_map["class"]))


def _style(html):
    return re.search(r"<style>(.*?)</style>", html, re.S).group(1)


def _classes(html):
    parser = _StructureParser()
    parser.feed(html)
    return parser.classes


def test_legacy_builder_output_is_frozen_before_and_after_extraction():
    assert hashlib.sha256(_legacy_html().encode()).hexdigest() == LEGACY_SHA256


def test_report_builder_uses_original_units_one_decimal_and_integer_counts():
    html = _build_html_table(
        [{
            "week": "2026-W44",
            "lotcount": 7.0,
            "wfCount": 14.0,
            "VTH": 0.0106,
            "pt1c_CFTA": 0.7452,
            "gms_cum0": 86.51,
        }],
        "4SA",
        filter_params=["VTH"],
    )

    assert '<td>7</td><td>14</td>' in html
    assert '>0.0</td>' in html
    assert '>0.7</td>' in html
    assert '>86.5</td>' in html
    assert "%</td>" not in html


def _long_report_args(module):
    return module.YieldTableInput(
        period_kind="week",
        title="4SA weekly report",
        product_label="4SA",
        rows=[
            {"period": "2026-43", "process": "PT1H TEST", "param": "VTH", "value": 0.02},
            {"period": "202644", "process": "pt1h", "param": "VTH", "value": 0.01},
            {"period": "2026-W44", "process": "PT1C TEST", "param": "PT1C", "value": 0.91},
            {"period": "2026-W44", "process": "pt1c", "param": "CFTA", "value": 1.25},
            {"period": "2026-W44", "process": "pt1h", "param": "NOT_FIXED", "value": 999},
        ],
        periods=[
            {"period": "2026-W42"},
            {
                "period": "202644",
                "lot_count": 7,
                "wf_count": 14,
                "gms": {"fab": 98.1, "prb": None, "cum0": 97.2, "cum2": 98.3, "pnt": 99.1},
            },
        ],
    )


def test_report_pivots_long_rows_in_metadata_order():
    module = importlib.import_module("harness.tools.table_artifact")

    rows = report_rows(_long_report_args(module))

    assert [row["week"] for row in rows] == ["2026-W42", "2026-W43", "2026-W44"]
    assert rows[0]["lotcount"] == rows[0]["wfCount"] == "-"
    assert rows[1]["VTH"] == 0.02
    assert rows[2]["VTH"] == 0.01
    assert rows[2]["pt1c_PT1C"] == 0.91
    assert rows[2]["pt1c_CFTA"] == 1.25
    assert rows[2]["gms_cum0"] == 97.2
    assert rows[2]["gms_prb"] == "-"
    assert "NOT_FIXED" not in rows[2]
    assert list(rows[2]) == [
        "week", "lotcount", "wfCount", *COLUMNS["pt1h"],
        *(f"pt1c_{param}" for param in COLUMNS["pt1c"]),
        *(f"gms_{param}" for param in GMS_COLUMNS),
    ]

    html = report_html(_long_report_args(module))
    assert ">1.0</td>" in html
    assert "%</td>" not in html
    assert "NOT_FIXED" not in html
    assert '<tr class="delta">' in html


def test_report_matches_builder_with_metadata_columns_and_formats():
    module = importlib.import_module("harness.tools.table_artifact")
    legacy_rows = [
        {
            "week": "2026-W43", "lotcount": 7, "wfCount": 14,
            "VTH": 0.01, "IDSAT": 0.02,
            "pt1c_PT1C": 0.93, "pt1c_CFTA": 1.2,
            "gms_cum0": 97.1, "gms_cum2": 97.2, "gms_fab": 98.1,
            "gms_prb": None, "gms_pnt": 99.1,
        },
        {
            "week": "2026-W44", "lotcount": 8, "wfCount": 16,
            "VTH": 0.02, "IDSAT": None,
            "pt1c_PT1C": 0.94, "pt1c_CFTA": 1.3,
            "gms_cum0": 97.3, "gms_cum2": 97.4, "gms_fab": 98.2,
            "gms_prb": 98.4, "gms_pnt": 99.2,
        },
    ]
    long_rows = []
    periods = []
    for row in legacy_rows:
        periods.append({
            "period": row["week"],
            "lot_count": row["lotcount"],
            "wf_count": row["wfCount"],
            "gms": {param: row.get(f"gms_{param}") for param in GMS_COLUMNS},
        })
        for param in PARA_COLUMNS:
            if param in row:
                long_rows.append({
                    "period": row["week"], "process": "PT1H TEST",
                    "param": param, "value": row[param],
                })
        for param in PT1C_COLUMNS:
            key = f"pt1c_{param}"
            if key in row:
                long_rows.append({
                    "period": row["week"], "process": "PT1C TEST",
                    "param": param, "value": row[key],
                })

    args = module.YieldTableInput(
        period_kind="week",
        title="report", product_label="4SA",
        rows=long_rows, periods=periods,
    )

    assert report_html(args).encode() == _build_html_table(
        legacy_rows, "4SA", unit="weekly", parameter_columns=COLUMNS,
        parameter_formats={**{k: ("scaled", 1) for k in report_data(args).parameter_keys},
                           **{f"gms_{p}": ("number", 1) for p in GMS_COLUMNS}}, comparison=(0, 1),
    ).encode()


@pytest.mark.parametrize("field", ["rows", "periods"])
def test_report_rejects_duplicate_normalized_keys(field):
    module = importlib.import_module("harness.tools.table_artifact")
    payload = {
        "title": "duplicates",
        "rows": [
            {"period": "2026-44", "process": "pt1h", "param": "VTH", "value": 0.1},
        ],
        "periods": [{"period": "2026-W44"}],
    }
    payload[field] = payload[field] * 2

    with pytest.raises(ValidationError, match="duplicate"):
        module.YieldTableInput(period_kind="week", **payload)


@pytest.mark.parametrize(
    "extra",
    [
        {"columns": ["period"]},
        {"delta_row": False},
        {"column_formats": {"value": "ratio"}},
        {"column_groups": [{"label": "x", "columns": ["value"]}]},
    ],
)
def test_report_rejects_custom_presentation(extra):
    module = importlib.import_module("harness.tools.table_artifact")
    payload = {"title": "report", "rows": [], **extra}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        module.YieldTableInput(period_kind="week", **payload)


def test_report_escapes_by_rejecting_unstructured_values():
    module = importlib.import_module("harness.tools.table_artifact")

    with pytest.raises(ValidationError):
        module.YieldTableInput(
            period_kind="week",
            title="report",
            rows=[{
                "period": "2026-W44", "process": "pt1h", "param": "VTH",
                "value": "<script>alert(1)</script>",
            }],
        )

    args = module.YieldTableInput(
        period_kind="week",
        title="<script>title</script>",
        product_label="<img src=x onerror=alert(1)>", rows=[],
    )
    assert "<script>title</script>" not in report_html(args)
    assert "<img src=x onerror=alert(1)>" not in report_html(args)


def test_registered_report_counts_rendered_periods_and_columns():
    module = importlib.import_module("harness.tools.table_artifact")
    from harness.tools.registry import domain_registry

    args = _long_report_args(module)
    result = asyncio.run(domain_registry().invoke(
        "render_yield_table", args.model_dump(mode="json", exclude_unset=True), None,
    ))
    rendered = report_data(args)

    assert "TRIM(YIELD_TABLE)='o' shown" in result.summary
    assert result.scope["row_count"] == 3
    assert result.scope["column_count"] == len(rendered.column_keys)
    assert result.scope["parameter_count"] == len(rendered.parameter_keys)
    assert result.artifacts[0]["data"] == report_html(args)


def test_report_tool_reuses_legacy_css_and_report_structure():
    module = importlib.import_module("harness.tools.table_artifact")
    html = report_html(_long_report_args(module))
    assert _style(html) == _style(_legacy_html())
    classes = _classes(html)
    for tag, css_class in (
        ("th", "th-section th-section-pt1h"),
        ("th", "th-section th-section-pt1c"),
        ("th", "th-section th-section-gms"),
        ("tr", "delta"),
        ("td", "delta-week"),
    ):
        assert (tag, css_class) in classes
    assert '<th class="th-meta" rowspan="2">LOT</th>' in html
    assert '<th class="th-meta" rowspan="2">WF</th>' in html
    assert ("td", "td-pt1c-sep") in classes
    assert ("td", "td-gms-sep") in classes

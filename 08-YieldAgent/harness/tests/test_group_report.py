from harness.tests.report_table_fakes import fake_report_metadata, group_html as _html

import pytest
from pydantic import ValidationError

from harness.tools.table_artifact import YieldTableInput, _ReportInput


def group_args(**extra):
    payload = dict(
        title="group report",
        periods=[{"period": "today", "label": "선택 자재"}, {"period": "rest", "label": "나머지"}],
        rows=[
            {"period": group, "process": "PT1H TEST", "param": param, "value": value}
            for group, param, value in [
                ("today", "PT1H", .750883), ("rest", "PT1H", .741138),
                ("today", "VTH", 1.23456), ("rest", "VTH", 1.23001),
            ]
        ],
        comparison={"current": "today", "baseline": "rest"},
    )
    payload.update(extra)
    return _ReportInput(**payload)


def test_group_report_preserves_order_and_scaled_delta():
    html = _html(group_args())
    assert html.index("선택 자재</td>") < html.index("나머지</td>")
    assert ">75.1</td>" in html
    assert ">+1.0</td>" in html
    assert ">+0.5</td>" in html
    assert "Δ 선택 자재 − 나머지" in html
    assert "th-section-pt1h" in html


def test_group_report_without_comparison_has_no_footer():
    assert '<tr class="delta">' not in _html(group_args(comparison=None))


@pytest.mark.parametrize("change, message", [
    ({"periods": [{"period": "today"}, {"period": "today"}]}, "duplicate"),
    ({"periods": [{"period": "today", "label": "same"}, {"period": "rest", "label": " same "}]}, "labels"),
    ({"comparison": {"current": "today", "baseline": "today"}}, "must differ"),
    ({"comparison": {"current": "missing", "baseline": "today"}}, "unknown"),
    ({"rows": [{"period": "other", "process": "pt1h", "param": "PT1H", "value": .5}]}, "undeclared"),
    ({"rows": [{"period": "today", "process": "pt1h", "param": "PT1H", "value": float('nan')}]}, "finite"),
])
def test_invalid_group_contract(change, message):
    with pytest.raises(ValidationError, match=message):
        group_args(**change)


def test_group_report_escapes_labels_and_parameter_headers():
    dangerous = '<img src=x onerror="alert(1)">'
    args = group_args(
        period_label=dangerous,
        periods=[{"period": "today", "label": dangerous}, {"period": "rest", "label": "rest"}],
        rows=[{"period": "today", "process": "pt1h", "param": dangerous, "value": 1}],
    )
    html = _html(args)
    assert '<img' not in html.lower()
    assert '&lt;img' in html


def test_parameter_axis_rows_are_rejected():
    with pytest.raises(ValidationError):
        YieldTableInput(title="parameters", rows=[
            {"process": "pt1h", "param": "VTH", "a": 1, "b": 2},
            {"process": "pt1h", "param": "IDSAT", "a": 2, "b": 3},
        ])


def test_group_labels_are_escaped_without_format_options():
    args = _ReportInput(title="weekly", periods=[
        {"period": "2026-W45", "label": '<img src=x onerror="alert(1)">'},
    ])
    assert "<img" not in _html(args)
    assert "&lt;img" in _html(args)


def test_group_report_rejects_custom_parameter_columns():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        group_args(columns=[
            {"key": "param", "role": "identifier"},
            {"key": "count", "role": "count"},
            {"key": "value", "role": "parameter", "group": "pt1h"},
        ])


@pytest.mark.parametrize("process, other", [("pt1h", "pt1c"), ("pt1c", "pt1h")])
def test_single_process_does_not_emit_empty_section(process, other):
    args = group_args(rows=[{"period": "today", "process": process, "param": "CUSTOM", "value": 2}],
                      )
    html = _html(args)
    assert 'colspan="0"' not in html
    assert f'th-section-{process}' in html
    assert f'class="th-section th-section-{other}"' not in html


def test_comparison_selects_named_groups_not_last_two_rows():
    args = group_args(periods=[{"period": "third"}, {"period": "today"}, {"period": "rest"}],
                      rows=[{"period": p, "process": "pt1h", "param": "PT1H", "value": v}
                            for p, v in [("third", .99), ("today", .75), ("rest", .74)]],
                      comparison={"current": "third", "baseline": "rest"},
                      )
    assert ">+25.0</td>" in _html(args)


def test_group_parameters_use_report_scaling():
    args = _ReportInput(title="groups", comparison={"current": "2026-W45", "baseline": "2026-W44"}, rows=[
        {"period": "2026-W44", "process": "pt1h", "param": "VTH", "value": 1.234},
        {"period": "2026-W45", "process": "pt1h", "param": "VTH", "value": 1.236},
    ])
    assert ">+0.2</td>" in _html(args)

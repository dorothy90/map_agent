"""Offline metadata and explicit render inputs for report contract tests."""
import pytest
from harness.tools import table_artifact as report

COLUMNS = {'pt1h': ['VTH', 'IDSAT'], 'pt1c': ['PT1C', 'CFTA']}


@pytest.fixture(autouse=True)
def fake_report_metadata(monkeypatch):
    monkeypatch.setattr(report, '_fetch_parameter_metadata', lambda lot: [
        (process.upper(), param, seq, 'P', 'o')
        for process, params in COLUMNS.items() for seq, param in enumerate(params)
    ] + [('PT1C', 'EXCLUDED', 99, 'P', 'x ')])


def report_data(args):
    return report._report_render_data(args, COLUMNS)


def report_rows(args):
    return report._pivot_report_rows(args, COLUMNS)


def report_html(args):
    return report._html(args, report_data(args))


def group_html(args):
    # These tests supply metadata explicitly, including custom/escaped names.
    columns = {process: list(dict.fromkeys(row.param for row in args.rows
               if report._normalize_process(row.process) == process))
               for process in ('pt1h', 'pt1c')}
    return report._html(args, report._report_render_data(args, columns))

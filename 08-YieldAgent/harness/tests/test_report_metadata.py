import importlib

import pytest
from pydantic import ValidationError

from harness.tools import table_artifact as report
from harness.tests.test_report_only_contract import fake_period_report


def test_metadata_columns_values_and_evidence(monkeypatch):
    def metadata(lot):
        assert lot == '4SS'
        return [('PT1H', 'VTH(V)', 1, 'P', 'o'), ('PT1H', 'DDA(d)', 2, 'P', 'o'), ('PT1H', 'Missing', 3, 'P', 'o'), ('PT1C', 'PT1C', 26, 'P', 'o')]
    monkeypatch.setattr(report, '_fetch_parameter_metadata', metadata, raising=False)
    periods = ['selected', 'rest']
    args = report._ReportInput(title='report', product_label=' 4SS ',
        rows=[{'period': p, 'process': 'PT1H TEST', 'param': param, 'value': value}
              for p, param, value in [(periods[0], 'VTH(V)', .7469), (periods[1], 'VTH(V)', .7569),
                                      (periods[0], 'DDA(d)', .0107), (periods[0], 'DDA(D)', .99),
                                      (periods[0], 'EXCLUDED', .99)]],
        periods=[{'period': p, 'lot_count': 2, 'wf_count': 3, 'gms': {'cum0': 86.51}} for p in periods],
        comparison={'current': periods[1], 'baseline': periods[0]})
    result = report._render_report(args, '4SS', metadata('4SS'), [])
    assert result.status is None
    assert result.scope['parameter_count'] == 4
    assert result.scope['displayed_columns'] == ['lot', 'wf', 'PT1H TEST/VTH(V)', 'PT1H TEST/DDA(d)',
        'PT1H TEST/Missing', 'PT1C TEST/PT1C', 'GMS/cum0', 'GMS/cum2', 'GMS/fab', 'GMS/prb', 'GMS/pnt']
    assert "DF_ALL_PARA_PRB LOT_CD=4SS" in result.summary[:240]
    assert "TRIM(YIELD_TABLE)='o' shown" in result.summary[:240]
    assert "'x' excluded" in result.summary[:240]
    assert "parameters ×100, GMS raw" in result.summary[:240]
    assert "Δ = current − baseline" in result.summary[:240]
    table = result.tables[0]
    assert table.table_id == 'displayed_yield_table' and table.complete
    cells = {(r['row'], r['column']): r for r in table.rows}
    assert cells[periods[0], 'PT1H TEST/VTH(V)']['shown'] == '74.7'
    assert cells[periods[0], 'PT1H TEST/DDA(d)']['shown'] == '1.1'
    assert cells['Δ', 'PT1H TEST/VTH(V)']['shown'] == '+1.0'
    assert cells[periods[0], 'GMS/cum0']['shown'] == '86.5'
    assert cells[periods[0], 'lot']['shown'] == '2'
    assert cells[periods[1], 'PT1H TEST/DDA(d)']['raw'] is None
    assert cells[periods[1], 'PT1H TEST/DDA(d)']['shown'] == '-'
    assert cells[periods[0], 'PT1H TEST/VTH(V)']['raw'] == .7469
    assert cells['Δ', 'PT1H TEST/VTH(V)']['raw'] == pytest.approx(.01)
    html = result.artifacts[0]['data']
    from html.parser import HTMLParser
    class Cells(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows, self.current, self.in_cell = [], [], False
        def handle_starttag(self, tag, attrs):
            if tag == 'tr': self.current = []
            if tag == 'td':
                self.in_cell = True
                self.current.append('')
        def handle_data(self, data):
            if self.in_cell: self.current[-1] += data
        def handle_endtag(self, tag):
            if tag == 'td': self.in_cell = False
            if tag == 'tr' and self.current: self.rows.append(self.current)
    parser = Cells()
    parser.feed(html)
    for label, rendered in zip([*periods, 'Δ'], parser.rows):
        assert rendered[1:] == [cells[label, c]['shown'] for c in result.scope['displayed_columns']]

    assert 'EXCLUDED' not in html and 'DDA(D)' not in html
    assert '%</td>' not in html and '%p</td>' not in html
    for value in ['74.7', '1.1', '+1.0', '86.5']:
        assert f'>{value}</td>' in html
    assert 'Missing</div>' in html


@pytest.mark.parametrize('metadata', [[], [('PT1H', 'excluded', 1, 'P', 'x')]])
def test_empty_metadata_fails_closed(monkeypatch, metadata):
    monkeypatch.setattr(report, '_fetch_parameter_metadata', lambda lot: metadata, raising=False)
    result = report._render_report(report._ReportInput(title='report'), '4SS', metadata, [])
    assert result.status == 'error'
    assert "no YIELD_TABLE='o' parameters" in result.summary
    assert not result.artifacts and not result.tables


@pytest.mark.parametrize('fail', [False, True])
def test_metadata_query_is_bound_ordered_and_closed(monkeypatch, fail):
    query = importlib.import_module('harness.tools.query_db')
    class Cursor:
        closed = False
        def execute(self, sql, binds):
            assert binds == {'lot': "4SS'"}
            assert "4SS'" not in sql
            assert "TRIM(YIELD_TABLE)=" not in sql
            assert "PARAM_KIND_CD, YIELD_TABLE" in sql
            assert "CASE PROC_GBN_CD WHEN 'PT1H' THEN 0" in sql
            assert 'PARAM_SEQNO' in sql and "'PT1C'" in sql
            if fail:
                raise RuntimeError('query failed')
        def fetchall(self):
            return [('PT1H', 'VTH(V)', 1), ('PT1C', 'DDA(d)', 27)]
        def close(self): self.closed = True
    class Connection:
        closed = False
        def cursor(self): return cursor
        def close(self): self.closed = True
    cursor, connection = Cursor(), Connection()
    monkeypatch.setattr(query, '_connection', lambda: connection)
    if fail:
        with pytest.raises(RuntimeError): report._fetch_parameter_metadata("4SS'")
    else:
        assert report._fetch_parameter_metadata("4SS'") == cursor.fetchall()
    assert connection.call_timeout == 60_000
    assert connection.closed and cursor.closed


@pytest.mark.parametrize('lot, expected', [
    ('4SS', ['PT1H TEST/VTH(V)', 'PT1H TEST/DDA(d)', 'PT1C TEST/PT1C']),
    ('4SA', ['PT1H TEST/VMIN', 'PT1H TEST/RD']),
])
def test_product_metadata_order_and_x_exclusion_with_fake_connection(monkeypatch, lot, expected):
    query = importlib.import_module('harness.tools.query_db')
    metadata = [
        ('4SS', 'PT1C', 'o', 'PT1C', 26),
        ('4SS', 'PT1H', 'o ', 'DDA(d)', 8),
        ('4SS', 'PT1H', 'x ', 'EXCLUDED', 2),
        ('4SS', 'PT1H', 'o', 'VTH(V)', 1),
        ('4SA', 'PT1H', 'o', 'RD', 4),
        ('4SA', 'PT1H', 'x', 'IOFF', 2),
        ('4SA', 'PT1H', 'o', 'VMIN', 1),
    ]
    class Cursor:
        def execute(self, sql, binds):
            assert binds == {'lot': lot}
            assert "WHERE LOT_CD=:lot " in sql
            assert "TRIM(YIELD_TABLE)=" not in sql
            assert "ORDER BY CASE PROC_GBN_CD WHEN 'PT1H' THEN 0 WHEN 'PT1C' THEN 1 ELSE 2 END, PARAM_SEQNO" in sql
            self.rows = sorted([(proc, param, seq, 'P', flag) for product, proc, flag, param, seq in metadata
                                if product == binds['lot']],
                               key=lambda row: (row[0] != 'PT1H', row[2]))
        def fetchall(self): return self.rows
        def close(self): pass
    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass
    monkeypatch.setattr(query, '_connection', Connection)
    args = report._ReportInput(title='report', product_label='Display label', rows=[
        {'period': 'selected', 'process': proc, 'param': param, 'value': .5}
        for product, proc, flag, param, seq in metadata if product == lot
    ])
    result = report._render_report(args, lot, report._fetch_parameter_metadata(lot), [])
    assert result.scope['displayed_columns'][2:-5] == expected
    assert result.scope['parameter_count'] == len(expected)
    assert 'EXCLUDED' not in result.artifacts[0]['data']
    assert 'IOFF' not in result.artifacts[0]['data']

    metadata_table = result.tables[1]
    assert metadata_table.table_id == 'column_metadata'
    assert metadata_table.columns == ['PROC_GBN_CD', 'PARAM_NM', 'PARAM_SEQNO', 'PARAM_KIND_CD', 'YIELD_TABLE', 'displayed']
    assert len(metadata_table.rows) == len(expected) + 1
    assert sum(row['displayed'] for row in metadata_table.rows) == len(expected)
    assert all(row['displayed'] == (row['YIELD_TABLE'].strip() == 'o') for row in metadata_table.rows)
    assert result.scope['selection_counts'] == {'o': len(expected), 'x': 1}


def test_title_only_public_report_fails_closed_before_data_access(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid public scope must not access metadata or DB")

    query = importlib.import_module('harness.tools.query_db')
    monkeypatch.setattr(query, '_connection', forbidden)
    monkeypatch.setattr(report, 'fetch_period_report', forbidden)
    monkeypatch.setattr(report, '_fetch_parameter_metadata', forbidden)
    with pytest.raises(ValidationError, match='lot_cd.*period.*report_scope_id'):
        report.YieldTableInput(title='report')


def test_public_period_data_fetch_runs_off_event_loop(fake_period_report, monkeypatch):
    import asyncio
    import threading

    main_thread = threading.get_ident()
    fake_fetch = report.fetch_period_report

    def fetch(lot_cd, period):
        assert threading.get_ident() != main_thread
        return fake_fetch(lot_cd, period)

    monkeypatch.setattr(report, 'fetch_period_report', fetch)
    result = asyncio.run(report.render_yield_table(report.YieldTableInput(
        lot_cd='4SS', period={'kind': 'weeks', 'weeks': ['2026-45']},
    ), None))
    assert result.status == 'success'

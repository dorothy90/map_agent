"""Weekly report contract, using DB-shaped aggregates without DB/network access."""
import asyncio
import importlib

import pytest
from pydantic import ValidationError

from harness.tools.table_artifact import YieldTableInput, render_yield_table


WEEKS = ['2026-43', '2026-44', '2026-45', '2026-46']
PERIOD = {'kind': 'weeks', 'weeks': WEEKS}


def test_period_resolution():
    from harness.tools.yield_report_data import resolve_weeks
    recent = YieldTableInput(title='weekly', lot_cd='4SS', period={
        'kind': 'recent_weeks', 'base_date': '2027-01-06', 'weeks': 3})
    assert resolve_weeks(recent.period) == ['2026-52', '2026-53', '2027-01']
    explicit = YieldTableInput(title='weekly', lot_cd='4SS', period={
        'kind': 'weeks', 'weeks': ['2027-W01', '202653', '2026-52']})
    assert resolve_weeks(explicit.period) == ['2026-52', '2026-53', '2027-01']
    assert explicit.period.model_dump() == {'kind': 'weeks', 'weeks': ['2026-52', '2026-53', '2027-01']}


@pytest.mark.parametrize('period', [
    {}, {'kind': 'weeks'}, {'kind': 'recent_weeks', 'weeks': 3},
    {'kind': 'weeks', 'base_date': '2026-11-12', 'weeks': ['2026-46']},
    {'kind': 'recent_weeks', 'base_date': '2026-11-12', 'weeks': ['2026-46']},
    *[{'kind': 'recent_weeks', 'base_date': '2026-11-12', 'weeks': n} for n in (0, 27)],
    {'kind': 'recent_weeks', 'base_date': '20261112', 'weeks': 1},
    {'kind': 'recent_weeks', 'base_date': '2026-02-30', 'weeks': 1},
    {'kind': 'weeks', 'weeks': ['2025-53']},
    {'kind': 'weeks', 'weeks': ['2026-W43', '202643']},
    {'kind': 'weeks', 'weeks': []},
    {'kind': 'weeks', 'weeks': [f'2026-{i:02}' for i in range(1, 28)]},
])
def test_invalid_period(period):
    with pytest.raises(ValidationError):
        YieldTableInput(title='weekly', lot_cd='4SS', period=period)


@pytest.mark.parametrize('extra', [
    {'rows': []}, {'periods': []}, {'long_result_id': 'x'},
    {'counts_result_id': 'x'}, {'gms_result_id': 'x'},
    {'long_columns': {}}, {'counts_columns': {}}, {'gms_columns': {}},
    {'comparison': {'current': '2026-46', 'baseline': '2026-45'}},
])
def test_period_forbids_pass_in_sources(extra):
    with pytest.raises(ValidationError, match='period.*forbids'):
        YieldTableInput(title='weekly', lot_cd='4SS', period=PERIOD, **extra)


@pytest.mark.parametrize('lot', [None, '', ' '])
def test_period_requires_product_code(lot):
    with pytest.raises(ValidationError, match='lot_cd'):
        YieldTableInput(title='weekly', lot_cd=lot, product_label='4SS', period=PERIOD)


@pytest.fixture
def weekly_db(monkeypatch):
    query = importlib.import_module('harness.tools.query_db')
    class Cursor:
        closed = False
        def execute(self, sql, binds):
            self.calls.append((sql, binds))
            if 'AVG(' in sql:
                self.rows = self.yields
            elif 'COUNT(' in sql:
                self.rows = self.counts if 'GROUP BY' in sql else [self.totals]
            elif 'DF_GMS_YIELD_WEEKLY' in sql:
                self.rows = self.gms
            else:
                self.rows = self.metadata
        def fetchall(self): return self.rows
        def close(self): self.closed = True
    cursor = Cursor()
    cursor.calls = []
    cursor.metadata = [('PT1H', 'VTH(V)', 1, 'P', 'o'), ('PT1H', 'DDA(d)', 8, 'P', 'o '),
                       ('PT1H', 'EXCLUDED', 9, 'P', 'x'), ('PT1C', 'PT1C', 26, 'P', 'o')]
    cursor.yields = [(w, 'PT1H TEST', 'VTH(V)', .7469 + i / 100)
                     for i, w in enumerate(WEEKS)] + [('2026-46', 'PT1C TEST', 'PT1C', .91)]
    cursor.counts = list(zip(WEEKS, [15, 18, 14, 19], [300, 400, 300, 400]))
    cursor.totals = (40, 900)
    cursor.gms = [(w.replace('-', ''), 96.01, 90.23, 86.51) for w in WEEKS]
    class Connection:
        closed = False
        opened = 0
        def cursor(self): return cursor
        def close(self): self.closed = True
    connection = Connection()
    def connect():
        connection.opened += 1
        return connection
    monkeypatch.setattr(query, '_connection', connect)
    return cursor, connection


def run_report():
    return asyncio.run(render_yield_table(YieldTableInput(
        title='weekly', lot_cd='4SS', period=PERIOD), None))


def test_period_distinct_counts_evidence_and_sql(weekly_db):
    cursor, connection = weekly_db
    result = run_report()
    assert result.status == 'success'
    assert result.source_result_ids == []
    assert [t.table_id for t in result.tables] == [
        'report_totals', 'weekly_counts', 'yield_long', 'gms_weekly',
        'displayed_yield_table', 'column_metadata']
    total = result.tables[0].rows[0]
    assert total['period_lot_count'] == 40
    assert total['period_wafer_count'] == 900
    assert total['sum_of_weekly_lot_count'] == 66
    assert total['sum_of_weekly_wafer_count'] == 1400
    assert total['counts_process'] == 'PT1H TEST'
    assert total['weeks'] == ','.join(WEEKS) and total['week_count'] == 4
    assert total['definition'] == 'period_* = distinct over all requested weeks; weekly counts overlap, do not add them'
    assert result.summary.startswith('LOT_CD=4SS 2026-43~2026-46 (4주, PT1H): 기간 distinct lot 40, wafer 900; 주별 lot 15/18/14/19')
    assert 'Δ = latest week − previous week' in result.summary
    assert connection.opened == 1 and connection.closed and cursor.closed
    sqls = [sql for sql, _ in cursor.calls]
    assert len(sqls) == 5
    assert all('DF_DIE_TO_WF_YLD' not in sql for sql in sqls)
    data_sql = next(sql for sql in sqls if 'AVG(' in sql)
    assert 'DF_WF_ALL_PRB_V2' in data_sql and 'DF_ALL_PARA_PRB' in data_sql
    assert "TRIM(m.YIELD_TABLE)='o'" in data_sql
    assert "m.PROC_GBN_CD||' TEST'" in data_sql and 'm.LOT_CD' in data_sql
    count_queries = [(s, b) for s, b in cursor.calls if 'COUNT(' in s]
    assert len(count_queries) == 2
    for sql, binds in count_queries:
        assert 'COUNT(DISTINCT LOT_ID)' in sql and 'COUNT(DISTINCT GORUPKEY)' in sql
        assert 'DF_WF_ALL_PRB_V2' in sql and "PROCESS='PT1H TEST'" in sql
    assert sum('GROUP BY' not in s for s, _ in count_queries) == 1
    for sql, binds in cursor.calls:
        assert binds['lot'] == '4SS' and '4SS' not in sql
        if 'w0' in binds:
            expected = [w.replace('-', '') for w in WEEKS] if 'DF_GMS_YIELD_WEEKLY' in sql else WEEKS
            assert [binds[f'w{i}'] for i in range(4)] == expected
            assert ' IN (' in sql and all(w not in sql for w in WEEKS)
    assert result.scope['period'] == PERIOD
    assert result.scope['resolved_weeks'] == WEEKS
    assert result.scope['missing_weeks'] == []
    assert 'CUM2' in str(result.scope) and 'PNT' in str(result.scope)
    cells = {(r['row'], r['column']): r['shown'] for r in result.tables[4].rows}
    assert cells['2026-43', 'PT1H TEST/VTH(V)'] == '74.7'
    assert cells['2026-46', 'GMS/cum0'] == '86.5'
    assert cells['2026-46', 'GMS/cum2'] == cells['2026-46', 'GMS/pnt'] == '-'
    assert result.scope['displayed_columns'][2:-5] == ['PT1H TEST/VTH(V)', 'PT1H TEST/DDA(d)', 'PT1C TEST/PT1C']
    html = result.artifacts[0]['data']
    assert 'EXCLUDED' not in html and '>74.7</td>' in html
    assert 'th-section-pt1h' in html and '<tr class="delta">' in html


def test_missing_week_stays_in_table_and_is_partial(weekly_db):
    cursor, _ = weekly_db
    cursor.yields = [row for row in cursor.yields if row[0] != WEEKS[1]]
    cursor.counts = [row for row in cursor.counts if row[0] != WEEKS[1]]
    cursor.gms = [row for row in cursor.gms if row[0] != '202644']
    result = run_report()
    assert result.status == 'partial'
    assert result.scope['missing_weeks'] == ['2026-44']
    assert result.tables[1].rows[1] == {'week': '2026-44', 'lot_count': None, 'wafer_count': None,
                                      'has_yield_data': False, 'has_gms': False}
    assert all(r['shown'] == '-' for r in result.tables[4].rows if r['row'] == '2026-44')
    assert '2026-44</td>' in result.artifacts[0]['data']


@pytest.mark.parametrize('counts, status', [(False, 'empty'), (True, 'partial')])
def test_no_yield_status(weekly_db, counts, status):
    cursor, _ = weekly_db
    cursor.yields = []
    if not counts:
        cursor.counts, cursor.totals = [], (0, 0)
    result = run_report()
    assert result.status == status
    assert result.scope['row_count'] == 4


def test_query_failure_closes_connection(weekly_db):
    cursor, connection = weekly_db
    def fail(*args): raise RuntimeError('offline query failure')
    cursor.execute = fail
    with pytest.raises(RuntimeError, match='offline query failure'):
        run_report()
    assert cursor.closed and connection.closed


def test_empty_product_still_returns_all_evidence_tables(weekly_db):
    cursor, _ = weekly_db
    cursor.metadata, cursor.yields, cursor.counts, cursor.gms, cursor.totals = [], [], [], [], (0, 0)
    result = run_report()
    assert result.status == 'empty'
    assert len(result.tables) == 6
    assert result.tables[-1].rows == []
    assert result.scope['row_count'] == 4
    assert result.artifacts


def test_single_week_has_no_delta(weekly_db):
    cursor, _ = weekly_db
    cursor.yields = cursor.yields[:1]
    cursor.counts, cursor.gms = cursor.counts[:1], cursor.gms[:1]
    result = asyncio.run(render_yield_table(YieldTableInput(
        title='one week', lot_cd='4SS', period={'kind': 'weeks', 'weeks': ['202643']}), None))
    assert result.status == 'success'
    assert '<tr class="delta">' not in result.artifacts[0]['data']

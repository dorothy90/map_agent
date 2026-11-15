"""Period report integration through mocked aggregate cursor, never a live DB."""
import asyncio
import importlib
from datetime import datetime

import pytest
from pydantic import ValidationError
from harness.tools.table_artifact import YieldTableInput, render_yield_table


@pytest.fixture
def period_db(monkeypatch):
    class Cursor:
        def __init__(self):
            self.calls = []
            self.base = datetime(2026, 11, 30)
            self.closed = False
            self.missing = set()
            self.missing_gms = set()
            self.fail = None
        def execute(self, sql, binds):
            self.calls.append((sql, binds))
            if self.fail and self.fail in sql:
                raise RuntimeError('query failed')
            buckets = [v for k, v in binds.items() if k.startswith('b') and k[1:].isdigit()]
            if 'MAX(' in sql:
                self.rows = [(self.base,)]
            elif '/* report_lot_' in sql:
                self.rows = []
            elif 'AVG(' in sql:
                self.rows = [(b, p, n, .7469 + i / 100) for i, b in enumerate(buckets)
                             if b not in self.missing for p, n in [('PT1H TEST', 'VTH'), ('PT1C TEST', 'COLD')]]
            elif 'COUNT(' in sql:
                self.rows = [(b, 10, 100) for b in buckets if b not in self.missing] if 'GROUP BY' in sql else [(15, 150)]
            elif 'DF_GMS_YIELD_' in sql:
                self.rows = [(b, 96.01, 90.23, 86.51) for b in buckets if b not in {m.replace('-', '') for m in self.missing | self.missing_gms}]
            else:
                self.rows = [('PT1H', 'VTH', 1, 'P', 'o'), ('PT1H', 'EXCLUDED', 2, 'P', 'x'), ('PT1C', 'COLD', 3, 'P', 'o')]
        def fetchall(self): return self.rows
        def close(self): self.closed = True
    cursor = Cursor()
    class Connection:
        opened = 0
        closed = False
        def cursor(self): return cursor
        def close(self): self.closed = True
    conn = Connection()
    def connect():
        conn.opened += 1
        return conn
    monkeypatch.setattr(importlib.import_module('harness.tools.query_db'), '_connection', connect)
    return cursor, conn


def run(period):
    return asyncio.run(render_yield_table(YieldTableInput(title='report', lot_cd='4SS', period=period), None))


def tables(result): return {t.table_id: t.rows for t in result.tables}


@pytest.mark.parametrize('period,key,buckets', [
    ({'kind': 'months', 'months': ['202611', '2026-09']}, 'months', ['2026-09', '2026-11']),
    ({'kind': 'recent', 'months': 3}, 'months', ['2026-09', '2026-10', '2026-11']),
    ({'kind': 'days', 'days': ['20261130', '2026-11-29']}, 'days', ['2026-11-29', '2026-11-30']),
    ({'kind': 'recent', 'days': 3}, 'days', ['2026-11-28', '2026-11-29', '2026-11-30']),
    ({'kind': 'recent', 'weeks': 4}, 'weeks', ['2026-46', '2026-47', '2026-48', '2026-49']),
])
def test_grain_query_render_and_window(period_db, period, key, buckets):
    cursor, conn = period_db
    result = run(period)
    grain = {'months': 'monthly', 'days': 'daily', 'weeks': 'weekly'}[key]
    singular = key[:-1]
    assert result.status == 'success'
    assert result.scope['window'][key] == buckets
    if period['kind'] == 'recent':
        assert result.scope['window']['base_date'] == '2026-11-30'
        assert 'MAX' in result.scope['window']['base_date_source']
        sql = next(s for s, _ in cursor.calls if 'MAX(' in s)
        assert "YEAR||LPAD(MONTH,2,'0')||LPAD(DAY,2,'0')" in sql
        assert "PROCESS='PT1H TEST'" in sql
        assert 'LAST_UPDATE' not in sql
    evidence = tables(result)
    assert [r[singular] for r in evidence[grain + '_counts']] == buckets
    assert evidence['report_totals'][0]['period_lot_count'] == 15
    cells = {(r['row'], r['column']): r['shown'] for r in evidence['displayed_yield_table']}
    assert cells[buckets[0], 'PT1H TEST/VTH'] == '74.7'
    assert cells[buckets[0], 'GMS/cum0'] == '86.5'
    assert cells[buckets[0], 'GMS/pnt'] == '-'
    html = result.artifacts[0]['data']
    assert 'EXCLUDED' not in html and 'th-section-pt1c' in html
    assert '<tr class="delta">' in html
    assert f'latest {singular} − previous {singular}' in str(result.scope)
    gms_sql, gms_binds = next((s, b) for s, b in cursor.calls if 'DF_GMS_YIELD_' in s)
    assert 'DF_GMS_YIELD_' + grain.upper() in gms_sql
    assert all(b.replace('-', '') in gms_binds.values() for b in buckets)
    assert conn.opened == 1 and conn.closed and cursor.closed


def test_combined_one_window_two_tables_and_nonadditive_totals(period_db):
    cursor, _ = period_db
    cursor.missing = {'2026-36'}
    result = run({'kind': 'recent', 'months': 3, 'grains': ['week', 'month']})
    window = result.scope['window']
    assert window['start'] == '2026-09-01' and window['end'] == '2026-11-30'
    assert window['months'] == ['2026-09', '2026-10', '2026-11']
    assert window['weeks'] == [f'2026-{w}' for w in range(36, 50)]
    evidence = tables(result)
    assert result.status == 'partial'
    assert evidence['weekly_counts'][0]['has_gms'] is False
    assert len(evidence['weekly_counts']) == 14
    total = evidence['report_totals'][0]['period_lot_count']
    assert total != sum(r['lot_count'] or 0 for r in evidence['monthly_counts'])
    assert total != sum(r['lot_count'] or 0 for r in evidence['weekly_counts'])
    assert {r['grain'] for r in evidence['yield_long']} == {'month', 'week'}
    assert {r['grain'] for r in evidence['displayed_yield_table']} == {'month', 'week'}
    html = result.artifacts[0]['data']
    assert len(result.artifacts) == 1 and html.count('<table') == 2
    assert html.count('<html') == html.count('</html>') == 1
    assert html.count('<tr class="delta">') == 2
    assert html.index('>MONTH<') < html.index('>WEEK<')
    assert '2026-36</td>' in html
    assert len([s for s, _ in cursor.calls if 'COUNT(' in s and 'GROUP BY' not in s]) == 1
    for sql, binds in cursor.calls:
        if 'DF_WF_ALL_PRB_V2' in sql and 'MAX(' not in sql:
            assert binds['win_start'] == '2026-09-01' and binds['win_end'] == '2026-11-30'
            assert ':win_start' in sql and ':win_end' in sql
            assert ':end' not in sql.replace(':win_end', '')
            assert 'TO_DATE(' in sql


@pytest.mark.parametrize('period', [
    {'kind': 'recent'}, {'kind': 'recent', 'weeks': 4, 'base_date': '2026-11-12'},
    {'kind': 'recent', 'days': 28, 'start': '2026-10-15'},
    {'kind': 'recent', 'weeks': 4, 'months': 3, 'grains': ['month', 'week']},
    {'kind': 'recent', 'months': 0}, {'kind': 'recent', 'months': 25},
    {'kind': 'recent', 'days': 93}, {'kind': 'recent', 'weeks': 27},
    {'kind': 'recent', 'weeks': True}, {'kind': 'recent', 'days': '3'},
    {'kind': 'recent', 'months': 3, 'grains': []},
    {'kind': 'recent', 'months': 3, 'grains': ['month', 'month']},
    {'kind': 'months', 'months': ['2026-13']},
    {'kind': 'months', 'months': ['202611', '2026-11']},
    {'kind': 'days', 'days': ['2026-02-30']},
    {'kind': 'days', 'days': ['20261130', '2026-11-30']},
])
def test_invalid_new_contract(period):
    with pytest.raises(ValidationError):
        YieldTableInput(title='invalid', lot_cd='4SS', period=period)


def test_missing_month_gms_and_single_day_no_delta(period_db):
    cursor, _ = period_db
    cursor.missing = {'2026-10'}
    result = run({'kind': 'recent', 'months': 3})
    assert result.status == 'partial'
    assert '2026-10' in str(result.scope['missing_buckets'])
    missing = [r for r in tables(result)['displayed_yield_table'] if r['row'] == '2026-10']
    assert missing and all(r['shown'] == '-' for r in missing)
    result = run({'kind': 'days', 'days': ['2026-11-30']})
    assert '<tr class="delta">' not in result.artifacts[0]['data']
    assert 'Δ absent' in result.scope['delta_definition']


def test_no_base_date_never_invents_window(period_db):
    cursor, conn = period_db
    cursor.base = None
    from harness.tools.report_policy import ReportContractError
    with pytest.raises(ReportContractError) as error:
        run({'kind': 'recent', 'weeks': 4})
    assert error.value.code == 'REPORT_SCOPE_UNAVAILABLE'
    assert conn.closed and cursor.closed
    assert len(cursor.calls) == 1


@pytest.mark.parametrize('stage', ['MAX(', 'AVG(', 'DF_GMS_YIELD_MONTHLY'])
def test_recent_failure_closes_connection(period_db, stage):
    cursor, conn = period_db
    cursor.fail = stage
    with pytest.raises(RuntimeError, match='query failed'):
        run({'kind': 'recent', 'months': 3})
    assert conn.opened == 1 and conn.closed and cursor.closed


@pytest.mark.parametrize('period,expected', [
    ({'kind': 'recent', 'weeks': 3}, ['2026-52', '2026-53', '2027-01']),
    ({'kind': 'recent', 'months': 3}, ['2026-11', '2026-12', '2027-01']),
    ({'kind': 'recent', 'days': 3}, ['2027-01-04', '2027-01-05', '2027-01-06']),
])
def test_year_boundary(period_db, period, expected):
    cursor, _ = period_db
    cursor.base = datetime(2027, 1, 6)
    key = next(k for k in ('weeks', 'months', 'days') if k in period)
    assert run(period).scope['window'][key] == expected


def test_daily_leap_boundary(period_db):
    cursor, _ = period_db
    cursor.base = datetime(2024, 3, 1)
    assert run({'kind': 'recent', 'days': 3}).scope['window']['days'] == ['2024-02-28', '2024-02-29', '2024-03-01']


@pytest.mark.parametrize('key,values', [
    ('months', ['2026-09', '2026-11']), ('days', ['2026-11-01', '2026-11-30']),
    ('weeks', ['2026-43', '2026-49']),
])
def test_explicit_gaps_are_excluded_in_totals_and_every_aggregate(period_db, key, values):
    cursor, _ = period_db
    run({'kind': key, key: values})
    assert not any('MAX(' in s for s, _ in cursor.calls)
    for sql, binds in cursor.calls:
        if 'DF_WF_ALL_PRB_V2' in sql:
            assert ' IN (:b0, :b1)' in sql
            assert binds['b0'] == values[0] and binds['b1'] == values[1]
            assert ' BETWEEN ' in sql
            if '/* report_lot_' in sql:
                continue
            if 'AVG(' in sql:
                assert 'GROUP BY' in sql
                assert "m.LOT_CD=y.LOT_CD" in sql
                assert "m.PARAM_NM=y.PARAM_NM" in sql
                assert "m.PROC_GBN_CD||' TEST'=y.PROCESS" in sql
                assert "TRIM(m.YIELD_TABLE)='o'" in sql
                assert "y.PROCESS IN ('PT1H TEST','PT1C TEST')" in sql
            else:
                assert "PROCESS='PT1H TEST'" in sql
                assert 'COUNT(DISTINCT LOT_ID)' in sql and 'COUNT(DISTINCT GORUPKEY)' in sql
    assert all('DF_DIE_TO_WF_YLD' not in s for s, _ in cursor.calls)


def test_gms_only_missing_is_not_fabricated(period_db):
    cursor, _ = period_db
    cursor.missing_gms = {'2026-11'}
    result = run({'kind': 'recent', 'months': 2})
    assert result.status == 'success'
    assert result.scope['missing_gms'] == {'month': ['2026-11']}
    counts = tables(result)['monthly_counts'][-1]
    assert counts['has_yield_data'] and not counts['has_gms']
    cells = [r for r in tables(result)['displayed_yield_table'] if r['row'] == '2026-11' and r['column'].startswith('GMS/')]
    assert len(cells) == 5 and all(r['shown'] == '-' for r in cells)


def test_recent_weekly_distinct_and_bucket_counts(period_db):
    result = run({'kind': 'recent', 'weeks': 4})
    evidence = tables(result)
    assert result.scope['resolved_weeks'] == ['2026-46', '2026-47', '2026-48', '2026-49']
    assert result.scope['resolved_weeks'] != ['2026-43', '2026-44', '2026-45', '2026-46']
    assert evidence['report_totals'][0]['period_lot_count'] == 15
    assert sum(r['lot_count'] for r in evidence['weekly_counts']) == 40
    assert evidence['report_totals'][0]['period_wafer_count'] == 150
    assert sum(r['wafer_count'] for r in evidence['weekly_counts']) == 400


def test_render_tool_schema_explains_recent_units():
    from harness.tools.registry import ToolRegistry
    from harness.tools.table_artifact import register
    import json

    registry = ToolRegistry()
    register(registry)
    schema = registry.tools['render_yield_table'].schema.model_json_schema()
    fields = schema['$defs']['RecentPeriod']['properties']
    expected = {
        'weeks': ('최근 N주 → weeks:N', '최근 4주 → {kind:"recent",weeks:4}', 'days:28로 환산하지 않는다'),
        'months': ('최근 N개월/N달 → months:N', '최근 3개월 → months:3', 'days:90이나 weeks:12로 환산하지 않는다'),
        'days': ('최근 N일 → days:N', '최근 28일은 days:28', '주·개월을 일로 환산하지 않는다'),
        'grains': ('주별/월별/일별', '기간 단위가 아니다', 'weeks:4,grains:["week"]'),
    }
    exposed = json.dumps(registry.model_tools(), ensure_ascii=False)
    for field, phrases in expected.items():
        description = fields[field]['description']
        for phrase in phrases:
            assert phrase in description
        assert json.dumps(description, ensure_ascii=False) in exposed


@pytest.mark.parametrize('period,start,key,count', [
    ({'kind': 'recent', 'weeks': 4, 'grains': ['week']}, '2026-11-09', 'weeks', 4),
    ({'kind': 'recent', 'days': 28}, '2026-11-03', 'days', 28),
])
def test_recent_week_and_genuine_day_windows_remain_distinct(period_db, period, start, key, count):
    result = run(period)
    assert result.status == 'success'
    window = result.scope['window']
    assert window['base_date'] == '2026-11-30'
    assert window['start'] == start
    assert len(window[key]) == count

"""Captured group aggregates use weighted values and distinct wafer identities."""
from harness.tools.yield_report_data import PeriodReportData, aggregate_groups


def test_aggregate_groups_uses_counts_and_identity_not_average_of_means():
    data = PeriodReportData('4SS', {}, [('PT1H', 'PT1H', 1, 'P', 'o')], [], {}, 2, 2, {},
        lot_parameter_stats=[
            dict(grain='all', bucket='', lot_id='A', process='PT1H TEST', param='PT1H', value_sum=1, value_count=2),
            dict(grain='all', bucket='', lot_id='B', process='PT1H TEST', param='PT1H', value_sum=3, value_count=3)],
        lot_wafer_members=[dict(grain='all', bucket='', lot_id=l, gorupkey=w)
                           for l, w in [('A', '1'), ('B', '1'), ('B', '2')]])
    result = aggregate_groups(data, [{'lot_id': 'A', 'group_id': 'g'}, {'lot_id': 'B', 'group_id': 'g'}], ['g', 'empty'])
    assert result['rows'] == [dict(period='g', process='PT1H TEST', param='PT1H', value=.8)]
    assert result['counts'] == [dict(period='g', lot_count=2, wf_count=2, has_yield_data=True),
                                dict(period='empty', lot_count=0, wf_count=0, has_yield_data=False)]


def test_capture_same_connection_full_window_and_requested_grains(monkeypatch):
    from contextlib import contextmanager
    from decimal import Decimal
    from harness.tools import yield_report_data as module
    class Cursor:
        calls = []
        def execute(self, sql, binds):
            self.calls.append((sql, binds))
            bucket = None if "SELECT ''" in sql else '2026-43'
            if 'report_lot_population' in sql:
                self.rows = [('A', 2, Decimal('.8'), 2), ('B', 1, None, 0), (None, 1, None, 0)]
            elif 'report_lot_stats' in sql:
                self.rows = [(bucket, 'A', 'PT1H TEST', 'PT1H', Decimal('1.6'), 2)]
            elif 'report_lot_members' in sql:
                self.rows = [(bucket, 'A', 'W1'), (bucket, 'A', 'W2')]
            elif 'DF_ALL_PARA_PRB' in sql and 'AVG(' not in sql:
                self.rows = [('PT1H', 'OTHER', 1, 'P', 'o')]
            elif 'AVG(' in sql:
                self.rows = []
            elif 'COUNT(' in sql:
                self.rows = [('2026-43', 1, 2)] if 'GROUP BY' in sql else [(1, 2)]
            else:
                self.rows = []
        def fetchall(self): return self.rows
    cursor = Cursor()
    opened = []
    @contextmanager
    def fake_cursor():
        opened.append(True)
        yield cursor
    monkeypatch.setattr(module, '_report_cursor', fake_cursor)
    data = module.fetch_period_report('4SS', module.ExplicitWeeks(kind='weeks', weeks=['2026-43']))
    assert len(opened) == 1
    assert data.lot_population == [dict(lot_id='A', wafer_count=2, pt1h_mean=.8, pt1h_value_count=2),
                                   dict(lot_id='B', wafer_count=1, pt1h_mean=None, pt1h_value_count=0)]
    assert {(r['grain'], r['bucket']) for r in data.lot_parameter_stats} == {('all', ''), ('week', '2026-43')}
    assert len(data.lot_wafer_members) == 4
    assert all(isinstance(r['value_sum'], float) for r in data.lot_parameter_stats)
    captures = [(s, b) for s, b in cursor.calls if '/* report_lot_' in s]
    assert len(captures) == 5
    for sql, binds in captures:
        assert ' IN (:b0)' in sql
        assert binds['b0'] == '2026-43'
        assert binds['win_start'] == '2026-10-19'
        assert binds['win_end'] == '2026-10-25'
    stat_sql = next(s for s, _ in captures if 'report_lot_stats' in s)
    assert "y.PARAM_NM='PT1H'" in stat_sql
    assert "TRIM(m.YIELD_TABLE)='o'" in stat_sql


def test_aggregate_requested_bucket_excludes_other_buckets_and_missing_values():
    data = PeriodReportData('4SS', {}, [('PT1C', 'COLD', 1, 'P', 'o')], [], {}, 1, 1, {},
        lot_parameter_stats=[
            dict(grain='week', bucket=b, lot_id='A', process='PT1C TEST', param='COLD', value_sum=s, value_count=n)
            for b, s, n in [('2026-43', 0, 0), ('2026-44', 99, 1)]],
        lot_wafer_members=[dict(grain='week', bucket='2026-43', lot_id='A', gorupkey='W')])
    result = aggregate_groups(data, [{'lot_id': 'A', 'group_id': 'g'}], ['g'], grain='week', bucket='2026-43')
    assert result == dict(rows=[dict(period='g', process='PT1C TEST', param='COLD', value=None)],
                          counts=[dict(period='g', lot_count=1, wf_count=1, has_yield_data=True)])


def test_score_only_capture_is_not_displayed_or_yield_data():
    data = PeriodReportData('4SS', {}, [('PT1H', 'PT1H', 1, 'P', 'x')], [], {}, 1, 1, {},
        lot_parameter_stats=[dict(grain='all', bucket='', lot_id='A', process='PT1H TEST',
                                 param='PT1H', value_sum=.8, value_count=1)],
        lot_wafer_members=[dict(grain='all', bucket='', lot_id='A', gorupkey=None)])
    result = aggregate_groups(data, [{'lot_id': 'A', 'group_id': 'g'}], ['g'])
    assert result == dict(rows=[], counts=[dict(period='g', lot_count=1, wf_count=0, has_yield_data=False)])

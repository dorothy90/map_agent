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
            elif 'report_wafer_stats' in sql:
                self.rows = [(bucket, 'A', '7', 'PT1H TEST', 'PT1H', Decimal('.8'), 1)]
            elif 'report_lot_members' in sql:
                self.rows = [(bucket, 'A', 'W1', '7'), (bucket, 'A', 'W2', '8')]
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
    assert {r['wf_id'] for r in data.lot_wafer_members} == {'7', '8'}
    assert data.wafer_parameter_stats == [dict(grain=g, bucket=b, lot_id='A', wf_id='7',
        process='PT1H TEST', param='PT1H', value_sum=.8, value_count=1)
        for g, b in [('all', ''), ('week', '2026-43')]]
    wafer_sql = [(s, b) for s, b in cursor.calls if 'report_wafer_stats' in s]
    assert len(wafer_sql) == 2
    for sql, binds in wafer_sql:
        lot_sql = next(s for s, b in cursor.calls if 'report_lot_stats' in s and b == binds and
                       ("SELECT ''" in s) == ("SELECT ''" in sql))
        assert sql == lot_sql.replace('report_lot_stats', 'report_wafer_stats').replace(
            'y.LOT_ID, y.PROCESS', 'y.LOT_ID, y.WFID, y.PROCESS')
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


def test_lot_rounding_boundary_keeps_lot_sum_and_addition_order():
    from decimal import Decimal
    data = PeriodReportData('4SS', {}, [('PT1H','PT1H',1,'P','o')], [], {}, 1, 2, {},
        lot_parameter_stats=[dict(grain='all',bucket='',lot_id='A',process='PT1H TEST',param='PT1H',
            value_sum=float(Decimal('.007')),value_count=2)],
        lot_wafer_members=[dict(grain='all',bucket='',lot_id='A',gorupkey='A.'+wf,wf_id=wf) for wf in ['7','8']],
        wafer_parameter_stats=[dict(grain='all',bucket='',lot_id='A',wf_id=wf,process='PT1H TEST',param='PT1H',
            value_sum=float(Decimal(value)),value_count=1) for wf,value in [('7','.0021'),('8','.0049')]])
    lot = aggregate_groups(data, [dict(group_id='G',lot_id='A')], ['G'])
    wafer = aggregate_groups(data, [dict(group_id='G',lot_id='A',wf_id=wf) for wf in ['7','8']], ['G'], member_grain='wafer')
    assert lot['rows'][0]['value'] == .0035
    assert wafer['rows'][0]['value'] == (.0021+.0049)/2
    assert format(lot['rows'][0]['value']*100,'.1f') == '0.4'
    assert format(wafer['rows'][0]['value']*100,'.1f') == '0.3'


from harness.tests.test_period_report_data import period_db


def test_every_wafer_read_uses_product_latest_test_source(period_db):
    from harness.tools import yield_report_data as module
    # The existing cursor records all generated SQL, including the base date.
    cursor, _ = period_db
    module.fetch_period_report('4SS', module.RecentPeriod(kind='recent', weeks=2))
    queries = [sql for sql, _ in cursor.calls if 'DF_WF_ALL_PRB_V2' in sql]
    assert len(queries) == 11
    for sql in queries:
        assert module._latest_test_source() in sql
        assert 'DENSE_RANK() OVER' in sql
        assert 'PARTITION BY LOT_CD, LOT_ID, WFID, PROCESS' in sql
        assert 'ORDER BY MEASURETIME_START DESC, MEASURETIME_END DESC' in sql
        assert 'report_test_rank=1 OR LOT_ID IS NULL OR WFID IS NULL' in sql
        assert ':end' not in sql


class RetestCursor:
    """DB-shaped fake: rank product tests, then apply report predicates."""
    def __init__(self):
        from datetime import datetime
        self.calls, self.source = [], []
        def add(wf, start, end, value, *, lot='A', product='4SS', process='PT1H TEST', param='PT1H'):
            self.source.append(dict(lot_cd=product, lot_id=lot, wf_id=wf, process=process, param=param,
                start=datetime.fromisoformat(start), end=datetime.fromisoformat(end), value=value,
                gorupkey=f'{lot}.{wf}'))
        add('1', '2026-10-19T10:00', '2026-10-19T11:00', .1)
        add('1', '2026-10-26T10:00', '2026-10-26T11:00', .8)
        add('2', '2026-10-27T10:00', '2026-10-27T11:00', .2)
        add('2', '2026-10-27T10:00', '2026-10-27T12:00', .6)
        add('2', '2026-10-27T10:00', '2026-10-27T12:00', .4)
        add('3', '2026-10-20T10:00', '2026-10-20T11:00', .9)
        add('3', '2026-11-02T10:00', '2026-11-02T11:00', .7)
        add('4', '2026-10-28T09:00', '2026-11-03T11:00', .1)
        add('4', '2026-10-28T10:00', '2026-10-28T11:00', .3)
        add('1', '2026-10-20T10:00', '2026-10-20T11:00', .5, process='PT1C TEST', param='COLD')
        add('1', '2026-10-27T10:00', '2026-10-27T11:00', .9, process='PT1C TEST', param='COLD')
        add('1', '2026-11-09T10:00', '2026-11-09T11:00', 99, product='4SA')
        for lot, wf in [(None, '5'), ('N', None)]:
            add(wf, '2026-10-19T10:00', '2026-10-19T11:00', .1, lot=lot)
            add(wf, '2026-10-26T10:00', '2026-10-26T11:00', .2, lot=lot)
        # A parameter present only in an old test must disappear, not carry forward.
        add('1', '2026-10-19T10:00', '2026-10-19T11:00', 42, param='OLD')

    def execute(self, sql, binds):
        from harness.tools.yield_report_data import _bucket, _latest_test_source
        self.calls.append((sql, binds))
        if 'DF_WF_ALL_PRB_V2' not in sql:
            self.rows = [] if 'DF_GMS' in sql else [('PT1H', 'PT1H', 1, 'B', 'o'),
                ('PT1H', 'OLD', 2, 'I', 'o'), ('PT1C', 'COLD', 1, 'B', 'o')]
            return
        assert _latest_test_source() in sql
        source = [r for r in self.source if r['lot_cd'] == binds['lot']]
        latest = {}
        key = lambda r: (r['lot_cd'], r['lot_id'], r['wf_id'], r['process'])
        for r in source:
            latest[key(r)] = max(latest.get(key(r), (r['start'], r['end'])), (r['start'], r['end']))
        source = [r for r in source if r['lot_id'] is None or r['wf_id'] is None
                  or (r['start'], r['end']) == latest[key(r)]]
        if 'MAX(' in sql:
            self.rows = [(max(r['start'].date() for r in source if r['process'] == 'PT1H TEST'),)]
            return
        buckets = [v for k, v in binds.items() if k.startswith('b')]
        source = [r for r in source if binds['win_start'] <= r['start'].date().isoformat() <= binds['win_end']
                  and _bucket(r['start'].date(), 'week') in buckets]
        if "PROCESS='PT1H TEST'" in sql and 'y.PROCESS' not in sql:
            source = [r for r in source if r['process'] == 'PT1H TEST']
        bucket = lambda r: None if "SELECT ''" in sql or "SELECT DISTINCT ''" in sql else _bucket(r['start'].date(), 'week')
        def grouped(keys):
            groups = {}
            for r in source:
                groups.setdefault(keys(r), []).append(r)
            return groups.items()
        def values(rs):
            from decimal import Decimal
            vs = [Decimal(str(r['value'])) for r in rs if r['value'] is not None]
            return float(sum(vs)), len(vs)
        def counts(rs):
            return len({r['lot_id'] for r in rs if r['lot_id'] is not None}), len({r['gorupkey'] for r in rs})
        if 'report_lot_population' in sql:
            self.rows = [(lot, counts(rs)[1], values(rs)[0] / values(rs)[1], values(rs)[1])
                         for lot, rs in grouped(lambda r: r['lot_id'])]
        elif 'report_lot_stats' in sql or 'report_wafer_stats' in sql:
            wafer = 'report_wafer_stats' in sql
            self.rows = [(*k, *values(rs)) for k, rs in grouped(lambda r:
                (bucket(r), r['lot_id'], *((r['wf_id'],) if wafer else ()), r['process'], r['param']))]
        elif 'report_lot_members' in sql:
            self.rows = list(dict.fromkeys((bucket(r), r['lot_id'], r['gorupkey'], r['wf_id']) for r in source))
        elif 'AVG(' in sql:
            self.rows = [(*k, values(rs)[0] / values(rs)[1]) for k, rs in grouped(lambda r:
                (bucket(r), r['process'], r['param']))]
        elif 'GROUP BY' in sql:
            self.rows = [(k, *counts(rs)) for k, rs in grouped(bucket)]
        else:
            self.rows = [counts(source)]

    def fetchall(self):
        return self.rows


def test_retests_latest_ties_window_buckets_and_all_report_paths(monkeypatch):
    from contextlib import contextmanager
    from harness.tools import yield_report_data as module
    cursor = RetestCursor()
    @contextmanager
    def fake_cursor():
        yield cursor
    monkeypatch.setattr(module, '_report_cursor', fake_cursor)
    data = module.fetch_period_report('4SS', module.ExplicitWeeks(kind='weeks', weeks=['2026-43', '2026-44']))
    stats = [r for r in data.wafer_parameter_stats if r['grain'] == 'all' and r['lot_id'] == 'A']
    assert {(r['wf_id'], r['process'], r['value_sum'], r['value_count']) for r in stats} == {
        ('1', 'PT1H TEST', .8, 1), ('2', 'PT1H TEST', 1., 2), ('4', 'PT1H TEST', .3, 1), ('1', 'PT1C TEST', .9, 1)}
    assert all(r['param'] != 'OLD' and r['wf_id'] != '3' for r in data.wafer_parameter_stats)
    for grain, bucket in [('all', ''), ('week', '2026-43'), ('week', '2026-44')]:
        lot = aggregate_groups(data, [dict(group_id='G', lot_id='A')], ['G'], grain=grain, bucket=bucket)
        wafer = aggregate_groups(data, [dict(group_id='G', lot_id='A', wf_id=wf) for wf in ['1', '2', '4']],
                                 ['G'], grain=grain, bucket=bucket, member_grain='wafer')
        assert lot == wafer
        assert lot['counts'][0]['wf_count'] == (0 if bucket == '2026-43' else 3)
    # Both NULL-key histories survive, including their earlier bucket.
    assert sum(r['value_count'] for r in data.wafer_parameter_stats
               if r['grain'] == 'all' and (r['lot_id'] is None or r['wf_id'] is None)) == 4
    assert (data.period_lot_count, data.period_wafer_count) == (2, 5)
    assert [(r['lot_count'], r['wafer_count']) for r in data.counts['week']] == [(1, 2), (2, 5)]
    for row in data.yield_long:
        captured = [s for s in data.wafer_parameter_stats if s['grain'] == 'week'
                    and (s['bucket'], s['process'], s['param']) == (row['bucket'], row['process'], row['param'])]
        from decimal import Decimal
        assert row['value'] == float(sum(Decimal(str(r['value_sum'])) for r in captured)) / sum(r['value_count'] for r in captured)
    population = next(r for r in data.lot_population if r['lot_id'] == 'A')
    assert population['wafer_count'] == 3
    assert population['pt1h_mean'] == (0.8 + 1.0 + .3) / 4
    recent = module.fetch_period_report('4SS', module.RecentPeriod(kind='recent', weeks=2))
    assert recent.window['base_date'] == '2026-11-02'  # START, not the older test's later END

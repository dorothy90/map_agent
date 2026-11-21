from contextlib import contextmanager
from datetime import date, datetime
import sqlite3

from harness.tools import report_scatter


def test_scatter_uses_one_product_two_processes_and_the_latest_wafer_measurement(monkeypatch):
    db = sqlite3.connect(':memory:', detect_types=sqlite3.PARSE_COLNAMES)
    db.executescript("""
        CREATE TABLE DF_WF_ALL_PRB_V2
        (LOT_CD, PROCESS, LOT_ID, WFID, PARAM_NM, MEASURETIME_START, MEASURETIME_END, VALUE);
        CREATE TABLE DF_PARAM_META (SOURCE_ID, LOT_CD, PROCESS, PARAM_NM, UNIT, BETTER_WHEN);
    """)
    for process, direction in [('PROC1', 'higher'), ('PROC2', 'lower')]:
        db.execute("INSERT INTO DF_PARAM_META VALUES ('wafer_parameter', 'A', ?, 'same', 'fraction', ?)",
                   (process, direction))
        for lot, timestamp, value in [('W0', '2031-01-06', .4), ('W1', '2031-01-13', .8),
                                      ('retest', '2031-01-06', .1), ('retest', '2031-01-20', .9)]:
            db.execute("INSERT INTO DF_WF_ALL_PRB_V2 VALUES ('A', ?, ?, '01', 'same', ?, ?, ?)",
                       (process + ' TEST', lot, timestamp + ' 09:00:00', timestamp + ' 09:15:00', value))
    db.execute("INSERT INTO DF_WF_ALL_PRB_V2 VALUES ('B', 'PROC2 TEST', 'other', '01', 'same',"
               " '2031-01-06 09:00:00', '2031-01-06 09:15:00', 50)")
    @contextmanager
    def cursor():
        yield db.cursor()

    monkeypatch.setattr(report_scatter, '_report_cursor', cursor)
    artifacts, tables, scope = report_scatter.build_report_scatter(
        'A', ['PROC1 TEST', 'PROC2 TEST'], date(2031, 1, 6), date(2031, 1, 13), date(2031, 1, 14))
    assert len(artifacts) == 1
    ranks, points = (table.rows for table in tables)
    assert [(row['process'], row['direction'], row['previous_count'], row['recent_count']) for row in ranks] == [
        ('PROC2 TEST', 'deteriorated', 1, 1), ('PROC1 TEST', 'improved', 1, 1)]
    assert {row['lot_id'] for row in points} == {'W0', 'W1'}
    assert len(points) == 4
    assert scope['deteriorated'] == scope['improved'] == 1
    assert all(row['wf_id'] == '01' and datetime.fromisoformat(row['measured_at']).hour == 9 for row in points)
    db.close()


def test_larger_report_can_show_twenty_parameters_per_direction(monkeypatch):
    rows = [
        ('T', str(i), 'fraction', 'higher', .4, .5, 1, 1, score, week, 'L', '1', '2031-01-09', .5)
        for i in range(40) for score in [i - 19.5] for week in [0, 1]
    ]
    monkeypatch.setattr(report_scatter, '_scatter_rows', lambda *args: rows)
    artifacts, _, summary = report_scatter.build_report_scatter(
        'A', ['T'], '2031-01-06', '2031-01-13', '2031-01-14', top_n=20)
    assert len(artifacts) == 1
    assert summary['coverage_note'] == ''
    assert summary['deteriorated'] == summary['improved'] == 20

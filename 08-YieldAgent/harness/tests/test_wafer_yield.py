from datetime import date
import pytest
import yield_db


def test_complete_parameter_fetch_has_boundaries_without_scatter_limit(monkeypatch):
    assert hasattr(yield_db, '_fetch_wafer_values'), 'unbounded wafer query is missing'
    class Cursor:
        def execute(self, sql, params):
            assert 'FETCH FIRST' not in sql and 'ROW_NUMBER' not in sql
            assert 'MEASURETIME_START <' in sql
            assert params['param'] == "VTH' OR 1=1"
            assert "VTH' OR 1=1" not in sql
        def __iter__(self):
            return iter([(date(2026, 1, 1), 'L', 1, .2)])
        def close(self): pass
    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass
    monkeypatch.setattr(yield_db, '_get_oracle_connection', Connection)
    rows = yield_db._fetch_wafer_values('P', '20260101', '20260102', 'PT1H', "VTH' OR 1=1")
    assert rows[0]['value'] == .2


def test_coverage_propagates_connection_error(monkeypatch):
    assert hasattr(yield_db, '_inspect_yield_coverage'), 'coverage query is missing'
    def fail(): raise ConnectionError('offline')
    monkeypatch.setattr(yield_db, '_get_oracle_connection', fail)
    with pytest.raises(ConnectionError): yield_db._inspect_yield_coverage(None)


def test_wafer_tool_preserves_empty_period_and_measurement_policy(monkeypatch):
    from harness.tools import yield_tools as y
    assert hasattr(y, '_query_wafer_yield'), 'wafer tool is missing'
    monkeypatch.setattr(yield_db, '_get_period_date_ranges', lambda *a: [{'label': 'one', 'start': '20260101', 'end': '20260102'}, {'label': 'empty', 'start': '20260102', 'end': '20260103'}])
    def fetch(*a):
        return [{'lot_id': 'L', 'wf_id': 1, 'end_tm': '2026-01-01', 'value': .1}, {'lot_id': 'L', 'wf_id': 1, 'end_tm': '2026-01-01 12:00:00', 'value': .9}] if a[1] == '20260101' else []
    monkeypatch.setattr(yield_db, '_fetch_wafer_values', fetch)
    args = y.WaferYieldInput(lotcd='P', ref_date='2026-01-02', metric='parameter', parameter='VTH', measurement_policy='latest_per_wafer')
    result = y._query_wafer_yield(args)
    rows = result.tables[0].rows
    assert rows[0]['value'] == .9
    assert rows[1]['value'] is None
    assert result.tables[0].complete
    assert result.scope['measurement_policy'] == 'latest_per_wafer'


def test_anomaly_adapter_reports_actual_relative_change_without_fake_threshold(monkeypatch):
    from harness.tools import yield_tools as y
    assert hasattr(y, '_analyze_yield'), 'anomaly tool is missing'
    monkeypatch.setattr(yield_db, '_fetch_periods', lambda *a, **k: [{'week': 'a', 'VTH': 10, 'wfCount': 3}, {'week': 'b', 'VTH': 9, 'wfCount': 4}])
    result = y._analyze_yield(y.YieldInput(lotcd='P', ref_date='2026-01-02'))
    assert result.tables[1].rows[0]['change_pct'] == -10
    assert result.scope['threshold'] is None
    assert result.scope['comparison'] == 'last_two_requested_periods'


def test_scatter_is_incomplete_and_propagates_db_errors(monkeypatch):
    from harness.tools.yield_tools import YieldInput, _render_yield_scatter
    monkeypatch.setattr(yield_db, '_fetch_wafer_scatter', lambda *a, **kw: [{'ts': '2026-01-01', 'param': 'VTH', 'value': .9, 'period': 'a', 'lotid': 'L', 'wfid': '1'}])
    result = _render_yield_scatter(YieldInput(lotcd='P', ref_date='2026-01-02'))
    assert result.status == 'partial'
    assert result.tables[0].complete is False
    assert result.artifacts[0]['mime'] == 'text/html'
    def fail(): raise ConnectionError('offline')
    monkeypatch.undo()
    monkeypatch.setattr(yield_db, '_get_oracle_connection', fail)
    with pytest.raises(ConnectionError):
        yield_db._fetch_wafer_scatter('P', date(2026, 1, 2), 'weekly', 4, 'pt1h', strict=True)


def test_analysis_distinguishes_unavailable_comparison_from_unchanged(monkeypatch):
    from harness.tools import yield_tools as y
    args = y.YieldInput(lotcd='P', ref_date='2026-01-02')
    monkeypatch.setattr(yield_db, '_fetch_periods', lambda *a, **k: [{'VTH': 10}, {'VTH': None}])
    assert y._analyze_yield(args).scope['analysis_status'] == 'partial'
    monkeypatch.setattr(yield_db, '_fetch_periods', lambda *a, **k: [{'VTH': 10}, {'VTH': 10}])
    assert y._analyze_yield(args).scope['analysis_status'] == 'no_findings'
    monkeypatch.setattr(yield_db, '_fetch_periods', lambda *a, **k: [{'VTH': 10}, {'VTH': 9}])
    assert y._analyze_yield(args).scope['analysis_status'] == 'findings'

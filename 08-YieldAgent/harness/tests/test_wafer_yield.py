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
    def fetch(*a, **kwargs):
        return [{'lot_id': 'L', 'wf_id': 1, 'end_tm': '2026-01-01', 'map_val_json': {'MAP': ['0,0,B']}}, {'lot_id': 'L', 'wf_id': 1, 'end_tm': '2026-01-01 12:00:00', 'map_val_json': {'MAP': ['0,0,?']}}] if a[1] == '20260101' else []
    import sys
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules, 'map_agent', SimpleNamespace(_query_wafer_data_by_date=fetch))
    args = y.WaferYieldInput(lotcd='P', ref_date='2026-01-02', metric='pass_rate', measurement_policy='latest_per_wafer')
    result = y._query_wafer_yield(args)
    rows = result.tables[0].rows
    assert rows[0]['value'] == 100
    assert rows[1]['value'] is None
    assert result.tables[0].complete
    assert result.scope['measurement_policy'] == 'latest_per_wafer'



def test_wafer_tool_rejects_retired_parameter_metric():
    from harness.tools.yield_tools import WaferYieldInput
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        WaferYieldInput(lotcd='P', ref_date='2026-01-02', metric='parameter',
            parameter='VTH', measurement_policy='latest_per_wafer')

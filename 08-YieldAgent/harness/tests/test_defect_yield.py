import json
from datetime import datetime

import pytest


def record(lot, bins, day=1):
    return {'lot_id': lot, 'wf_id': 1, 'end_tm': datetime(2026, 1, day),
            'map_val_json': json.dumps({'MAP': [f'0,{i},{b},{b}' for i, b in enumerate(bins)]})}


def args(**kwargs):
    from harness.tools.defect_tools import DefectYieldInput
    return DefectYieldInput(lotcd='P', start_date='2026-01-01', end_date='2026-02-01',
                           oper='PT1H', **kwargs)


def test_full_population_union_ranking_and_latest_measurement(monkeypatch):
    import map_agent
    from harness.tools.defect_tools import _query_defect_yield
    def fetch(*a, **kw):
        assert kw['limit'] is None and kw['strict']
        return [record('A', 'KKKK'), record('A', 'AAAK', 2), record('B', 'AKQA'), record('C', 'AAAA')]
    monkeypatch.setattr(map_agent, '_query_wafer_data_by_date', fetch)
    result = _query_defect_yield(args(parameters=['TPD', 'BVDS'], top_n=1))
    ranking = result.tables[0].rows
    assert ranking[0]['wafer_id'] == 'B.01'
    assert ranking[0]['defect_yield_percent'] == 50
    assert ranking[0]['defect_die_count'] == 2
    assert len(ranking) == 3
    assert result.tables[1].rows == ranking[:1]
    assert result.scope['source_measurement_count'] == 4
    assert result.scope['population_wafer_count'] == 3
    assert result.scope['target_bins'] == ['K', 'Q']


def test_exact_ties_and_real_binmap(monkeypatch):
    import map_agent
    from harness.tools.defect_tools import _query_defect_yield
    monkeypatch.setattr(map_agent, '_query_wafer_data_by_date', lambda *a, **k: [record('B', 'AAKK'), record('A', 'AAKK')])
    result = _query_defect_yield(args(target_bins=['K'], top_n=1, include_binmap=True))
    assert result.tables[1].rows[0]['wafer_id'] == 'A.01'
    assert result.scope['cutoff_tie_count'] == 2
    assert result.artifacts[0]['data'].startswith(b'\x89PNG')
    assert result.scope['rendered_wafer_ids'] == ['A.01']


def test_conflicting_latest_measurements_rejected(monkeypatch):
    import map_agent
    from harness.tools.defect_tools import _query_defect_yield
    monkeypatch.setattr(map_agent, '_query_wafer_data_by_date', lambda *a, **k: [record('A', 'AAAA'), record('A', 'KKKK')])
    with pytest.raises(ValueError, match='Conflicting'):
        _query_defect_yield(args(target_bins=['K']))


def test_historical_conflicting_ties_do_not_override_newer_measurement(monkeypatch):
    import map_agent
    from harness.tools.defect_tools import _query_defect_yield
    monkeypatch.setattr(map_agent, '_query_wafer_data_by_date', lambda *a, **k:
                        [record('A', 'AAAA'), record('A', 'KKKK'), record('A', 'AAAK', 2)])
    result = _query_defect_yield(args(target_bins=['K']))
    assert result.tables[0].rows[0]['defect_yield_percent'] == 75


@pytest.mark.parametrize('options', [dict(), {'parameters':['JUNCTION(J)']},
    {'target_bins':['A']}, {'parameters':['TPD'],'target_bins':['K']}])
def test_invalid_or_unverified_mapping_rejected(options):
    with pytest.raises(ValueError):
        args(**options)


def test_empty_result_and_registry(monkeypatch):
    import map_agent
    from harness.tools.defect_tools import _query_defect_yield
    from harness.tools.registry import domain_registry
    monkeypatch.setattr(map_agent, '_query_wafer_data_by_date', lambda *a, **k: [])
    result = _query_defect_yield(args(parameters=['TPD']))
    assert result.status == 'empty' and not result.artifacts
    assert 'query_defect_yield' in domain_registry().tools


def test_empty_map_is_reported_as_incomplete(monkeypatch):
    import map_agent
    from harness.tools.defect_tools import _query_defect_yield
    monkeypatch.setattr(map_agent, '_query_wafer_data_by_date', lambda *a, **k: [record('A',''),record('B','AAKK')])
    result = _query_defect_yield(args(target_bins=['K']))
    assert result.status == 'partial'
    assert result.scope['excluded_empty_maps'] == 1
    assert not result.tables[0].complete

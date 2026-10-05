import importlib.util
import pytest
import map_agent


def test_date_map_supports_complete_query_and_strict_errors(monkeypatch):
    def fail(): raise ConnectionError('offline')
    monkeypatch.setattr(map_agent, '_get_oracle_connection_common', fail)
    with pytest.raises(ConnectionError):
        map_agent._query_wafer_data_by_date('P', '20260101', '20260102', 'PT1H TEST', strict=True, limit=None)


def test_map_metrics_preserve_three_periods_and_numeric_deltas():
    assert importlib.util.find_spec('harness.tools.map_tools') is not None, 'expanded map tool is missing'
    from harness.tools.map_tools import map_statistics, compare_statistics
    rows = [{'map_val_json': {'MAP': ['0,0,A', '0,1,B']}}]
    stats, cells = map_statistics(rows, target_bin=None)
    assert stats['mean_coordinate_pass_percent'] == 50
    assert stats['pooled_die_pass_percent'] == 50
    assert stats['measurement_count'] == 1
    changed = [{'map_val_json': {'MAP': ['0,0,B', '0,1,B']}}]
    _, other = map_statistics(changed, target_bin=None)
    delta = compare_statistics([('a', cells), ('b', other), ('c', cells)])
    assert {r['period'] for r in delta} == {'b', 'c'}
    assert next(r for r in delta if r['period'] == 'b' and r['col'] == 0)['delta_pp'] == -100
    assert map_statistics(rows, target_bin='B')[0]['pooled_die_pass_percent'] == 50


def test_map_input_accepts_product_date_and_rejects_half_range():
    assert importlib.util.find_spec('harness.tools.map_tools') is not None
    from harness.tools.map_tools import MapInput, CompareMapInput
    assert MapInput(lotcd='P', start_date='2026-01-01', end_date='2026-02-01', oper='PT1H').lotcd == 'P'
    with pytest.raises(ValueError): MapInput(lotcd='P', start_date='2026-01-01', oper='PT1H')
    inp = CompareMapInput(oper='PT1H', measurement_policy='all_measurements', periods=[{'label': str(i), 'lotcd': 'P', 'start_date': f'2026-0{i}-01', 'end_date': f'2026-0{i+1}-01'} for i in range(1,4)])
    assert len(inp.periods) == 3


def test_comparison_actual_numeric_and_png_preserves_all_periods(monkeypatch):
    from harness.tools.map_tools import CompareMapInput, _compare_maps
    def fetch(product, start, end, *a, **kw):
        assert kw == {'strict': True, 'limit': None}
        return [{'lot_id': 'L', 'wf_id': 2, 'end_tm': start, 'map_val_json': {'MAP': ['0,0,A' if start != '20260201' else '0,0,B', '0,1,B']}}]
    monkeypatch.setattr(map_agent, '_query_wafer_data_by_date', fetch)
    args = CompareMapInput(oper='PT1H', measurement_policy='all_measurements',
        periods=[{'label': str(i), 'lotcd': 'P', 'start_date': f'2026-0{i}-01', 'end_date': f'2026-0{i+1}-01'} for i in range(1, 4)],
        regions=[{'label': 'left', 'row_min': 0, 'row_max': 0, 'col_min': 0, 'col_max': 0}])
    result = _compare_maps(args)
    assert len(result.tables[1].rows) == 3
    assert len(result.artifacts) == 2
    assert all(a['data'].startswith(b'\x89PNG') for a in result.artifacts)
    assert result.tables[3].rows[0]['mean_delta_pp'] == -100


def test_map_average_denominators_and_missing_coordinates():
    from harness.tools.map_tools import map_statistics, compare_statistics
    stats, cells = map_statistics([{'map_val_json': {'MAP': ['0,0,A', '0,1,B']}}, {'map_val_json': {'MAP': ['0,0,A']}}])
    assert stats['pooled_die_pass_percent'] == pytest.approx(200/3)
    assert stats['mean_coordinate_pass_percent'] == 50
    assert compare_statistics([('a', cells), ('b', cells[:1])])[1]['delta_pp'] is None


def test_lot_cap_is_partial_even_after_filtering(monkeypatch):
    from harness.tools.map_tools import MapInput, _fetch_maps
    monkeypatch.setattr(map_agent, '_query_wafer_data', lambda **kw: [{'wf_id': 1}]*10000)
    records, complete = _fetch_maps(MapInput(lot_ids=['L'], oper='PT1H', wf_ids=[2]))
    assert not complete
    assert records == []


def test_map_bounds_include_every_measurement_coordinate():
    rows = [{'map_val_json': {'MAP': ['0,0,A', '0,1,A']}}, {'map_val_json': {'MAP': ['5,6,B']}}]
    assert map_agent._get_map_bounds(rows) == (0, 5, 0, 6)


def test_cummap_title_distinguishes_measurements_from_unique_wafers(monkeypatch):
    import matplotlib.pyplot as plt
    titles = []
    monkeypatch.setattr(plt, 'savefig', lambda *a, **kw: titles.append(plt.gca().get_title()))
    records = [{'lot_id': 'L', 'wf_id': 1, 'map_val_json': {'MAP': ['0,0,A']}}] * 2
    _, value = map_agent._visualize_cummap_inner(records, 'left_bin', None, None, None, 'PT1H')
    assert value == 100
    assert '2 measurements' in titles[0]
    assert '1 unique wafers' in titles[0]

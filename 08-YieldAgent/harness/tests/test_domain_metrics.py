import importlib.util
import pytest


def metrics():
    assert importlib.util.find_spec('harness.domain_metrics') is not None, 'metric contract is missing'
    from harness import domain_metrics
    return domain_metrics


def test_bin_pass_and_parameter_are_different_metrics():
    m = metrics()
    r = m.wafer_pass_metric({'MAP': ['0,0,A', '0,1,B', '1,0,A']})
    assert r['value'] == pytest.approx(200 / 3)
    assert r['die_count'] == 3
    assert m.metric_metadata('pass_rate')['unit'] == 'percent'
    assert m.metric_metadata('parameter')['unit'] == 'source_value_unit_unspecified'
    assert m.wafer_pass_metric({'MAP': []})['value'] is None


def test_ranking_beyond_preview_and_ties_and_empty_period():
    m = metrics()
    rows = [{'period': 'a', 'wafer_id': str(i), 'value': 90} for i in range(10001)]
    rows += [{'period': 'a', 'wafer_id': 'low1', 'value': 10}, {'period': 'a', 'wafer_id': 'low2', 'value': 10}]
    ranked = m.rank_wafers(rows, ['a', 'empty'])
    assert {r['wafer_id'] for r in ranked if r['rank'] == 1} == {'low1', 'low2'}
    assert ranked[-1]['value'] is None
    assert ranked[-1]['rank'] is None


def test_latest_measurement_retains_timestamp_ties():
    m = metrics()
    rows = [{'lot_id': 'L', 'wf_id': 1, 'end_tm': t, 'v': v} for t, v in [('2026-01-01', 1), ('2026-01-02', 2), ('2026-01-02', 3)]]
    assert [r['v'] for r in m.select_measurements(rows, 'latest_per_wafer')] == [2, 3]
    assert len(m.select_measurements(rows, 'all_measurements')) == 3
    with pytest.raises(ValueError):
        m.select_measurements([{'lot_id': 'L', 'wf_id': 1}], 'latest_per_wafer')

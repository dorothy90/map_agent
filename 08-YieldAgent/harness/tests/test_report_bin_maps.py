import numpy as np

from harness.tools.report_bin_maps import _bin_figure, _count_weeks


def test_bins_respect_test_definitions_dedup_retests_observed_denominators_and_missing_cells():
    def row(test, week, cells, wafer=1):
        return dict(lot_cd='P', lot_id='L', wf_id=wafer, oper=test, end_tm=week, week_index=week,
                    map_val_json={'MAP': cells})

    first = row('A', 0, ['0,0,A,B', '1,0,Q,Q'])
    retest = dict(first, end_tm='later')
    rows = [first, first, retest, row('A', 0, ['0,0,?,?'], wafer=2),
            row('A', 1, ['0,0,?,?']), row('B', 1, ['0,0,A,B'])]
    stats = _count_weeks(rows, ['A', 'B'], {('P', 'A'): {'first': 'A.', 'overlap': '.B'},
                                             ('P', 'B'): {'different': 'AB'}})
    previous, recent = stats['A']
    assert previous.measurements == 3 and previous.duplicates == 1 and previous.unclassified == 2
    assert previous.by_bin == {'first': {(0, 0): 2}}
    assert stats['B'][1].by_bin == {'different': {(0, 0): 1}}
    fig = _bin_figure((previous, recent), ['first'], 'Title', ['before', 'after'])
    prior, current, delta = [trace.z for trace in fig.data]
    np.testing.assert_allclose(prior, [[200/3], [0]])
    np.testing.assert_allclose(current, [[0], [np.nan]], equal_nan=True)
    np.testing.assert_allclose(delta, [[-200/3], [np.nan]], equal_nan=True)
    assert fig.layout.yaxis3.scaleanchor == 'x3'

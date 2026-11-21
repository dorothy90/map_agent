import json

import pytest

from harness.tools.map_aggregate import CummapAggregation, _summarize


def measurement(wafer, cells, group='R', variants=1, **extra):
    return dict(lot_cd='P', oper='T', lot_id='L', wf_id=wafer, start_tm='1', end_tm='2',
                group_id=group, variants=variants, map_val_json=json.dumps({'MAP': cells}), **extra)


def summarize(rows, selected_bins=None):
    spec = CummapAggregation(group_by='recipe', group_oper='OVI', selected_bins=selected_bins or [])
    patterns = {('P', 'T'): {'failure 1': 'AB', 'failure 2': 'CD'}}
    result = _summarize(rows, spec, patterns, 'source-id')
    return result.scope['map_aggregate'], {table.table_id: table.rows for table in result.tables}


def test_aggregates_distinct_measurements_only_and_excludes_unassigned_groups():
    first = measurement(1, ['-2,7,A,B', '0,0,?,?'])
    second = measurement(2, ['-2,7,?,?'])
    retest = dict(first, end_tm='3')
    rows = [first, first, second, retest, measurement(3, ['0,0,?,?'], None, 0),
            measurement(4, ['0,0,?,?'], 'R', 2)]
    scope, tables = summarize(rows)
    assert (scope['wafer_count'], scope['measurement_count'], scope['included_wafer_count'],
            scope['included_measurement_count']) == (4, 5, 2, 3)
    assert (scope['missing_group_measurements'], scope['conflicting_group_measurements'],
            scope['duplicate_measurement_count']) == (1, 1, 1)
    assert len(tables['wafer_map_members']) == 3
    assert 'wafer_map_dies' not in tables
    assert [(r['row'], r['col'], r['observation_count'], r['fail_count']) for r in tables['wafer_map_cells']] == [
        (-2, 7, 3, 2), (0, 0, 2, 0)]


def test_selected_bin_changes_only_the_numerator():
    rows = [measurement(i, [f'1,2,{code[0]},{code[1]}']) for i, code in enumerate(['AB', 'CD', '??'])]
    _, tables = summarize(rows, ['CD'])
    assert tables['wafer_map_cells'][0]['fail_rate'] == 1/3
    _, absent = summarize(rows, ['QQ'])
    assert absent['wafer_map_cells'][0]['fail_count'] == 0
    with pytest.raises(ValueError, match='bin'):
        summarize([measurement(1, ['1,2,Q,Q'])])


def test_schema_disallows_ungrounded_or_inapplicable_process_dimension():
    with pytest.raises(ValueError):
        CummapAggregation(group_by='equipment')
    with pytest.raises(ValueError):
        CummapAggregation(group_by='week', group_oper='old operation')
    with pytest.raises(ValueError):
        CummapAggregation(group_by='RCP GROUP BY SQL')

def test_reports_partial_scope_for_missing_requested_lot_wafer_pair():
    rows = [measurement(1, ['1,2,?,?']), dict(measurement(2, ['1,2,?,?']), lot_id='M')]
    result = _summarize(rows, CummapAggregation(), {}, 'source-id', lot_ids=['L', 'M'], wf_ids=[1, 2])
    assert result.status == 'partial'
    assert result.scope['map_aggregate']['source_exhausted']
    assert result.scope['map_aggregate']['missing_requests']['lot_wafers'] == [['L', 2], ['M', 1]]

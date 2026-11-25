import asyncio
import json
import sys

import pytest

from harness.native_verification import validate_wafer_map_contract, normalize_wafer_map_tables
from harness.runtime.process import ProcessRuntime


def source_rows(mode):
    members = [('FIXTURE-A', wf) for wf in (7, 8, 9)] if mode == 'wafer' else [
        (lot, wf) for lot in ('FIXTURE-A', 'FIXTURE-B', 'FIXTURE-C') for wf in range(1, 26)]
    rows = []
    for index, (lot, wafer) in enumerate(members):
        for row, col in ((-1, 0), (0, -1), (0, 0), (0, 1), (1, 0)):
            if (row, col) == (1, 0) and index % 2 == 0:
                continue
            bin_name = ('AA', 'BB', 'PASS')[(index + row + col) % 3]
            rows.append(dict(lot_id=lot, wf_id=wafer, measurement_id=f'{lot}-{wafer}',
                row=row, col=col, bin=bin_name,
                fail_bin_name=None if bin_name == 'PASS' else bin_name))
    return rows


def analysis_code(mode, selected):
    bins = json.dumps(['AA'] if selected else [])
    return f'''
import json
from plotly.subplots import make_subplots
frame = tables['source']['wafer_map_dies'].copy()
frame['group_id'] = {'"selected"' if mode == 'wafer' else "frame['lot_id']"}
groups = list(frame.group_id.unique())
manifests, members, cells = [], [], []
figure = make_subplots(rows=1, cols=len(groups), subplot_titles=groups)
for panel, group in enumerate(groups, 1):
    part = frame[frame.group_id == group].copy()
    part['failure'] = {"part.bin.eq('AA')" if selected else 'part.fail_bin_name.notna()'}
    unique = part[['lot_id', 'wf_id', 'measurement_id']].drop_duplicates()
    manifests.append(dict(contract_version='wafer-map-evidence/v1', map_type='cummap',
        selection_id='selection', grouping_mode={'"selection_set"' if mode == 'wafer' else '"explicit_groups"'},
        group_id=group, member_count=len(unique), measurement_policy='all selected measurements',
        numerator_mode={'"selected_bins"' if selected else '"defined_fail_bins"'},
        numerator_bins_json={bins!r}, source_result_ids_json='["source"]'))
    for member in unique.to_dict('records'):
        members.append(dict(contract_version='wafer-map-evidence/v1', selection_id='selection',
            group_id=group, source_result_id='source', **member))
    counts = part.groupby(['row', 'col']).failure.agg(['sum', 'count'])
    z = [[None for _ in range(3)] for _ in range(3)]
    for row, col in frame[['row', 'col']].drop_duplicates().itertuples(index=False, name=None):
        fail, observed = (int(counts.loc[(row, col), 'sum']), int(counts.loc[(row, col), 'count'])) if (row, col) in counts.index else (0, 0)
        rate = fail / observed if observed else None
        cells.append(dict(contract_version='wafer-map-evidence/v1', selection_id='selection',
            group_id=group, row=row, col=col, fail_count=fail, observation_count=observed, fail_rate=rate))
        z[row + 1][col + 1] = rate
    figure.add_trace(go.Heatmap(x=[-1, 0, 1], y=[-1, 0, 1], z=z,
        zmin=0, zmax=1, colorscale='Viridis', showscale=panel == len(groups)), row=1, col=panel)
    x_axis = 'x' if panel == 1 else f'x{{panel}}'
    figure.update_yaxes(scaleanchor=x_axis, scaleratio=1, row=1, col=panel)
figure.update_layout(autosize=True, height=440, title='Synthetic fixture: {mode} cumulative maps')
emit_table(pd.DataFrame(manifests))
emit_table(pd.DataFrame(members))
emit_table(pd.DataFrame(cells))
emit_plot(figure)
print(figure.to_json())
'''


async def run_case(mode, selected):
    rows = source_rows(mode)
    result = await ProcessRuntime(sys.executable).execute(
        analysis_code(mode, selected), {'source': {'wafer_map_dies': rows}})
    return rows, result


@pytest.mark.parametrize('mode,selected', [('wafer', False), ('wafer', True), ('lot', False), ('lot', True)])
def test_cummap_unnamed_evidence_recovers_without_changing_cells_or_panel_axes(mode, selected):
    rows, result = asyncio.run(run_case(mode, selected))
    assert result['status'] == 'success', result
    tables, renamed = normalize_wafer_map_tables({table['table_id']: table['rows'] for table in result['tables']})
    assert set(renamed) == {'table_1', 'table_2', 'table_3'}
    assert set(tables) == {'wafer_map_manifest', 'wafer_map_members', 'wafer_map_cells'}
    assert validate_wafer_map_contract(tables, {'source': rows})['verdict'] == 'pass'
    member_counts = [row['member_count'] for row in tables['wafer_map_manifest']]
    assert member_counts == ([3] if mode == 'wafer' else [25, 25, 25])
    html = result['plots'][0]
    assert 'Plotly.newPlot' in html
    start = html.rindex('Plotly.newPlot(')
    decoder = json.JSONDecoder()
    rest = html[start + len('Plotly.newPlot('):].lstrip()
    _, offset = decoder.raw_decode(rest)
    rest = rest[offset:].lstrip().removeprefix(',').lstrip()
    traces, offset = decoder.raw_decode(rest)
    rest = rest[offset:].lstrip().removeprefix(',').lstrip()
    layout, _ = decoder.raw_decode(rest)
    for panel, manifest in enumerate(tables['wafer_map_manifest'], 1):
        group = manifest['group_id']
        group_rows = rows if mode == 'wafer' else [r for r in rows if r['lot_id'] == group]
        expected = {}
        for row in group_rows:
            key = row['row'], row['col']
            counts = expected.setdefault(key, [0, 0])
            counts[0] += int(row['bin'] == 'AA' if selected else row['fail_bin_name'] is not None)
            counts[1] += 1
        for cell in [cell for cell in tables['wafer_map_cells'] if cell['group_id'] == group]:
            numerator, denominator = expected[(cell['row'], cell['col'])]
            assert cell['fail_count'] == numerator
            assert cell['observation_count'] == denominator
            assert cell['fail_rate'] == pytest.approx(numerator / denominator)
            assert traces[panel - 1]['z'][cell['row'] + 1][cell['col'] + 1] == pytest.approx(cell['fail_rate'])
        suffix = '' if panel == 1 else str(panel)
        assert layout[f'yaxis{suffix}']['scaleanchor'] == f'x{suffix}'
        assert layout[f'yaxis{suffix}']['scaleratio'] == 1
        assert traces[panel - 1]['z'][0][0] is None
        assert expected[(1, 0)][1] < len({r['measurement_id'] for r in group_rows})

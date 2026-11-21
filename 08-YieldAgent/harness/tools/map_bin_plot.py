"""Render individual MAP measurements with their original coordinates and bin symbols."""
from collections import defaultdict
from html import escape
from itertools import groupby
import json
from operator import itemgetter

from plotly import graph_objects as go
from plotly.subplots import make_subplots

from bin_mapping import PASS_BIN
from ..native_verification import WAFER_MAP_CONTRACT_VERSION
from ..types import ResultTable
from .map_aggregate_plot import _coordinate_axis


def _trace(dies, label):
    x = _coordinate_axis(die['col'] for die in dies)
    y = _coordinate_axis(die['row'] for die in dies)
    xi = {coordinate: i for i, coordinate in enumerate(x)}
    yi = {coordinate: i for i, coordinate in enumerate(y)}
    z = [[None] * len(x) for _ in y]
    bins = [[None] * len(x) for _ in y]
    for die in dies:
        row, col = yi[die['row']], xi[die['col']]
        left, right = die['left_bin'], die['right_bin']
        z[row][col] = int(left != PASS_BIN) + 2 * int(right != PASS_BIN)
        bins[row][col] = [left, right]
    meta = {k: dies[0][k] for k in ('lot_id', 'wf_id', 'oper', 'measurement_id', 'start_tm', 'end_tm')}
    return go.Heatmap(x=x, y=y, z=z, customdata=bins, meta=dict(label=label, **meta),
        coloraxis='coloraxis', hoverongaps=False,
        hovertemplate='행 %{y} · 열 %{x}<br>좌측 BIN %{customdata[0]} · 우측 BIN %{customdata[1]}<extra></extra>')


def grouped_binmaps(die_rows, groups, source_id):
    measurements = defaultdict(list)
    for _, rows in groupby(die_rows, key=itemgetter('measurement_id')):
        dies = list(rows)
        measurements[dies[0]['lot_id'], dies[0]['wf_id']].append(dies)
    panels = {}
    for group in groups:
        selected = panels.setdefault(group['label'], {})
        for wf in group['wf_ids']:
            for dies in measurements[group['lot_id'], wf]:
                selected[dies[0]['measurement_id']] = dies

    columns = min(2, max(len(selected) for selected in panels.values()))
    titles, traces, members, manifest = [], [], [], []
    for label, selected in panels.items():
        if not selected:
            continue
        evidence = dict(contract_version=WAFER_MAP_CONTRACT_VERSION, selection_id=label, group_id=label)
        for identity, dies in selected.items():
            d = dies[0]
            position = len(titles)
            traces.append((position // columns + 1, position % columns + 1, _trace(dies, label)))
            title = f"{escape(label)} · {escape(d['lot_id'])} #{d['wf_id']}"
            if len(measurements[d['lot_id'], d['wf_id']]) > 1:
                title += f"<br>{escape(str(d['oper']))} · {escape(str(d['end_tm']))}"
            titles.append(title)
            members.append(dict(**evidence, source_result_id=source_id, lot_id=d['lot_id'],
                                wf_id=d['wf_id'], measurement_id=identity))
        if len(titles) % columns:
            titles.append('')
        manifest.append(dict(**evidence, map_type='binmap', grouping_mode='selection_set',
            member_count=len(selected), measurement_policy='All distinct MAP measurements, including retests',
            numerator_mode='defined_fail_bins', numerator_bins_json='[]', source_result_ids_json=json.dumps([source_id])))
    rows = len(titles) // columns
    figure = make_subplots(rows=rows, cols=columns, subplot_titles=titles, vertical_spacing=0.3 / rows)
    for row, col, trace in traces:
        figure.add_trace(trace, row=row, col=col)
    colors = ['#c7d8ce', '#e69443', '#697bd1', '#b14559']
    scale = [(position, color) for i, color in enumerate(colors) for position in (i/4, (i+1)/4)]
    figure.update_xaxes(constrain='domain', showgrid=False, zeroline=False)
    figure.for_each_yaxis(lambda axis: axis.update(scaleanchor=axis.anchor, scaleratio=1,
        autorange='reversed', constrain='domain', showgrid=False, zeroline=False))
    operations = list(dict.fromkeys(d['oper'] for d in die_rows if d['oper']))
    title = f"{' · '.join(operations)} 웨이퍼 맵".strip()
    figure.update_layout(title=escape(title), template='plotly_white', autosize=True, height=120 + rows * 320,
        margin=dict(l=40, r=30, t=95, b=35), coloraxis=dict(cmin=-0.5, cmax=3.5, colorscale=scale,
            colorbar=dict(tickvals=[0, 1, 2, 3], ticktext=['통과', '좌측 불량', '우측 불량', '양측 불량'],
                          thickness=12, len=min(1, 0.9 / rows))))
    report_height = """document.body.style.margin = '0';
        const height = document.getElementById('{plot_id}').offsetHeight;
        window.parent.postMessage({type: 'yield-table-height', height}, '*');"""
    artifact = dict(title=title, mime='text/html', data=figure.to_html(include_plotlyjs=True, full_html=True,
        config={'responsive': True, 'displaylogo': False}, post_script=report_height))
    tables = [ResultTable(table_id=name, title=name, rows=records, complete=True)
              for name, records in [('wafer_map_members', members), ('wafer_map_manifest', manifest)]]
    return artifact, tables

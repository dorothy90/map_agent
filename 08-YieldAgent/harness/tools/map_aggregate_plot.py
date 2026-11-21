"""Render each cumulative map directly from its persisted coordinate counts."""
from collections import defaultdict
from html import escape

from plotly import graph_objects as go
from plotly.subplots import make_subplots


def _coordinate_axis(values):
    occupied = sorted(set(values))
    # Leave holes transparent without expanding a large gap into every integer.
    edges = {edge for a, b in zip(occupied, occupied[1:]) if b - a > 1 for edge in (a + 1, b - 1)}
    return sorted(set(occupied) | edges)


def _heatmap_data(panel):
    x = _coordinate_axis(cell['col'] for cell in panel)
    y = _coordinate_axis(cell['row'] for cell in panel)
    xi = {value: i for i, value in enumerate(x)}
    yi = {value: i for i, value in enumerate(y)}
    rates = [[None] * len(x) for _ in y]
    counts = [[None] * len(x) for _ in y]
    for cell in panel:
        r, c = yi[cell['row']], xi[cell['col']]
        rates[r][c] = cell['fail_rate']
        counts[r][c] = [cell['fail_count'], cell['observation_count']]
    return dict(x=x, y=y, z=rates, customdata=counts)


def cummap_artifact(cells, groups):
    panels = defaultdict(list)
    for cell in cells:
        panels[cell['group_id']].append(cell)
    if not panels or not groups:
        raise ValueError('A cumulative map requires at least one observed coordinate.')
    # Keep a large collection of groups on one interactive axis pair.
    switchable = len(groups) > 9
    display_count = 1 if switchable else len(groups)
    columns = min(display_count, 3)
    rows = (display_count + columns - 1) // columns
    titles = [f"{escape(str(g['group_id']))} · {g['measurement_count']:,}개 측정" for g in groups]
    figure = make_subplots(rows=rows, cols=columns, subplot_titles=titles[:display_count])
    choices = []
    for index, group in enumerate(groups):
        data = _heatmap_data(panels[group['group_id']])
        if index < display_count:
            figure.add_trace(go.Heatmap(**data, coloraxis='coloraxis', hoverongaps=False,
                hovertemplate='행 %{y} · 열 %{x}<br>불량 %{customdata[0]} / 관측 %{customdata[1]}'
                              '<br>불량률 %{z:.2%}<extra></extra>'),
                row=index // columns + 1, col=index % columns + 1)
        if switchable:
            choices.append(dict(label=escape(str(group['group_id'])), method='update',
                args=[{key: [values] for key, values in data.items()}, {'annotations[0].text': titles[index]}]))
    if switchable:
        figure.update_layout(updatemenus=[dict(buttons=choices, x=0, y=1.18, xanchor='left', yanchor='bottom')])
    figure.update_xaxes(constrain='domain', showgrid=False, zeroline=False)
    figure.for_each_yaxis(lambda axis: axis.update(scaleanchor=axis.anchor, scaleratio=1,
        autorange='reversed', constrain='domain', showgrid=False, zeroline=False))
    title = '웨이퍼 누적 불량 맵'
    figure.update_layout(title=title, template='plotly_white', autosize=True, height=160 + 310 * rows,
        margin=dict(l=35, r=25, t=115 if switchable else 85, b=40),
        coloraxis=dict(colorscale='Viridis', cmin=0, cmax=max(c['fail_rate'] for c in cells) or 1,
            colorbar=dict(title='불량률', tickformat='.0%', thickness=12)))
    return {'title': title, 'mime': 'text/html',
            'data': figure.to_html(include_plotlyjs=True, full_html=True,
                                   config={'responsive': True, 'displaylogo': False})}

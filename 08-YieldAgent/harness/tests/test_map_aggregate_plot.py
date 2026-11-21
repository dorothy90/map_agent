import json

from harness.tools.map_aggregate_plot import cummap_artifact


def _plot(html):
    chart = html[html.rindex('Plotly.newPlot(') + len('Plotly.newPlot('):]
    decoder = json.JSONDecoder()
    args = []
    for _ in range(3):
        value, end = decoder.raw_decode(chart.lstrip())
        chart = chart.lstrip()[end:].lstrip().removeprefix(',')
        args.append(value)
    return args[1:]


def test_cummap_panels_share_scale_and_preserve_observations():
    groups = [{'group_id': 'group<&' + str(i), 'measurement_count': i + 1} for i in range(4)]
    cells = []
    for group in groups:
        for row, col, fails, observations in [(-1, 4, 0, 1), (1, 6, 1, 2)]:
            cells.append(dict(group_id=group['group_id'], row=row, col=col, fail_count=fails,
                              observation_count=observations, fail_rate=fails / observations))
    artifact = cummap_artifact(cells, groups)
    assert artifact['mime'] == 'text/html'
    traces, layout = _plot(artifact['data'])
    assert len(traces) == len(groups)
    for i, trace in enumerate(traces):
        x, y = trace['x'], trace['y']
        assert trace['z'][y.index(-1)][x.index(4)] == 0
        assert trace['z'][y.index(1)][x.index(6)] == 0.5
        assert trace['z'][y.index(0)][x.index(5)] is None
        assert trace['customdata'][y.index(1)][x.index(6)] == [1, 2]
        assert trace['coloraxis'] == 'coloraxis'
        suffix = '' if i == 0 else str(i + 1)
        assert layout['yaxis' + suffix]['scaleanchor'] == 'x' + suffix
        assert layout['yaxis' + suffix]['scaleratio'] == 1
        assert layout['yaxis' + suffix]['autorange'] == 'reversed'
    assert layout['coloraxis']['cmin'] == 0
    assert layout['coloraxis']['cmax'] == 0.5
    assert all('group&lt;&amp;' in ann['text'] for ann in layout['annotations'])


def test_many_groups_remain_selectable_without_creating_axes_for_every_group():
    groups = [{'group_id': f'lot-{i}', 'measurement_count': 1} for i in range(80)]
    cells = [dict(group_id=g['group_id'], row=i, col=3, fail_count=i % 2,
                  observation_count=1, fail_rate=i % 2) for i, g in enumerate(groups)]
    traces, layout = _plot(cummap_artifact(cells, groups)['data'])
    assert len(traces) == 1
    buttons = layout['updatemenus'][0]['buttons']
    assert len(buttons) == len(groups)
    assert buttons[-1]['label'] == 'lot-79'
    assert buttons[-1]['args'][0]['y'] == [[79]]
    assert buttons[-1]['args'][0]['z'] == [[[1]]]

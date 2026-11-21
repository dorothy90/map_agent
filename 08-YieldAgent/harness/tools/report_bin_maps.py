"""Per-test, weekly BIN occurrence and change from a single scoped MAP cursor."""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from html import escape
import re

import numpy as np
from plotly import graph_objects as go
from plotly.subplots import make_subplots

from bin_mapping import PASS_BIN
from .load_wafer_maps import _configure_cursor, _connection, _load_symbol_patterns, _measurement_id, _parse_payload
from .map_aggregate_plot import _coordinate_axis


@dataclass
class WeekCounts:
    observations: Counter = field(default_factory=Counter)
    by_bin: dict = field(default_factory=lambda: defaultdict(Counter))
    measurements: int = 0
    unclassified: int = 0
    duplicates: int = 0


def _count_weeks(rows, tests, patterns):
    by_test = {test: (WeekCounts(), WeekCounts()) for test in tests}
    rules = {key: [(name, re.compile(expr)) for name, expr in definitions.items()]
             for key, definitions in patterns.items()}

    @lru_cache(maxsize=4096)
    def fail_bin(product, test, symbol):
        return next((name for name, rule in rules.get((product, test), ()) if rule.fullmatch(symbol)), None)

    seen = set()
    for row in rows:
        week = by_test[row['oper']][row['week_index']]
        canonical, cells = _parse_payload(row['map_val_json'])
        identity = _measurement_id(row, canonical)
        if identity in seen:
            week.duplicates += 1
            continue
        seen.add(identity)
        week.measurements += 1
        for y, x, left, right in cells:
            coord = y, x
            week.observations[coord] += 1
            symbol = left + right
            if symbol == PASS_BIN * 2:
                continue
            name = fail_bin(row['lot_cd'], row['oper'], symbol)
            if name is None:
                week.unclassified += 1
            else:
                week.by_bin[name][coord] += 1
    return by_test


def _bin_figure(weeks, bins, title, week_labels):
    coords = weeks[0].observations.keys() | weeks[1].observations.keys()
    xs = _coordinate_axis(x for y, x in coords)
    ys = _coordinate_axis(y for y, x in coords)
    xindex = {x: i for i, x in enumerate(xs)}
    yindex = {y: i for i, y in enumerate(ys)}
    shape = len(ys), len(xs)
    denominators = []
    for week in weeks:
        counts = np.full(shape, np.nan)
        for (y, x), total in week.observations.items():
            counts[yindex[y], xindex[x]] = total
        denominators.append(counts)

    fig = make_subplots(rows=3, cols=len(bins), column_titles=[escape(b) for b in bins],
                        horizontal_spacing=0.012, vertical_spacing=0.07)
    max_rate = max_delta = 0.0
    for col, name in enumerate(bins, start=1):
        rates = []
        for row, (week, denominator) in enumerate(zip(weeks, denominators), start=1):
            numerator = np.zeros(shape, dtype=np.int32)
            for (y, x), n in week.by_bin.get(name, {}).items():
                numerator[yindex[y], xindex[x]] = n
            rate = numerator / denominator * 100
            rates.append(rate)
            max_rate = max(max_rate, rate[np.isfinite(rate)].max(initial=0))
            fig.add_trace(go.Heatmap(x=xs, y=ys, z=rate, coloraxis='coloraxis', hoverongaps=False,
                customdata=np.stack([numerator, denominator], axis=-1),
                hovertemplate=escape(name) + ' · %{x}, %{y}<br>불량 %{customdata[0]} / 관측 %{customdata[1]}'
                              '<br>%{z:.2f}%<extra></extra>'), row=row, col=col)
        delta = rates[1] - rates[0]
        max_delta = max(max_delta, np.abs(delta[np.isfinite(delta)]).max(initial=0))
        fig.add_trace(go.Heatmap(x=xs, y=ys, z=delta, coloraxis='coloraxis2', hoverongaps=False,
            hovertemplate=escape(name) + ' · %{x}, %{y}<br>변화 %{z:+.2f}%p<extra></extra>'), row=3, col=col)
    fig.update_xaxes(visible=False, constrain='domain')
    fig.for_each_yaxis(lambda axis: axis.update(visible=False, autorange='reversed', constrain='domain',
                                                scaleanchor=axis.anchor, scaleratio=1))
    fig.update_annotations(font_size=11)
    for row, label in enumerate([*week_labels, '델타 (%p)']):
        axis_no = row * len(bins) + 1
        axis_name = 'yaxis' if axis_no == 1 else f'yaxis{axis_no}'
        y0, y1 = fig.layout[axis_name].domain
        fig.add_annotation(x=0, y=(y0+y1)/2, xref='paper', yref='paper', text=label,
                           xanchor='right', xshift=-6, showarrow=False, font_size=11)
    fig.update_layout(title=escape(title), template='plotly_white', height=550, autosize=True,
        margin=dict(l=76, r=62, t=86, b=22), coloraxis=dict(colorscale='Blues', cmin=0, cmax=max_rate or 1,
            colorbar=dict(title='발생률 %', thickness=9, len=0.56, y=0.73, tickfont_size=10)),
        coloraxis2=dict(colorscale='RdBu_r', cmin=-(max_delta or 1), cmax=max_delta or 1,
            colorbar=dict(title='Δ %p', thickness=9, len=0.30, y=0.17, tickfont_size=10)))
    return fig


def report_bin_maps(lot_cd, tests, start, current_start, end, top_n=10):
    if not tests or not start < current_start < end or top_n < 1:
        raise ValueError('BIN comparison requires tests, two adjacent windows and a positive BIN count.')
    binds = {f'test_{i}': test for i, test in enumerate(tests)}
    slots = ', '.join(f':{key}' for key in binds)
    binds.update(lot_cd=lot_cd, start_tm=start, current_tm=current_start, end_tm=end)
    with _connection() as connection, connection.cursor() as cursor:
        _configure_cursor(cursor)
        patterns = _load_symbol_patterns(cursor, [dict(lot_cd=lot_cd, oper=test) for test in tests])
        cursor.execute(f"""SELECT LOT_CD, LOT_ID, WF_ID, OPER_DET_DESC AS oper, START_TM, END_TM,
            CASE WHEN END_TM < :current_tm THEN 0 ELSE 1 END AS week_index, MAP_VAL_JSON
            FROM MAP WHERE LOT_CD = :lot_cd AND OPER_DET_DESC IN ({slots})
            AND END_TM >= :start_tm AND END_TM < :end_tm""", binds)
        columns = [col[0].lower() for col in cursor.description]
        by_test = _count_weeks((dict(zip(columns, values)) for values in cursor), tests, patterns)
    artifacts, facts = [], {}
    labels = [f"직전 주<br>{start.strftime('%G-W%V')}", f"최근 주<br>{current_start.strftime('%G-W%V')}"]
    for test, weeks in by_test.items():
        totals = Counter()
        for week in weeks:
            totals.update({name: sum(counts.values()) for name, counts in week.by_bin.items()})
        bins = sorted(totals, key=lambda name: (-totals[name], name))[:top_n]
        facts[test] = dict(bins=bins, occurrences={name: totals[name] for name in bins},
            measurements=[w.measurements for w in weeks], observations=[sum(w.observations.values()) for w in weeks],
            duplicates=[w.duplicates for w in weeks], unclassified=[w.unclassified for w in weeks],
            panel_count=3 * len(bins))
        if bins:
            title = f'{lot_cd} · {test} · 주차별 BIN CUMMAP 및 델타'
            figure = _bin_figure(weeks, bins, title, labels)
            artifacts.append(dict(title=title, mime='text/html', data=figure.to_html(
                full_html=True, include_plotlyjs=True, config={'responsive': True, 'displaylogo': False})))
    return artifacts, facts

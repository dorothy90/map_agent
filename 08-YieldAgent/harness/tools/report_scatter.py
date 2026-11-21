"""Rank two-week changes using the same scoped wafer observations shown on the plot."""
from datetime import datetime, time
from html import escape

from plotly import graph_objects as go
from plotly.subplots import make_subplots

from ..types import ResultTable
from .yield_report_data import _latest_test_source, _report_cursor


def _scatter_rows(lot_cd, operations, start, split, end, top_n):
    binds = {f'op{i}': oper for i, oper in enumerate(operations)}
    slots = ', '.join(f':{key}' for key in binds)
    binds.update(lot=lot_cd, win_start=start, week_split=split, win_end=end, top_n=top_n)
    sql = f"""
        WITH observations AS (
            SELECT w.PROCESS, w.PARAM_NM, m.UNIT, m.BETTER_WHEN, w.LOT_ID, w.WFID,
                   w.MEASURETIME_START, w.VALUE,
                   CASE WHEN w.MEASURETIME_START < :week_split THEN 0 ELSE 1 END recent
            FROM {_latest_test_source()} w
            JOIN DF_PARAM_META m ON m.SOURCE_ID='wafer_parameter' AND m.LOT_CD=w.LOT_CD
                AND m.PROCESS||' TEST'=w.PROCESS AND m.PARAM_NM=w.PARAM_NM
            WHERE w.PROCESS IN ({slots}) AND w.MEASURETIME_START >= :win_start
                AND w.MEASURETIME_START < :win_end AND w.VALUE IS NOT NULL
                AND w.LOT_ID IS NOT NULL AND w.WFID IS NOT NULL
                AND m.BETTER_WHEN IN ('higher','lower')
        ), weekly AS (
            SELECT PROCESS, PARAM_NM, UNIT, BETTER_WHEN,
                   AVG(CASE WHEN recent=0 THEN VALUE END) previous_mean,
                   AVG(CASE WHEN recent=1 THEN VALUE END) recent_mean,
                   COUNT(CASE WHEN recent=0 THEN VALUE END) previous_count,
                   COUNT(CASE WHEN recent=1 THEN VALUE END) recent_count
            FROM observations GROUP BY PROCESS, PARAM_NM, UNIT, BETTER_WHEN
        ), scores AS (
            SELECT weekly.*, (CASE WHEN BETTER_WHEN='higher' THEN 1 ELSE -1 END)
                * 200 * (recent_mean-previous_mean)
                / NULLIF(ABS(previous_mean)+ABS(recent_mean), 0) score
            FROM weekly WHERE previous_count>0 AND recent_count>0
        ), ranked AS (
            SELECT scores.*, ROW_NUMBER() OVER (
                PARTITION BY CASE WHEN score<0 THEN 0 ELSE 1 END
                ORDER BY ABS(score) DESC, PROCESS, PARAM_NM) position_in_group
            FROM scores WHERE score<>0
        )
        SELECT r.PROCESS, r.PARAM_NM, r.UNIT, r.BETTER_WHEN, r.previous_mean, r.recent_mean,
               r.previous_count, r.recent_count, r.score,
               o.recent, o.LOT_ID, o.WFID, o.MEASURETIME_START, o.VALUE
        FROM ranked r JOIN observations o ON o.PROCESS=r.PROCESS AND o.PARAM_NM=r.PARAM_NM
        WHERE r.position_in_group<=:top_n
        ORDER BY CASE WHEN r.score<0 THEN 0 ELSE 1 END, r.position_in_group, o.MEASURETIME_START,
                 o.LOT_ID, o.WFID
    """
    with _report_cursor() as cursor:
        cursor.arraysize = 1000
        cursor.execute(sql, binds)
        return cursor.fetchall()


def _plot(lot_cd, panels, coverage_note):
    columns = min(len(panels), 2)
    row_count = (len(panels) + columns - 1) // columns
    titles = []
    for rank, _ in panels.values():
        description = '열화' if rank['score'] < 0 else '개선'
        titles.append(f"{description} · {escape(rank['process'])} · {escape(rank['parameter'])}"
                      f"<br><sup>직전 {rank['previous_count']:,}장 · 최근 {rank['recent_count']:,}장</sup>")
    fig = make_subplots(rows=row_count, cols=columns, subplot_titles=titles,
                        vertical_spacing=min(0.17, 0.48 / row_count))
    for i, (rank, points) in enumerate(panels.values()):
        row, col = i // columns + 1, i % columns + 1
        fraction = rank['unit'] == 'fraction'
        fig.update_yaxes(title_text='측정값 (%)' if fraction else escape(rank['unit']),
                         tickformat='.1%' if fraction else None, row=row, col=col)
        for week, label, color in [('previous', '직전 주', '#4c6a86'), ('recent', '최근 주', '#c25122')]:
            data = [p for p in points if p['week'] == week]
            value_format = '%{y:.3%}' if fraction else '%{y}'
            fig.add_trace(go.Scatter(name=label, legendgroup=week, showlegend=i == 0,
                x=[p['measured_at'] for p in data], y=[p['value'] for p in data],
                customdata=[[p['lot_id'], p['wf_id']] for p in data],
                marker=dict(color=color, size=5, opacity=0.68), mode='markers',
                hovertemplate='LOT %{customdata[0]} · WF %{customdata[1]}<br>%{x|%Y-%m-%d %H:%M:%S}'
                              '<br>측정값 ' + value_format + '<extra></extra>'), row=row, col=col)
    title = f'{lot_cd} · 웨이퍼별 열화·개선 산점도'
    if coverage_note:
        fig.add_annotation(text=escape(coverage_note), xref='paper', yref='paper', x=0, y=1,
                           yshift=44, xanchor='left', showarrow=False, font=dict(size=11))
    fig.update_layout(title=escape(title), template='plotly_white', autosize=True, height=180+295*row_count,
        margin=dict(l=65, r=30, t=110, b=65), legend=dict(orientation='h', y=0, yanchor='bottom', yref='container'), hovermode='closest')
    return {'title': title, 'mime': 'text/html', 'data': fig.to_html(
        include_plotlyjs=True, full_html=True, config={'responsive': True, 'displaylogo': False})}


def build_report_scatter(lot_cd, operations, start, split, end, *, top_n=3):
    """Use half-open local measurement times. Keep per-test parameter identities throughout."""
    def local_boundary(value):
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        return datetime.combine(value, time.min)

    start, split, end = map(local_boundary, (start, split, end))
    if not operations or not start < split < end or top_n < 1:
        raise ValueError('Scatter requires a test scope, two ordered intervals and a positive rank count.')
    panels = {}
    for (process, param, unit, better, previous, recent, previous_n, recent_n, score, week, lot,
         wafer, measured, value) in _scatter_rows(lot_cd, operations, start, split, end, top_n):
        key = process, param
        if key not in panels:
            panels[key] = (dict(process=process, parameter=param, unit=unit, better_when=better,
                direction='deteriorated' if score < 0 else 'improved', score=float(score),
                previous_mean=float(previous), recent_mean=float(recent),
                previous_count=int(previous_n), recent_count=int(recent_n)), [])
        panels[key][1].append(dict(process=process, parameter=param, lot_id=lot, wf_id=wafer,
            measured_at=measured.isoformat() if isinstance(measured, datetime) else str(measured),
            value=float(value), week='recent' if week else 'previous'))
    rankings = [rank for rank, _ in panels.values()]
    points = [point for _, values in panels.values() for point in values]
    tables = [ResultTable(table_id='wafer_scatter_rankings', title='웨이퍼 파라미터 주간 변화',
                          rows=rankings, complete=True),
              ResultTable(table_id='wafer_scatter_points', title='산점도 웨이퍼 측정값',
                          rows=points, complete=True)]
    negatives = sum(rank['score'] < 0 for rank in rankings)
    positives = len(rankings) - negatives
    missing = [f'{label} {count}/{top_n}개' for label, count in [('열화', negatives), ('개선', positives)]
               if count < top_n]
    note = ' · '.join(missing) + ' — 두 주 모두 측정값이 있고 선호 방향에 따른 변화가 있는 항목만 표시.' if missing else ''
    return ([_plot(lot_cd, panels, note)] if panels else [], tables,
            dict(lot_cd=lot_cd, operations=list(operations), start=start.isoformat(), split=split.isoformat(),
                 end_exclusive=end.isoformat(), deteriorated=negatives, improved=positives,
                 coverage_note=note,
                 point_count=len(points), requested_per_direction=top_n,
                 ranking='두 주 모두 유효한 웨이퍼 관측과 선호 방향이 있는 항목. 주 평균 차이를 두 평균 절댓값의 '
                         '평균으로 나눈 대칭 변화율(%)에 선호 방향을 적용해 열화·개선 순위 산정. 변화 0은 제외.',
                 retest_policy='제품·LOT·웨이퍼·검사별 최신 측정 시작/종료 시각 기준'))

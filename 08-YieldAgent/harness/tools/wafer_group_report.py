"""Present a complete single-lot measurement table and its threshold groups together."""
from decimal import Decimal, InvalidOperation
from html import escape


def wafer_group_report(rows, threshold_pct):
    columns = ('LOT_ID', 'PARAM_NM', 'WFID', 'VALUE')
    if not rows or any(any(row.get(key) in (None, '') for key in columns) for row in rows):
        raise ValueError('The report needs complete LOT_ID, PARAM_NM, WFID and VALUE columns.')
    lots = {row['LOT_ID'] for row in rows}
    metrics = {row['PARAM_NM'] for row in rows}
    if len(lots) != 1 or len(metrics) != 1:
        raise ValueError('Select one lot and one yield metric for this report.')
    title = f"{next(iter(lots))} · {next(iter(metrics))} 수율"
    measured = {}
    for row in rows:
        wafer = str(row['WFID'])
        if wafer in measured:
            raise ValueError('Repeated WFID: resolve retests before rendering one value per wafer.')
        try:
            value = Decimal(str(row['VALUE']))
        except InvalidOperation as exc:
            raise ValueError('VALUE must be a yield fraction between 0 and 1.') from exc
        if not value.is_finite() or not 0 <= value <= 1:
            raise ValueError('VALUE must be a yield fraction between 0 and 1.')
        measured[wafer] = value * 100

    threshold = Decimal(str(threshold_pct))
    members = {False: [], True: []}
    detail = []
    for wafer in sorted(measured, key=lambda w: (0, int(w), w) if w.isdecimal() else (1, 0, w)):
        value = measured[wafer]
        members[value >= threshold].append(wafer)
        detail.append(f'<tr><td>{escape(wafer)}</td><td>{value:.1f}</td></tr>')

    summary, groups = {}, []
    display_threshold = format(threshold.normalize(), 'f')
    for above, wafers in members.items():
        label = f"{threshold:g}% {'이상' if above else '미만'}"
        average = (f'{sum(measured[w] for w in wafers) / len(wafers):.1f}%' if wafers else '—')
        summary[above] = (f"{display_threshold}% {'이상' if above else '미만'} {len(wafers)}장", f'(평균 {average})')
        groups.append(f'<tr><th scope="row">{label}</th><td>{escape(", ".join(wafers)) or "—"}</td>'
                      f'<td>{len(wafers)}</td><td>{average}</td></tr>')

    below_count, below_average = summary[False]
    comparison = f"{below_average} · {' '.join(summary[True])}"
    caption = f'{below_count} {comparison}'
    html = f"""<!doctype html><html lang="ko"><meta charset="utf-8"><title>{escape(title)}</title>
<style>body{{font:14px/1.55 system-ui;color:#252627;margin:12px}}table{{border-collapse:collapse;width:100%;margin-bottom:16px}}
th,td{{padding:5px 10px;text-align:left;border-bottom:1px solid #ddd;font-variant-numeric:tabular-nums}}</style>
<p><strong>{escape(below_count)}</strong> {escape(comparison)}</p>
<table aria-label="그룹 요약"><thead><tr><th>그룹</th><th>웨이퍼 번호</th><th>개수</th><th>평균 수율</th></tr></thead>
<tbody>{''.join(groups)}</tbody></table>
<table aria-label="웨이퍼별 수율"><thead><tr><th>웨이퍼 번호</th><th>수율 (%)</th></tr></thead>
<tbody>{''.join(detail)}</tbody></table></html>"""
    return caption, {'title': title, 'mime': 'text/html', 'data': html}

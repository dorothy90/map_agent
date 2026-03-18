"""
Yield 시각화 모듈
=================
HTML 테이블, scatter plot, 이상감지, lot 비교 테이블.
yield_query_agent.py에서 분리.
"""
from __future__ import annotations

import os
import uuid

from tabulate import tabulate

from common import (
    PARA_COLUMNS,
    PT1C_COLUMNS,
    GMS_COLUMNS,
    GMS_HIGHER_IS_BETTER,
    HIGHER_IS_BETTER,
)


# ── HTML 파일 저장 ────────────────────────────────────────────
GENERATED_DIR = os.path.join(os.path.dirname(__file__), "generated")
os.makedirs(GENERATED_DIR, exist_ok=True)


def _save_html_to_file(html: str, prefix: str) -> str:
    """HTML 문자열을 파일로 저장하고 file:// 경로를 반환."""
    fname = f"{prefix}_{uuid.uuid4().hex[:8]}.html"
    fpath = os.path.join(GENERATED_DIR, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(html)
    return f"file://{fpath}"


# ── 값 포맷터 ─────────────────────────────────────────────────
def _fmt_val(val) -> str:
    """raw value → 소수점 2자리 포맷. 숫자가 아니면 '-' 반환."""
    if val in (None, "-", ""):
        return "-"
    try:
        return f"{float(val):.2f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_gms_val(val) -> str:
    """gms raw value → 소수점 2자리 (×100 없음, 이미 % 단위)"""
    if val in (None, "-", ""):
        return "-"
    try:
        return f"{float(val):.2f}"
    except (TypeError, ValueError):
        return "-"


# ── 텍스트 테이블 ─────────────────────────────────────────────
_PERIOD_LABEL = {"weekly": "WEEK", "monthly": "MONTH", "daily": "DATE", "lot": "LOT"}


def _build_table(weeks_data: list[dict], lotcd: str, filter_params=None,
                 unit: str = "weekly") -> str:
    """n기간치 데이터를 테이블 문자열로 변환 (pt1h + pt1c + gms 3섹션)"""
    cols = [c for c in PARA_COLUMNS if c in filter_params] if filter_params else PARA_COLUMNS
    gms_cols = GMS_COLUMNS
    period_label = _PERIOD_LABEL.get(unit, "WEEK")

    headers = (
        [period_label, "LOT", "WF"]
        + [f"PT1H_{c}" for c in cols]
        + [f"PT1C_{c}" for c in PT1C_COLUMNS]
        + [f"GMS_{c}" for c in gms_cols]
    )

    rows = []
    for wd in weeks_data:
        row = [wd.get("week", "?"), wd.get("lotcount", "-"), wd.get("wfCount", "-")]
        row += [_fmt_val(wd.get(col, "-")) for col in cols]
        row += [_fmt_val(wd.get(f"pt1c_{col}", "-")) for col in PT1C_COLUMNS]
        row += [_fmt_gms_val(wd.get(f"gms_{col}", "-")) for col in gms_cols]
        rows.append(row)

    n = len(weeks_data)
    title = f"\n[{lotcd}] {period_label} pt1h+pt1c+gms Metrics (최근 {n}개)\n"
    table = tabulate(rows, headers=headers, tablefmt="grid", numalign="right", stralign="center")

    return title + table


# ── Scatter Plot (Period 모드) ────────────────────────────────
_PERIOD_COLORS = [
    "#3b82f6", "#ef4444", "#22c55e", "#f59e0b",
    "#8b5cf6", "#ec4899", "#14b8a6", "#f97316",
    "#6366f1", "#84cc16", "#06b6d4", "#e11d48",
]


def _build_scatter_html(
    wafer_rows: list[dict], anomaly_params: list[dict],
) -> str:
    """Period 모드 scatter plot HTML — X축 MEASURETIME_START, 모든 wafer 타점."""
    if not wafer_rows:
        return ""

    import json as _json
    from collections import defaultdict

    all_periods = sorted({r.get("period", "") for r in wafer_rows})
    use_periods = len(all_periods) > 1

    param_period_data: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in wafer_rows:
        param_period_data[r["param"]][r.get("period", "")].append(
            {"x": r["ts"], "y": round(r["value"], 4),
             "lotid": r.get("lotid", ""), "wfid": r.get("wfid", "")}
        )

    period_color = {p: _PERIOD_COLORS[i % len(_PERIOD_COLORS)] for i, p in enumerate(all_periods)}

    improved = [a for a in anomaly_params if a["direction"] == "개선"][:3]
    degraded = [a for a in anomaly_params if a["direction"] == "열화"][:3]

    rows_config: list[tuple[str, str, list[tuple[str, str]]]] = []
    rows_config.append(("고정 파라미터", "#3b82f6", [("VTH", "VTH"), ("PT1C", "PT1C")]))
    if improved:
        rows_config.append(("개선 파라미터", "#22c55e", [(a["param"], a["param"]) for a in improved]))
    if degraded:
        rows_config.append(("열화 파라미터", "#ef4444", [(a["param"], a["param"]) for a in degraded]))

    chart_blocks = []
    chart_id = 0
    for row_title, row_color, charts in rows_config:
        cells = []
        for label, param_name in charts:
            cid = f"sc_{chart_id}"
            chart_id += 1

            if use_periods:
                datasets = []
                for pl in all_periods:
                    color = period_color[pl]
                    pts = param_period_data.get(param_name, {}).get(pl, [])
                    if not pts:
                        continue
                    datasets.append({
                        "label": pl,
                        "data": pts,
                        "backgroundColor": color,
                        "borderColor": color,
                        "pointRadius": 2.5,
                        "showLine": False,
                    })
                datasets_js = _json.dumps(datasets, ensure_ascii=False)
            else:
                pts = param_period_data.get(param_name, {}).get(all_periods[0] if all_periods else "", [])
                datasets_js = _json.dumps([{
                    "label": label,
                    "data": pts,
                    "backgroundColor": row_color,
                    "borderColor": row_color,
                    "pointRadius": 2.5,
                    "showLine": False,
                }], ensure_ascii=False)

            cells.append(f"""
<div style="flex:1;min-width:320px;max-width:500px;">
  <canvas id="{cid}"></canvas>
  <script>
  new Chart(document.getElementById('{cid}'), {{
    type: 'scatter',
    data: {{
      datasets: {datasets_js}
    }},
    options: {{
      responsive: true,
      plugins: {{
        title: {{ display: true, text: '{label}', font: {{ size: 14 }} }},
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            label: function(ctx) {{
              return 'LOT: ' + ctx.raw.lotid + '  WF: ' + ctx.raw.wfid + '  Value: ' + ctx.raw.y;
            }}
          }}
        }}
      }},
      scales: {{
        x: {{
          type: 'time',
          time: {{ tooltipFormat: 'yyyy-MM-dd HH:mm:ss', unit: 'day', displayFormats: {{ day: 'MM-dd' }} }},
          title: {{ display: true, text: 'MEASURETIME' }},
          ticks: {{ maxRotation: 45, font: {{ size: 9 }} }}
        }},
        y: {{ title: {{ display: true, text: '{label}' }} }}
      }}
    }}
  }});
  </script>
</div>""")
        chart_blocks.append(
            f'<div style="margin-bottom:8px;font-weight:600;font-size:13px;color:#475569;">{row_title}</div>'
            f'<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px;">{"".join(cells)}</div>'
        )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 12px; background: #fff; }}
</style>
</head><body>
{"".join(chart_blocks)}
</body></html>"""
    return html


# ── HTML 테이블 ───────────────────────────────────────────────
def _build_html_table(
    weeks_data: list[dict],
    lotcd: str,
    filter_params=None,
    anomaly_params=None,
    unit: str = "weekly",
) -> str:
    """n기간치 데이터를 동적 HTML 테이블로 변환 (pt1h + pt1c + gms 3섹션)"""
    cols = [c for c in PARA_COLUMNS if c in filter_params] if filter_params else PARA_COLUMNS
    gms_cols = GMS_COLUMNS
    BLUE_COLS = {"VTH", "IDSAT"}

    anomaly_map = {a["param"]: a for a in anomaly_params} if anomaly_params else {}

    # Delta 계산 (최신주 - 직전주)
    delta_pt1h: dict = {}
    delta_pt1c: dict = {}
    delta_gms: dict = {}
    if len(weeks_data) >= 2:
        prev, curr = weeks_data[-2], weeks_data[-1]
        for col in cols:
            pv = prev.get(col)
            cv = curr.get(col)
            if pv in (None, "-", "") or cv in (None, "-", ""):
                delta_pt1h[col] = None
            else:
                try:
                    delta_pt1h[col] = float(cv) - float(pv)
                except (TypeError, ValueError):
                    delta_pt1h[col] = None
        for col in PT1C_COLUMNS:
            pv = prev.get(f"pt1c_{col}")
            cv = curr.get(f"pt1c_{col}")
            if pv in (None, "-", "") or cv in (None, "-", ""):
                delta_pt1c[col] = None
            else:
                try:
                    delta_pt1c[col] = float(cv) - float(pv)
                except (TypeError, ValueError):
                    delta_pt1c[col] = None
        for col in gms_cols:
            pv = prev.get(f"gms_{col}")
            cv = curr.get(f"gms_{col}")
            if pv in (None, "-", "") or cv in (None, "-", ""):
                delta_gms[col] = None
            else:
                try:
                    delta_gms[col] = float(cv) - float(pv)
                except (TypeError, ValueError):
                    delta_gms[col] = None

    pt1h_span = len(cols)
    pt1c_span = len(PT1C_COLUMNS)
    gms_span = len(gms_cols)

    html = """<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 8px; background: #fff; }
table { border-collapse: collapse; border: 1px solid #cbd5e1; }
th {
  background: #1e293b; color: #e2e8f0;
  font-size: 11px; font-weight: 600;
  border: 1px solid #334155;
}
th.th-week {
  background: #334155; min-width: 80px;
  text-align: center; vertical-align: middle; padding: 8px;
}
th.th-meta {
  background: #475569; min-width: 44px;
  text-align: center; vertical-align: middle; padding: 8px 6px;
}
th.th-meta-pt1c { background: #2d4a7a; }
th.th-meta-gms  { background: #2d5a3d; }
th.th-section {
  text-align: center; padding: 6px; font-size: 12px; font-weight: 700;
}
th.th-section-pt1h { background: #1e293b; }
th.th-section-pt1c { background: #1e3a5f; border-left: 3px solid #3b82f6; }
th.th-section-gms  { background: #1a3a2a; border-left: 3px solid #22c55e; }
th.th-param {
  width: 44px; height: 100px;
  padding: 0; vertical-align: bottom;
}
th.th-blue       { background: #1d4ed8; }
th.th-amber      { background: #92400e; }
th.th-blue-pt1c  { background: #1e40af; border-left: 1px solid #3b82f6; }
th.th-amber-pt1c { background: #78350f; border-left: 1px solid #3b82f6; }
th.th-gms        { background: #14532d; border-left: 1px solid #22c55e; }
.th-param-inner {
  display: flex; flex-direction: column;
  align-items: center; justify-content: flex-end;
  height: 100%; padding: 4px 2px;
  gap: 3px;
}
.hname {
  writing-mode: vertical-rl;
  transform: rotate(180deg);
  white-space: nowrap;
  font-size: 11px; font-weight: 600; letter-spacing: 0.3px;
  flex: 1; display: flex; align-items: center;
}
.badge-d { color: #fca5a5; font-size: 9px; margin-top: 2px; }
.badge-i { color: #86efac; font-size: 9px; margin-top: 2px; }
td {
  border: 1px solid #e2e8f0; padding: 4px 9px;
  text-align: right; font-size: 12px; color: #374151;
  white-space: nowrap;
}
td.td-week {
  text-align: center; font-weight: 600; font-size: 11px;
  color: #1e293b; background: #f1f5f9 !important;
}
tr:nth-child(odd) td:not(.td-week) { background: #ffffff; }
tr:nth-child(even) td:not(.td-week) { background: #f8fafc; }
td.degraded {
  background: #fef2f2 !important; color: #b91c1c;
  font-weight: 600; border-left: 2px solid #ef4444;
}
td.improved {
  background: #f0fdf4 !important; color: #15803d;
  font-weight: 600; border-left: 2px solid #22c55e;
}
td.td-pt1c-sep { border-left: 3px solid #3b82f6 !important; }
td.td-gms-sep  { border-left: 3px solid #22c55e !important; }
tr.delta td {
  background: #1e293b !important; color: #94a3b8;
  font-size: 11px; font-weight: 500; border-color: #334155;
}
tr.delta td.delta-week {
  text-align: center; color: #e2e8f0; font-weight: 700; font-size: 12px;
}
td.delta-neg { background: #7f1d1d !important; color: #fca5a5; font-weight: 600; }
td.delta-pos { background: #14532d !important; color: #86efac; font-weight: 600; }
</style></head><body>
<table>
<thead>"""

    period_label = _PERIOD_LABEL.get(unit, "WEEK")

    # Row 1: 섹션 헤더
    html += (
        f'\n<tr>'
        f'<th class="th-week" rowspan="2">{period_label}</th>'
        f'<th class="th-meta" rowspan="2">LOT</th>'
        f'<th class="th-meta" rowspan="2">WF</th>'
        f'<th colspan="{pt1h_span}" class="th-section th-section-pt1h">pt1h</th>'
        f'<th colspan="{pt1c_span}" class="th-section th-section-pt1c">pt1c</th>'
        f'<th colspan="{gms_span}" class="th-section th-section-gms">gms</th>'
        f'</tr>'
    )

    # Row 2: 서브헤더
    html += "\n<tr>"

    for col in cols:
        css = "th-blue" if col in BLUE_COLS else "th-amber"
        badge = ""
        if col in anomaly_map:
            badge = '<span class="badge-d">▼</span>' if anomaly_map[col]["direction"] == "열화" else '<span class="badge-i">▲</span>'
        tip = ""
        if col in anomaly_map:
            a = anomaly_map[col]
            tip = f' title="{a["prev_val"]} → {a["curr_val"]} ({a["change_pct"]:+.1f}%)"'
        html += (
            f'<th class="th-param {css}"{tip}>'
            f'<div class="th-param-inner">'
            f'<div class="hname">{col}{badge}</div>'
            f'</div></th>'
        )

    for j, col in enumerate(PT1C_COLUMNS):
        sep = ' style="border-left: 3px solid #3b82f6;"' if j == 0 else ""
        html += (
            f'<th class="th-param th-amber-pt1c"{sep}>'
            f'<div class="th-param-inner">'
            f'<div class="hname">{col}</div>'
            f'</div></th>'
        )

    for j, col in enumerate(gms_cols):
        sep = ' style="border-left: 3px solid #22c55e;"' if j == 0 else ""
        html += (
            f'<th class="th-param th-gms"{sep}>'
            f'<div class="th-param-inner">'
            f'<div class="hname">{col}</div>'
            f'</div></th>'
        )

    html += "\n</tr>\n</thead>\n<tbody>\n"

    last_idx = len(weeks_data) - 1
    for i, wd in enumerate(weeks_data):
        html += "<tr>"
        html += f'<td class="td-week">{wd.get("week", "?")}</td>'

        html += f'<td>{wd.get("lotcount", "-")}</td>'
        html += f'<td>{wd.get("wfCount", "-")}</td>'

        for col in cols:
            display = _fmt_val(wd.get(col, "-"))
            if i == last_idx and col in anomaly_map:
                a = anomaly_map[col]
                css = "degraded" if a["direction"] == "열화" else "improved"
                tip = f"{a['prev_val']} → {a['curr_val']} ({a['change_pct']:+.1f}%)"
                html += f'<td class="{css}" title="{tip}">{display}</td>'
            else:
                html += f"<td>{display}</td>"

        for j, col in enumerate(PT1C_COLUMNS):
            display = _fmt_val(wd.get(f"pt1c_{col}", "-"))
            sep = ' class="td-pt1c-sep"' if j == 0 else ""
            html += f"<td{sep}>{display}</td>"

        for j, col in enumerate(gms_cols):
            display = _fmt_gms_val(wd.get(f"gms_{col}", "-"))
            sep = ' class="td-gms-sep"' if j == 0 else ""
            html += f"<td{sep}>{display}</td>"

        html += "</tr>\n"

    # Δ 행
    if len(weeks_data) >= 2:
        html += '<tr class="delta">'
        html += '<td class="delta-week">Δ</td>'
        html += "<td>—</td><td>—</td>"

        for col in cols:
            d = delta_pt1h.get(col)
            if d is None or abs(d) < 1e-12:
                html += "<td>—</td>"
            else:
                is_better = (
                    (col in HIGHER_IS_BETTER and d > 0)
                    or (col not in HIGHER_IS_BETTER and d < 0)
                )
                css = "delta-pos" if is_better else "delta-neg"
                html += f'<td class="{css}">{d:+.2f}</td>'

        for j, col in enumerate(PT1C_COLUMNS):
            d = delta_pt1c.get(col)
            sep = " td-pt1c-sep" if j == 0 else ""
            if d is None or abs(d) < 1e-12:
                html += f'<td class="{sep.strip()}">—</td>' if sep else "<td>—</td>"
            else:
                css = "delta-pos" + sep
                html += f'<td class="{css}">{d:+.2f}</td>'

        for j, col in enumerate(gms_cols):
            d = delta_gms.get(col)
            sep = " td-gms-sep" if j == 0 else ""
            if d is None or abs(d) < 1e-12:
                html += f'<td class="{sep.strip()}">—</td>' if sep else "<td>—</td>"
            else:
                is_better = col in GMS_HIGHER_IS_BETTER and d > 0
                css = ("delta-pos" if is_better else "delta-neg") + sep
                html += f'<td class="{css}">{d:+.2f}</td>'

        html += "</tr>\n"

    html += "</tbody></table>\n</body></html>"
    return html


# ── 이상감지 ──────────────────────────────────────────────────
TOP_N_ANOMALIES = int(os.getenv("TOP_N_ANOMALIES", "3"))


def _detect_anomalies(weeks_data: list[dict], top_n: int = TOP_N_ANOMALIES) -> list[dict]:
    """직전주 vs 최신주 파라미터 변화율 계산, 열화 Top N + 개선 Top N 반환"""
    if len(weeks_data) < 2:
        return []

    prev, curr = weeks_data[-2], weeks_data[-1]
    degraded = []
    improved = []

    all_params = [(p, p) for p in PARA_COLUMNS] + [(f"pt1c_{p}", p) for p in PT1C_COLUMNS]

    for data_key, param in all_params:
        pv = prev.get(data_key)
        cv = curr.get(data_key)
        if pv in (None, "-", 0, "") or cv in (None, "-", ""):
            continue
        try:
            pv, cv = float(pv), float(cv)
        except (TypeError, ValueError):
            continue
        if pv == 0:
            continue
        if abs(pv) < 1e-15:
            continue

        change_pct = (cv - pv) / abs(pv) * 100
        if change_pct == 0:
            continue

        is_better = (
            (param in HIGHER_IS_BETTER and change_pct > 0)
            or (param not in HIGHER_IS_BETTER and change_pct < 0)
        )
        entry = {
            "param": param,
            "prev_val": round(pv, 2),
            "curr_val": round(cv, 2),
            "change_pct": round(change_pct, 1),
            "direction": "개선" if is_better else "열화",
        }
        if is_better:
            improved.append(entry)
        else:
            degraded.append(entry)

    degraded.sort(key=lambda x: -abs(x["change_pct"]))
    improved.sort(key=lambda x: -abs(x["change_pct"]))

    return degraded[:top_n] + improved[:top_n]


# ── Lot 비교 테이블 ───────────────────────────────────────────
def _build_lot_table(lot_data: dict[str, dict], mode: str = "lot", filter_params=None) -> str:
    """lot 비교 텍스트 테이블."""
    if not lot_data:
        return "(lot 데이터 없음)"

    cols = [c for c in PARA_COLUMNS if c in filter_params] if filter_params else PARA_COLUMNS
    all_params = cols + [f"pt1c_{c}" for c in PT1C_COLUMNS]
    lot_keys = sorted(k for k in lot_data if not k.startswith("_"))

    def _avg_param(p):
        vals = [lot_data[k].get(p) for k in lot_keys if isinstance(lot_data[k].get(p), (int, float))]
        return sum(vals) / len(vals) if vals else None

    if mode == "lot":
        headers = ["LOT", "WF_CNT"] + all_params
        avg_wf = sum(lot_data[k].get("_wf_count", 0) for k in lot_keys)
        rows = [["Avg", str(avg_wf)] + [_fmt_val(_avg_param(p)) for p in all_params]]
        for key in lot_keys:
            wf_cnt = lot_data[key].get("_wf_count", "-")
            rows.append([key, str(wf_cnt)] + [_fmt_val(lot_data[key].get(p)) for p in all_params])
    else:
        headers = ["LOT"] + all_params
        rows = [["Avg"] + [_fmt_val(_avg_param(p)) for p in all_params]]
        for key in lot_keys:
            rows.append([key] + [_fmt_val(lot_data[key].get(p)) for p in all_params])

    return tabulate(rows, headers=headers, tablefmt="grid", numalign="right", stralign="center")


def _build_lot_html_table(lot_data: dict[str, dict], mode: str = "lot", filter_params=None) -> str:
    """lot 비교 HTML 테이블."""
    if not lot_data:
        return "<p>(lot 데이터 없음)</p>"

    cols = [c for c in PARA_COLUMNS if c in filter_params] if filter_params else PARA_COLUMNS
    gms_cols = GMS_COLUMNS
    BLUE_COLS = {"VTH", "IDSAT"}

    lot_keys = sorted(k for k in lot_data if not k.startswith("_"))

    def _numeric(key, param):
        v = lot_data[key].get(param)
        return float(v) if isinstance(v, (int, float)) else None

    def _avg_param(p):
        vals = [_numeric(k, p) for k in lot_keys]
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    pt1h_span = len(cols)
    pt1c_span = len(PT1C_COLUMNS)
    gms_span  = len(gms_cols)

    html = """<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 8px; background: #fff; }
table { border-collapse: collapse; border: 1px solid #cbd5e1; }
th {
  background: #1e293b; color: #e2e8f0;
  font-size: 11px; font-weight: 600;
  border: 1px solid #334155;
}
th.th-week {
  background: #334155; min-width: 100px;
  text-align: center; vertical-align: middle; padding: 8px;
}
th.th-meta {
  background: #475569; min-width: 44px;
  text-align: center; vertical-align: middle; padding: 8px 6px;
}
th.th-section {
  text-align: center; padding: 6px; font-size: 12px; font-weight: 700;
}
th.th-section-pt1h { background: #1e293b; }
th.th-section-pt1c { background: #1e3a5f; border-left: 3px solid #3b82f6; }
th.th-section-gms  { background: #1a3a2a; border-left: 3px solid #22c55e; }
th.th-param {
  width: 44px; height: 80px;
  padding: 0; vertical-align: bottom;
}
th.th-blue       { background: #1d4ed8; }
th.th-amber      { background: #92400e; }
th.th-amber-pt1c { background: #78350f; border-left: 1px solid #3b82f6; }
th.th-gms        { background: #14532d; border-left: 1px solid #22c55e; }
.th-param-inner {
  display: flex; flex-direction: column;
  align-items: center; justify-content: flex-end;
  height: 100%; padding: 4px 2px;
}
.hname {
  writing-mode: vertical-rl;
  transform: rotate(180deg);
  white-space: nowrap;
  font-size: 11px; font-weight: 600; letter-spacing: 0.3px;
  flex: 1; display: flex; align-items: center;
}
td {
  border: 1px solid #e2e8f0; padding: 4px 9px;
  text-align: right; font-size: 12px; color: #374151;
  white-space: nowrap;
}
td.td-week {
  text-align: center; font-weight: 600; font-size: 11px;
  color: #1e293b; background: #f1f5f9 !important;
}
tr:nth-child(odd) td:not(.td-week) { background: #ffffff; }
tr:nth-child(even) td:not(.td-week) { background: #f8fafc; }
tr.avg-row td { background: #eff6ff !important; font-weight: 600; color: #1d4ed8; }
tr.avg-row td.td-week { background: #1d4ed8 !important; color: #fff; }
td.td-pt1c-sep { border-left: 3px solid #3b82f6 !important; }
td.td-gms-sep  { border-left: 3px solid #22c55e !important; }
</style></head><body>
<table>
<thead>"""

    html += (
        f'\n<tr>'
        f'<th class="th-week" rowspan="2">LOT</th>'
    )
    if mode == "lot":
        html += '<th class="th-meta" rowspan="2">WF_CNT</th>'
    html += (
        f'<th colspan="{pt1h_span}" class="th-section th-section-pt1h">pt1h</th>'
        f'<th colspan="{pt1c_span}" class="th-section th-section-pt1c">pt1c</th>'
        f'<th colspan="{gms_span}" class="th-section th-section-gms">gms</th>'
        f'</tr>'
    )

    html += "\n<tr>"
    for col in cols:
        css = "th-blue" if col in BLUE_COLS else "th-amber"
        html += (
            f'<th class="th-param {css}">'
            f'<div class="th-param-inner"><div class="hname">{col}</div></div></th>'
        )
    for j, col in enumerate(PT1C_COLUMNS):
        sep = ' style="border-left: 3px solid #3b82f6;"' if j == 0 else ""
        html += (
            f'<th class="th-param th-amber-pt1c"{sep}>'
            f'<div class="th-param-inner"><div class="hname">{col}</div></div></th>'
        )
    for j, col in enumerate(gms_cols):
        sep = ' style="border-left: 3px solid #22c55e;"' if j == 0 else ""
        html += (
            f'<th class="th-param th-gms"{sep}>'
            f'<div class="th-param-inner"><div class="hname">{col}</div></div></th>'
        )
    html += "\n</tr>\n</thead>\n<tbody>\n"

    # Avg 행
    html += '<tr class="avg-row">'
    html += '<td class="td-week">Avg</td>'
    if mode == "lot":
        total_wf = sum(lot_data[k].get("_wf_count", 0) for k in lot_keys)
        html += f'<td>{total_wf}</td>'
    for col in cols:
        html += f'<td>{_fmt_val(_avg_param(col))}</td>'
    for j, col in enumerate(PT1C_COLUMNS):
        sep = ' class="td-pt1c-sep"' if j == 0 else ""
        html += f'<td{sep}>{_fmt_val(_avg_param(f"pt1c_{col}"))}</td>'
    for j, col in enumerate(gms_cols):
        sep = ' class="td-gms-sep"' if j == 0 else ""
        html += f'<td{sep}>-</td>'
    html += "</tr>\n"

    # 데이터 행
    for key in lot_keys:
        html += "<tr>"
        html += f'<td class="td-week">{key}</td>'
        if mode == "lot":
            wf_cnt = lot_data[key].get("_wf_count", "-")
            html += f'<td>{wf_cnt}</td>'
        for col in cols:
            html += f'<td>{_fmt_val(_numeric(key, col))}</td>'
        for j, col in enumerate(PT1C_COLUMNS):
            sep = ' class="td-pt1c-sep"' if j == 0 else ""
            html += f'<td{sep}>{_fmt_val(_numeric(key, f"pt1c_{col}"))}</td>'
        for j, col in enumerate(gms_cols):
            sep = ' class="td-gms-sep"' if j == 0 else ""
            html += f'<td{sep}>-</td>'
        html += "</tr>\n"

    html += "</tbody></table>\n</body></html>"
    return html

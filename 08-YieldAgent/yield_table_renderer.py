from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReportCell:
    text: str
    css_class: str = ""
    title: str | None = None


@dataclass(frozen=True)
class ReportColumn:
    label: str
    css_class: str
    style: str = ""
    title: str | None = None
    badge: str = ""


@dataclass(frozen=True)
class ReportGroup:
    label: str
    css_class: str
    columns: list[ReportColumn] = field(default_factory=list)
    style: str = ""


@dataclass(frozen=True)
class ReportRow:
    metadata: list[ReportCell]
    values: list[ReportCell]


REPORT_STYLE = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  margin: 0;
  padding: 8px;
  background: #ffffff;
  color: rgba(0, 0, 0, 0.85);
  overflow-y: auto !important;
  overflow-x: auto !important;
}
.table-container {
  border-radius: 8px;
  overflow-x: auto;
  border: 1px solid #f0f0f0;
  background: #ffffff;
  display: block;
  width: max-content;
  min-width: 0;
  max-width: 100%;
}
.table-container::-webkit-scrollbar {
  height: 5px;
}
.table-container::-webkit-scrollbar-thumb {
  background: #d9d9d9;
  border-radius: 3px;
}
.table-container::-webkit-scrollbar-thumb:hover {
  background: #bfbfbf;
}
table {
  border-collapse: collapse;
  width: max-content;
  min-width: 0;
  border: none;
}
th {
  background: #fafafa !important;
  color: rgba(0, 0, 0, 0.85);
  font-size: 11px;
  font-weight: 500;
  border: 1px solid #f0f0f0;
  vertical-align: middle;
  padding: 8px 8px;
}
th.th-week {
  background: #fafafa !important;
  min-width: 70px;
  text-align: center;
  color: rgba(0, 0, 0, 0.85);
  font-size: 11px;
}
th.th-meta {
  background: #fafafa !important;
  min-width: 36px;
  text-align: center;
  color: rgba(0, 0, 0, 0.85);
  font-size: 11px;
}
th.th-section {
  text-align: center;
  padding: 8px;
  font-size: 11px;
  font-weight: 600;
  color: rgba(0, 0, 0, 0.85);
}
th.th-section-pt1h {
  border-top: 3px solid #1890ff;
  background: #e6f7ff !important;
  color: #0050b3 !important;
}
th.th-section-pt1c {
  border-top: 3px solid #722ed1;
  border-left: 2px solid #bfbfbf !important;
  background: #f9f0ff !important;
  color: #391085 !important;
}
th.th-section-gms  {
  border-top: 3px solid #52c41a;
  border-left: 2px solid #bfbfbf !important;
  background: #f6ffed !important;
  color: #237804 !important;
}
th.th-param {
  width: 36px;
  height: 75px;
  padding: 0;
  vertical-align: bottom;
  background: #fafafa !important;
  border-bottom: 1px solid #f0f0f0;
  border-left: 1px solid #f0f0f0;
}
th.th-blue, th.th-amber, th.th-blue-pt1c, th.th-amber-pt1c, th.th-gms {
  background: #fafafa;
}

.th-param-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  height: 100%;
  padding: 4px 1px;
}
.hname {
  writing-mode: vertical-rl;
  transform: rotate(180deg);
  white-space: nowrap;
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.5px;
  color: rgba(0, 0, 0, 0.85);
  flex: 1;
  display: flex;
  align-items: center;
  gap: 2px;
}
.badge-d { color: #cf1322; font-size: 10px; font-weight: bold; }
.badge-i { color: #389e0d; font-size: 10px; font-weight: bold; }

td {
  border: 1px solid #f0f0f0;
  padding: 4px 6px;
  text-align: right;
  font-size: 11px;
  color: rgba(0, 0, 0, 0.65);
  font-family: 'JetBrains Mono', monospace;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  transition: background 0.3s ease;
}
td.td-week {
  text-align: center;
  font-family: 'Inter', sans-serif;
  font-weight: 500;
  font-size: 11px;
  color: rgba(0, 0, 0, 0.65);
  background: #fafafa !important;
}
tr td:not(.td-week) { background: #ffffff; }

tr:hover td:not(.td-week) {
  background: #fafafa !important;
}

td.degraded {
  background: #fff1f0 !important;
  color: #cf1322 !important;
  font-weight: 500;
}
td.improved {
  background: #f6ffed !important;
  color: #389e0d !important;
  font-weight: 500;
}
td.td-pt1c-sep { border-left: 2px solid #bfbfbf !important; }
td.td-gms-sep  { border-left: 2px solid #bfbfbf !important; }

tr.delta td {
  background: #fafafa !important;
  color: rgba(0, 0, 0, 0.85);
  font-size: 10px;
  font-weight: 500;
  border-top: 1px solid #f0f0f0;
}
tr.delta td.delta-week {
  text-align: center;
  color: #1890ff;
  font-weight: 600;
  font-size: 11px;
  font-family: 'Inter', sans-serif;
}
td.delta-neg { background: #fff1f0 !important; color: #cf1322 !important; font-weight: 500; }
td.delta-pos { background: #f6ffed !important; color: #389e0d !important; font-weight: 500; }
"""


def _attrs(cell: ReportCell) -> str:
    attrs = f' class="{cell.css_class}"' if cell.css_class else ""
    if cell.title is not None:
        attrs += f' title="{cell.title}"'
    return attrs


def render_report_html(
    rows: list[ReportRow],
    groups: list[ReportGroup],
    *,
    metadata_columns: list[str],
    period_label: str,
    delta_row: list[ReportCell] | None,
) -> str:
    html = """<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>""" + REPORT_STYLE + """</style></head><body>
<div class="table-container">
<table>
<thead>"""

    html += f'\n<tr><th class="th-week" rowspan="2">{period_label}</th>'
    for label in metadata_columns:
        html += f'<th class="th-meta" rowspan="2">{label}</th>'
    for group in groups:
        style = f' style="{group.style}"' if group.style else ""
        html += (
            f'<th colspan="{len(group.columns)}" '
            f'class="th-section {group.css_class}"{style}>{group.label}</th>'
        )
    html += "</tr>"

    html += "\n<tr>"
    for group in groups:
        for column in group.columns:
            style = f' style="{column.style}"' if column.style else ""
            title = f' title="{column.title}"' if column.title is not None else ""
            html += (
                f'<th class="th-param {column.css_class}"{style}{title}>'
                f'<div class="th-param-inner">'
                f'<div class="hname">{column.label}{column.badge}</div>'
                f"</div></th>"
            )
    html += "\n</tr>\n</thead>\n<tbody>\n"

    for row in rows:
        html += "<tr>"
        for cell in row.metadata:
            html += f"<td{_attrs(cell)}>{cell.text}</td>"
        for cell in row.values:
            html += f"<td{_attrs(cell)}>{cell.text}</td>"
        html += "</tr>\n"

    if delta_row is not None:
        html += '<tr class="delta">'
        for cell in delta_row:
            html += f"<td{_attrs(cell)}>{cell.text}</td>"
        html += "</tr>\n"

    html += """</tbody></table>
</div>
<script>
function sendHeight() {
  var height = Math.ceil(document.body.getBoundingClientRect().height);
  window.parent.postMessage({ type: 'yield-table-height', height: height }, '*');
}
window.addEventListener('load', function() {
  sendHeight();
});
window.addEventListener('resize', sendHeight);
if (window.ResizeObserver) {
  new ResizeObserver(sendHeight).observe(document.body);
}
</script>
</body></html>"""
    return html

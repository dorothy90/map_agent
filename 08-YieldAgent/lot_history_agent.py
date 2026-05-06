"""
LOT History Agent 노드
=======================
5개 Oracle 테이블에서 LOT_ID 종합 이력을 조회하고
HTML 아티팩트로 렌더링합니다.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from typing import Any, Dict, List

from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langfuse import observe

from common import timed, html_escape as _h, is_transient_error
from lot_history_tools import _tool_payload_var, query_lot_history

load_dotenv(override=True)

logger = logging.getLogger("yield_agent.lot_history_agent")


# ── HTML 렌더링 ──────────────────────────────────────────────


_CSS = """\
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Pretendard',-apple-system,sans-serif;background:#fafafa;color:#1a1a1a;padding:24px;max-width:1200px;min-width:720px;margin:0 auto}
.header{display:flex;align-items:center;gap:16px;margin-bottom:14px}
.header h1{font-size:20px;font-weight:700;color:#1a1a1a}
.header .lot-count{background:#1a1a1a;color:#fff;padding:2px 10px;border-radius:12px;font-size:13px}

/* sticky LOT nav (multi-LOT only) */
.lot-nav{position:sticky;top:0;z-index:10;background:rgba(250,250,250,0.95);backdrop-filter:blur(6px);padding:10px 0;margin-bottom:16px;display:flex;gap:8px;flex-wrap:wrap;border-bottom:1px solid #d4d4d4}
.lot-nav .tab{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;background:#fff;border:1px solid #d4d4d4;border-radius:8px;text-decoration:none;color:#1a1a1a;font-size:13px;font-weight:500;transition:all 0.15s}
.lot-nav .tab:hover{background:#f5f5f5;border-color:#a3a3a3}
.lot-nav .tab.active{background:#1a1a1a;color:#fff;border-color:#1a1a1a}
.lot-nav .risk-dot{font-size:12px;line-height:1}

/* summary table */
.summary-table{width:100%;border-collapse:collapse;margin-bottom:24px;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.06);border:1px solid #e5e5e5}
.summary-table th{background:#f5f5f5;color:#525252;padding:10px 14px;font-size:12px;font-weight:600;text-align:center;letter-spacing:0.04em;text-transform:uppercase;border-bottom:1px solid #d4d4d4}
.summary-table td{padding:11px 14px;text-align:center;font-size:13.5px;border-bottom:1px solid #e5e5e5}
.summary-table tr:last-child td{border-bottom:none}
.summary-table .lot-id{text-align:left;font-weight:700;color:#1a1a1a}
.summary-table .lot-id a{color:inherit;text-decoration:none}
.summary-table .lot-id a:hover{text-decoration:underline}
.cnt-zero{color:#a3a3a3}
.cnt-nonzero{font-weight:700;color:#1a1a1a}
/* risk dots: grayscale by severity (red=darkest, green=lightest) */
.risk-red{color:#1a1a1a;font-size:15px}
.risk-yellow{color:#737373;font-size:15px}
.risk-green{color:#a3a3a3;font-size:15px}

/* LOT card */
.lot-card{background:#fff;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,0.06);margin-bottom:20px;overflow:hidden;scroll-margin-top:64px;border:1px solid #e5e5e5;border-left:4px solid #d4d4d4}
.lot-card.risk-red{border-left-color:#1a1a1a}
.lot-card.risk-yellow{border-left-color:#737373}
.lot-card.risk-green{border-left-color:#a3a3a3}
.lot-card-header{padding:14px 20px;background:#f5f5f5;color:#1a1a1a;display:flex;align-items:center;gap:12px;border-bottom:1px solid #e5e5e5}
.lot-card-header h2{font-size:17px;font-weight:700;color:#1a1a1a}
.lot-card-header .badge{padding:3px 10px;border-radius:8px;font-size:11px;font-weight:700;letter-spacing:0.02em}
.badge-red{background:#1a1a1a;color:#fff}
.badge-yellow{background:#525252;color:#fff}
.badge-green{background:#d4d4d4;color:#1a1a1a}

/* highlights box (핵심 이슈 진입 미리보기) */
.lot-highlights{padding:12px 20px;background:#fafafa;border-bottom:1px solid #e5e5e5;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.lot-highlights .hl-label{color:#737373;font-size:12px;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;margin-right:4px}
.lot-highlights .highlight-chip{display:inline-flex;align-items:center;gap:8px;padding:6px 12px;border-radius:8px;font-size:13px;font-weight:600;text-decoration:none;transition:all 0.15s;border:1px solid transparent}
.lot-highlights .highlight-chip:hover{filter:brightness(0.95);transform:translateY(-1px)}
.lot-highlights .highlight-chip.halt{background:#1a1a1a;color:#fff;border-color:#1a1a1a}
.lot-highlights .highlight-chip.long{background:#525252;color:#fff;border-color:#525252}
.lot-highlights .highlight-chip.qtime{background:#e5e5e5;color:#1a1a1a;border-color:#d4d4d4}
.lot-highlights .highlight-chip .hl-tag{font-size:11px;font-weight:700;letter-spacing:0.04em;opacity:0.85}
.lot-highlights .highlight-chip .hl-detail{font-weight:500}
.lot-highlights .hl-clear{color:#525252;font-size:13px;font-weight:600}

/* sections (HTML5 details) — default closed */
details.section{padding:0;border-bottom:1px solid #e5e5e5}
details.section:last-child{border-bottom:none}
details.section > summary{padding:14px 20px;cursor:pointer;list-style:none;display:flex;align-items:center;gap:10px;font-size:14px;font-weight:700;user-select:none;scroll-margin-top:64px;color:#1a1a1a}
details.section > summary:hover{background:#fafafa}
details.section > summary::-webkit-details-marker{display:none}
details.section > summary::before{content:"▸";font-size:11px;color:#a3a3a3;width:12px;display:inline-block;transition:transform 0.15s}
details.section[open] > summary::before{transform:rotate(90deg)}
details.section > .section-body{padding:0 20px 16px 20px}
details.section.empty-section{opacity:0.5}
details.section.empty-section > summary{font-weight:500}
details.section:target > summary{background:#f0f0f0}
.section-title-text{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.section-title-text .icon{font-size:14px;filter:grayscale(1) opacity(0.75)}
.section-title-spacer{flex:1}

.cnt{background:#e5e5e5;color:#525252;padding:2px 10px;border-radius:10px;font-size:14px;font-weight:700;white-space:nowrap}
.cnt-alert{background:#1a1a1a;color:#fff}
.empty{color:#a3a3a3;font-size:13px;padding:4px 0}

/* severity chips — grayscale */
.severity-chip{padding:1px 7px;border-radius:8px;font-size:11px;font-weight:700;letter-spacing:0.02em;white-space:nowrap}
.severity-chip.halt{background:#1a1a1a;color:#fff}
.severity-chip.warning{background:#737373;color:#fff}
.severity-chip.watch{background:#d4d4d4;color:#1a1a1a}
.severity-chip.long{background:#1a1a1a;color:#fff}
.severity-chip.short{background:#d4d4d4;color:#1a1a1a}

/* data table */
.data-table{width:100%;border-collapse:collapse;font-size:13.5px;line-height:1.5}
.data-table th{background:#f5f5f5;color:#737373;padding:9px 14px;text-align:left;font-weight:600;border-bottom:1px solid #d4d4d4;white-space:nowrap;font-size:12px;letter-spacing:0.04em;text-transform:uppercase}
.data-table td{padding:9px 14px;border-bottom:1px solid #f0f0f0;vertical-align:top;color:#1a1a1a}
.data-table tr:last-child td{border-bottom:none}
.data-table tbody tr:nth-child(2n) td{background:#fafafa}
/* row-level severity (overrides zebra) */
.data-table tr.row-halt > td,
.data-table tr.row-halt:nth-child(2n) > td{background:#f0f0f0;border-left:0;font-weight:600}
.data-table tr.row-halt > td:first-child{border-left:4px solid #1a1a1a;padding-left:10px}
.data-table tr.row-long > td,
.data-table tr.row-long:nth-child(2n) > td{background:#f5f5f5;border-left:0}
.data-table tr.row-long > td:first-child{border-left:4px solid #525252;padding-left:10px}

/* level badges — grayscale */
.level{padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700;display:inline-block;letter-spacing:0.02em}
.level-halt{background:#1a1a1a;color:#fff}
.level-warning{background:#737373;color:#fff}
.level-watch{background:#d4d4d4;color:#1a1a1a}
.spec-over{color:#1a1a1a;font-weight:700}

/* row-overflow (Top-N more) */
details.row-overflow{margin-top:6px}
details.row-overflow > summary{cursor:pointer;list-style:none;padding:6px 10px;background:#f5f5f5;color:#525252;border-radius:6px;font-size:12px;font-weight:600;text-align:center;user-select:none}
details.row-overflow > summary::-webkit-details-marker{display:none}
details.row-overflow > summary:hover{background:#e5e5e5}
details.row-overflow > summary::before{content:"+ "}
details.row-overflow[open] > summary::before{content:"− "}
details.row-overflow[open] > summary{background:#e5e5e5}
details.row-overflow > .data-table{margin-top:6px}

/* group view */
details.group-row{background:#fafafa;border:1px solid #e5e5e5;border-radius:8px;margin-bottom:6px;overflow:hidden}
details.group-row > summary{cursor:pointer;list-style:none;padding:9px 12px;display:flex;align-items:center;gap:10px;font-size:13px;user-select:none;flex-wrap:wrap;color:#1a1a1a}
details.group-row > summary::-webkit-details-marker{display:none}
details.group-row > summary::before{content:"▸";font-size:11px;color:#a3a3a3;width:12px;display:inline-block;transition:transform 0.15s;flex-shrink:0}
details.group-row[open] > summary::before{transform:rotate(90deg)}
details.group-row > summary:hover{background:#f0f0f0}
details.group-row .group-title{font-weight:700;color:#1a1a1a}
details.group-row .group-cnt{margin-left:auto;color:#737373;font-size:12px;font-weight:600}
details.group-row .group-recent{color:#a3a3a3;font-size:11px;margin-left:6px}
details.group-row > .data-table,
details.group-row > details.row-overflow{border-top:1px solid #e5e5e5}
details.group-row > details.row-overflow{margin-top:0;border-radius:0}

/* cell-text expand */
td.cell-text{max-width:380px}
td.cell-text > details{display:inline-block}
td.cell-text > details > summary{cursor:pointer;list-style:none;color:#1a1a1a}
td.cell-text > details > summary::-webkit-details-marker{display:none}
td.cell-text > details > summary:hover{color:#525252;text-decoration:underline}
td.cell-text > details[open] > summary{color:#a3a3a3;font-style:italic}
td.cell-text .cell-full{white-space:pre-wrap;word-break:break-word;color:#1a1a1a;background:#f5f5f5;padding:6px 8px;border-radius:4px;border-left:3px solid #a3a3a3;margin-top:4px}
</style>
"""


_JS = """\
<script>
(function(){
  // anchor 점프 시 target details 엘리먼트 및 조상 details 자동 open
  function expandTargetDetails(){
    var hash = location.hash;
    if(!hash || hash.length < 2) return;
    var el;
    try{ el = document.querySelector(hash); }catch(_){ return; }
    if(!el) return;
    if(el.tagName === 'DETAILS') el.open = true;
    var cur = el.parentNode;
    while(cur && cur !== document){
      if(cur.tagName === 'DETAILS') cur.open = true;
      cur = cur.parentNode;
    }
  }
  window.addEventListener('hashchange', expandTargetDetails);
  // initial
  if(location.hash) setTimeout(expandTargetDetails, 0);

  // highlight chip click → expand + smooth scroll
  document.querySelectorAll('.lot-highlights a.highlight-chip').forEach(function(a){
    a.addEventListener('click', function(e){
      var hash = a.getAttribute('href');
      var target = hash && document.querySelector(hash);
      if(target){
        e.preventDefault();
        if(target.tagName === 'DETAILS') target.open = true;
        history.replaceState(null, '', hash);
        target.scrollIntoView({behavior:'smooth', block:'start'});
      }
    });
  });

  var tabs = document.querySelectorAll('.lot-nav a.tab');
  if(!tabs.length) return;
  // LOT nav smooth scroll
  tabs.forEach(function(a){
    a.addEventListener('click', function(e){
      var hash = a.getAttribute('href');
      var target = hash && document.querySelector(hash);
      if(target){
        e.preventDefault();
        target.scrollIntoView({behavior:'smooth', block:'start'});
        history.replaceState(null, '', hash);
      }
    });
  });
  // active tab via IntersectionObserver
  var byId = {};
  tabs.forEach(function(a){ byId[a.getAttribute('href').slice(1)] = a; });
  if(!('IntersectionObserver' in window)) return;
  var io = new IntersectionObserver(function(entries){
    entries.forEach(function(en){
      if(en.isIntersecting){
        tabs.forEach(function(t){ t.classList.remove('active'); });
        var t = byId[en.target.id];
        if(t) t.classList.add('active');
      }
    });
  }, {rootMargin:'-30% 0px -55% 0px', threshold:0});
  Object.keys(byId).forEach(function(id){
    var el = document.getElementById(id);
    if(el) io.observe(el);
  });
})();
</script>
"""


_FDC_RANK = {"HALT": 0, "WARNING": 1, "WATCH": 2}
_MIN_DT = _dt.datetime(1900, 1, 1)
_TOP_N = 8
_GROUP_MIN_ROWS = 5
_GROUP_MIN_KEYS = 2
_CELL_TEXT_MAX = 60
_LONG_DELAY_MIN = 24 * 60  # 24h


def _safe_id(lot_id: str) -> str:
    """anchor id로 안전한 슬러그."""
    return re.sub(r'[^A-Za-z0-9_-]', '_', str(lot_id))


def _tm_sort_key(v: Any) -> tuple:
    """datetime sort key — 누락은 가장 오래된 것으로 처리, reverse=True와 호환."""
    if isinstance(v, _dt.datetime):
        return (1, v)
    return (0, _MIN_DT)


def _parse_delay_minutes(delay_str: Any) -> int:
    """`Xd HH:MM:SS` 또는 `HH:MM:SS` → 총 분. 파싱 실패 시 0."""
    if delay_str is None:
        return 0
    s = str(delay_str).strip()
    m = re.match(r'^(?:(\d+)d\s+)?(\d+):(\d+):(\d+)$', s)
    if not m:
        return 0
    days = int(m.group(1) or 0)
    hours = int(m.group(2))
    mins = int(m.group(3))
    return days * 24 * 60 + hours * 60 + mins


def _top_n_split(rows: List[Dict], n: int = _TOP_N) -> tuple:
    """(visible, overflow) 분할."""
    if len(rows) <= n:
        return (rows, [])
    return (rows[:n], rows[n:])


def _render_cell_text(value: Any, max_chars: int = _CELL_TEXT_MAX) -> str:
    """긴 텍스트는 <details>로 토글, 짧으면 평문."""
    s = "" if value is None else str(value)
    if len(s) <= max_chars:
        return _h(s)
    short = s[:max_chars]
    return (
        f'<details><summary title="{_h(s)}">{_h(short)}…</summary>'
        f'<div class="cell-full">{_h(s)}</div></details>'
    )


def _fdc_chips(rows: List[Dict]) -> str:
    halt = sum(1 for r in rows if r.get("alarm_level_cd") == "HALT")
    warn = sum(1 for r in rows if r.get("alarm_level_cd") == "WARNING")
    watch = sum(1 for r in rows if r.get("alarm_level_cd") == "WATCH")
    parts = []
    if halt:
        parts.append(f'<span class="severity-chip halt">HALT {halt}</span>')
    if warn:
        parts.append(f'<span class="severity-chip warning">WARN {warn}</span>')
    if watch:
        parts.append(f'<span class="severity-chip watch">WATCH {watch}</span>')
    return " ".join(parts)


def _trouble_chips(rows: List[Dict]) -> str:
    long = sum(1 for r in rows if _parse_delay_minutes(r.get("delay_time")) >= _LONG_DELAY_MIN)
    short = len(rows) - long
    parts = []
    if long:
        parts.append(f'<span class="severity-chip long">장기 {long}</span>')
    if short:
        parts.append(f'<span class="severity-chip short">단기 {short}</span>')
    return " ".join(parts)


# ── 정렬 ────────────────────────────────────────────────────


def _sort_fdc(rows: List[Dict]) -> List[Dict]:
    rs = sorted(rows, key=lambda r: _tm_sort_key(r.get("transfer_tm")), reverse=True)
    return sorted(rs, key=lambda r: _FDC_RANK.get(r.get("alarm_level_cd") or "", 9))


def _sort_qtime(rows: List[Dict]) -> List[Dict]:
    def key(r):
        try:
            return abs(float(r.get("bal") or 0))
        except (TypeError, ValueError):
            return 0.0
    return sorted(rows, key=key, reverse=True)


def _sort_trouble(rows: List[Dict]) -> List[Dict]:
    return sorted(rows, key=lambda r: _parse_delay_minutes(r.get("delay_time")), reverse=True)


def _sort_action(rows: List[Dict]) -> List[Dict]:
    return sorted(rows, key=lambda r: _tm_sort_key(r.get("action_time")), reverse=True)


# ── 섹션 헤더 헬퍼 ──────────────────────────────────────────


def _section_head(icon: str, title: str, chips_html: str, cnt: int, default_open: bool, section_id: str = "") -> str:
    """공통 섹션 헤더 + 본문 시작 태그. 호출자는 본문 + '</div></details>' 추가.

    section_id 가 주어지면 highlight chip의 anchor 타겟 (`#lot-{LOT}-{kind}`)으로 사용.
    default_open 인자는 시그니처 호환을 위해 유지하지만 모든 섹션은 default-closed.
    사용자가 명시적으로 클릭(또는 highlight chip anchor 점프)으로만 펼침.
    """
    del default_open  # default-closed 정책으로 인자 무시
    cls_extra = "" if cnt else " empty-section"
    cnt_cls = "cnt-alert" if cnt else "cnt"
    chips_part = f' {chips_html}' if chips_html else ""
    id_attr = f' id="{section_id}"' if section_id else ""
    return (
        f'<details class="section{cls_extra}"{id_attr}>'
        f'<summary>'
        f'<span class="section-title-text"><span class="icon">{icon}</span> {title}{chips_part}</span>'
        f'<span class="section-title-spacer"></span>'
        f'<span class="cnt {cnt_cls}">{cnt}건</span>'
        f'</summary>'
        f'<div class="section-body">'
    )


def _wrap_overflow(table_fn, rows_overflow: List[Dict]) -> str:
    n = len(rows_overflow)
    if not n:
        return ""
    return (
        f'<details class="row-overflow"><summary>{n}건 더보기</summary>'
        f'{table_fn(rows_overflow)}'
        f'</details>'
    )


# ── FDC ALARM ───────────────────────────────────────────────


def _fdc_table(rows: List[Dict]) -> str:
    html = (
        '<table class="data-table"><thead><tr>'
        '<th>시간</th><th>장비</th><th>항목</th><th>레벨</th><th>결과값</th><th>SPEC 범위</th>'
        '</tr></thead><tbody>'
    )
    for r in rows:
        tm = _h(str(r.get("transfer_tm", ""))[5:16])
        level = r.get("alarm_level_cd", "") or ""
        level_cls = f"level-{level.lower()}" if level else ""
        row_cls = ' class="row-halt"' if level == "HALT" else ""
        html += (
            f'<tr{row_cls}><td>{tm}</td><td>{_h(r.get("eqp_id"))}</td><td>{_h(r.get("item_nm"))}</td>'
            f'<td><span class="level {level_cls}">{_h(level)}</span></td>'
            f'<td class="spec-over">{_h(r.get("rslt_val"))}</td>'
            f'<td>{_h(r.get("spec_min_val"))} ~ {_h(r.get("spec_max_val"))}</td></tr>'
        )
    return html + '</tbody></table>'


def _render_flat_fdc(rows: List[Dict]) -> str:
    visible, overflow = _top_n_split(rows)
    return _fdc_table(visible) + _wrap_overflow(_fdc_table, overflow)


def _render_grouped_fdc(rows: List[Dict]) -> str:
    """eqp_id로 그룹핑. HALT 많은 그룹 먼저, HALT 있는 그룹은 default open."""
    groups: Dict[str, List[Dict]] = {}
    for r in rows:
        groups.setdefault(r.get("eqp_id") or "(N/A)", []).append(r)

    def gkey(item):
        _eqp, grows = item
        halt_n = sum(1 for r in grows if r.get("alarm_level_cd") == "HALT")
        return (-halt_n, -len(grows))

    html = ""
    for eqp, grows in sorted(groups.items(), key=gkey):
        # 그룹 내 정렬도 동일 룰 적용
        grows_sorted = _sort_fdc(grows)
        chips = _fdc_chips(grows_sorted)
        most_recent = ""
        for r in grows_sorted:
            tm = r.get("transfer_tm")
            if tm:
                most_recent = str(tm)[5:10]
                break
        recent_html = f'<span class="group-recent">최근 {_h(most_recent)}</span>' if most_recent else ""
        html += (
            f'<details class="group-row"><summary>'
            f'<span class="group-title">{_h(eqp)}</span>'
            f'{chips}'
            f'<span class="group-cnt">{len(grows_sorted)}건</span>'
            f'{recent_html}'
            f'</summary>'
        )
        html += _render_flat_fdc(grows_sorted)
        html += '</details>'
    return html


def _render_fdc_section(rows: List[Dict], default_open: bool, section_id: str = "") -> str:
    cnt = len(rows)
    chips_html = _fdc_chips(rows) if cnt else ""
    head = _section_head("🔴", "FDC ALARM", chips_html, cnt, default_open, section_id)
    if not cnt:
        return head + '<div class="empty">해당 없음</div></div></details>'
    sorted_rows = _sort_fdc(rows)
    distinct_eqp = {r.get("eqp_id") for r in sorted_rows if r.get("eqp_id")}
    if cnt >= _GROUP_MIN_ROWS and len(distinct_eqp) >= _GROUP_MIN_KEYS:
        body = _render_grouped_fdc(sorted_rows)
    else:
        body = _render_flat_fdc(sorted_rows)
    return head + body + '</div></details>'


# ── Q-TIME ──────────────────────────────────────────────────


def _qtime_table(rows: List[Dict]) -> str:
    html = (
        '<table class="data-table"><thead><tr>'
        '<th>FROM</th><th>→</th><th>TO</th><th>제한</th><th>실제</th><th>초과</th>'
        '</tr></thead><tbody>'
    )
    for r in rows:
        html += (
            f'<tr><td>{_h(r.get("from_oper"))}</td><td>→</td><td>{_h(r.get("to_oper"))}</td>'
            f'<td>{_h(r.get("control_limit"))}분</td>'
            f'<td class="spec-over">{_h(r.get("q_time"))}분</td>'
            f'<td class="spec-over">{_h(r.get("bal"))}분</td></tr>'
        )
    return html + '</tbody></table>'


def _render_qtime_section(rows: List[Dict], default_open: bool, section_id: str = "") -> str:
    cnt = len(rows)
    head = _section_head("⏱", "Q-TIME 초과", "", cnt, default_open, section_id)
    if not cnt:
        return head + '<div class="empty">해당 없음</div></div></details>'
    sorted_rows = _sort_qtime(rows)
    visible, overflow = _top_n_split(sorted_rows)
    body = _qtime_table(visible) + _wrap_overflow(_qtime_table, overflow)
    return head + body + '</div></details>'


# ── TROUBLE LOT ─────────────────────────────────────────────


def _trouble_table(rows: List[Dict]) -> str:
    html = (
        '<table class="data-table"><thead><tr>'
        '<th>Hold시간</th><th>공정</th><th>원인장비</th><th>지연시간</th><th>코드</th><th>내용</th>'
        '</tr></thead><tbody>'
    )
    for r in rows:
        tm = _h(str(r.get("hold_time", ""))[5:16])
        content_html = _render_cell_text(r.get("contents"))
        is_long = _parse_delay_minutes(r.get("delay_time")) >= _LONG_DELAY_MIN
        row_cls = ' class="row-long"' if is_long else ""
        html += (
            f'<tr{row_cls}><td>{tm}</td><td>{_h(r.get("step_desc"))}</td><td>{_h(r.get("cause_eq"))}</td>'
            f'<td>{_h(r.get("delay_time"))}</td><td>{_h(r.get("h_code"))}</td>'
            f'<td class="cell-text">{content_html}</td></tr>'
        )
    return html + '</tbody></table>'


def _render_flat_trouble(rows: List[Dict]) -> str:
    visible, overflow = _top_n_split(rows)
    return _trouble_table(visible) + _wrap_overflow(_trouble_table, overflow)


def _render_grouped_trouble(rows: List[Dict]) -> str:
    """cause_eq로 그룹핑. 장기 지연 많은 그룹 먼저, 장기 있으면 default open."""
    groups: Dict[str, List[Dict]] = {}
    for r in rows:
        groups.setdefault(r.get("cause_eq") or "(N/A)", []).append(r)

    def gkey(item):
        _eq, grows = item
        long_n = sum(1 for r in grows if _parse_delay_minutes(r.get("delay_time")) >= _LONG_DELAY_MIN)
        return (-long_n, -len(grows))

    html = ""
    for eq, grows in sorted(groups.items(), key=gkey):
        grows_sorted = _sort_trouble(grows)
        chips = _trouble_chips(grows_sorted)
        most_recent = ""
        for r in grows_sorted:
            tm = r.get("hold_time")
            if tm:
                most_recent = str(tm)[5:10]
                break
        recent_html = f'<span class="group-recent">최근 {_h(most_recent)}</span>' if most_recent else ""
        html += (
            f'<details class="group-row"><summary>'
            f'<span class="group-title">{_h(eq)}</span>'
            f'{chips}'
            f'<span class="group-cnt">{len(grows_sorted)}건</span>'
            f'{recent_html}'
            f'</summary>'
        )
        html += _render_flat_trouble(grows_sorted)
        html += '</details>'
    return html


def _render_trouble_section(rows: List[Dict], default_open: bool, section_id: str = "") -> str:
    cnt = len(rows)
    chips_html = _trouble_chips(rows) if cnt else ""
    head = _section_head("🔧", "TROUBLE LOT", chips_html, cnt, default_open, section_id)
    if not cnt:
        return head + '<div class="empty">해당 없음</div></div></details>'
    sorted_rows = _sort_trouble(rows)
    distinct_eq = {r.get("cause_eq") for r in sorted_rows if r.get("cause_eq")}
    if cnt >= _GROUP_MIN_ROWS and len(distinct_eq) >= _GROUP_MIN_KEYS:
        body = _render_grouped_trouble(sorted_rows)
    else:
        body = _render_flat_trouble(sorted_rows)
    return head + body + '</div></details>'


# ── FUTURE ACTION ───────────────────────────────────────────


def _action_table(rows: List[Dict]) -> str:
    html = (
        '<table class="data-table"><thead><tr>'
        '<th>시간</th><th>STEP</th><th>내용</th><th>FLAG</th><th>영역</th>'
        '</tr></thead><tbody>'
    )
    for r in rows:
        tm = _h(str(r.get("action_time", ""))[5:16])
        comment_html = _render_cell_text(r.get("action_comment"))
        html += (
            f'<tr><td>{tm}</td><td>{_h(r.get("action_step"))}</td>'
            f'<td class="cell-text">{comment_html}</td>'
            f'<td>{_h(r.get("action_flag"))}</td><td>{_h(r.get("reason_area"))}</td></tr>'
        )
    return html + '</tbody></table>'


def _render_action_section(rows: List[Dict], default_open: bool, section_id: str = "") -> str:
    cnt = len(rows)
    head = _section_head("📌", "FUTURE ACTION", "", cnt, default_open, section_id)
    if not cnt:
        return head + '<div class="empty">해당 없음</div></div></details>'
    sorted_rows = _sort_action(rows)
    visible, overflow = _top_n_split(sorted_rows)
    body = _action_table(visible) + _wrap_overflow(_action_table, overflow)
    return head + body + '</div></details>'


# ── SAMPLE SPLIT ────────────────────────────────────────────


def _sample_table(rows: List[Dict]) -> str:
    html = (
        '<table class="data-table"><thead><tr>'
        '<th>이벤트</th><th>STEP</th><th>공정</th><th>슬롯</th><th>SPLIT_ID</th><th>수량</th>'
        '</tr></thead><tbody>'
    )
    for r in rows:
        html += (
            f'<tr><td>{_h(r.get("event"))}</td><td>{_h(r.get("step"))}</td><td>{_h(r.get("oper_desc"))}</td>'
            f'<td>{_h(r.get("sample_slot"))}</td><td>{_h(r.get("sample_split_id"))}</td><td>{_h(r.get("qty"))}</td></tr>'
        )
    return html + '</tbody></table>'


def _render_sample_section(rows: List[Dict], default_open: bool, section_id: str = "") -> str:
    cnt = len(rows)
    head = _section_head("🧪", "SAMPLE SPLIT", "", cnt, default_open, section_id)
    if not cnt:
        return head + '<div class="empty">해당 없음</div></div></details>'
    visible, overflow = _top_n_split(rows)
    body = _sample_table(visible) + _wrap_overflow(_sample_table, overflow)
    return head + body + '</div></details>'


# ── 위험도/요약 바 ──────────────────────────────────────────


def _risk_level(data: Dict[str, List[Dict]]) -> str:
    for row in data.get("fdc_alarm", []):
        if row.get("alarm_level_cd") == "HALT":
            return "red"
    if data.get("fdc_alarm") or data.get("qtime_over") or data.get("trouble_lot"):
        return "yellow"
    return "green"


# ── 카드 / 전체 ─────────────────────────────────────────────


def _render_lot_highlights(lot_safe_id: str, data: Dict[str, List[Dict]]) -> str:
    """LOT 카드 헤더 바로 아래의 '핵심 이슈' 미리보기 박스.

    HALT FDC, 장기 Hold(24h+), Q-TIME 초과 중 가장 두드러진 1건씩 chip으로 노출.
    chip 클릭 시 해당 섹션 anchor 점프(`#lot-{ID}-{kind}`).
    셋 다 0이면 라이트 톤 "특이사항 없음".
    """
    chips: List[str] = []

    halt_rows = [r for r in data.get("fdc_alarm", []) if r.get("alarm_level_cd") == "HALT"]
    if halt_rows:
        # 최신 HALT 1건
        halt_rows_sorted = sorted(halt_rows, key=lambda r: _tm_sort_key(r.get("transfer_tm")), reverse=True)
        top = halt_rows_sorted[0]
        eqp = _h(top.get("eqp_id"))
        item = _h(top.get("item_nm"))
        chips.append(
            f'<a class="highlight-chip halt" href="#lot-{lot_safe_id}-fdc">'
            f'<span class="hl-tag">HALT</span>'
            f'<span class="hl-detail">{eqp} · {item}</span>'
            f'</a>'
        )

    long_rows = [r for r in data.get("trouble_lot", [])
                 if _parse_delay_minutes(r.get("delay_time")) >= _LONG_DELAY_MIN]
    if long_rows:
        # 가장 긴 지연 1건
        top = max(long_rows, key=lambda r: _parse_delay_minutes(r.get("delay_time")))
        eq = _h(top.get("cause_eq"))
        delay = _h(top.get("delay_time"))
        chips.append(
            f'<a class="highlight-chip long" href="#lot-{lot_safe_id}-trouble">'
            f'<span class="hl-tag">장기 HOLD</span>'
            f'<span class="hl-detail">{eq} · {delay}</span>'
            f'</a>'
        )

    qtime_rows = data.get("qtime_over", [])
    if qtime_rows:
        def _bal_abs(r):
            try:
                return abs(float(r.get("bal") or 0))
            except (TypeError, ValueError):
                return 0.0
        top = max(qtime_rows, key=_bal_abs)
        bal = _h(top.get("bal"))
        from_op = str(top.get("from_oper") or "").split()[0] if top.get("from_oper") else ""
        chips.append(
            f'<a class="highlight-chip qtime" href="#lot-{lot_safe_id}-qtime">'
            f'<span class="hl-tag">Q-TIME</span>'
            f'<span class="hl-detail">{bal}분 · {_h(from_op)}…</span>'
            f'</a>'
        )

    if not chips:
        return '<div class="lot-highlights"><span class="hl-clear">✓ 특이사항 없음</span></div>'
    return (
        '<div class="lot-highlights">'
        '<span class="hl-label">핵심 이슈</span>'
        + "".join(chips) +
        '</div>'
    )


def _render_lot_card(lot_id: str, data: Dict[str, List[Dict]]) -> str:
    """단일 LOT 카드 HTML"""
    risk = _risk_level(data)
    risk_label = {"red": "HIGH RISK", "yellow": "MEDIUM", "green": "NORMAL"}[risk]
    safe = _safe_id(lot_id)

    lot_cd = ""
    for key in ["fdc_alarm", "qtime_over", "trouble_lot"]:
        if data.get(key):
            lot_cd = data[key][0].get("lot_cd", "")
            break

    fdc_rows = data.get("fdc_alarm", [])
    qtime_rows = data.get("qtime_over", [])
    trouble_rows = data.get("trouble_lot", [])
    action_rows = data.get("future_action", [])
    sample_rows = data.get("sample_split", [])

    fdc_open = bool(fdc_rows) and (
        any(r.get("alarm_level_cd") == "HALT" for r in fdc_rows) or len(fdc_rows) <= _TOP_N
    )
    qtime_open = bool(qtime_rows)
    trouble_open = bool(trouble_rows) and (
        any(_parse_delay_minutes(r.get("delay_time")) >= _LONG_DELAY_MIN for r in trouble_rows)
        or len(trouble_rows) <= 5
    )

    html = f'<div class="lot-card risk-{risk}" id="lot-{safe}">'
    html += f'<div class="lot-card-header"><h2>{_h(lot_id)}</h2>'
    if lot_cd:
        html += f'<span class="badge badge-{risk}">{_h(lot_cd)}</span>'
    html += f'<span class="badge badge-{risk}">● {risk_label}</span></div>'

    html += _render_lot_highlights(safe, data)

    html += _render_fdc_section(fdc_rows, default_open=fdc_open, section_id=f"lot-{safe}-fdc")
    html += _render_qtime_section(qtime_rows, default_open=qtime_open, section_id=f"lot-{safe}-qtime")
    html += _render_trouble_section(trouble_rows, default_open=trouble_open, section_id=f"lot-{safe}-trouble")
    html += _render_action_section(action_rows, default_open=False, section_id=f"lot-{safe}-action")
    html += _render_sample_section(sample_rows, default_open=False, section_id=f"lot-{safe}-sample")
    html += '</div>'
    return html


def _render_lot_nav(lot_ids: List[str], all_results: Dict[str, Dict[str, List[Dict]]]) -> str:
    html = '<nav class="lot-nav">'
    for lid in lot_ids:
        risk = _risk_level(all_results[lid])
        html += (
            f'<a href="#lot-{_safe_id(lid)}" class="tab">'
            f'<span class="risk-dot risk-{risk}">●</span> {_h(lid)}'
            f'</a>'
        )
    html += '</nav>'
    return html


def _render_lot_history_html(all_results: Dict[str, Dict[str, List[Dict]]]) -> str:
    """전체 결과를 HTML로 렌더링"""
    lot_ids = list(all_results.keys())
    multi = len(lot_ids) > 1

    html = _CSS
    html += '<div class="header"><h1>📋 LOT 종합 이력</h1>'
    if multi:
        html += f'<span class="lot-count">{len(lot_ids)} LOTs</span>'
    html += '</div>'

    if multi:
        html += _render_lot_nav(lot_ids, all_results)
        html += '<table class="summary-table"><thead><tr>'
        html += '<th style="text-align:left">LOT_ID</th><th>FDC</th><th>Q-TIME</th><th>TROUBLE</th><th>ACTION</th><th>SAMPLE</th><th>위험도</th>'
        html += '</tr></thead><tbody>'
        for lid in lot_ids:
            d = all_results[lid]
            risk = _risk_level(d)

            def _cnt_td(n: int) -> str:
                cls = "cnt-nonzero" if n else "cnt-zero"
                return f'<td class="{cls}">{n}</td>'

            html += (
                f'<tr><td class="lot-id">'
                f'<a href="#lot-{_safe_id(lid)}">{_h(lid)}</a>'
                f'</td>'
            )
            html += _cnt_td(len(d.get("fdc_alarm", [])))
            html += _cnt_td(len(d.get("qtime_over", [])))
            html += _cnt_td(len(d.get("trouble_lot", [])))
            html += _cnt_td(len(d.get("future_action", [])))
            html += _cnt_td(len(d.get("sample_split", [])))
            html += f'<td><span class="risk-{risk}">●</span></td></tr>'
        html += '</tbody></table>'

    for lid in lot_ids:
        html += _render_lot_card(lid, all_results[lid])

    html += _JS
    return html


# ── Agent 노드 ──────────────────────────────────────────────

@observe(name="lot_history_agent_node")
@timed
def lot_history_agent_node(state: dict, config: RunnableConfig) -> dict:
    """LOT History deterministic 노드 — ReAct 없이 query_lot_history 직접 호출.

    LangGraph canonical pattern (custom-workflow "Deterministic node"): LLM reasoning/
    tool-selection 불필요한 단일 도구 worker는 plain function node가 최적. create_react_agent는
    langgraph v1에서 deprecated이고, lot_history는 reasoning 자체가 불필요 → deterministic
    function 전환. C1 패턴(lot_history_sql_result AIMessage)으로 downstream chained 확장 지원.
    """
    lh_lot_ids = state.get("lh_lot_ids", "")
    current_task_id = state.get("current_task_id", "")
    logger.info("[LOT History Agent] lot_ids=%s", lh_lot_ids)

    if not lh_lot_ids:
        msg = AIMessage(
            content="LOT ID가 제공되지 않았습니다. 조회하려면 LOT ID를 알려주세요.",
            name="lot_history_agent",
        )
        return {
            "messages": [msg],
            "lot_history_artifacts": [],
            "past_steps": [(current_task_id, "LOT ID 없음 — 조회 스킵")],
        }

    # 요청별 격리된 저장소 초기화 + @tool decorated 함수 직접 호출 (LLM 불필요)
    storage: Dict[str, Any] = {}
    _tool_payload_var.set(storage)

    try:
        tool_summary = query_lot_history.invoke({"lot_ids": lh_lot_ids})
    except Exception as e:
        if is_transient_error(e):
            logger.warning("[LOT History Agent] transient 오류, retry 위임: %s", e)
            raise
        logger.error("[LOT History Agent] 영구 오류: %s", e, exc_info=True)
        error_message = AIMessage(
            content=f"LOT 이력 조회 중 오류가 발생했습니다: {e}",
            name="lot_history_agent",
        )
        return {
            "messages": [error_message],
            "lot_history_artifacts": [],
            "past_steps": [(current_task_id, f"LOT 이력 영구 오류: {e}")],
        }

    # ContextVar에서 structured 결과 추출 → HTML 렌더링
    lot_history_data = storage.get("lot_history")
    artifacts = []
    if isinstance(lot_history_data, dict) and "error" not in lot_history_data and lot_history_data:
        html = _render_lot_history_html(lot_history_data)
        artifacts.append({
            "type": "html",
            "mime": "text/html",
            "data": html,
            "title": "lot_history_report",
        })

    # query_lot_history tool의 한국어 요약을 사용자 메시지로 사용
    answer = tool_summary if isinstance(tool_summary, str) else str(tool_summary)
    result_message = AIMessage(content=answer, name="lot_history_agent")

    # C1 패턴 확장: lot_history_sql_result structured AIMessage 발행.
    # downstream chained task가 per-lot 위험도에 접근할 수 있도록 additional_kwargs에 dict 저장.
    out_messages: list = [result_message]
    if isinstance(lot_history_data, dict) and "error" not in lot_history_data and lot_history_data:
        per_lot_summary = {
            lid: {
                "fdc_alarm_count": len(data.get("fdc_alarm", [])),
                "qtime_over_count": len(data.get("qtime_over", [])),
                "trouble_lot_count": len(data.get("trouble_lot", [])),
                "future_action_count": len(data.get("future_action", [])),
                "sample_split_count": len(data.get("sample_split", [])),
            }
            for lid, data in lot_history_data.items()
        }
        sql_result_msg = AIMessage(
            content=f"[LOT History 결과] {len(per_lot_summary)}개 LOT 이력 조회 완료",
            name="lot_history_sql_result",
            additional_kwargs={
                "lot_history_result": {
                    "lot_ids": list(per_lot_summary.keys()),
                    "per_lot_summary": per_lot_summary,
                },
            },
        )
        out_messages.insert(0, sql_result_msg)

    return {
        "messages": out_messages,
        "lot_history_artifacts": artifacts,
        "agent_suggestion": "",
        "past_steps": [(current_task_id, answer[:300])],
    }

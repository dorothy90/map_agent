# ============================================================
# 1. 환경 설정
# ============================================================
from dotenv import load_dotenv

load_dotenv(override=True)

import os
import time
import httpx
import logging
import functools
from datetime import date, datetime, timedelta
from tabulate import tabulate

from langchain_core.messages import convert_to_messages, AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI

# ── Langfuse 트레이싱 ────────────────────────────────────
from langfuse import observe

# ── 로깅 설정 ────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("yield_agent")


def timed(func):
    """함수 실행 시간을 측정하는 데코레이터"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        logger.info(f"▶ {func.__name__} 시작")
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        logger.info(f"◀ {func.__name__} 완료 ({elapsed:.2f}s)")
        return result
    return wrapper


# ============================================================
# 2. 헬퍼 함수
# ============================================================
def pretty_print_message(message, indent=False):
    """개별 메시지를 포맷팅하여 출력합니다."""
    pretty_message = message.pretty_repr(html=True)
    if not indent:
        print(pretty_message)
        return

    indented = "\n".join("\t" + c for c in pretty_message.split("\n"))
    print(indented)


def pretty_print_messages(update, last_message=False):
    """그래프 실행 결과의 메시지 업데이트를 포맷팅하여 출력합니다."""
    is_subgraph = False

    if isinstance(update, tuple):
        ns, update = update
        if len(ns) == 0:
            return

        graph_id = ns[-1].split(":")[0]
        print(f"[서브그래프 {graph_id}로부터 업데이트]")
        print("\n")
        is_subgraph = True

    for node_name, node_update in update.items():
        if node_update is None or "messages" not in node_update:
            continue
        update_label = f"[노드 {node_name}로부터 업데이트]"
        if is_subgraph:
            update_label = "\t" + update_label

        print(update_label)
        print("\n")

        messages = convert_to_messages(node_update["messages"])
        if last_message:
            messages = messages[-1:]

        for m in messages:
            pretty_print_message(m, indent=is_subgraph)
        print("\n")


# ============================================================
# 3. 날짜 유틸리티
# ============================================================
def _iso_week_str(d: date) -> str:
    """date -> ISO 주차 문자열 (예: 2026-W06)"""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _week_monday(d: date) -> date:
    """date가 속한 주의 월요일을 반환"""
    return d - timedelta(days=d.isoweekday() - 1)


def _get_4_week_dates(ref: date) -> list[date]:
    """기준 날짜가 속한 주부터 과거 4주의 월요일 리스트 반환 (오래된순)

    예: ref가 2026-W06이면 -> [W03 월요일, W04 월요일, W05 월요일, W06 월요일]
    """
    monday = _week_monday(ref)
    return [monday - timedelta(weeks=i) for i in range(3, -1, -1)]


# ============================================================
# 4. FastAPI 호출 함수
# ============================================================
API_BASE_URL = os.getenv("YIELD_API_BASE_URL", "http://127.0.0.1:8000")

# 파라미터 컬럼 순서 (API 응답의 키 중 lotcount, wfCount 제외한 pt1h 파라미터)
PARA_COLUMNS = [
    "VTH", "IDSAT", "IDLIN", "IOFF", "ION", "IGATE", "IDDQ",
    "VMIN", "FMAX", "TPD", "GM_MAX", "SS", "DIBL",
    "RON", "RDS_ON", "BVDS",
    "LEAK_ID", "LEAK_IG",
    "CGB", "CGS", "CGD", "CDS",
    "RSH", "RD", "RS",
]


@observe(name="fetch_weekly_data")
@timed
def _fetch_weekly_data(lotcd: str, date_str: str) -> dict | None:
    """FastAPI /pt1h/weekly 엔드포인트에서 1주치 데이터 조회

    Args:
        lotcd: 제품코드 (예: "4SS")
        date_str: YYYYMMDD 형식 날짜

    Returns:
        dict: API 응답 (lotcount, wfCount, VTH, IDSAT, ...) 또는 None
    """
    url = f"{API_BASE_URL}/pt1h/weekly"
    params = {
        "lotcd": lotcd,
        "unit": "weekly",
        "process": "pt1h",
        "date": date_str,
    }
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            # _pt1hbin 접미사 제거 (FastAPI에서 해당 접미사를 붙여서 전송)
            if "pt1hPara" in data and isinstance(data["pt1hPara"], dict):
                data["pt1hPara"] = {
                    k.replace("_pt1hbin", ""): v
                    for k, v in data["pt1hPara"].items()
                }

            return data
    except httpx.HTTPError as e:
        print(f"[ERROR] API 호출 실패 ({date_str}): {e}")
        return None


@observe(name="fetch_4_weeks")
@timed
def _fetch_4_weeks(lotcd: str, ref_date: date) -> list[dict]:
    """기준 날짜로부터 최근 4주 데이터를 순차 조회 (오래된 순)

    Returns:
        list[dict]: 각 dict에 'week' 키가 추가된 API 응답 리스트
    """
    mondays = _get_4_week_dates(ref_date)
    results = []

    for monday in mondays:
        date_str = monday.strftime("%Y%m%d")
        week_str = _iso_week_str(monday)
        print(f"  [API] {week_str} ({date_str}) 조회 중...")

        data = _fetch_weekly_data(lotcd, date_str)
        if data:
            data["week"] = week_str
            results.append(data)
        else:
            results.append({"week": week_str, "lotcount": "-", "wfCount": "-"})

    return results


# ============================================================
# 5. 테이블 생성 함수
# ============================================================
def _build_table(weeks_data: list[dict], lotcd: str, filter_params=None) -> str:
    """4주치 데이터를 테이블 문자열로 변환

    행: 주차 (WEEK)
    열: LOT, WF, VTH, IDSAT, ... (파라미터명)

    Args:
        weeks_data: _fetch_4_weeks 반환값
        lotcd: 제품코드
        filter_params: 표시할 파라미터 목록 (None 또는 빈 리스트 = 전체)

    Returns:
        str: 포맷된 테이블 문자열
    """
    cols = [c for c in PARA_COLUMNS if c in filter_params] if filter_params else PARA_COLUMNS
    headers = ["WEEK", "LOT", "WF"] + cols

    rows = []
    for wd in weeks_data:
        week = wd.get("week", "?")
        lotcount = wd.get("lotcount", "-")
        wf_count = wd.get("wfCount", "-")

        row = [week, lotcount, wf_count]
        for col in cols:
            val = wd.get(col, "-")
            row.append(val)
        rows.append(row)

    title = f"\n[{lotcd}] Weekly pt1h Metrics (최근 4주)\n"
    table = tabulate(rows, headers=headers, tablefmt="grid", numalign="right", stralign="center")

    return title + table


def _build_sparkline_svg(vals: list, param: str) -> str:
    """SVG sparkline 생성 (36x14 pixels, 헤더 다크 배경용 밝은 색상)"""
    valid = [(i, v) for i, v in enumerate(vals) if v is not None]
    if len(valid) < 2:
        return '<svg width="36" height="14"><line x1="0" y1="7" x2="36" y2="7" stroke="#64748b" stroke-width="1"/></svg>'

    all_vals = [v for _, v in valid]
    min_v, max_v = min(all_vals), max(all_vals)
    n = len(vals)
    w, h, margin = 36, 14, 2

    def to_x(idx):
        return int(idx * (w - 1) / max(n - 1, 1))

    def to_y(v):
        if max_v == min_v:
            return h // 2
        return int(margin + (1 - (v - min_v) / (max_v - min_v)) * (h - 2 * margin))

    points = " ".join(f"{to_x(i)},{to_y(v)}" for i, v in valid)

    first_v, last_v = valid[0][1], valid[-1][1]
    delta = last_v - first_v
    is_better = (
        (param in HIGHER_IS_BETTER and delta > 0)
        or (param not in HIGHER_IS_BETTER and delta < 0)
    )
    if max_v == min_v or abs(delta) < (max_v - min_v + 1e-15) * 0.05:
        color = "#94a3b8"
    elif is_better:
        color = "#86efac"
    else:
        color = "#fca5a5"

    return f'<svg width="36" height="14"><polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.5"/></svg>'


def _build_html_table(
    weeks_data: list[dict],
    lotcd: str,
    filter_params=None,
    anomaly_params=None,
) -> str:
    """4주치 데이터를 동적 HTML 테이블로 변환

    Args:
        weeks_data: _fetch_4_weeks 반환값
        lotcd: 제품코드
        filter_params: 표시할 파라미터 목록 (None 또는 빈 리스트 = 전체)
        anomaly_params: _detect_anomalies 반환값 (None이면 하이라이팅 없음)

    Returns:
        str: HTML 문자열 (이상감지 하이라이팅, Sparkline, Δ행 포함)
    """
    cols = [c for c in PARA_COLUMNS if c in filter_params] if filter_params else PARA_COLUMNS
    headers = ["WEEK", "LOT", "WF"] + cols
    BLUE_COLS = {"VTH", "IDSAT"}

    # anomaly lookup dict {param: info}
    anomaly_map = {a["param"]: a for a in anomaly_params} if anomaly_params else {}

    # Δ 계산 (최신주 - 직전주, 절대값)
    delta_vals = {}
    if len(weeks_data) >= 2:
        prev, curr = weeks_data[-2], weeks_data[-1]
        for col in cols:
            pv, cv = prev.get(col), curr.get(col)
            if pv in (None, "-", "") or cv in (None, "-", ""):
                delta_vals[col] = None
                continue
            try:
                delta_vals[col] = float(cv) - float(pv)
            except (TypeError, ValueError):
                delta_vals[col] = None

    # Sparkline 데이터 (컬럼별 4개 float or None)
    sparkline_data = {}
    for col in cols:
        vals = []
        for wd in weeks_data:
            v = wd.get(col)
            if v in (None, "-", ""):
                vals.append(None)
            else:
                try:
                    vals.append(float(v))
                except (TypeError, ValueError):
                    vals.append(None)
        sparkline_data[col] = vals

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
th.th-param {
  width: 44px; height: 100px;
  padding: 0; vertical-align: bottom;
}
th.th-blue { background: #1d4ed8; }
th.th-amber { background: #92400e; }
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
.spark { line-height: 0; }
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
<thead><tr>"""

    # 헤더: WEEK/LOT/WF는 가로, 파라미터는 -90도 회전 + 스파크라인
    for h in headers:
        if h == "WEEK":
            html += '<th class="th-week">WEEK</th>'
        elif h in ("LOT", "WF"):
            html += f'<th class="th-meta">{h}</th>'
        else:
            css = "th-blue" if h in BLUE_COLS else "th-amber"
            badge = ""
            if h in anomaly_map:
                badge = '<span class="badge-d">▼</span>' if anomaly_map[h]["direction"] == "열화" else '<span class="badge-i">▲</span>'
            svg = _build_sparkline_svg(sparkline_data.get(h, []), h)
            tip = ""
            if h in anomaly_map:
                a = anomaly_map[h]
                tip = f' title="{a["prev_val"]} → {a["curr_val"]} ({a["change_pct"]:+.1f}%)"'
            html += (
                f'<th class="th-param {css}"{tip}>'
                f'<div class="th-param-inner">'
                f'<div class="hname">{h}{badge}</div>'
                f'<div class="spark">{svg}</div>'
                f'</div></th>'
            )
    html += "</tr></thead>\n<tbody>\n"

    last_idx = len(weeks_data) - 1
    for i, wd in enumerate(weeks_data):
        html += "<tr>"
        html += f'<td class="td-week">{wd.get("week", "?")}</td>'
        html += f'<td>{wd.get("lotcount", "-")}</td>'
        html += f'<td>{wd.get("wfCount", "-")}</td>'
        for col in cols:
            val = wd.get(col, "-")
            if i == last_idx and col in anomaly_map:
                a = anomaly_map[col]
                css = "degraded" if a["direction"] == "열화" else "improved"
                tip = f"{a['prev_val']} → {a['curr_val']} ({a['change_pct']:+.1f}%)"
                html += f'<td class="{css}" title="{tip}">{val}</td>'
            else:
                html += f"<td>{val}</td>"
        html += "</tr>\n"

    # Δ 행 (최신주 - 직전주)
    if delta_vals:
        html += '<tr class="delta">'
        html += '<td class="delta-week">Δ</td>'
        html += "<td>—</td><td>—</td>"
        for col in cols:
            d = delta_vals.get(col)
            if d is None or abs(d) < 1e-12:
                html += "<td>—</td>"
            else:
                is_better = (
                    (col in HIGHER_IS_BETTER and d > 0)
                    or (col not in HIGHER_IS_BETTER and d < 0)
                )
                css = "delta-pos" if is_better else "delta-neg"
                html += f'<td class="{css}">{d:+.4g}</td>'
        html += "</tr>\n"

    html += "</tbody></table>\n</body></html>"
    return html


# ============================================================
# 5.1 파라미터 극성 정의
# ============================================================
# 값이 높아지면 개선인 파라미터 (2개)
HIGHER_IS_BETTER = {"VTH", "IDSAT"}
# 그 외 나머지 23개: 값이 낮아지면 개선


# ============================================================
# 5.1.1 수치 기반 이상감지
# ============================================================
ANOMALY_THRESHOLD = float(os.getenv("ANOMALY_THRESHOLD", "10.0"))


def _detect_anomalies(weeks_data: list[dict], threshold: float = ANOMALY_THRESHOLD) -> list[dict]:
    """직전주 vs 최신주 파라미터 변화율 계산, |change%| > threshold인 것만 반환

    Args:
        weeks_data: 4주치 데이터 리스트 (오래된 순)
        threshold: 이상 감지 임계값 (기본 10.0%)

    Returns:
        list[dict]: 이상 파라미터 목록 (열화 우선, 변화율 내림차순)
    """
    if len(weeks_data) < 2:
        return []

    prev, curr = weeks_data[-2], weeks_data[-1]
    anomalies = []

    for param in PARA_COLUMNS:
        pv = prev.get(param)
        cv = curr.get(param)
        if pv in (None, "-", 0, "") or cv in (None, "-", ""):
            continue
        try:
            pv, cv = float(pv), float(cv)
        except (TypeError, ValueError):
            continue
        if pv == 0:
            continue
        # 극소값(e.g. 1e-12) 오버플로 방지: abs(pv) < 1e-15 이면 스킵
        if abs(pv) < 1e-15:
            continue

        change_pct = (cv - pv) / abs(pv) * 100
        if abs(change_pct) > threshold:
            is_better = (
                (param in HIGHER_IS_BETTER and change_pct > 0)
                or (param not in HIGHER_IS_BETTER and change_pct < 0)
            )
            direction = "개선" if is_better else "열화"
            anomalies.append({
                "param": param,
                "prev_val": round(pv, 4),
                "curr_val": round(cv, 4),
                "change_pct": round(change_pct, 1),
                "direction": direction,
            })

    # 열화 우선, 변화율 절댓값 내림차순 정렬
    anomalies.sort(key=lambda x: (x["direction"] != "열화", -abs(x["change_pct"])))
    return anomalies


# ============================================================
# 5.2 LLM 분석 함수
# ============================================================
ANALYSIS_SYSTEM_PROMPT = """당신은 반도체 수율(yield) 분석 전문가입니다.
주어진 주차별 pt1h 파라미터 데이터를 분석하여 인사이트를 제공합니다.
항상 한국어로 답변하세요."""

ANALYSIS_USER_PROMPT = """아래는 [{lotcd}] 제품의 최근 4주 pt1h 파라미터 데이터입니다.

{table}

=== 파라미터 극성 규칙 ===
- VTH, IDSAT: 값이 높아지면 개선 (↑ = 개선)
- 그 외 모든 파라미터: 값이 낮아지면 개선 (↓ = 개선)

=== 비교 대상 ===
- 직전주: {prev_week}
- 최신주: {curr_week}

위 두 주차를 비교하여 다음을 분석해주세요:

1. **가장 열화된 파라미터 Top 3** (파라미터명, 직전주 값, 최신주 값, 변화율%)
2. **가장 개선된 파라미터 Top 3** (파라미터명, 직전주 값, 최신주 값, 변화율%)
3. **전반적인 트렌드 요약** (1~2문장)

마크다운 표 형식으로 깔끔하게 정리해주세요."""


@observe(name="analyze_with_llm")
@timed
def _analyze_with_llm(weeks_data: list[dict], table_str: str, lotcd: str, llm, config=None) -> str:
    """최근 2주 데이터를 LLM에게 전달하여 열화/개선 분석을 받는 헬퍼 함수

    Args:
        weeks_data: 4주치 데이터 리스트 (오래된 순)
        table_str: 이미 생성된 테이블 문자열
        lotcd: 제품코드
        llm: ChatOpenAI 인스턴스

    Returns:
        str: LLM 분석 결과 (마크다운)
    """
    if len(weeks_data) < 2:
        return "분석에 필요한 2주 이상의 데이터가 부족합니다."

    prev_week = weeks_data[-2]
    curr_week = weeks_data[-1]

    user_prompt = ANALYSIS_USER_PROMPT.format(
        lotcd=lotcd,
        table=table_str,
        prev_week=prev_week.get("week", "?"),
        curr_week=curr_week.get("week", "?"),
    )

    try:
        response = llm.invoke(
            [
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            config=config,
        )
        return response.content
    except Exception as e:
        print(f"[ERROR] LLM 분석 실패: {e}")
        return f"LLM 분석 중 오류가 발생했습니다: {e}"


# ============================================================
# 6. LLM 모델 설정
# ============================================================
model = ChatOpenAI(
    model="gpt-oss-120b",
    base_url=os.getenv("OPENROUTER_BASE_URL"),
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0,
    request_timeout=180,
)


# ============================================================
# 8. Yield Agent 노드 구현
# ============================================================
@observe(name="yield_agent_node")
@timed
def yield_agent_node(state: dict, config: RunnableConfig) -> dict:
    """Yield Agent 노드: State에서 파라미터를 읽어 API 호출 + 테이블 생성

    Supervisor가 추출한 lotcd, ref_date를 사용하여
    최근 4주 데이터를 조회하고 테이블로 포맷합니다.
    """
    # State에서 파라미터 읽기
    lotcd = state.get("lotcd", "4SS")
    ref_date_str = state.get("ref_date", date.today().strftime("%Y%m%d"))
    filter_params = state.get("filter_params") or None  # 빈 list → None (전체 표시)

    # 날짜 파싱
    try:
        ref_date = datetime.strptime(ref_date_str, "%Y%m%d").date()
    except ValueError:
        ref_date = date.today()

    # 파라미터 로깅
    print("=" * 60)
    print("[Yield Agent] State에서 파라미터 읽기:")
    print(f"  - lotcd: {lotcd}")
    print(f"  - ref_date: {ref_date_str} ({_iso_week_str(ref_date)})")
    print("=" * 60)

    # FastAPI에서 4주치 데이터 조회
    print(f"\n[Yield Agent] {lotcd} 최근 4주 데이터 조회 시작...")
    weeks_data = _fetch_4_weeks(lotcd, ref_date)

    if not weeks_data or all(wd.get("lotcount") == "-" for wd in weeks_data):
        error_message = AIMessage(
            content="데이터를 조회할 수 없습니다. FastAPI 서버가 실행 중인지 확인해주세요.",
            name="yield_agent",
        )
        return {"messages": [error_message], "weeks_data": [], "table_result": ""}

    # 수치 기반 이상감지 (html_table 생성 전에 먼저 계산)
    anomaly_params = _detect_anomalies(weeks_data)
    print(f"[Yield Agent] 이상 감지: {len(anomaly_params)}개 파라미터")

    # 테이블 생성 (텍스트: LLM 분석용, HTML: 사용자 표시용)
    table_str = _build_table(weeks_data, lotcd, filter_params)
    html_table = _build_html_table(weeks_data, lotcd, filter_params, anomaly_params)
    print(table_str)

    # LLM 분석 (최근 2주 비교)
    print(f"\n[Yield Agent] LLM 분석 시작 (최근 2주 비교)...")
    analysis = _analyze_with_llm(weeks_data, table_str, lotcd, model, config=config)
    print(f"\n[LLM 분석 결과]\n{analysis}")

    # HTML 아티팩트 생성
    yield_artifacts = [{
        "type": "html",
        "mime": "text/html",
        "data": html_table,
        "title": "yield_table",
    }]

    # 결과 메시지 생성 (HTML 테이블은 아티팩트로 별도 전달)
    result_msg = f"[{lotcd}] 최근 4주 pt1h 수율 데이터입니다.\n"
    result_msg += f"기준: {_iso_week_str(ref_date)} ({ref_date_str})\n\n"
    result_msg += f"\n\n---\n\n{analysis}"

    if anomaly_params:
        anomaly_lines = "\n".join(
            f"- {a['param']}: {a['prev_val']} → {a['curr_val']} ({a['change_pct']:+.1f}%, {a['direction']})"
            for a in anomaly_params[:5]
        )
        result_msg += f"\n\n---\n\n**⚠️ 이상 감지된 파라미터 ({len(anomaly_params)}개)**\n{anomaly_lines}"
        result_msg += "\n\n> WADS 열화 검출 리포트를 확인하시겠습니까?"
    else:
        result_msg += "\n\n> ✅ 이상 파라미터 없음 (±10% 기준)"

    result_message = AIMessage(content=result_msg, name="yield_agent")

    return {
        "messages": [result_message],
        "weeks_data": weeks_data,
        "table_result": table_str,
        "analysis_result": analysis,
        "yield_artifacts": yield_artifacts,
        "anomaly_params": anomaly_params,
    }



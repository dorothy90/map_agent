"""Cummap grid 성능 프로파일링 — 최근 3주, lotcd=4SS."""
import os, sys, time, logging
from datetime import date, timedelta
from dotenv import load_dotenv

# 프로젝트 루트의 .env 사용
load_dotenv("/Users/daehwankim/yield-agent/.env")
sys.path.insert(0, os.path.dirname(__file__))

# 코드 자체 로그도 보기
logging.basicConfig(level=logging.INFO, format="%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S")

# ── 모듈별 timing instrumentation ──
import map_agent as ma
import yield_viz as yv

orig_query = ma._query_wafer_data_by_date
orig_parse_old = ma._parse_wafer_for_cummap
orig_parse_new = ma._parse_wafer_bins

stats = {"query_n": 0, "query_t": 0.0, "query_rows": 0,
         "parse_n": 0, "parse_t": 0.0}

def timed_query(lotcd, sd, ed, category=None):
    t0 = time.perf_counter()
    res = orig_query(lotcd, sd, ed, category=category)
    dt = time.perf_counter() - t0
    stats["query_n"] += 1; stats["query_t"] += dt; stats["query_rows"] += len(res)
    sample_bytes = sum(len(r["map_val_json"]) for r in res[:50] if r.get("map_val_json"))
    avg = sample_bytes / max(1, min(len(res), 50))
    print(f"  [QUERY] {category} {sd}-{ed}: {len(res)} wafers in {dt:.2f}s (avg JSON ~{avg/1024:.1f}KB)")
    return res

def timed_parse_old(args):
    t0 = time.perf_counter()
    res = orig_parse_old(args)
    stats["parse_n"] += 1; stats["parse_t"] += time.perf_counter() - t0
    return res

def timed_parse_new(map_val_json):
    t0 = time.perf_counter()
    res = orig_parse_new(map_val_json)
    stats["parse_n"] += 1; stats["parse_t"] += time.perf_counter() - t0
    return res

ma._query_wafer_data_by_date = timed_query
ma._parse_wafer_for_cummap = timed_parse_old
ma._parse_wafer_bins = timed_parse_new
# yield_viz 도 from import 흔적 있으므로 패치
yv._query_wafer_data_by_date = timed_query
yv._parse_wafer_bins = timed_parse_new

# 가짜 anomaly_params (PT1H 6개) 로 6 fail 컬럼 강제
from common import CATEGORY_TO_BIN
pt1h_params = list(CATEGORY_TO_BIN.keys())[:6]
print(f"using PT1H fail params: {pt1h_params}")
anomaly_params = (
    [{"param": p, "direction": "개선"} for p in pt1h_params[:3]] +
    [{"param": p, "direction": "열화"} for p in pt1h_params[3:6]]
)

# ── 실행 ──
lotcd = "4SS"
ref = date(2026, 5, 7)
unit = "weekly"
periods = 3   # 최근 3주

print(f"\n=== _build_cummap_grid_html(lotcd={lotcd}, ref={ref}, unit={unit}, periods={periods}) ===\n")

t_total = time.perf_counter()
t_render_start = None

# matplotlib 단독 시간 분리: imshow 호출이 plt.subplots/savefig 안에 있음
# 간단히 grid 계산 끝나는 지점부터 savefig 끝까지 측정 — savefig 만 따로 패치
import matplotlib.pyplot as plt
orig_savefig = plt.Figure.savefig
def timed_savefig(self, *a, **kw):
    t0 = time.perf_counter()
    r = orig_savefig(self, *a, **kw)
    stats["savefig_t"] = stats.get("savefig_t", 0.0) + (time.perf_counter() - t0)
    return r
plt.Figure.savefig = timed_savefig

html = yv._build_cummap_grid_html(lotcd, ref, unit, periods, anomaly_params)
total = time.perf_counter() - t_total

print(f"\n--- 결과 ---")
print(f"total wall:     {total:6.2f}s")
print(f"  Oracle queries: {stats['query_n']:3d}회, {stats['query_t']:.2f}s, rows={stats['query_rows']}")
print(f"  JSON parse:     {stats['parse_n']:5d}회, {stats['parse_t']:.2f}s")
print(f"  matplotlib savefig: {stats.get('savefig_t', 0):.2f}s")
remainder = total - stats['query_t'] - stats['parse_t'] - stats.get('savefig_t', 0)
print(f"  나머지(numpy + plot setup + io 등): {remainder:.2f}s")
print(f"\nHTML size: {len(html)} bytes (PNG embedded)")

"""변경 전(OLD) vs 변경 후(NEW) cummap grid 의 수치 동등성 + perf 비교.

같은 fetch 결과를 가지고 OLD inline 로직과 NEW 함수 경로를 모두 돌린 뒤
모든 cell 의 pass_rate 행렬이 np.allclose(equal_nan=True) 인지 확인.
"""
import os, sys, time, logging
from datetime import date
from concurrent.futures import ThreadPoolExecutor
import multiprocessing as mp
import numpy as np
from dotenv import load_dotenv

load_dotenv("/Users/daehwankim/yield-agent/.env")
sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(level=logging.WARNING)

import map_agent as ma
from common import CATEGORY_TO_BIN, PT1C_COLUMNS

# ── 파라미터 (perf script 와 동일) ──
lotcd = "4SS"
ref = date(2026, 5, 7)
unit = "weekly"
periods = 3

# columns (yield_viz._build_cummap_grid_html 과 동일하게 구성)
PT1C_PARAM_SET = set(PT1C_COLUMNS)
pt1h_params = list(CATEGORY_TO_BIN.keys())[:6]
anomaly_params = (
    [{"param": p, "direction": "개선"} for p in pt1h_params[:3]] +
    [{"param": p, "direction": "열화"} for p in pt1h_params[3:6]]
)
columns: list[tuple[str, str | None, str]] = [
    ("PT1H", None, "PT1H TEST"),
    ("PT1C", None, "PT1C TEST"),
]
improved = [a for a in anomaly_params if a["direction"] == "개선"][:3]
degraded = [a for a in anomaly_params if a["direction"] == "열화"][:3]
for prefix, items in [("개선", improved), ("열화", degraded)]:
    for a in items:
        param = a["param"]
        if param in PT1C_PARAM_SET:
            columns.append((f"{prefix}_{param}", None, "PT1C TEST"))
        elif param in CATEGORY_TO_BIN:
            columns.append((f"{prefix}_{param}", CATEGORY_TO_BIN[param], "PT1H TEST"))

# date_ranges 재구성 (yield_viz 와 동일)
from yield_db import _get_period_date_ranges, DEFAULT_PERIODS
n = periods if periods > 0 else DEFAULT_PERIODS.get(unit, 4)
date_ranges = _get_period_date_ranges(ref, unit, n)[-2:]
n_periods = len(date_ranges)

# ── DB fetch 한 번 ──
needed_cats = {cat for _, _, cat in columns}
fetch_tasks = [(pi, cat) for pi in range(n_periods) for cat in needed_cats]
period_data: dict = {i: {} for i in range(n_periods)}

def _fetch(task):
    pi, cat = task
    dr = date_ranges[pi]
    return pi, cat, ma._query_wafer_data_by_date(lotcd, dr["start"], dr["end"], category=cat)

with ThreadPoolExecutor(max_workers=12) as ex:
    for pi, cat, data in ex.map(_fetch, fetch_tasks):
        period_data[pi][cat] = data
        print(f"  fetched period={date_ranges[pi]['label']} cat={cat}: {len(data)} wafers")

all_wafers = [w for d in period_data.values() for ws in d.values() for w in ws]
min_row, max_row, min_col, max_col = ma._get_map_bounds(all_wafers)
height = max_row - min_row + 1
width = max_col - min_col + 1

# ── OLD: 컬럼별로 매번 _parse_wafer_for_cummap 재호출 ──
def run_old():
    t0 = time.perf_counter()
    parse_calls = [0]
    def counting_parse(args):
        parse_calls[0] += 1
        return ma._parse_wafer_for_cummap(args)
    grid = []
    for pi in range(n_periods):
        row = []
        for _, target_bin, cat in columns:
            wafers = period_data[pi].get(cat, [])
            if not wafers:
                row.append((None, 0.0)); continue
            args_list = [(w["map_val_json"], "left_bin", target_bin) for w in wafers]
            n_workers = min(mp.cpu_count(), len(wafers), 8)
            with ThreadPoolExecutor(max_workers=n_workers) as ex2:
                parsed = list(ex2.map(counting_parse, args_list))
            ar, ac, ap = [], [], []
            for r, c, p in parsed:
                ar.extend(r); ac.extend(c); ap.extend(p)
            arr_rows = np.array(ar) - min_row
            arr_cols = np.array(ac) - min_col
            arr_pass = np.array(ap)
            pass_sum = np.zeros((height, width)); count = np.zeros((height, width))
            np.add.at(pass_sum, (arr_rows, arr_cols), arr_pass)
            np.add.at(count, (arr_rows, arr_cols), 1)
            with np.errstate(divide="ignore", invalid="ignore"):
                pr = np.where(count > 0, pass_sum / count, np.nan)
            valid = pr[~np.isnan(pr)]
            row.append((pr, float(np.mean(valid) * 100) if len(valid) else 0.0))
        grid.append(row)
    return grid, time.perf_counter() - t0, parse_calls[0]

# ── NEW: 새 yield_viz 의 parsed_cache 로직 (현행 코드 그대로) ──
def run_new():
    t0 = time.perf_counter()
    parse_calls = [0]
    def counting_parse(args):
        parse_calls[0] += 1
        return ma._parse_wafer_bins(args)
    cache = {}
    for pi in range(n_periods):
        for cat in needed_cats:
            wafers = period_data[pi].get(cat, [])
            if not wafers: continue
            args_list = [w["map_val_json"] for w in wafers]
            n_workers = min(mp.cpu_count(), len(wafers), 8)
            with ThreadPoolExecutor(max_workers=n_workers) as ex2:
                parsed = list(ex2.map(counting_parse, args_list))
            ar, ac, ab = [], [], []
            for r, c, b in parsed:
                ar.extend(r); ac.extend(c); ab.extend(b)
            if not ar: continue
            arr_rows = np.asarray(ar, dtype=np.int32) - min_row
            arr_cols = np.asarray(ac, dtype=np.int32) - min_col
            arr_bins = np.asarray(ab, dtype="U2")
            count_2d = np.zeros((height, width), dtype=np.int32)
            np.add.at(count_2d, (arr_rows, arr_cols), 1)
            cache[(pi, cat)] = (arr_rows, arr_cols, arr_bins, count_2d)

    grid = []
    for pi in range(n_periods):
        row = []
        for _, target_bin, cat in columns:
            cell = cache.get((pi, cat))
            if cell is None:
                row.append((None, 0.0)); continue
            arr_rows, arr_cols, arr_bins, count_2d = cell
            if target_bin:
                arr_pass = (arr_bins != target_bin).astype(np.int8)
            else:
                arr_pass = (arr_bins == "A").astype(np.int8)
            pass_sum = np.zeros((height, width), dtype=np.int32)
            np.add.at(pass_sum, (arr_rows, arr_cols), arr_pass)
            with np.errstate(divide="ignore", invalid="ignore"):
                pr = np.where(count_2d > 0, pass_sum / count_2d, np.nan)
            valid = pr[~np.isnan(pr)]
            row.append((pr, float(np.mean(valid) * 100) if len(valid) else 0.0))
        grid.append(row)
    return grid, time.perf_counter() - t0, parse_calls[0]

print("\n=== OLD path (parse-per-column) ===")
grid_old, t_old, n_old = run_old()
print(f"  wall={t_old:.2f}s, parse_calls={n_old}")

print("=== NEW path (parse-once + vector mask) ===")
grid_new, t_new, n_new = run_new()
print(f"  wall={t_new:.2f}s, parse_calls={n_new}")
print(f"  speedup: {t_old / t_new:.1f}×, parse {n_old} → {n_new}")

# ── 수치 동등성 ──
print("\n=== 동등성 검사 ===")
mismatches = 0
for pi in range(n_periods):
    for ci, (label, _, _) in enumerate(columns):
        a, _ = grid_old[pi][ci]
        b, _ = grid_new[pi][ci]
        if a is None and b is None:
            continue
        if (a is None) != (b is None):
            print(f"  ! {label} period={pi}: None mismatch (old={a is None}, new={b is None})")
            mismatches += 1
            continue
        if not np.allclose(a, b, equal_nan=True, atol=1e-12):
            diff = np.nanmax(np.abs(a - b))
            print(f"  ! {label} period={pi}: max abs diff = {diff:.2e}")
            mismatches += 1
        else:
            avg_old = grid_old[pi][ci][1]; avg_new = grid_new[pi][ci][1]
            print(f"  ✓ {label} period={pi}: identical (avg_old={avg_old:.2f} avg_new={avg_new:.2f})")

print(f"\n결과: {mismatches} mismatches" if mismatches else "\n결과: ALL IDENTICAL ✓")
sys.exit(0 if mismatches == 0 else 1)

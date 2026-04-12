"""
Map Agent Node — Wafer Map 시각화
==================================
Oracle DB에서 wafer 데이터를 조회하고 binmap/cummap PNG를 생성하여
base64 인코딩 HTML로 반환합니다.

supervisor.py를 import하지 않음 (순환 import 방지).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

import numpy as np
import matplotlib
matplotlib.use("Agg")  # 서버 환경: GUI 없이 PNG 저장
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import platform

from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langfuse import observe, get_client

load_dotenv(override=True)

logger = logging.getLogger("yield_agent.map_agent")

from common import (  # noqa: E402
    get_oracle_connection as _get_oracle_connection_common,
    lot_id_variants,
    timed,
    BIN_CATEGORY_MAP,
    CATEGORY_TO_BIN,
)

ORACLE_TABLE = os.getenv("ORACLE_TABLE", "LANGGRAPH_DATA")

# ── 한글 폰트 설정 ───────────────────────────────────────
_current_os = platform.system()
if _current_os == "Windows":
    _font_path = "C:/Windows/Fonts/malgun.ttf"
    _fontprop = fm.FontProperties(fname=_font_path, size=12)
    plt.rc("font", family=_fontprop.get_name())
elif _current_os == "Darwin":
    plt.rcParams["font.family"] = "AppleGothic"
else:
    try:
        plt.rcParams["font.family"] = "NanumGothic"
    except Exception:
        pass
plt.rcParams["axes.unicode_minus"] = False

# ── orjson fallback ──────────────────────────────────────
try:
    import orjson

    def _fast_json_loads(s):
        return orjson.loads(s)

except ImportError:

    def _fast_json_loads(s):
        return json.loads(s)


# ============================================================
# 내부 DB 조회 함수
# ============================================================
def _query_wafer_data(
    lot_id: Optional[str] = None,
    lot_ids: Optional[str] = None,
    wf_ids: Optional[str] = None,
    groupkey: Optional[str] = None,
    oper: Optional[str] = None,
) -> list:
    """Oracle DB에서 wafer 데이터 조회

    조회 방식:
    1. lot_ids: 여러 lot의 모든 wafer 조회
    2. groupkey: lot_id.wf_id 형식으로 특정 wafer 조회
    3. lot_id + wf_ids: 단일 lot의 특정 wafer 조회
    4. lot_id only: 단일 lot의 모든 wafer 조회
    """
    logger.info("[MapAgent] _query_wafer_data: lot_id=%r, lot_ids=%r, wf_ids=%r, groupkey=%r, oper=%r", lot_id, lot_ids, wf_ids, groupkey, oper)
    oper_filter = ""
    oper_param = {}
    if oper:
        oper_filter = " AND OPER_DET_DESC = :oper"
        oper_param = {"oper": f"{oper} TEST"}
    try:
        conn = _get_oracle_connection_common()
    except Exception as e:
        logger.error("[MapAgent] Oracle 연결 실패: %s", e)
        return []

    try:
        cur = conn.cursor()
        results = []

        if lot_ids:
            lot_list = [x.strip() for x in lot_ids.split(",")]
            # 4↔T 변환: 각 lot_id에 대해 variant 추가
            expanded = []
            for lot in lot_list:
                for v in lot_id_variants(lot):
                    if v not in expanded:
                        expanded.append(v)
            lot_placeholders = ",".join([f":lot{i}" for i in range(len(expanded))])
            params = {f"lot{i}": lot for i, lot in enumerate(expanded)}

            # wf_ids 필터가 있으면 WHERE 절에 추가
            wf_filter = ""
            if wf_ids:
                wf_id_list = [int(x.strip()) for x in wf_ids.split(",")]
                wf_placeholders = ",".join([f":wf{i}" for i in range(len(wf_id_list))])
                wf_filter = f" AND wf_id IN ({wf_placeholders})"
                for i, wf_id in enumerate(wf_id_list):
                    params[f"wf{i}"] = wf_id

            sql = f"""
                SELECT lot_id, wf_id, map_val_json, fab_id, lot_cd, start_tm, end_tm
                FROM {ORACLE_TABLE}
                WHERE lot_id IN ({lot_placeholders}){wf_filter}{oper_filter}
                ORDER BY lot_id, wf_id
                FETCH FIRST 10000 ROWS ONLY
            """
            params.update(oper_param)
            cur.execute(sql, params)
            columns = [desc[0].lower() for desc in cur.description]
            for row in cur.fetchall():
                record = dict(zip(columns, row))
                if record.get("map_val_json") and hasattr(record["map_val_json"], "read"):
                    record["map_val_json"] = record["map_val_json"].read()
                results.append(record)

        elif groupkey:
            specs = [s.strip() for s in groupkey.split(",")]
            for spec in specs:
                if "." in spec:
                    parts = spec.rsplit(".", 1)
                    spec_lot_id = parts[0]
                    spec_wf_id = int(parts[1])
                    variants = lot_id_variants(spec_lot_id)
                    sql = f"""
                        SELECT lot_id, wf_id, map_val_json, fab_id, lot_cd, start_tm, end_tm
                        FROM {ORACLE_TABLE}
                        WHERE lot_id IN (:lot_a, :lot_b) AND wf_id = :wf_id{oper_filter}
                    """
                    cur.execute(sql, {"lot_a": variants[0], "lot_b": variants[-1], "wf_id": spec_wf_id, **oper_param})
                else:
                    variants = lot_id_variants(spec)
                    sql = f"""
                        SELECT lot_id, wf_id, map_val_json, fab_id, lot_cd, start_tm, end_tm
                        FROM {ORACLE_TABLE}
                        WHERE lot_id IN (:lot_a, :lot_b){oper_filter}
                        ORDER BY wf_id
                    """
                    cur.execute(sql, {"lot_a": variants[0], "lot_b": variants[-1], **oper_param})
                columns = [desc[0].lower() for desc in cur.description]
                for row in cur.fetchall():
                    record = dict(zip(columns, row))
                    if record.get("map_val_json") and hasattr(record["map_val_json"], "read"):
                        record["map_val_json"] = record["map_val_json"].read()
                    results.append(record)

        elif lot_id:
            wf_id_list = None
            if wf_ids:
                wf_id_list = [int(x.strip()) for x in wf_ids.split(",")]

            variants = lot_id_variants(lot_id)
            if not wf_id_list:
                sql = f"""
                    SELECT lot_id, wf_id, map_val_json, fab_id, lot_cd, start_tm, end_tm
                    FROM {ORACLE_TABLE}
                    WHERE lot_id IN (:lot_a, :lot_b){oper_filter}
                    ORDER BY wf_id
                    FETCH FIRST 10000 ROWS ONLY
                """
                cur.execute(sql, {"lot_a": variants[0], "lot_b": variants[-1], **oper_param})
            else:
                placeholders = ",".join([f":wf{i}" for i in range(len(wf_id_list))])
                sql = f"""
                    SELECT lot_id, wf_id, map_val_json, fab_id, lot_cd, start_tm, end_tm
                    FROM {ORACLE_TABLE}
                    WHERE lot_id IN (:lot_a, :lot_b) AND wf_id IN ({placeholders}){oper_filter}
                    ORDER BY wf_id
                    FETCH FIRST 10000 ROWS ONLY
                """
                params = {"lot_a": variants[0], "lot_b": variants[-1], **oper_param}
                for i, wf_id in enumerate(wf_id_list):
                    params[f"wf{i}"] = wf_id
                cur.execute(sql, params)

            columns = [desc[0].lower() for desc in cur.description]
            for row in cur.fetchall():
                record = dict(zip(columns, row))
                if record.get("map_val_json") and hasattr(record["map_val_json"], "read"):
                    record["map_val_json"] = record["map_val_json"].read()
                results.append(record)

        logger.info("[MapAgent] _query_wafer_data 완료: %d rows", len(results))
        return results
    except Exception as e:
        logger.error("[MapAgent] wafer 데이터 조회 실패: %s", e, exc_info=True)
        return []
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()


def _parse_map_json(map_val_json) -> dict:
    """map_val_json → {"x_y": {"left_bin": "A", "right_bin": "B"}}"""
    raw = _fast_json_loads(map_val_json) if isinstance(map_val_json, str) else map_val_json
    result = {}
    for item in raw["MAP"]:
        parts = item.split(",")
        x, y = parts[0], parts[1]
        result[f"{x}_{y}"] = {
            "left_bin": parts[2] if len(parts) > 2 else "",
            "right_bin": parts[3] if len(parts) > 3 else "",
        }
    return result


def _parse_wafer_for_cummap(args):
    """단일 wafer 파싱 (cummap 병렬 처리용) → (rows, cols, passes)

    args: (map_val_json, bin_type, target_bin)
      target_bin이 None이면 기존 로직 (A=Pass, 나머지=Fail)
      target_bin이 지정되면 해당 bin이 아닌 die가 Pass (category별 수율)
    """
    map_val_json, bin_type, target_bin = args
    raw = _fast_json_loads(map_val_json) if isinstance(map_val_json, str) else map_val_json
    rows, cols, passes = [], [], []
    bin_idx = 2 if bin_type == "left_bin" else 3
    for item in raw["MAP"]:
        parts = item.split(",")
        rows.append(int(parts[0]))
        cols.append(int(parts[1]))
        bin_val = parts[bin_idx] if len(parts) > bin_idx else ""
        if target_bin:
            passes.append(0 if bin_val == target_bin else 1)
        else:
            passes.append(1 if bin_val == "A" else 0)
    return rows, cols, passes


def _parse_wafer_for_binmap(map_val_json) -> dict:
    """단일 wafer 파싱 (binmap 병렬 처리용) → {"x_y": {...}}"""
    raw = _fast_json_loads(map_val_json) if isinstance(map_val_json, str) else map_val_json
    result = {}
    for item in raw["MAP"]:
        parts = item.split(",")
        x, y = parts[0], parts[1]
        result[f"{x}_{y}"] = {
            "left_bin": parts[2] if len(parts) > 2 else "",
            "right_bin": parts[3] if len(parts) > 3 else "",
        }
    return result


def _get_map_bounds(map_data_list: list) -> tuple:
    """가장 많은 좌표를 가진 wafer에서 경계값 계산"""
    if not map_data_list:
        return 0, 0, 0, 0
    longest = max(map_data_list, key=lambda d: len(d["map_val_json"]))
    map_json = _parse_map_json(longest["map_val_json"])
    rows, cols = [], []
    for key in map_json:
        r, c = map(int, key.split("_"))
        rows.append(r)
        cols.append(c)
    return min(rows), max(rows), min(cols), max(cols)


def _visualize_binmap(map_data_list: list, bin_type: str = "left_bin") -> str:
    """여러 wafer의 binmap을 개별적으로 시각화 → PNG 파일 경로"""
    if not map_data_list:
        return ""

    try:
        return _visualize_binmap_inner(map_data_list, bin_type)
    except Exception as e:
        logger.error("[MapAgent] binmap 시각화 실패: %s", e)
        return ""


def _visualize_binmap_inner(map_data_list: list, bin_type: str) -> str:
    n_wafers = len(map_data_list)
    n_cols = min(4, n_wafers)
    n_rows = (n_wafers + n_cols - 1) // n_cols

    min_row, max_row, min_col, max_col = _get_map_bounds(map_data_list)
    height = max_row - min_row + 1
    width = max_col - min_col + 1

    n_workers = min(mp.cpu_count(), len(map_data_list), 8)
    args_list = [d["map_val_json"] for d in map_data_list]
    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        parsed_maps = list(executor.map(_parse_wafer_for_binmap, args_list))

    all_values = set()
    for map_json in parsed_maps:
        for value in map_json.values():
            bin_val = value.get(bin_type, "")
            if bin_val:
                all_values.add(bin_val)
    value_to_num = {v: i for i, v in enumerate(sorted(all_values))}

    fig, axes = plt.subplots(n_rows, n_cols, figsize=(4 * n_cols, 4 * n_rows))
    if n_wafers == 1:
        axes = np.array([[axes]])
    elif n_rows == 1:
        axes = axes.reshape(1, -1)
    elif n_cols == 1:
        axes = axes.reshape(-1, 1)

    unique_lots = list(set(d["lot_id"] for d in map_data_list))
    if len(unique_lots) == 1:
        title = f"Binmap ({bin_type}) - Lot: {unique_lots[0]} ({n_wafers} wafers)"
    else:
        title = f"Binmap ({bin_type}) - {len(unique_lots)} Lots ({n_wafers} wafers)"
    fig.suptitle(title, fontsize=14)

    for idx, (data, map_json) in enumerate(zip(map_data_list, parsed_maps)):
        row_idx = idx // n_cols
        col_idx = idx % n_cols
        ax = axes[row_idx, col_idx]
        map_array = np.full((height, width), np.nan)
        for key, value in map_json.items():
            r, c = map(int, key.split("_"))
            bin_val = value.get(bin_type, "")
            if bin_val and bin_val in value_to_num:
                map_array[r - min_row, c - min_col] = value_to_num[bin_val]
        ax.imshow(map_array, cmap="tab20", interpolation="nearest")
        ax.set_title(f"{data['lot_id']}.{data['wf_id']}")
        ax.set_xlabel("Col")
        ax.set_ylabel("Row")

    for idx in range(n_wafers, n_rows * n_cols):
        axes[idx // n_cols, idx % n_cols].axis("off")

    plt.tight_layout()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_lot = unique_lots[0] if len(unique_lots) == 1 else "multi"
    filepath = f"binmap_{file_lot}_{timestamp}.png"
    plt.savefig(filepath, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return filepath


def _visualize_cummap(
    map_data_list: list,
    bin_type: str = "left_bin",
    target_bin: Optional[str] = None,
    category_name: Optional[str] = None,
    subtitle: Optional[str] = None,
) -> tuple:
    """여러 wafer를 Pass Rate 기반 cummap으로 시각화 → (filepath, avg_pass_rate)"""
    if not map_data_list:
        return None, 0

    try:
        return _visualize_cummap_inner(map_data_list, bin_type, target_bin, category_name, subtitle)
    except Exception as e:
        logger.error("[MapAgent] cummap 시각화 실패: %s", e)
        return None, 0


def _visualize_cummap_inner(
    map_data_list: list,
    bin_type: str,
    target_bin: Optional[str],
    category_name: Optional[str],
    subtitle: Optional[str],
) -> tuple:
    n_wafers = len(map_data_list)
    min_row, max_row, min_col, max_col = _get_map_bounds(map_data_list)
    height = max_row - min_row + 1
    width = max_col - min_col + 1

    n_workers = min(mp.cpu_count(), len(map_data_list), 8)
    args_list = [(d["map_val_json"], bin_type, target_bin) for d in map_data_list]
    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        results = list(executor.map(_parse_wafer_for_cummap, args_list))

    all_rows, all_cols, all_pass = [], [], []
    for rows, cols, passes in results:
        all_rows.extend(rows)
        all_cols.extend(cols)
        all_pass.extend(passes)

    all_rows = np.array(all_rows) - min_row
    all_cols = np.array(all_cols) - min_col
    all_pass = np.array(all_pass)

    pass_sum = np.zeros((height, width))
    count = np.zeros((height, width))
    np.add.at(pass_sum, (all_rows, all_cols), all_pass)
    np.add.at(count, (all_rows, all_cols), 1)

    with np.errstate(divide="ignore", invalid="ignore"):
        pass_rate = np.where(count > 0, pass_sum / count, np.nan)

    valid_rates = pass_rate[~np.isnan(pass_rate)]
    avg_pass_rate = float(np.mean(valid_rates) * 100) if len(valid_rates) > 0 else 0.0

    fig, ax = plt.subplots(figsize=(10, 8))
    unique_lots = list(set(d["lot_id"] for d in map_data_list))
    cat_label = f" [{category_name}]" if category_name else ""
    if len(unique_lots) == 1:
        title = f"Cummap{cat_label} ({bin_type}) - Lot: {unique_lots[0]}\n({n_wafers} wafers, Avg Pass Rate: {avg_pass_rate:.1f}%)"
    else:
        title = f"Cummap{cat_label} ({bin_type}) - {len(unique_lots)} Lots\n({n_wafers} wafers, Avg Pass Rate: {avg_pass_rate:.1f}%)"
    if subtitle:
        title += f"\n{subtitle}"
    ax.set_title(title, fontsize=14)

    im = ax.imshow(pass_rate, cmap="RdYlGn", vmin=0, vmax=1, interpolation="nearest")
    cbar = plt.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("Pass Rate", fontsize=12)
    cbar.set_ticks([0, 0.25, 0.5, 0.75, 1.0])
    cbar.set_ticklabels(["0%\n(Fail)", "25%", "50%", "75%", "100%\n(Pass)"])
    ax.set_xlabel("Col")
    ax.set_ylabel("Row")
    plt.tight_layout()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_lot = unique_lots[0] if len(unique_lots) == 1 else "multi"
    cat_suffix = f"_{category_name}" if category_name else ""
    filepath = f"cummap_{file_lot}{cat_suffix}_{timestamp}.png"
    plt.savefig(filepath, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return filepath, avg_pass_rate


def _visualize_combined_cummap(
    weekly_data: list[dict],
    category_name: str,
    lotcd: str,
) -> str | None:
    """N주치 cummap을 하나의 figure에 subplot으로 합쳐 단일 PNG로 저장

    Args:
        weekly_data: [{"week", "pass_rate_array", "avg_pass_rate", "lot_count", "wafer_count"}, ...]
                     pass_rate_array가 None인 항목은 '데이터 없음'으로 표시
        category_name: "IOFF" 등
        lotcd: "4SS" 등
    Returns:
        filepath or None
    """
    if not weekly_data:
        return None

    ref_shape = None
    for d in weekly_data:
        arr = d.get("pass_rate_array")
        if arr is not None:
            ref_shape = arr.shape
            break
    if ref_shape is None:
        return None

    n = len(weekly_data)
    fig, axes = plt.subplots(1, n, figsize=(5 * n, 6), squeeze=False)
    axes = axes[0]

    im = None
    for ax, d in zip(axes, weekly_data):
        arr = d.get("pass_rate_array")
        if arr is not None:
            im = ax.imshow(arr, cmap="RdYlGn", vmin=0, vmax=1, interpolation="nearest")
            ax.set_title(
                f"{d['week']}\n"
                f"Pass {d['avg_pass_rate']:.1f}%\n"
                f"({d['lot_count']}L / {d['wafer_count']}W)",
                fontsize=10,
            )
        else:
            blank = np.full(ref_shape, np.nan)
            ax.imshow(blank, cmap="RdYlGn", vmin=0, vmax=1, interpolation="nearest")
            ax.text(0.5, 0.5, "데이터 없음", ha="center", va="center",
                    transform=ax.transAxes, fontsize=12, color="gray")
            ax.set_title(f"{d['week']}\n(no data)", fontsize=10)
        ax.set_xlabel("Col")
        ax.set_ylabel("Row")

    if im is not None:
        cbar = fig.colorbar(im, ax=list(axes), shrink=0.75, pad=0.02)
        cbar.set_label("Pass Rate")
        cbar.set_ticks([0, 0.25, 0.5, 0.75, 1.0])
        cbar.set_ticklabels(["0%", "25%", "50%", "75%", "100%"])

    fig.suptitle(f"{lotcd}  {category_name} Weekly Cummap", fontsize=14)
    plt.tight_layout(rect=[0, 0, 1, 0.95])

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = f"cummap_weekly_{category_name}_{timestamp}.png"
    plt.savefig(filepath, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return filepath


def _query_wafer_data_by_date(
    lotcd: str, start_date: str, end_date: str, category: str | None = None,
) -> list:
    """날짜 범위(end_tm 기준)로 wafer 데이터 직접 조회 (lot_ids 2단계 불필요).

    Args:
        lotcd: 제품코드 (lot_cd)
        start_date: 시작일 'YYYYMMDD'
        end_date: 종료일 'YYYYMMDD' (exclusive)
        category: 'VTH', 'PT1C' 등. None이면 전체 조회.
    """
    try:
        conn = _get_oracle_connection_common()
    except Exception as e:
        logger.error("[MapAgent] Oracle 연결 실패 (wafer_by_date): %s", e)
        return []
    try:
        cur = conn.cursor()
        sql = f"""
            SELECT lot_id, wf_id, map_val_json, fab_id, lot_cd, start_tm, end_tm
            FROM {ORACLE_TABLE}
            WHERE lot_cd = :lotcd
              AND end_tm >= TO_DATE(:sd, 'YYYYMMDD')
              AND end_tm < TO_DATE(:ed, 'YYYYMMDD')
        """
        params: dict = {"lotcd": lotcd, "sd": start_date, "ed": end_date}
        if category:
            sql += "  AND OPER_DET_DESC = :cat"
            params["cat"] = category
        sql += "\n            ORDER BY lot_id, wf_id\n            FETCH FIRST 5000 ROWS ONLY"
        cur.execute(sql, params)
        columns = [desc[0].lower() for desc in cur.description]
        results = []
        for row in cur.fetchall():
            record = dict(zip(columns, row))
            if record.get("map_val_json") and hasattr(record["map_val_json"], "read"):
                record["map_val_json"] = record["map_val_json"].read()
            results.append(record)
        return results
    except Exception as e:
        logger.error("[MapAgent] wafer_by_date 조회 실패: %s", e)
        return []
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()


def _query_lot_ids_by_date(lotcd: str, start_date: str, end_date: str) -> list[str]:
    """지정 기간(end_tm 기준)의 DISTINCT lot_id 목록 조회"""
    try:
        conn = _get_oracle_connection_common()
    except Exception as e:
        logger.error("[MapAgent] Oracle 연결 실패 (lot_ids_by_date): %s", e)
        return []
    try:
        cur = conn.cursor()
        sql = f"""
            SELECT DISTINCT lot_id FROM {ORACLE_TABLE}
            WHERE lot_cd = :lotcd
              AND end_tm >= TO_DATE(:sd, 'YYYYMMDD')
              AND end_tm < TO_DATE(:ed, 'YYYYMMDD')
            ORDER BY lot_id
        """
        cur.execute(sql, {"lotcd": lotcd, "sd": start_date, "ed": end_date})
        return [row[0] for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@timed
def _generate_weekly_cummaps(
    lotcd: str,
    weeks: list[dict],
    bin_type: str,
    target_bin: str,
    category_name: str,
) -> list[dict]:
    """4주치 cummap 생성 — 각 주차별 pass_rate array 수집 후 단일 combined PNG 생성

    Args:
        lotcd: 제품코드
        weeks: [{"week": "2026-W04", "start": "20260119", "end": "20260126"}, ...]
        bin_type: "left_bin" | "right_bin"
        target_bin: fail bin value (예: "H")
        category_name: category 이름 (예: "IOFF")

    Returns:
        dict: {"filepath": str|None, "weekly_summary": list[dict]}
    """
    MAX_LOTS_PER_WEEK = 200

    weekly_viz_data = []
    weekly_summary = []

    for w in weeks:
        week_label = w["week"]
        lot_ids = _query_lot_ids_by_date(lotcd, w["start"], w["end"])
        if len(lot_ids) > MAX_LOTS_PER_WEEK:
            import random
            random.seed(w["start"])
            lot_ids = sorted(random.sample(lot_ids, MAX_LOTS_PER_WEEK))
            logger.info("[CummapWeekly] %s: %d lots → %d lots (sampled)", week_label, len(lot_ids), MAX_LOTS_PER_WEEK)
        if not lot_ids:
            logger.info("[CummapWeekly] %s: lot 없음 (skip)", week_label)
            weekly_viz_data.append({"week": week_label, "pass_rate_array": None, "avg_pass_rate": 0, "lot_count": 0, "wafer_count": 0})
            weekly_summary.append({"week": week_label, "pass_rate": 0, "lot_count": 0, "wafer_count": 0})
            continue

        lot_ids_str = ",".join(lot_ids)
        map_data = _query_wafer_data(lot_ids=lot_ids_str)
        if not map_data:
            weekly_viz_data.append({"week": week_label, "pass_rate_array": None, "avg_pass_rate": 0, "lot_count": len(lot_ids), "wafer_count": 0})
            weekly_summary.append({"week": week_label, "pass_rate": 0, "lot_count": len(lot_ids), "wafer_count": 0})
            continue

        min_row, max_row, min_col, max_col = _get_map_bounds(map_data)
        height = max_row - min_row + 1
        width = max_col - min_col + 1

        n_workers = min(mp.cpu_count(), len(map_data), 8)
        args_list = [(d["map_val_json"], bin_type, target_bin) for d in map_data]
        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            parsed = list(executor.map(_parse_wafer_for_cummap, args_list))

        all_rows, all_cols, all_pass = [], [], []
        for rows, cols, passes in parsed:
            all_rows.extend(rows)
            all_cols.extend(cols)
            all_pass.extend(passes)

        arr_rows = np.array(all_rows) - min_row
        arr_cols = np.array(all_cols) - min_col
        arr_pass = np.array(all_pass)

        pass_sum = np.zeros((height, width))
        count = np.zeros((height, width))
        np.add.at(pass_sum, (arr_rows, arr_cols), arr_pass)
        np.add.at(count, (arr_rows, arr_cols), 1)

        with np.errstate(divide="ignore", invalid="ignore"):
            pass_rate_arr = np.where(count > 0, pass_sum / count, np.nan)

        valid_rates = pass_rate_arr[~np.isnan(pass_rate_arr)]
        avg_pr = float(np.mean(valid_rates) * 100) if len(valid_rates) > 0 else 0.0

        weekly_viz_data.append({
            "week": week_label,
            "pass_rate_array": pass_rate_arr,
            "avg_pass_rate": avg_pr,
            "lot_count": len(lot_ids),
            "wafer_count": len(map_data),
        })
        weekly_summary.append({
            "week": week_label,
            "pass_rate": avg_pr,
            "lot_count": len(lot_ids),
            "wafer_count": len(map_data),
        })
        logger.info("[CummapWeekly] %s: %d lots, %d wafers, pass_rate=%.1f%%",
                     week_label, len(lot_ids), len(map_data), avg_pr)

    filepath = _visualize_combined_cummap(weekly_viz_data, category_name, lotcd)
    return {"filepath": filepath, "weekly_summary": weekly_summary}


def show_wafer_map(
    lot_id: Optional[str] = None,
    lot_ids: Optional[str] = None,
    wf_ids: Optional[str] = None,
    groupkey: Optional[str] = None,
    map_type: str = "binmap",
    oper: Optional[str] = None,
) -> str:
    """Wafer map 시각화 (DB 조회 + PNG 생성)

    Returns:
        str: 결과 메시지 (PNG 파일 경로 포함)
    """
    map_data_list = _query_wafer_data(
        lot_id=lot_id, lot_ids=lot_ids, wf_ids=wf_ids, groupkey=groupkey, oper=oper
    )
    if not map_data_list:
        return "조회된 데이터가 없습니다. lot_id와 wf_id를 확인해주세요."

    n_wafers = len(map_data_list)
    lot_info = map_data_list[0]["lot_id"]
    bin_type = "left_bin"

    requested_types = {t.strip().lower() for t in map_type.split(",")}
    if "all" in requested_types:
        requested_types = {"binmap", "cummap"}

    results = []
    if "binmap" in requested_types:
        filepath = _visualize_binmap(map_data_list, bin_type=bin_type)
        if filepath:
            results.append(f"Binmap: {filepath}")

    if "cummap" in requested_types:
        subtitle_parts = []
        if lot_ids:
            subtitle_parts.append(f"Lots: {lot_ids}")
        if wf_ids:
            subtitle_parts.append(f"Wafers: {wf_ids}")
        if groupkey:
            subtitle_parts.append(f"Groupkey: {groupkey}")
        cummap_subtitle = " | ".join(subtitle_parts) if subtitle_parts else None
        filepath, avg_pass_rate = _visualize_cummap(
            map_data_list, bin_type=bin_type, subtitle=cummap_subtitle,
        )
        if filepath:
            results.append(f"Cummap: {filepath} (평균 Pass Rate: {avg_pass_rate:.1f}%)")
        else:
            results.append("Cummap 생성 실패")

    if not results:
        return "유효한 map_type이 지정되지 않았습니다. (binmap, cummap, all 중 선택)"

    result_msg = "이미지가 생성되었습니다:\n"
    result_msg += "\n".join(f"  - {r}" for r in results)
    result_msg += f"\n\n- Lot: {lot_info}\n- Wafer 수: {n_wafers}개\n- Oper: {oper}"
    return result_msg


# ============================================================
# HTML 변환 헬퍼
# ============================================================
def _png_to_html(png_path: str, title: str) -> str:
    """PNG 파일을 base64 인코딩하여 <img> 태그 HTML로 반환 후 파일 삭제"""
    with open(png_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    try:
        os.remove(png_path)
    except OSError:
        logger.debug("PNG 임시 파일 삭제 실패: %s", png_path)
    return (
        f'<div style="margin:8px 0">'
        f'<p style="font-weight:600">{title}</p>'
        f'<img src="data:image/png;base64,{b64}" style="max-width:100%"/>'
        f'</div>'
    )


# ============================================================
# LangGraph 노드
# ============================================================
@observe(name="map_agent_node")
@timed
def map_agent_node(state: dict, config: RunnableConfig) -> dict:
    """Wafer map 시각화 노드 — supervisor가 직접 호출"""
    return _handle_standard_map(state)


def _handle_weekly_cummap(state: dict) -> dict:
    """4주치 category별 cummap 생성 (yield_agent → map_agent 순차 흐름)"""
    lotcd = state.get("lotcd", "4SS")
    cummap_weeks = state.get("cummap_weeks", [])
    target_bin = state.get("cummap_target_bin", "")
    category_name = state.get("cummap_category", "")
    bin_type = "left_bin"

    if not cummap_weeks or not target_bin:
        msg = AIMessage(content="cummap 생성에 필요한 정보가 부족합니다.", name="map_agent")
        return {"messages": [msg], "map_result": "", "map_artifacts": [], "agent_suggestion": ""}

    logger.info("[MapAgent] 4주치 %s cummap 생성 시작 (target_bin=%s)", category_name, target_bin)
    gen_result = _generate_weekly_cummaps(
        lotcd=lotcd,
        weeks=cummap_weeks,
        bin_type=bin_type,
        target_bin=target_bin,
        category_name=category_name,
    )

    filepath = gen_result["filepath"]
    weekly_summary = gen_result["weekly_summary"]

    summary_lines = []
    for ws in weekly_summary:
        if ws["wafer_count"] > 0:
            summary_lines.append(f"  - {ws['week']}: {ws['pass_rate']:.1f}% ({ws['lot_count']} lots, {ws['wafer_count']} wafers)")
        else:
            summary_lines.append(f"  - {ws['week']}: 데이터 없음")

    map_html = ""
    artifacts = []
    if filepath and os.path.exists(filepath):
        title = f"{lotcd} {category_name} Weekly Cummap"
        map_html = _png_to_html(filepath, title)
        artifacts.append({"type": "html", "mime": "text/html", "data": map_html, "title": "weekly_cummap"})

    result_str = f"[{lotcd}] {category_name} 4주치 Cummap:\n" + "\n".join(summary_lines)

    try:
        get_client().update_current_span(output={
            "map_result": result_str,
            "png_count": 1 if filepath else 0,
            "category": category_name,
            "target_bin": target_bin,
        })
    except Exception:
        pass

    result_message = AIMessage(content=result_str, name="map_agent")
    return {
        "messages": [result_message],
        "map_result": result_str,
        "map_artifacts": artifacts,
        "agent_suggestion": "",
    }


def _handle_standard_map(state: dict) -> dict:
    """기존 binmap/cummap 생성 로직"""
    lot_id   = state.get("map_lot_id", "")
    lot_ids  = state.get("map_lot_ids", "")
    wf_ids   = state.get("map_wf_ids", "")
    groupkey = state.get("map_groupkey", "")
    map_type = state.get("map_type", "binmap")
    oper     = state.get("map_oper", "")

    logger.info(
        "[MapAgent] _handle_standard_map: lot_id=%r, lot_ids=%r, wf_ids=%r, groupkey=%r, map_type=%s, oper=%s",
        lot_id, lot_ids, wf_ids, groupkey, map_type, oper,
    )

    result_str = show_wafer_map(
        lot_id=lot_id or None,
        lot_ids=lot_ids or None,
        wf_ids=wf_ids or None,
        groupkey=groupkey or None,
        map_type=map_type,
        oper=oper or None,
    )

    png_paths = re.findall(r'[\w./\-]+\.png', result_str)
    html_parts = []
    for p in png_paths:
        if os.path.exists(p):
            html_parts.append(_png_to_html(p, os.path.basename(p)))
    map_html = "\n".join(html_parts) if html_parts else ""

    artifacts = []
    if map_html:
        artifacts.append({"type": "html", "mime": "text/html", "data": map_html, "title": "map"})

    try:
        get_client().update_current_span(output={
            "map_result": result_str,
            "png_count": len(png_paths),
            "map_type": map_type,
            "oper": oper,
        })
    except Exception:
        pass

    result_message = AIMessage(content=result_str, name="map_agent")
    return {
        "messages": [result_message],
        "map_result": result_str,
        "map_artifacts": artifacts,
        "agent_suggestion": "",
    }

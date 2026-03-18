"""
Yield Oracle SQL 쿼리 모듈
==========================
pt1h / pt1c / gms 데이터를 Oracle DB에서 조회하는 함수들.
yield_query_agent.py에서 분리.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from langfuse import observe

from common import (
    get_oracle_connection as _get_oracle_connection,
    lot_id_variants,
    timed,
    iso_week_str as _iso_week_str,
    week_monday as _week_monday,
    PARA_COLUMNS,
    PT1C_COLUMNS,
    GMS_COLUMNS,
)

logger = logging.getLogger("yield_agent")

# ── 상수 ──────────────────────────────────────────────────────
YLD_TABLE = "DF_DIE_TO_WF_YLD"


# ── Week/Period 변환 헬퍼 ─────────────────────────────────────
def _week_to_db_yld(week_agent: str) -> str:
    """'2026-W06' → '2026-06'  (DF_DIE_TO_WF_YLD WEEK 컬럼용)"""
    return week_agent.replace("-W", "-")


def _week_to_db_gms(week_agent: str) -> str:
    """'2026-W06' → '202606'  (DF_GMS_YIELD_WEEKLY PERIOD_DATE 컬럼용)"""
    return week_agent.replace("-W", "")


# ── 날짜 리스트 생성 ──────────────────────────────────────────
def _get_n_weeks(ref: date, n: int) -> list[date]:
    """ref 기준 최근 n주의 월요일 리스트 (오래된순)"""
    monday = _week_monday(ref)
    return [monday - timedelta(weeks=i) for i in range(n - 1, -1, -1)]


def _get_n_months(ref: date, n: int) -> list[str]:
    """ref 기준 최근 n개월의 YYYY-MM 리스트 (오래된순)"""
    result = []
    year, month = ref.year, ref.month
    for i in range(n - 1, -1, -1):
        m = month - i
        y = year
        while m <= 0:
            m += 12
            y -= 1
        result.append(f"{y:04d}-{m:02d}")
    return result


def _get_n_days(ref: date, n: int) -> list[date]:
    """ref 기준 최근 n일의 date 리스트 (오래된순)"""
    return [ref - timedelta(days=n - 1 - i) for i in range(n)]


# ── SQL 조회 함수 ─────────────────────────────────────────────
def _fetch_weekly_sql(lotcd: str, week_strs: list[str], process: str) -> dict[str, dict]:
    """pt1h 또는 pt1c 프로세스의 n주 데이터를 Oracle SQL 한 번으로 조회."""
    db_weeks = [_week_to_db_yld(w) for w in week_strs]
    placeholders = ", ".join(f":w{i}" for i in range(len(week_strs)))
    sql = f"""
        WITH base AS (
            SELECT WEEK, LOTID, WFID, PARAM, VALUE
            FROM DF_DIE_TO_WF_YLD
            WHERE LOT_CD = :lot_cd
              AND WEEK IN ({placeholders})
              AND PROCESS = :process
        ),
        counts AS (
            SELECT WEEK,
                   COUNT(DISTINCT LOTID) AS LOT_COUNT,
                   COUNT(DISTINCT LOTID || '-' || TO_CHAR(WFID)) AS WF_COUNT
            FROM base GROUP BY WEEK
        ),
        param_avgs AS (
            SELECT WEEK, PARAM, AVG(VALUE) AS AVG_VALUE
            FROM base GROUP BY WEEK, PARAM
        )
        SELECT pa.WEEK, pa.PARAM, pa.AVG_VALUE, c.LOT_COUNT, c.WF_COUNT
        FROM param_avgs pa
        JOIN counts c ON pa.WEEK = c.WEEK
        ORDER BY pa.WEEK, pa.PARAM
    """
    db_to_agent = {_week_to_db_yld(w): w for w in week_strs}
    params = {"lot_cd": lotcd, "process": process}
    for i, w in enumerate(db_weeks):
        params[f"w{i}"] = w

    result: dict[str, dict] = {}
    try:
        conn = _get_oracle_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            for db_week, param, avg_val, lot_cnt, wf_cnt in cur:
                agent_week = db_to_agent.get(db_week, db_week)
                if agent_week not in result:
                    result[agent_week] = {"lotcount": int(lot_cnt), "wfCount": int(wf_cnt)}
                result[agent_week][param] = float(avg_val) if avg_val is not None else "-"
        finally:
            conn.close()
    except Exception as e:
        logger.error("[%s/%s] Oracle 조회 실패: %s", process, lotcd, e)

    return result


def _fetch_gms_sql(lotcd: str, week_strs: list[str]) -> dict[str, dict]:
    """GMS 주차별 yield 조회 (DF_GMS_YIELD_WEEKLY)."""
    db_periods = [_week_to_db_gms(w) for w in week_strs]
    placeholders = ", ".join(f":p{i}" for i in range(len(week_strs)))
    sql = f"""
        SELECT PERIOD_DATE, FAB_YIELD, PRB_YIELD, CUM0_YIELD
        FROM DF_GMS_YIELD_WEEKLY
        WHERE LOTCODE = :lot_cd
          AND PERIOD_DATE IN ({placeholders})
    """
    db_to_agent = {_week_to_db_gms(w): w for w in week_strs}
    params = {"lot_cd": lotcd}
    for i, p in enumerate(db_periods):
        params[f"p{i}"] = p

    result: dict[str, dict] = {}
    try:
        conn = _get_oracle_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            for period_date, fab, prb, cum0 in cur:
                agent_week = db_to_agent.get(period_date, period_date)
                result[agent_week] = {
                    "fab":      float(fab)  if fab  is not None else "-",
                    "prb":      float(prb)  if prb  is not None else "-",
                    "cum0":     float(cum0) if cum0 is not None else "-",
                    "cum2":     "-",
                    "pnt":      "-",
                    "lotcount": "-",
                    "wfCount":  "-",
                }
        finally:
            conn.close()
    except Exception as e:
        logger.error("[gms/%s] Oracle 조회 실패: %s", lotcd, e)

    return result


def _fetch_monthly_sql(lotcd: str, month_strs: list[str], process: str) -> dict[str, dict]:
    """YEAR + MONTH 컬럼 기반 월별 조회 (DF_DIE_TO_WF_YLD)."""
    params = {"lot_cd": lotcd, "process": process}
    for i, ym in enumerate(month_strs):
        y, m = ym.split("-")
        params[f"y{i}"] = y
        params[f"m{i}"] = m

    clauses = [
        f"(YEAR = :y{i} AND TO_NUMBER(MONTH) = TO_NUMBER(:m{i}))"
        for i in range(len(month_strs))
    ]
    where_ym = " OR ".join(clauses)

    sql = f"""
        WITH base AS (
            SELECT YEAR, MONTH, LOTID, WFID, PARAM, VALUE
            FROM DF_DIE_TO_WF_YLD
            WHERE LOT_CD = :lot_cd
              AND ({where_ym})
              AND PROCESS = :process
        ),
        counts AS (
            SELECT YEAR, MONTH,
                   COUNT(DISTINCT LOTID) AS LOT_COUNT,
                   COUNT(DISTINCT LOTID || '-' || TO_CHAR(WFID)) AS WF_COUNT
            FROM base GROUP BY YEAR, MONTH
        ),
        param_avgs AS (
            SELECT YEAR, MONTH, PARAM, AVG(VALUE) AS AVG_VALUE
            FROM base GROUP BY YEAR, MONTH, PARAM
        )
        SELECT pa.YEAR, pa.MONTH, pa.PARAM, pa.AVG_VALUE, c.LOT_COUNT, c.WF_COUNT
        FROM param_avgs pa
        JOIN counts c ON pa.YEAR = c.YEAR AND pa.MONTH = c.MONTH
        ORDER BY pa.YEAR, pa.MONTH, pa.PARAM
    """

    result: dict[str, dict] = {}
    try:
        conn = _get_oracle_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            for year, month, param, avg_val, lot_cnt, wf_cnt in cur:
                key = f"{year}-{str(month).zfill(2)}"
                if key not in result:
                    result[key] = {"lotcount": int(lot_cnt), "wfCount": int(wf_cnt)}
                result[key][param] = float(avg_val) if avg_val is not None else "-"
        finally:
            conn.close()
    except Exception as e:
        logger.error("[%s/%s] monthly Oracle 조회 실패: %s", process, lotcd, e)

    return result


def _fetch_daily_sql(lotcd: str, days: list[date], process: str) -> dict[str, dict]:
    """MEASURETIME_START 범위 조건 기반 일별 조회 (DF_DIE_TO_WF_YLD, range scan)."""
    start_dt = datetime.combine(min(days), datetime.min.time())
    end_dt = datetime.combine(max(days) + timedelta(days=1), datetime.min.time())
    days_set = {d.strftime("%Y-%m-%d") for d in days}

    sql = """
        WITH base AS (
            SELECT TRUNC(MEASURETIME_START) AS DAY_DATE,
                   LOTID, WFID, PARAM, VALUE
            FROM DF_DIE_TO_WF_YLD
            WHERE LOT_CD = :lot_cd
              AND MEASURETIME_START >= :start_dt
              AND MEASURETIME_START <  :end_dt
              AND PROCESS = :process
        ),
        counts AS (
            SELECT DAY_DATE,
                   COUNT(DISTINCT LOTID) AS LOT_COUNT,
                   COUNT(DISTINCT LOTID || '-' || TO_CHAR(WFID)) AS WF_COUNT
            FROM base GROUP BY DAY_DATE
        ),
        param_avgs AS (
            SELECT DAY_DATE, PARAM, AVG(VALUE) AS AVG_VALUE
            FROM base GROUP BY DAY_DATE, PARAM
        )
        SELECT pa.DAY_DATE, pa.PARAM, pa.AVG_VALUE, c.LOT_COUNT, c.WF_COUNT
        FROM param_avgs pa JOIN counts c ON pa.DAY_DATE = c.DAY_DATE
        ORDER BY pa.DAY_DATE, pa.PARAM
    """
    result: dict[str, dict] = {}
    try:
        conn = _get_oracle_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql, {
                "lot_cd": lotcd,
                "start_dt": start_dt,
                "end_dt": end_dt,
                "process": process,
            })
            for day_date, param, avg_val, lot_cnt, wf_cnt in cur:
                if hasattr(day_date, "date"):
                    day_str = day_date.date().strftime("%Y-%m-%d")
                else:
                    day_str = day_date.strftime("%Y-%m-%d")
                if day_str not in days_set:
                    continue
                if day_str not in result:
                    result[day_str] = {"lotcount": int(lot_cnt), "wfCount": int(wf_cnt)}
                result[day_str][param] = float(avg_val) if avg_val is not None else "-"
        finally:
            conn.close()
    except Exception as e:
        logger.error("[%s/%s] daily Oracle 조회 실패: %s", process, lotcd, e)

    return result


def _fetch_gms_monthly_sql(lotcd: str, month_strs: list[str]) -> dict[str, dict]:
    """GMS 월별 yield 조회 (DF_GMS_YIELD_MONTHLY)."""
    db_periods = [m.replace("-", "") for m in month_strs]
    db_to_agent = {db: agent for db, agent in zip(db_periods, month_strs)}
    placeholders = ", ".join(f":p{i}" for i in range(len(month_strs)))
    sql = f"""
        SELECT PERIOD_DATE, FAB_YIELD, PRB_YIELD, CUM0_YIELD
        FROM DF_GMS_YIELD_MONTHLY
        WHERE LOTCODE = :lot_cd
          AND PERIOD_DATE IN ({placeholders})
    """
    params = {"lot_cd": lotcd}
    for i, p in enumerate(db_periods):
        params[f"p{i}"] = p

    result: dict[str, dict] = {}
    try:
        conn = _get_oracle_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            for period_date, fab, prb, cum0 in cur:
                agent_key = db_to_agent.get(period_date, period_date)
                result[agent_key] = {
                    "fab":      float(fab)  if fab  is not None else "-",
                    "prb":      float(prb)  if prb  is not None else "-",
                    "cum0":     float(cum0) if cum0 is not None else "-",
                    "cum2":     "-",
                    "pnt":      "-",
                    "lotcount": "-",
                    "wfCount":  "-",
                }
        finally:
            conn.close()
    except Exception as e:
        logger.error("[gms-monthly/%s] Oracle 조회 실패: %s", lotcd, e)

    return result


def _fetch_gms_daily_sql(lotcd: str, days: list[date]) -> dict[str, dict]:
    """GMS 일별 yield 조회 (DF_GMS_YIELD_DAILY)."""
    db_periods = [d.strftime("%Y%m%d") for d in days]
    db_to_agent = {db: d.strftime("%Y-%m-%d") for db, d in zip(db_periods, days)}
    placeholders = ", ".join(f":p{i}" for i in range(len(days)))
    sql = f"""
        SELECT PERIOD_DATE, FAB_YIELD, PRB_YIELD, CUM0_YIELD
        FROM DF_GMS_YIELD_DAILY
        WHERE LOTCODE = :lot_cd
          AND PERIOD_DATE IN ({placeholders})
    """
    params = {"lot_cd": lotcd}
    for i, p in enumerate(db_periods):
        params[f"p{i}"] = p

    result: dict[str, dict] = {}
    try:
        conn = _get_oracle_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            for period_date, fab, prb, cum0 in cur:
                agent_key = db_to_agent.get(period_date, period_date)
                result[agent_key] = {
                    "fab":      float(fab)  if fab  is not None else "-",
                    "prb":      float(prb)  if prb  is not None else "-",
                    "cum0":     float(cum0) if cum0 is not None else "-",
                    "cum2":     "-",
                    "pnt":      "-",
                    "lotcount": "-",
                    "wfCount":  "-",
                }
        finally:
            conn.close()
    except Exception as e:
        logger.error("[gms-daily/%s] Oracle 조회 실패: %s", lotcd, e)

    return result


# ── 기본 기간 수 ──────────────────────────────────────────────
DEFAULT_PERIODS = {"weekly": 4, "monthly": 3, "daily": 4}


@observe(name="fetch_periods")
@timed
def _fetch_periods(lotcd: str, ref_date: date,
                   unit: str = "weekly", periods: int = 0) -> list[dict]:
    """unit(weekly/monthly/daily) + periods 기반 데이터 조회."""
    n = periods if periods > 0 else DEFAULT_PERIODS.get(unit, 4)

    with ThreadPoolExecutor(max_workers=3) as ex:
        if unit == "monthly":
            month_strs = _get_n_months(ref_date, n)
            f_pt1h = ex.submit(_fetch_monthly_sql, lotcd, month_strs, "pt1h")
            f_pt1c = ex.submit(_fetch_monthly_sql, lotcd, month_strs, "pt1c")
            f_gms  = ex.submit(_fetch_gms_monthly_sql, lotcd, month_strs)
            period_labels = month_strs
        elif unit == "daily":
            days = _get_n_days(ref_date, n)
            f_pt1h = ex.submit(_fetch_daily_sql, lotcd, days, "pt1h")
            f_pt1c = ex.submit(_fetch_daily_sql, lotcd, days, "pt1c")
            f_gms  = ex.submit(_fetch_gms_daily_sql, lotcd, days)
            period_labels = [d.strftime("%Y-%m-%d") for d in days]
        else:
            # weekly (기본값)
            mondays = _get_n_weeks(ref_date, n)
            week_strs = [_iso_week_str(m) for m in mondays]
            f_pt1h = ex.submit(_fetch_weekly_sql, lotcd, week_strs, "pt1h")
            f_pt1c = ex.submit(_fetch_weekly_sql, lotcd, week_strs, "pt1c")
            f_gms  = ex.submit(_fetch_gms_sql, lotcd, week_strs)
            period_labels = week_strs
        pt1h_data = f_pt1h.result()
        pt1c_data = f_pt1c.result()
        gms_data  = f_gms.result()

    results = []
    for label in period_labels:
        logger.debug("  [SQL] %s 완료", label)

        pt1h = pt1h_data.get(label, {})
        pt1c = pt1c_data.get(label, {})
        gms  = gms_data.get(label, {})

        wd = {"week": label}
        wd["lotcount"] = pt1h.get("lotcount", "-")
        wd["wfCount"]  = pt1h.get("wfCount", "-")
        for col in PARA_COLUMNS:
            wd[col] = pt1h.get(col, "-")

        wd["pt1c_lotcount"] = pt1c.get("lotcount", "-")
        wd["pt1c_wfCount"]  = pt1c.get("wfCount", "-")
        for col in PT1C_COLUMNS:
            wd[f"pt1c_{col}"] = pt1c.get(col, "-")

        wd["gms_lotcount"] = gms.get("lotcount", "-")
        wd["gms_wfCount"]  = gms.get("wfCount", "-")
        for col in GMS_COLUMNS:
            wd[f"gms_{col}"] = gms.get(col, "-")

        results.append(wd)

    return results


@observe(name="fetch_4_weeks")
@timed
def _fetch_4_weeks(lotcd: str, ref_date: date) -> list[dict]:
    """[하위 호환] _fetch_periods(unit='weekly', periods=4)로 위임."""
    return _fetch_periods(lotcd, ref_date, unit="weekly", periods=4)


# ── Wafer-level scatter 데이터 조회 ──────────────────────────
def _fetch_wafer_scatter(
    lotcd: str, ref_date: date, unit: str, periods: int, process: str,
) -> list[dict]:
    """기간별 wafer-level raw 데이터 조회 (scatter plot용)."""
    n = periods if periods > 0 else DEFAULT_PERIODS.get(unit, 4)
    per_period_limit = max(10000 // n, 2000)
    rows: list[dict] = []

    try:
        conn = _get_oracle_connection()
        try:
            cur = conn.cursor()

            if unit == "weekly":
                mondays = _get_n_weeks(ref_date, n)
                week_strs = [_iso_week_str(m) for m in mondays]
                db_weeks = [_week_to_db_yld(w) for w in week_strs]
                db_to_period = {dw: ws for dw, ws in zip(db_weeks, week_strs)}
                placeholders = ", ".join(f":w{i}" for i in range(len(db_weeks)))
                sql = f"""
                    SELECT MEASURETIME_START, PARAM, VALUE, WEEK, LOTID, WFID FROM (
                        SELECT MEASURETIME_START, PARAM, VALUE, WEEK, LOTID, WFID,
                               ROW_NUMBER() OVER (PARTITION BY WEEK ORDER BY MEASURETIME_START) AS rn
                        FROM {YLD_TABLE}
                        WHERE LOT_CD = :lot_cd AND WEEK IN ({placeholders})
                          AND PROCESS = :process
                    ) WHERE rn <= :per_limit
                    ORDER BY MEASURETIME_START
                """
                params: dict = {"lot_cd": lotcd, "process": process,
                                "per_limit": per_period_limit}
                for i, w in enumerate(db_weeks):
                    params[f"w{i}"] = w
                cur.execute(sql, params)
                for ts, param, value, week_val, lotid, wfid in cur:
                    if value is None:
                        continue
                    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else str(ts)
                    period = db_to_period.get(str(week_val), str(week_val))
                    rows.append({"ts": ts_str, "param": str(param),
                                 "value": float(value), "period": period,
                                 "lotid": str(lotid) if lotid else "",
                                 "wfid": str(wfid) if wfid else ""})

            elif unit == "monthly":
                month_strs = _get_n_months(ref_date, n)
                bind: dict = {"lot_cd": lotcd, "process": process,
                              "per_limit": per_period_limit}
                clauses = []
                for i, ym in enumerate(month_strs):
                    y, m = ym.split("-")
                    bind[f"y{i}"] = y
                    bind[f"m{i}"] = m
                    clauses.append(f"(YEAR = :y{i} AND TO_NUMBER(MONTH) = TO_NUMBER(:m{i}))")
                where_ym = " OR ".join(clauses)
                sql = f"""
                    SELECT MEASURETIME_START, PARAM, VALUE, YEAR, MONTH, LOTID, WFID FROM (
                        SELECT MEASURETIME_START, PARAM, VALUE, YEAR, MONTH, LOTID, WFID,
                               ROW_NUMBER() OVER (PARTITION BY YEAR, MONTH ORDER BY MEASURETIME_START) AS rn
                        FROM {YLD_TABLE}
                        WHERE LOT_CD = :lot_cd AND ({where_ym}) AND PROCESS = :process
                    ) WHERE rn <= :per_limit
                    ORDER BY MEASURETIME_START
                """
                cur.execute(sql, bind)
                for ts, param, value, yr, mo, lotid, wfid in cur:
                    if value is None:
                        continue
                    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else str(ts)
                    period = f"{int(yr):04d}-{int(mo):02d}"
                    rows.append({"ts": ts_str, "param": str(param),
                                 "value": float(value), "period": period,
                                 "lotid": str(lotid) if lotid else "",
                                 "wfid": str(wfid) if wfid else ""})

            elif unit == "daily":
                days = _get_n_days(ref_date, n)
                start_dt = datetime.combine(min(days), datetime.min.time())
                end_dt = datetime.combine(max(days) + timedelta(days=1), datetime.min.time())
                sql = f"""
                    SELECT MEASURETIME_START, PARAM, VALUE, LOTID, WFID FROM (
                        SELECT MEASURETIME_START, PARAM, VALUE, LOTID, WFID,
                               ROW_NUMBER() OVER (PARTITION BY TRUNC(MEASURETIME_START) ORDER BY MEASURETIME_START) AS rn
                        FROM {YLD_TABLE}
                        WHERE LOT_CD = :lot_cd
                          AND MEASURETIME_START >= :start_dt
                          AND MEASURETIME_START <  :end_dt
                          AND PROCESS = :process
                    ) WHERE rn <= :per_limit
                    ORDER BY MEASURETIME_START
                """
                cur.execute(sql, {"lot_cd": lotcd, "start_dt": start_dt,
                                  "end_dt": end_dt, "process": process,
                                  "per_limit": per_period_limit})
                for ts, param, value, lotid, wfid in cur:
                    if value is None:
                        continue
                    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else str(ts)
                    period = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else ts_str[:10]
                    rows.append({"ts": ts_str, "param": str(param),
                                 "value": float(value), "period": period,
                                 "lotid": str(lotid) if lotid else "",
                                 "wfid": str(wfid) if wfid else ""})
        finally:
            conn.close()
    except Exception as e:
        logger.error("[wafer-scatter/%s/%s] Oracle 조회 실패: %s", process, lotcd, e)

    return rows


# ── Lot 비교 모드 ─────────────────────────────────────────────
def _parse_lot_specs(lot_ids: str = "", groupkey: str = "") -> list[tuple[str, int | None]]:
    """lot_ids / groupkey 문자열 → [(lotid, wfid_or_None), ...] 정규화"""
    specs: list[tuple[str, int | None]] = []

    def _add(token: str):
        token = token.strip()
        if not token:
            return
        if "." in token:
            parts = token.rsplit(".", 1)
            lotid = parts[0].strip().upper()
            try:
                wfid: int | None = int(parts[1])
            except ValueError:
                wfid = None
            specs.append((lotid, wfid))
        else:
            specs.append((token.upper(), None))

    for raw in (groupkey or "").split(","):
        _add(raw)

    for raw in (lot_ids or "").split(","):
        tok = raw.strip().upper()
        if tok and not any(s[0] == tok and s[1] is None for s in specs):
            specs.append((tok, None))

    # 중복 제거 (순서 유지)
    seen: set = set()
    result = []
    for item in specs:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


@observe(name="fetch_lot_sql")
@timed
def _fetch_lot_sql(lot_specs: list[tuple[str, int | None]], process: str) -> dict[str, dict]:
    """lot 단위 수율 비교 조회."""
    if not lot_specs:
        return {}

    result: dict[str, dict] = {}

    lot_level  = [(i, lotid) for i, (lotid, wfid) in enumerate(lot_specs) if wfid is None]
    wf_level   = [(i, lotid, wfid) for i, (lotid, wfid) in enumerate(lot_specs) if wfid is not None]

    try:
        conn = _get_oracle_connection()
        with conn.cursor() as cur:

            if lot_level:
                bind: dict = {"process": process}
                clauses = []
                for idx, (_, lotid) in enumerate(lot_level):
                    variants = lot_id_variants(lotid)
                    bind[f"l{idx}_a"] = variants[0]
                    bind[f"l{idx}_b"] = variants[-1]
                    clauses.append(f"LOTID IN (:l{idx}_a, :l{idx}_b)")
                where = " OR ".join(clauses)
                sql = f"""
                    SELECT LOTID, COUNT(DISTINCT WFID) AS WF_CNT, PARAM, AVG(VALUE) AS AVG_VALUE
                    FROM {YLD_TABLE}
                    WHERE ({where}) AND PROCESS = :process
                    GROUP BY LOTID, PARAM
                    ORDER BY LOTID, PARAM
                """
                cur.execute(sql, bind)
                for lotid, wf_cnt, param, avg_val in cur.fetchall():
                    key = str(lotid)
                    if key not in result:
                        result[key] = {"_wf_count": int(wf_cnt)}
                    result[key][str(param)] = float(avg_val) if avg_val is not None else None

            if wf_level:
                bind2: dict = {"process": process}
                clauses2 = []
                for idx, (_, lotid, wfid) in enumerate(wf_level):
                    variants = lot_id_variants(lotid)
                    bind2[f"l{idx}_a"] = variants[0]
                    bind2[f"l{idx}_b"] = variants[-1]
                    bind2[f"w{idx}"] = wfid
                    clauses2.append(f"(LOTID IN (:l{idx}_a, :l{idx}_b) AND WFID = :w{idx})")
                where2 = " OR ".join(clauses2)
                sql2 = f"""
                    SELECT LOTID, WFID, PARAM, AVG(VALUE) AS AVG_VALUE
                    FROM {YLD_TABLE}
                    WHERE ({where2}) AND PROCESS = :process
                    GROUP BY LOTID, WFID, PARAM
                    ORDER BY LOTID, WFID, PARAM
                """
                cur.execute(sql2, bind2)
                for lotid, wfid, param, avg_val in cur.fetchall():
                    key = f"{lotid}.{int(wfid):02d}"
                    if key not in result:
                        result[key] = {}
                    result[key][str(param)] = float(avg_val) if avg_val is not None else None

        conn.close()
    except Exception as e:
        logger.error("[fetch_lot_sql] 조회 실패 (process=%s): %s", process, e)

    return result


def _merge_lot_data(pt1h: dict[str, dict], pt1c: dict[str, dict]) -> dict[str, dict]:
    """pt1h + pt1c 데이터를 lot 키 기준으로 병합."""
    all_keys = set(pt1h) | set(pt1c)
    merged: dict[str, dict] = {}
    for key in all_keys:
        merged[key] = {}
        for param, val in (pt1h.get(key) or {}).items():
            merged[key][param] = val
        for param, val in (pt1c.get(key) or {}).items():
            merged[key][f"pt1c_{param}"] = val
    return merged

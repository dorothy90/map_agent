# ============================================================
# 1. 환경 설정
# ============================================================
from dotenv import load_dotenv

load_dotenv(override=True)

import os
import time
import oracledb
import logging
import functools
from datetime import date, datetime, timedelta
from tabulate import tabulate

from langchain_core.messages import convert_to_messages, AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI

# ── Langfuse 트레이싱 ────────────────────────────────────
from langfuse import observe, get_client
from langfuse.langchain import CallbackHandler as _LFHandler


def _lf_callbacks() -> list:
    """현재 Langfuse span에 연결된 LangChain CallbackHandler 반환"""
    lf = get_client()
    trace_id = lf.get_current_trace_id()
    if not trace_id:
        return []
    return [_LFHandler(trace_context={
        "trace_id": trace_id,
        "parent_span_id": lf.get_current_observation_id(),
    })]

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
# 4. Oracle SQL 조회 함수
# ============================================================
ORACLE_USER     = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN      = os.getenv("ORACLE_DSN")
YLD_TABLE       = "DF_DIE_TO_WF_YLD"

# 파라미터 컬럼 순서 (pt1h/pt1c PARAM 컬럼 값)
PARA_COLUMNS = [
    "VTH", "IDSAT", "IDLIN", "IOFF", "ION", "IGATE", "IDDQ",
    "VMIN", "FMAX", "TPD", "GM_MAX", "SS", "DIBL",
    "RON", "RDS_ON", "BVDS",
    "LEAK_ID", "LEAK_IG",
    "CGB", "CGS", "CGD", "CDS",
    "RSH", "RD", "RS",
]

PT1C_COLUMNS = ["PT1C", "CFTA"]

GMS_COLUMNS = ["cum0", "cum2", "fab", "prb", "pnt"]
GMS_HIGHER_IS_BETTER = set(GMS_COLUMNS)  # gms는 전부 높을수록 좋음


def _get_oracle_connection() -> oracledb.Connection:
    return oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)


def _week_to_db_yld(week_agent: str) -> str:
    """'2026-W06' → '2026-06'  (DF_DIE_TO_WF_YLD WEEK 컬럼용)"""
    return week_agent.replace("-W", "-")


def _week_to_db_gms(week_agent: str) -> str:
    """'2026-W06' → '202606'  (DF_GMS_YIELD_WEEKLY PERIOD_DATE 컬럼용)"""
    return week_agent.replace("-W", "")


def _fetch_weekly_sql(lotcd: str, week_strs: list[str], process: str) -> dict[str, dict]:
    """pt1h 또는 pt1c 프로세스의 n주 데이터를 Oracle SQL 한 번으로 조회.

    Returns:
        {week_agent_str: {"lotcount": N, "wfCount": N, PARAM: avg_val, ...}}
    """
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
    # DB week → agent week 역매핑
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
    """GMS 주차별 yield 조회 (DF_GMS_YIELD_WEEKLY).

    Returns:
        {week_agent_str: {"fab": v, "prb": v, "cum0": v, "cum2": "-", "pnt": "-",
                          "lotcount": "-", "wfCount": "-"}}
    """
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


def _get_n_weeks(ref: date, n: int) -> list[date]:
    """ref 기준 최근 n주의 월요일 리스트 (오래된순)"""
    monday = _week_monday(ref)
    return [monday - timedelta(weeks=i) for i in range(n - 1, -1, -1)]


def _get_n_months(ref: date, n: int) -> list[str]:
    """ref 기준 최근 n개월의 YYYY-MM 리스트 (오래된순)

    예: ref=2026-03-27, n=3 → ["2026-01", "2026-02", "2026-03"]
    """
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
    """ref 기준 최근 n일의 date 리스트 (오래된순)

    예: ref=2026-03-27, n=4 → [2026-03-24, 2026-03-25, 2026-03-26, 2026-03-27]
    """
    return [ref - timedelta(days=n - 1 - i) for i in range(n)]


def _fetch_monthly_sql(lotcd: str, month_strs: list[str], process: str) -> dict[str, dict]:
    """YEAR + MONTH 컬럼 기반 월별 조회 (DF_DIE_TO_WF_YLD).
    month_strs: "YYYY-MM" 포맷

    Returns:
        {"YYYY-MM": {"lotcount": N, "wfCount": N, PARAM: avg_val, ...}}
    """
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
    """MEASURETIME_START 범위 조건 기반 일별 조회 (DF_DIE_TO_WF_YLD, range scan).

    Returns:
        {"YYYY-MM-DD": {"lotcount": N, "wfCount": N, PARAM: avg_val, ...}}
    """
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
                # day_date는 datetime 또는 date 객체일 수 있음
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
    """GMS 월별 yield 조회 (DF_GMS_YIELD_MONTHLY).
    month_strs: "YYYY-MM" → DB PERIOD_DATE "YYYYMM" 변환

    Returns:
        {"YYYY-MM": {"fab": v, "prb": v, "cum0": v, ...}}
    """
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
    """GMS 일별 yield 조회 (DF_GMS_YIELD_DAILY).
    days: date 객체 → DB PERIOD_DATE "YYYYMMDD" 변환

    Returns:
        {"YYYY-MM-DD": {"fab": v, "prb": v, "cum0": v, ...}}
    """
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


DEFAULT_PERIODS = {"weekly": 4, "monthly": 3, "daily": 4}


@observe(name="fetch_periods")
@timed
def _fetch_periods(lotcd: str, ref_date: date,
                   unit: str = "weekly", periods: int = 0) -> list[dict]:
    """unit(weekly/monthly/daily) + periods 기반 데이터 조회.

    Args:
        lotcd: 제품코드
        ref_date: 기준 날짜
        unit: "weekly" | "monthly" | "daily"
        periods: 조회 기간 수 (0 = DEFAULT_PERIODS 기본값 사용)

    Returns:
        list[dict]: 각 기간별 데이터 (wd["week"] 키 유지)
    """
    n = periods if periods > 0 else DEFAULT_PERIODS.get(unit, 4)

    if unit == "monthly":
        month_strs = _get_n_months(ref_date, n)
        pt1h_data = _fetch_monthly_sql(lotcd, month_strs, "pt1h")
        pt1c_data = _fetch_monthly_sql(lotcd, month_strs, "pt1c")
        gms_data  = _fetch_gms_monthly_sql(lotcd, month_strs)
        period_labels = month_strs
    elif unit == "daily":
        days = _get_n_days(ref_date, n)
        pt1h_data = _fetch_daily_sql(lotcd, days, "pt1h")
        pt1c_data = _fetch_daily_sql(lotcd, days, "pt1c")
        gms_data  = _fetch_gms_daily_sql(lotcd, days)
        period_labels = [d.strftime("%Y-%m-%d") for d in days]
    else:
        # weekly (기본값)
        mondays = _get_n_weeks(ref_date, n)
        week_strs = [_iso_week_str(m) for m in mondays]
        pt1h_data = _fetch_weekly_sql(lotcd, week_strs, "pt1h")
        pt1c_data = _fetch_weekly_sql(lotcd, week_strs, "pt1c")
        gms_data  = _fetch_gms_sql(lotcd, week_strs)
        period_labels = week_strs

    results = []
    for label in period_labels:
        print(f"  [SQL] {label} 완료")

        pt1h = pt1h_data.get(label, {})
        pt1c = pt1c_data.get(label, {})
        gms  = gms_data.get(label, {})

        wd = {"week": label}   # downstream 코드가 wd["week"]를 사용하므로 키 이름 유지
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


# ============================================================
# 5. 테이블 생성 함수
# ============================================================
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


_PERIOD_LABEL = {"weekly": "WEEK", "monthly": "MONTH", "daily": "DATE", "lot": "LOT"}


def _build_table(weeks_data: list[dict], lotcd: str, filter_params=None,
                 unit: str = "weekly") -> str:
    """n기간치 데이터를 테이블 문자열로 변환 (pt1h + pt1c + gms 3섹션)

    Args:
        weeks_data: _fetch_periods 반환값
        lotcd: 제품코드
        filter_params: 표시할 파라미터 목록 (None 또는 빈 리스트 = 전체)
        unit: "weekly" | "monthly" | "daily"

    Returns:
        str: 포맷된 테이블 문자열
    """
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


def _build_html_table(
    weeks_data: list[dict],
    lotcd: str,
    filter_params=None,
    anomaly_params=None,
    unit: str = "weekly",
) -> str:
    """n기간치 데이터를 동적 HTML 테이블로 변환 (pt1h + pt1c + gms 3섹션)

    Args:
        weeks_data: _fetch_periods 반환값
        lotcd: 제품코드
        filter_params: 표시할 파라미터 목록 (None 또는 빈 리스트 = 전체)
        anomaly_params: _detect_anomalies 반환값 (None이면 하이라이팅 없음)
        unit: "weekly" | "monthly" | "daily"

    Returns:
        str: HTML 문자열 (이상감지 하이라이팅, Sparkline, Δ행 포함)
    """
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

    # Row 1: 섹션 헤더 (WEEK/MONTH/DATE rowspan=2, pt1h/pt1c/gms colspan)
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

    # Row 2: 서브헤더 (LOT, WF, 파라미터명 × 3섹션)
    html += "\n<tr>"

    # pt1h 서브헤더
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

    # pt1c 서브헤더 (첫 컬럼에 좌측 구분선)
    for j, col in enumerate(PT1C_COLUMNS):
        sep = ' style="border-left: 3px solid #3b82f6;"' if j == 0 else ""
        html += (
            f'<th class="th-param th-amber-pt1c"{sep}>'
            f'<div class="th-param-inner">'
            f'<div class="hname">{col}</div>'
            f'</div></th>'
        )

    # gms 서브헤더 (첫 컬럼에 좌측 구분선)
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

        # 공통 LOT/WF (한 번만)
        html += f'<td>{wd.get("lotcount", "-")}</td>'
        html += f'<td>{wd.get("wfCount", "-")}</td>'

        # pt1h 셀
        for col in cols:
            display = _fmt_val(wd.get(col, "-"))
            if i == last_idx and col in anomaly_map:
                a = anomaly_map[col]
                css = "degraded" if a["direction"] == "열화" else "improved"
                tip = f"{a['prev_val']} → {a['curr_val']} ({a['change_pct']:+.1f}%)"
                html += f'<td class="{css}" title="{tip}">{display}</td>'
            else:
                html += f"<td>{display}</td>"

        # pt1c 셀 (첫 셀에 구분선)
        for j, col in enumerate(PT1C_COLUMNS):
            display = _fmt_val(wd.get(f"pt1c_{col}", "-"))
            sep = ' class="td-pt1c-sep"' if j == 0 else ""
            html += f"<td{sep}>{display}</td>"

        # gms 셀 (첫 셀에 구분선)
        for j, col in enumerate(gms_cols):
            display = _fmt_gms_val(wd.get(f"gms_{col}", "-"))
            sep = ' class="td-gms-sep"' if j == 0 else ""
            html += f"<td{sep}>{display}</td>"

        html += "</tr>\n"

    # Δ 행 (최신주 - 직전주, 3섹션)
    if len(weeks_data) >= 2:
        html += '<tr class="delta">'
        html += '<td class="delta-week">Δ</td>'
        html += "<td>—</td><td>—</td>"  # 공통 LOT/WF

        # pt1h delta
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

        # pt1c delta (첫 셀에 구분선)
        for j, col in enumerate(PT1C_COLUMNS):
            d = delta_pt1c.get(col)
            sep = " td-pt1c-sep" if j == 0 else ""
            if d is None or abs(d) < 1e-12:
                html += f'<td class="{sep.strip()}">—</td>' if sep else "<td>—</td>"
            else:
                css = "delta-pos" + sep
                html += f'<td class="{css}">{d:+.2f}</td>'

        # gms delta (첫 셀에 구분선)
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
                "prev_val": round(pv, 2),
                "curr_val": round(cv, 2),
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
주어진 기간별 pt1h 파라미터 데이터를 분석하여 인사이트를 제공합니다.
항상 한국어로 답변하세요."""

ANALYSIS_USER_PROMPT = """아래는 [{lotcd}] 제품의 최근 {n}기간 pt1h 파라미터 데이터입니다.

{table}

=== 파라미터 극성 규칙 ===
- VTH, IDSAT: 값이 높아지면 개선 (↑ = 개선)
- 그 외 모든 파라미터: 값이 낮아지면 개선 (↓ = 개선)

=== 비교 대상 ===
- Pre:    {prev_week}
- Latest: {curr_week}

위 두 기간을 비교하여 다음을 분석해주세요:

1. **가장 열화된 파라미터 Top 3** (파라미터명, Pre 값, Latest 값, 변화율%)
2. **가장 개선된 파라미터 Top 3** (파라미터명, Pre 값, Latest 값, 변화율%)
3. **전반적인 트렌드 요약** (1~2문장)

마크다운 표 형식으로 깔끔하게 정리해주세요."""


@observe(name="analyze_with_llm")
@timed
def _analyze_with_llm(weeks_data: list[dict], table_str: str, lotcd: str, llm, n: int = 4, config=None) -> str:
    """최근 2기간 데이터를 LLM에게 전달하여 열화/개선 분석을 받는 헬퍼 함수

    Args:
        weeks_data: n기간 데이터 리스트 (오래된 순)
        table_str: 이미 생성된 테이블 문자열
        lotcd: 제품코드
        llm: ChatOpenAI 인스턴스
        n: 조회 기간 수

    Returns:
        str: LLM 분석 결과 (마크다운)
    """
    if len(weeks_data) < 2:
        return "분석에 필요한 2기간 이상의 데이터가 부족합니다."

    prev_week = weeks_data[-2]
    curr_week = weeks_data[-1]

    user_prompt = ANALYSIS_USER_PROMPT.format(
        lotcd=lotcd,
        table=table_str,
        n=n,
        prev_week=prev_week.get("week", "?"),
        curr_week=curr_week.get("week", "?"),
    )

    try:
        response = llm.invoke(
            [
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            config={**(config or {}), "callbacks": _lf_callbacks()},
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
# 7. Lot 비교 모드 유틸리티
# ============================================================

def _parse_lot_specs(lot_ids: str = "", groupkey: str = "") -> list[tuple[str, int | None]]:
    """lot_ids / groupkey 문자열 → [(lotid, wfid_or_None), ...] 정규화

    lot_ids="4SS2DPD,4SSXCEW"           → [("4SS2DPD", None), ("4SSXCEW", None)]
    groupkey="4SS2DPD.01,4SS2DPD.05"    → [("4SS2DPD", 1), ("4SS2DPD", 5)]
    mixed: groupkey에 점 없는 "LOT3"    → ("LOT3", None)
    """
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
@observe(name="fetch_lot_sql")
@timed
def _fetch_lot_sql(lot_specs: list[tuple[str, int | None]], process: str) -> dict[str, dict]:
    """lot 단위 수율 비교 조회.

    - wfid=None  → lot 전체 평균 (GROUP BY LOTID, PARAM). "_wf_count" 키에 WF 수 저장.
    - wfid!=None → 해당 WF만 조회 (GROUP BY LOTID, WFID, PARAM).

    Returns:
        {"4SSXCEW": {"_wf_count": 25, PARAM: avg_val, ...},
         "4SS2DPD.01": {PARAM: avg_val, ...}}
    """
    if not lot_specs:
        return {}

    result: dict[str, dict] = {}

    # lot-level 스펙과 wf-level 스펙을 분리
    lot_level  = [(i, lotid) for i, (lotid, wfid) in enumerate(lot_specs) if wfid is None]
    wf_level   = [(i, lotid, wfid) for i, (lotid, wfid) in enumerate(lot_specs) if wfid is not None]

    try:
        conn = _get_oracle_connection()
        with conn.cursor() as cur:

            # ── lot-level: LOTID별 전체 평균 + WF 수 ──
            if lot_level:
                bind: dict = {"process": process}
                clauses = []
                for idx, (_, lotid) in enumerate(lot_level):
                    bind[f"l{idx}"] = lotid
                    clauses.append(f"LOTID = :l{idx}")
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

            # ── wf-level: 특정 WF 조회 ──
            if wf_level:
                bind2: dict = {"process": process}
                clauses2 = []
                for idx, (_, lotid, wfid) in enumerate(wf_level):
                    bind2[f"l{idx}"] = lotid
                    bind2[f"w{idx}"] = wfid
                    clauses2.append(f"(LOTID = :l{idx} AND WFID = :w{idx})")
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
    """pt1h + pt1c 데이터를 lot 키 기준으로 병합.

    pt1c param들은 "pt1c_{PARAM}" 접두사로 저장.
    """
    all_keys = set(pt1h) | set(pt1c)
    merged: dict[str, dict] = {}
    for key in all_keys:
        merged[key] = {}
        for param, val in (pt1h.get(key) or {}).items():
            merged[key][param] = val
        for param, val in (pt1c.get(key) or {}).items():
            merged[key][f"pt1c_{param}"] = val
    return merged


def _build_lot_table(lot_data: dict[str, dict], mode: str = "lot", filter_params=None) -> str:
    """lot 비교 텍스트 테이블.

    mode="lot"      → lot ID 행, WF_CNT 컬럼 포함
    mode="groupkey" → LOT.WF 행, WF_CNT 컬럼 없음
    첫 행: Avg
    """
    if not lot_data:
        return "(lot 데이터 없음)"

    cols = [c for c in PARA_COLUMNS if c in filter_params] if filter_params else PARA_COLUMNS
    all_params = cols + [f"pt1c_{c}" for c in PT1C_COLUMNS]
    lot_keys = sorted(k for k in lot_data if not k.startswith("_"))

    # avg 계산
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
    """lot 비교 HTML 테이블.

    mode="lot"      → LOT 열 + WF_CNT 열, 첫 행 Avg
    mode="groupkey" → LOT 열만, 첫 행 Avg
    기존 period 테이블과 동일한 CSS/구조.
    """
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

    # ── CSS (period 테이블과 동일) ──────────────────────────
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

    # ── 섹션 헤더 ──────────────────────────────────────────
    meta_span = 2 if mode == "lot" else 1  # LOT + WF_CNT or LOT only
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

    # ── 파라미터 서브헤더 ───────────────────────────────────
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

    # ── Avg 행 (첫 행) ─────────────────────────────────────
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

    # ── 데이터 행 ──────────────────────────────────────────
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
    unit    = state.get("unit", "weekly")
    periods = state.get("periods", 0)
    yield_lot_ids  = state.get("yield_lot_ids", "")
    yㅈield_groupkey = state.get("yield_groupkey", "")

    # ── LOT 비교 모드 ────────────────────────────────────────
    if yield_lot_ids or yield_groupkey:
        logger.info("[YieldAgent] lot 비교 모드: lot_ids=%s groupkey=%s", yield_lot_ids, yield_groupkey)
        lot_specs = _parse_lot_specs(yield_lot_ids, yield_groupkey)

        print("=" * 60)
        print("[Yield Agent] LOT 비교 모드:")
        print(f"  - lot_specs: {lot_specs}")
        print("=" * 60)

        # groupkey 포함 여부로 모드 결정
        lot_mode = "groupkey" if yield_groupkey else "lot"

        pt1h_lot = _fetch_lot_sql(lot_specs, "pt1h")
        pt1c_lot = _fetch_lot_sql(lot_specs, "pt1c")
        merged   = _merge_lot_data(pt1h_lot, pt1c_lot)

        if not merged:
            error_message = AIMessage(
                content="해당 lot 데이터를 조회할 수 없습니다. lot ID와 process를 확인해주세요.",
                name="yield_agent",
            )
            return {"messages": [error_message], "weeks_data": [], "table_result": ""}

        table_str  = _build_lot_table(merged, mode=lot_mode, filter_params=filter_params)
        html_table = _build_lot_html_table(merged, mode=lot_mode, filter_params=filter_params)
        print(table_str)

        lot_label = yield_groupkey or yield_lot_ids
        result_msg = f"[LOT 비교] {lot_label} pt1h+pt1c 수율 비교 테이블입니다.\n\n"
        result_msg += table_str

        yield_artifacts = [{
            "type": "html",
            "mime": "text/html",
            "data": html_table,
            "title": "lot_compare_table",
        }]

        result_message = AIMessage(content=result_msg, name="yield_agent")
        return {
            "messages": [result_message],
            "weeks_data": [],
            "table_result": table_str,
            "analysis_result": "",
            "yield_artifacts": yield_artifacts,
            "anomaly_params": [],
            "agent_suggestion": "",
            "need_cummap": False,
            "cummap_weeks": [],
            "cummap_target_bin": "",
            "cummap_category": "",
        }

    # ── 기존 period 모드 ────────────────────────────────────
    # 날짜 파싱
    try:
        ref_date = datetime.strptime(ref_date_str, "%Y%m%d").date()
    except ValueError:
        ref_date = date.today()

    n = periods if periods > 0 else DEFAULT_PERIODS.get(unit, 4)

    # 파라미터 로깅
    print("=" * 60)
    print("[Yield Agent] State에서 파라미터 읽기:")
    print(f"  - lotcd: {lotcd}")
    print(f"  - ref_date: {ref_date_str}")
    print(f"  - unit: {unit}, periods: {n}")
    print("=" * 60)

    # 데이터 조회
    print(f"\n[Yield Agent] {lotcd} 최근 {n}{dict(weekly='주', monthly='달', daily='일').get(unit, unit)} 데이터 조회 시작...")
    weeks_data = _fetch_periods(lotcd, ref_date, unit=unit, periods=periods)

    if not weeks_data or all(wd.get("lotcount") == "-" for wd in weeks_data):
        error_message = AIMessage(
            content="데이터를 조회할 수 없습니다. Oracle DB 연결 또는 해당 기간 데이터를 확인해주세요.",
            name="yield_agent",
        )
        return {"messages": [error_message], "weeks_data": [], "table_result": ""}

    # 수치 기반 이상감지 (html_table 생성 전에 먼저 계산)
    anomaly_params = _detect_anomalies(weeks_data)
    print(f"[Yield Agent] 이상 감지: {len(anomaly_params)}개 파라미터")

    # 테이블 생성 (텍스트: LLM 분석용, HTML: 사용자 표시용)
    table_str = _build_table(weeks_data, lotcd, filter_params, unit=unit)
    html_table = _build_html_table(weeks_data, lotcd, filter_params, anomaly_params, unit=unit)
    print(table_str)

    # LLM 분석 (최근 2주 비교)
    print(f"\n[Yield Agent] LLM 분석 시작 (최근 2주 비교)...")
    analysis = _analyze_with_llm(weeks_data, table_str, lotcd, model, n=n, config=config)
    print(f"\n[LLM 분석 결과]\n{analysis}")

    # HTML 아티팩트 생성
    yield_artifacts = [{
        "type": "html",
        "mime": "text/html",
        "data": html_table,
        "title": "yield_table",
    }]

    # 결과 메시지 생성 (HTML 테이블은 아티팩트로 별도 전달)
    unit_label = {"weekly": f"주간 (최근 {n}주)", "monthly": f"월별 (최근 {n}달)", "daily": f"일별 (최근 {n}일)"}.get(unit, f"최근 {n}개")
    result_msg = f"[{lotcd}] {unit_label} pt1h 수율 데이터입니다.\n"
    result_msg += f"기준: {ref_date_str}\n\n"
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

    # agent_suggestion: UI 렌더링용 (anomaly가 있을 때만 제안)
    agent_suggestion = (
        "오늘 검출된 열화 Parameter를 보여드릴까요?"
        if anomaly_params else ""
    )

    # need_cummap 시 4주치 날짜 범위 + target_bin 설정
    need_cummap = state.get("need_cummap", False)
    cummap_weeks = []
    cummap_target_bin = ""
    cummap_category = ""

    if need_cummap and filter_params:
        from map_agent import CATEGORY_TO_BIN
        category = filter_params[0].upper()
        target_bin = CATEGORY_TO_BIN.get(category, "")
        if target_bin:
            cummap_target_bin = target_bin
            cummap_category = category
            mondays = _get_4_week_dates(ref_date)
            for m in mondays:
                end = m + timedelta(days=7)
                cummap_weeks.append({
                    "week": _iso_week_str(m),
                    "start": m.strftime("%Y%m%d"),
                    "end": end.strftime("%Y%m%d"),
                })
            logger.info("[YieldAgent] cummap 연계: %s (bin=%s), %d weeks",
                        category, target_bin, len(cummap_weeks))

    return {
        "messages": [result_message],
        "weeks_data": weeks_data,
        "table_result": table_str,
        "analysis_result": analysis,
        "yield_artifacts": yield_artifacts,
        "anomaly_params": anomaly_params,
        "agent_suggestion": agent_suggestion,
        "need_cummap": need_cummap and bool(cummap_weeks),
        "cummap_weeks": cummap_weeks,
        "cummap_target_bin": cummap_target_bin,
        "cummap_category": cummap_category,
    }




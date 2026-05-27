"""
WADS @tool 함수 모듈
====================
WADS 데이터 조회/리포트 도구 함수와 ContextVar 격리 로직.
wads_agent.py에서 분리.
"""
from __future__ import annotations

import contextvars
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import oracledb
import pandas as pd
from langchain_core.tools import tool
from langfuse import observe

from common import get_oracle_connection as _get_oracle_connection, get_llm

import os
from dotenv import load_dotenv

load_dotenv(override=True)
# 실제 WADS 스키마는 2개 테이블로 분리:
#   DF_WADS_REPORT  — 리포트(lotcd × category × parameter × end_tm 단위), HTML 보유
#   DF_WADS_WF_LIST — 검출된 wafer(GROUPKEY) 단위, HTML 없음
_REPORT_TABLE = os.getenv("WADS_REPORT_TABLE", "DF_WADS_REPORT")
_WF_LIST_TABLE = os.getenv("WADS_WF_LIST_TABLE", "DF_WADS_WF_LIST")
_ALLOWED_TABLES = {_REPORT_TABLE.upper(), _WF_LIST_TABLE.upper()}
_SQL_GEN_MODEL = os.getenv("WADS_SQL_GEN_MODEL", os.getenv("RETRIEVE_CHAIN_MODEL"))

logger = logging.getLogger("yield_agent.wads_tools")


# ── ContextVar (요청별 격리) ──────────────────────────────────
_tool_payload_var: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "_wads_tool_payload"
)


def _get_tool_payload() -> Dict[str, Any]:
    """현재 컨텍스트의 tool payload storage 반환 (없으면 초기화)"""
    try:
        return _tool_payload_var.get()
    except LookupError:
        storage: Dict[str, Any] = {"reports": []}
        _tool_payload_var.set(storage)
        return storage


# DF_WADS_REPORT 의 END_TM 은 "YY/MM/DD ..." 포맷. SUBSTR(END_TM, 1, 8) + TO_DATE(..., 'YY/MM/DD') 로 정규화.
_END_TM_DATE_EXPR = "TO_DATE(SUBSTR(END_TM, 1, 8), 'YY/MM/DD')"


def _normalize_date_input(s: str) -> str:
    """사용자 입력 'YYYY-MM-DD' / 'YY/MM/DD' 모두 받아 Oracle TO_DATE 용 'YY/MM/DD' 로 정규화."""
    s = (s or "").strip()
    if not s:
        return s
    # YYYY-MM-DD → YY/MM/DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return f"{s[2:4]}/{s[5:7]}/{s[8:10]}"
    # YY/MM/DD → 그대로
    if re.match(r"^\d{2}/\d{2}/\d{2}$", s):
        return s
    # 그 외(예: 'YYYY/MM/DD', 'YY-MM-DD') 도 best-effort
    digits = re.findall(r"\d+", s)
    if len(digits) >= 3:
        y, m, d = digits[0], digits[1], digits[2]
        if len(y) == 4:
            y = y[2:]
        return f"{y.zfill(2)}/{m.zfill(2)}/{d.zfill(2)}"
    return s


# ── Oracle 조회 ───────────────────────────────────────────────
@observe(name="wads_query_oracle")
def _query_wads_data(
    lotcd: Optional[str] = None,
    end_tm: Optional[str] = None,
    start_tm: Optional[str] = None,
    parameter: Optional[str] = None,
    category: Optional[str] = None,
    columns: str = "LOTCD, CATEGORY, PARAMETER, END_TM",
) -> pd.DataFrame:
    """DF_WADS_REPORT 조회 (SQL WHERE + LIKE 필터링).

    columns 는 내부 호출 전용. LLM/도구 인자로 직접 노출 금지 (HTML 누출 방지).
    """
    logger.info("[_query_wads_data] 조회 시작: lotcd=%s, end_tm=%s, start_tm=%s, parameter=%s, category=%s, columns=%s",
                lotcd, end_tm, start_tm, parameter, category, columns)
    conditions = []
    bind_vars = {}

    if lotcd:
        conditions.append("UPPER(LOTCD) LIKE UPPER(:lotcd)")
        bind_vars["lotcd"] = f"%{lotcd}%"
    if category:
        conditions.append("UPPER(CATEGORY) LIKE UPPER(:category)")
        bind_vars["category"] = f"%{category}%"
    if start_tm and end_tm:
        conditions.append(
            f"TRUNC({_END_TM_DATE_EXPR}) "
            "BETWEEN TO_DATE(:start_tm, 'YY/MM/DD') AND TO_DATE(:end_tm_range, 'YY/MM/DD')"
        )
        bind_vars["start_tm"] = _normalize_date_input(start_tm)
        bind_vars["end_tm_range"] = _normalize_date_input(end_tm)
    elif end_tm:
        conditions.append("END_TM LIKE :end_tm")
        bind_vars["end_tm"] = f"%{_normalize_date_input(end_tm)}%"
    if parameter:
        conditions.append("UPPER(PARAMETER) LIKE UPPER(:parameter)")
        bind_vars["parameter"] = f"%{parameter}%"

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    sql = f"SELECT {columns} FROM {_REPORT_TABLE} WHERE {where_clause}"
    logger.info("[_query_wads_data] SQL: %s | bind: %s", sql, bind_vars)

    conn = _get_oracle_connection()
    try:
        def output_type_handler(cursor, metadata):
            if metadata.type_code is oracledb.DB_TYPE_CLOB:
                return cursor.var(oracledb.DB_TYPE_LONG, arraysize=cursor.arraysize)

        conn.outputtypehandler = output_type_handler

        cursor = conn.cursor()
        cursor.execute(sql, bind_vars)

        col_names = [desc[0].lower() for desc in cursor.description]
        rows = cursor.fetchall()
        cursor.close()

        df = pd.DataFrame(rows, columns=col_names)
        logger.info("[_query_wads_data] 조회 완료: %d rows, columns=%s", len(df), list(col_names))
        return df
    except Exception as e:
        logger.error("[_query_wads_data] Oracle 조회 실패: %s", e, exc_info=True)
        raise
    finally:
        conn.close()


# ── @tool 함수 ────────────────────────────────────────────────
@tool
def wads_query_data(
    lotcd: Optional[str] = None,
    end_tm: Optional[str] = None,
    start_tm: Optional[str] = None,
    parameter: Optional[str] = None,
    category: Optional[str] = None,
) -> str:
    """
    DF_WADS_REPORT 조회. 선택적 필터를 적용하여 매칭되는 리포트의 메타정보를 반환합니다.
    HTML 컬럼은 절대 반환하지 않습니다 (리포트 본문은 wads_get_html_report 사용).

    Args:
        lotcd: 로트코드 필터 (예: "5NA", "4SS"). 부분 일치. 미입력시 전체.
        end_tm: 종료시간 필터 (예: "2026-01-01" 또는 "26/01/01"). 부분 일치. 단독 사용시.
        start_tm: 시작 날짜 ("YYYY-MM-DD" 또는 "YY/MM/DD"). end_tm 과 함께 사용하면 날짜 범위.
        parameter: fail_type 필터 (예: "EASY", "TWT"). 부분 일치. 미입력시 전체.
        category: 테스트 카테고리 필터 ("PT1H_TEST" 또는 "PT1C_TEST"). 부분 일치. 미입력시 전체.

    Returns:
        조회 결과 요약 (실제 데이터는 별도 저장됨)
    """
    storage = _get_tool_payload()
    defaults = storage.get("_defaults", {})
    lotcd = lotcd or defaults.get("lotcd")
    end_tm = end_tm or defaults.get("end_tm")
    start_tm = start_tm or defaults.get("start_tm")
    parameter = parameter or defaults.get("parameter")
    category = category or defaults.get("category")
    logger.info("[wads_query_data] 호출: lotcd=%s, end_tm=%s, start_tm=%s, parameter=%s, category=%s",
                lotcd, end_tm, start_tm, parameter, category)

    try:
        filtered_df = _query_wads_data(
            lotcd=lotcd,
            end_tm=end_tm,
            start_tm=start_tm,
            parameter=parameter,
            category=category,
            columns="LOTCD, CATEGORY, PARAMETER, END_TM",
        )
    except Exception as e:
        logger.error("[wads_query_data] Oracle 오류: %s", e, exc_info=True)
        storage["query"] = [
            {
                "error": "DBError",
                "detail": f"Oracle 연결/조회 오류: {e}",
            }
        ]
        return f"오류: Oracle 연결/조회에 실패했습니다. ({e})"

    if filtered_df.empty:
        logger.info("[wads_query_data] 결과 없음 (NoMatch)")
        storage["query"] = [
            {"error": "NoMatch", "detail": "조건에 맞는 WADS 데이터가 없습니다."}
        ]
        return "조건에 맞는 WADS 데이터가 없습니다."

    result = filtered_df[["lotcd", "category", "parameter", "end_tm"]].to_dict(orient="records")
    storage["query"] = result
    logger.info("[wads_query_data] 완료: %d건", len(result))

    return _summarize_query_result(result, lotcd, start_tm, end_tm)


def _top_counts(values: List[str], top_k: int = 5) -> str:
    """문자열 리스트의 빈도수 상위 top_k 를 '값=cnt, ...' 형태로 직렬화."""
    from collections import Counter
    counts = Counter(v for v in values if v)
    if not counts:
        return "(없음)"
    items = counts.most_common(top_k)
    extra = len(counts) - len(items)
    s = ", ".join(f"{k}={v}건" for k, v in items)
    if extra > 0:
        s += f", … (+{extra}종)"
    return s


def _summarize_query_result(
    rows: List[Dict[str, Any]],
    lotcd: Optional[str],
    start_tm: Optional[str],
    end_tm: Optional[str],
) -> str:
    """wads_query_data 결과를 LLM 이 본문에서 직접 인용할 수 있는 풍부한 요약 텍스트로 변환."""
    n = len(rows)
    lines = [f"WADS 리포트 조회 완료: 총 {n}건 (DF_WADS_REPORT)"]

    # 기간 / 일평균
    end_tms = [r.get("end_tm") for r in rows if r.get("end_tm")]
    unique_days = {str(t)[:8] for t in end_tms if t}
    if start_tm and end_tm:
        if unique_days:
            avg = n / max(len(unique_days), 1)
            lines.append(f"- 기간: {start_tm} ~ {end_tm} ({len(unique_days)}일치, 일평균 {avg:.1f}건)")
        else:
            lines.append(f"- 기간: {start_tm} ~ {end_tm}")
    elif end_tm:
        lines.append(f"- 날짜: {end_tm}")

    # 분포
    lines.append(f"- CATEGORY 분포: {_top_counts([r.get('category', '') for r in rows])}")
    lines.append(f"- PARAMETER(fail_type) 분포: {_top_counts([r.get('parameter', '') for r in rows])}")
    lines.append(f"- LOTCD 분포: {_top_counts([r.get('lotcd', '') for r in rows])}")

    # 최신 3건 샘플
    sample = rows[:3]
    if sample:
        lines.append("- 최신 3건:")
        for r in sample:
            lines.append(
                f"    · LOTCD={r.get('lotcd')}, CATEGORY={r.get('category')}, "
                f"PARAMETER={r.get('parameter')}, END_TM={r.get('end_tm')}"
            )

    return "\n".join(lines)


@tool
def wads_get_html_report(
    lotcd: Optional[str] = None,
    end_tm: Optional[str] = None,
    start_tm: Optional[str] = None,
    parameter: Optional[str] = None,
    category: Optional[str] = None,
) -> str:
    """
    DF_WADS_REPORT 의 열화 리포트 본문(HTML)을 조회합니다.
    **HTML 본문이 필요한 경우 유일한 통로** — 통계/건수/리스트는 wads_query_data 사용.
    여러 번 호출하면 리포트가 누적되어 표시됩니다.

    Args:
        lotcd: 로트코드 필터 (예: "5NA", "4SS"). 부분 일치.
        end_tm: 종료시간 필터 (예: "2026-01-01" 또는 "26/01/01"). 부분 일치.
        start_tm: 시작 날짜. end_tm 과 함께 사용하면 날짜 범위.
        parameter: fail_type 필터 (예: "EASY", "TWT"). 부분 일치.
        category: 테스트 카테고리 ("PT1H_TEST" 또는 "PT1C_TEST"). 부분 일치.

    Returns:
        조회 결과 요약 (실제 HTML은 별도 저장됨)
    """
    storage = _get_tool_payload()
    defaults = storage.get("_defaults", {})
    lotcd = lotcd or defaults.get("lotcd")
    end_tm = end_tm or defaults.get("end_tm")
    start_tm = start_tm or defaults.get("start_tm")
    parameter = parameter or defaults.get("parameter")
    category = category or defaults.get("category")
    logger.info("[wads_get_html_report] 호출: lotcd=%s, end_tm=%s, start_tm=%s, parameter=%s, category=%s",
                lotcd, end_tm, start_tm, parameter, category)

    try:
        filtered_df = _query_wads_data(
            lotcd=lotcd,
            end_tm=end_tm,
            start_tm=start_tm,
            parameter=parameter,
            category=category,
            columns="LOTCD, CATEGORY, PARAMETER, END_TM, HTML",
        )
    except Exception as e:
        logger.error("[wads_get_html_report] Oracle 오류: %s", e, exc_info=True)
        return f"오류: Oracle 연결/조회에 실패했습니다. ({e})"

    if filtered_df.empty:
        logger.info("[wads_get_html_report] 결과 없음")
        return "조건에 맞는 WADS 데이터가 없습니다."

    if "reports" not in storage:
        storage["reports"] = []

    for _, row in filtered_df.iterrows():
        storage["reports"].append(
            {
                "lotcd": row["lotcd"],
                "category": row["category"],
                "end_tm": row["end_tm"],
                "parameter": row["parameter"],
                "html": row["html"],
            }
        )

    count = len(filtered_df)
    summary = ", ".join(
        f"lotcd={r['lotcd']} category={r['category']} parameter={r['parameter']}"
        for r in storage["reports"][-count:]
    )
    return f"WADS HTML 리포트 조회 완료: {count}건 — {summary} (리포트는 화면에 별도 표시됩니다)"


# ── SQL 검증 (I-3: sqlparse 없이 4계층) ──────────────────────
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|MERGE|CREATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


def _strip_sql_comments(sql: str) -> str:
    """SQL 주석 제거 (-- 한줄, /* */ 블록)"""
    sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql


def _strip_string_literals(sql: str) -> str:
    """작은따옴표 문자열 리터럴을 빈 문자열로 치환 — HTML/* 토큰 검사 false positive 방지."""
    return re.sub(r"'(?:''|[^'])*'", "''", sql)


_HTML_TOKEN = re.compile(r"\bHTML\b", re.IGNORECASE)
_SELECT_STAR = re.compile(r"\bSELECT\s+\*", re.IGNORECASE)


def _validate_sql(sql: str) -> Tuple[bool, str]:
    """
    5계층 SQL 검증. (ok, reason) 반환.
    계층1: 주석 제거, 계층2: SELECT 확인, 계층3: 테이블 허용목록 + 금지 키워드,
    계층4: 쿼리 타임아웃(cursor-side), 계층5: HTML 컬럼 차단 + SELECT * 차단.
    """
    cleaned = _strip_sql_comments(sql).strip()
    # trailing 단일 세미콜론은 LLM의 흔한 습관이므로 자동 제거 (multi-statement 검사는 유지)
    cleaned = cleaned.rstrip(";").strip()
    if not cleaned:
        return False, "빈 SQL"

    # 세미콜론 금지 (multi-statement 방지)
    if ";" in cleaned:
        return False, "세미콜론(;)은 허용되지 않습니다"

    # 계층2: SELECT 확인
    if not cleaned.upper().startswith("SELECT"):
        return False, "SELECT 문만 허용됩니다"

    # 금지 키워드
    match = _FORBIDDEN_KEYWORDS.search(cleaned)
    if match:
        return False, f"금지 키워드 감지: {match.group()}"

    # 계층3: 테이블 허용목록 (DF_WADS_REPORT, DF_WADS_WF_LIST)
    table_refs = re.findall(r"\bFROM\s+(\w+)", cleaned, re.IGNORECASE)
    table_refs += re.findall(r"\bJOIN\s+(\w+)", cleaned, re.IGNORECASE)
    for tbl in table_refs:
        if tbl.upper() not in _ALLOWED_TABLES:
            return False, f"허용되지 않은 테이블: {tbl} (허용: {', '.join(sorted(_ALLOWED_TABLES))})"

    # 계층5-a: SELECT * 금지 — DF_WADS_REPORT 에 HTML 이 있으므로 * 는 잠재적 누출 통로
    if _SELECT_STAR.search(cleaned):
        return False, (
            "SELECT * 는 허용되지 않습니다. 컬럼을 명시적으로 나열하세요 "
            "(예: LOTCD, CATEGORY, PARAMETER, END_TM)."
        )

    # 계층5-b: HTML 컬럼 차단 — wads_query_sql 은 HTML 본문을 반환할 수 없음.
    # 문자열 리터럴 안의 'HTML' 은 토큰이 아니므로 검사 전에 제거.
    cleaned_no_strings = _strip_string_literals(cleaned)
    if _HTML_TOKEN.search(cleaned_no_strings):
        return False, (
            "HTML 컬럼은 wads_query_sql 로 조회할 수 없습니다. "
            "열화 리포트 본문이 필요하면 wads_get_html_report 도구를 사용하세요."
        )

    return True, ""


def _ensure_row_limit(sql: str, limit: int = 500) -> str:
    """FETCH FIRST / ROWNUM 행 제한이 없으면 자동 추가"""
    upper = sql.upper()
    if "FETCH FIRST" in upper or "ROWNUM" in upper:
        return sql
    return f"{sql.rstrip().rstrip(';')} FETCH FIRST {limit} ROWS ONLY"


# ── SQL 생성 프롬프트 (도구 내부 캡슐화) ──────────────────────
_SQL_GEN_PROMPT = """You are an Oracle SQL generator for WADS tables.

Tables:
  {report_table} (per-report row, with HTML)
    LOTCD      VARCHAR2  -- product code (e.g., "4SS", "5NA")
    CATEGORY   VARCHAR2  -- test category: "PT1H_TEST" or "PT1C_TEST"
    PARAMETER  VARCHAR2  -- fail_type (e.g., "EASY", "TWT")
    END_TM     VARCHAR2  -- end time in "YY/MM/DD ..." format
    HTML       CLOB      -- report body (FORBIDDEN — see Rules)

  {wf_list_table} (per-wafer detection row, no HTML)
    OPER_PARA  VARCHAR2  -- "{{CATEGORY}}_{{PARAMETER}}" e.g. "PT1H_TEST_EASY(W)"
    GROUPKEY   VARCHAR2  -- individual wafer key
    END_TM     VARCHAR2  -- end time in "YY/MM/DD ..." format
    LOT_CD     VARCHAR2  -- product code (same semantics as LOTCD)

Rules:
- SELECT statements ONLY (no INSERT/UPDATE/DELETE/DDL).
- NEVER select the HTML column. HTML is a large CLOB and is provided
  only via wads_get_html_report. If the user wants the report body,
  do NOT generate SQL — return the literal string:
    ERROR: use wads_get_html_report
- NEVER use SELECT *. Always list columns explicitly.
- Safe columns: LOTCD, CATEGORY, PARAMETER, END_TM (DF_WADS_REPORT);
                OPER_PARA, GROUPKEY, END_TM, LOT_CD (DF_WADS_WF_LIST).
- Use UPPER() for case-insensitive LOTCD/CATEGORY/PARAMETER/OPER_PARA comparisons.
- Date filtering: TRUNC(TO_DATE(SUBSTR(END_TM,1,8),'YY/MM/DD'))
- JOIN guidance: r.LOTCD = w.LOT_CD AND r.END_TM = w.END_TM
- Always add FETCH FIRST 500 ROWS ONLY.
- Output ONLY the SQL statement (or the ERROR string), no explanation.

Request: {query_description}"""


@tool
@observe(name="wads_query_sql")
def wads_query_sql(query_description: str) -> str:
    """WADS 데이터에 대한 복잡한 SQL 쿼리를 생성하고 실행합니다.
    wads_query_data/wads_get_html_report로 처리할 수 없는 복잡한 조건에만 사용하세요.
    이 도구는 내부 LLM 호출이 추가되어 다른 도구보다 느립니다.
    HTML 컬럼은 반환하지 않습니다 (열화 리포트 본문은 wads_get_html_report 사용).

    Args:
        query_description: 조회 내용을 자연어로 설명
            (예: "4SS 로트의 3월 EASY, TWT 건수를 PARAMETER별 집계")
            (예: "TWT 제외한 PT1H_TEST 전체 PARAMETER 목록")
            (예: "lotcd별 PT1H_TEST 건수 비교")

    Returns:
        조회 결과 요약 (작은 결과는 전체 JSON, 큰 결과는 top10 + 통계 메타).
    """
    storage = _get_tool_payload()

    # 1. LLM으로 SQL 생성
    try:
        llm = get_llm(model=_SQL_GEN_MODEL)
        prompt = _SQL_GEN_PROMPT.format(
            report_table=_REPORT_TABLE,
            wf_list_table=_WF_LIST_TABLE,
            query_description=query_description,
        )
        response = llm.invoke(prompt)
        raw_sql = response.content.strip()
        # 코드 블록 마커 제거
        if raw_sql.startswith("```"):
            raw_sql = re.sub(r"^```\w*\n?", "", raw_sql)
            raw_sql = re.sub(r"\n?```$", "", raw_sql)
        raw_sql = raw_sql.strip().rstrip(";").strip()
    except Exception as e:
        logger.error("[wads_query_sql] SQL 생성 실패: %s", e)
        return f"SQL 생성 실패: {e}. wads_query_data 도구를 대신 사용하세요."

    # 1.5. SQL 생성 LLM 이 ERROR 응답을 낸 경우 (예: 사용자가 HTML 본문 요청)
    if raw_sql.upper().startswith("ERROR"):
        logger.info("[wads_query_sql] SQL 생성 LLM 가드 응답: %s", raw_sql)
        return (
            "이 요청은 wads_query_sql 로 처리할 수 없습니다. "
            "열화 리포트 본문이 필요하면 wads_get_html_report 를 사용하세요."
        )

    # 2. SQL 검증
    ok, reason = _validate_sql(raw_sql)
    if not ok:
        logger.warning("[wads_query_sql] SQL 검증 실패: %s — SQL: %s", reason, raw_sql)
        return f"SQL 검증 실패: {reason}. wads_query_data 도구를 대신 사용하세요."

    # 행 제한 보장
    sql = _ensure_row_limit(raw_sql)
    logger.info("[wads_query_sql] 실행 SQL: %s", sql)

    # 3. Oracle 실행
    conn = _get_oracle_connection()
    try:
        def output_type_handler(cursor, metadata):
            if metadata.type_code is oracledb.DB_TYPE_CLOB:
                return cursor.var(oracledb.DB_TYPE_LONG, arraysize=cursor.arraysize)

        conn.outputtypehandler = output_type_handler
        cursor = conn.cursor()
        # 계층4: 쿼리 타임아웃 10초
        cursor.callTimeout = 10000
        cursor.execute(sql)

        col_names = [desc[0].lower() for desc in cursor.description]
        rows = cursor.fetchall()
        cursor.close()
    except oracledb.DatabaseError as e:
        logger.error("[wads_query_sql] Oracle 오류: %s", e)
        error_obj = e.args[0] if e.args else e
        error_code = getattr(error_obj, "code", 0)
        if error_code in (904, 942):
            return (
                f"컬럼/테이블 오류: {e}. 사용 가능 컬럼: "
                "DF_WADS_REPORT(LOTCD, CATEGORY, PARAMETER, END_TM), "
                "DF_WADS_WF_LIST(OPER_PARA, GROUPKEY, END_TM, LOT_CD)"
            )
        return f"SQL 실행 오류: {e}. query_description을 수정하여 재시도하세요."
    except Exception as e:
        logger.error("[wads_query_sql] 예기치 않은 오류: %s", e)
        return f"쿼리 실행 실패: {e}. wads_query_data 도구를 대신 사용하세요."
    finally:
        conn.close()

    # 4. 결과 처리 — ContextVar 저장 (A-3: 요약만 LLM에 반환)
    if not rows:
        storage["sql_result"] = []
        return "조건에 맞는 데이터가 없습니다."

    result = [dict(zip(col_names, row)) for row in rows]
    # _validate_sql 의 계층5 가 HTML 컬럼을 차단했으므로, 여기까지 도달했다면 HTML 없음.
    # (혹시 우회로 들어왔더라도 방어선 한 번 더 — HTML 컬럼은 sql_result 에 저장하지 않음.)
    safe_cols = [c for c in col_names if c.lower() != "html"]
    safe_result = [{c: r.get(c) for c in safe_cols} for r in result]
    storage["sql_result"] = safe_result
    # 결과 shape 로부터 layout 힌트 결정 (wads_agent_node 가 사용)
    storage["render_layout"] = _suggest_layout_hint(safe_result, safe_cols)

    return _summarize_sql_result(safe_result, safe_cols)


def _suggest_layout_hint(rows: List[Dict[str, Any]], cols: List[str]) -> str:
    """SQL 결과 shape 로부터 적절한 layout 자동 추천.

    반환: 'kpi' | 'distribution' | 'pivot' | 'trend' | 'table'
    wads_agent.py 의 _detect_layout 과 동일한 규칙 — 단일 truth 유지.
    """
    if not rows:
        return "table"
    first = rows[0]
    nrows, ncols = len(rows), len(cols)
    is_num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    is_date_col = lambda c: ("end_tm" in c.lower()) or ("date" in c.lower()) or c.lower().endswith("_tm")

    if nrows == 1 and 1 <= ncols <= 3 and any(is_num(first.get(c)) for c in cols):
        return "kpi"

    has_date = any(is_date_col(c) for c in cols)
    has_num = any(is_num(first.get(c)) for c in cols if not is_date_col(c))
    if has_date and has_num and nrows >= 2:
        return "trend"

    if ncols == 2 and 2 <= nrows <= 30:
        if sum(1 for c in cols if is_num(first.get(c))) == 1:
            return "distribution"

    if ncols >= 3 and all(not is_num(first.get(cols[i])) for i in range(2)):
        return "pivot"

    return "table"


def _is_numeric(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _summarize_sql_result(rows: List[Dict[str, Any]], col_names: List[str]) -> str:
    """SQL 결과를 LLM 이 인용 가능한 형태로 직렬화.

    - ≤20행: 전체 JSON 그대로 (LLM 이 숫자 직접 인용)
    - >20행: 상위 10행 + 통계 메타 (수치 컬럼 sum/min/max/avg)
    """
    import json
    n = len(rows)
    col_info = ", ".join(col_names)
    if n == 0:
        return "조건에 맞는 데이터가 없습니다."

    if n <= 20:
        body = json.dumps(rows, ensure_ascii=False, default=str)
        return (
            f"WADS SQL 결과: 총 {n}건, 컬럼=[{col_info}]\n"
            f"전체 데이터:\n{body}"
        )

    # 큰 결과: 상위 10 + 통계 메타
    head = rows[:10]
    head_body = json.dumps(head, ensure_ascii=False, default=str)

    # 수치 컬럼별 통계
    stats_lines = []
    for c in col_names:
        nums = [r.get(c) for r in rows if _is_numeric(r.get(c))]
        if not nums:
            continue
        stats_lines.append(
            f"  · {c}: n={len(nums)}, sum={sum(nums):,}, "
            f"min={min(nums):,}, max={max(nums):,}, avg={sum(nums)/len(nums):.2f}"
        )
    stats_block = "\n".join(stats_lines) if stats_lines else "  (수치 컬럼 없음)"

    return (
        f"WADS SQL 결과: 총 {n}건, 컬럼=[{col_info}]\n"
        f"상위 10건:\n{head_body}\n"
        f"수치 컬럼 통계:\n{stats_block}"
    )


# ── DF_WADS_WF_LIST 조회 ─────────────────────────────────────


@observe(name="wads_query_wf_list_oracle")
def _query_wf_list_data(
    lot_cd: Optional[str] = None,
    category: Optional[str] = None,
    parameter: Optional[str] = None,
    start_tm: Optional[str] = None,
    end_tm: Optional[str] = None,
    columns: str = "LOT_CD, OPER_PARA, GROUPKEY, END_TM",
) -> pd.DataFrame:
    """DF_WADS_WF_LIST 조회. category + parameter 는 OPER_PARA LIKE 합성."""
    logger.info(
        "[_query_wf_list_data] 조회: lot_cd=%s, category=%s, parameter=%s, start=%s, end=%s",
        lot_cd, category, parameter, start_tm, end_tm,
    )
    conditions = []
    bind_vars = {}

    if lot_cd:
        conditions.append("UPPER(LOT_CD) LIKE UPPER(:lot_cd)")
        bind_vars["lot_cd"] = f"%{lot_cd}%"
    # OPER_PARA 는 "{CATEGORY}_{PARAMETER}" 결합 — 두 인자를 합쳐서 LIKE
    if category and parameter:
        conditions.append("UPPER(OPER_PARA) LIKE UPPER(:oper_para)")
        bind_vars["oper_para"] = f"%{category}_{parameter}%"
    elif category:
        conditions.append("UPPER(OPER_PARA) LIKE UPPER(:oper_para)")
        bind_vars["oper_para"] = f"%{category}_%"
    elif parameter:
        conditions.append("UPPER(OPER_PARA) LIKE UPPER(:oper_para)")
        bind_vars["oper_para"] = f"%_{parameter}%"

    if start_tm and end_tm:
        conditions.append(
            f"TRUNC({_END_TM_DATE_EXPR}) "
            "BETWEEN TO_DATE(:start_tm, 'YY/MM/DD') AND TO_DATE(:end_tm_range, 'YY/MM/DD')"
        )
        bind_vars["start_tm"] = _normalize_date_input(start_tm)
        bind_vars["end_tm_range"] = _normalize_date_input(end_tm)
    elif end_tm:
        conditions.append("END_TM LIKE :end_tm")
        bind_vars["end_tm"] = f"%{_normalize_date_input(end_tm)}%"

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    sql = f"SELECT {columns} FROM {_WF_LIST_TABLE} WHERE {where_clause}"
    logger.info("[_query_wf_list_data] SQL: %s | bind: %s", sql, bind_vars)

    conn = _get_oracle_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, bind_vars)
        col_names = [desc[0].lower() for desc in cursor.description]
        rows = cursor.fetchall()
        cursor.close()
        df = pd.DataFrame(rows, columns=col_names)
        logger.info("[_query_wf_list_data] 완료: %d rows", len(df))
        return df
    finally:
        conn.close()


@tool
def wads_query_wf_list(
    lotcd: Optional[str] = None,
    category: Optional[str] = None,
    parameter: Optional[str] = None,
    start_tm: Optional[str] = None,
    end_tm: Optional[str] = None,
) -> str:
    """DF_WADS_WF_LIST(검출된 wafer 목록)를 조회합니다.

    DF_WADS_REPORT 는 리포트 단위(lotcd×category×parameter×end_tm)인 반면
    이 테이블은 그 리포트에 포함된 **개별 wafer(GROUPKEY)** 단위입니다.
    "검출된 wafer 몇 개", "어떤 GROUPKEY" 류 질의에 사용하세요.

    Args:
        lotcd: 로트코드 필터 (DF_WADS_WF_LIST.LOT_CD, 부분 일치). 미입력시 전체.
        category: 테스트 카테고리 ("PT1H_TEST" 또는 "PT1C_TEST"). OPER_PARA 합성에 사용.
        parameter: fail_type 필터 (예: "EASY", "TWT"). OPER_PARA 합성에 사용.
        start_tm: 시작 날짜 (YYYY-MM-DD 또는 YY/MM/DD). end_tm 과 함께 범위.
        end_tm: 종료 날짜.

    Returns:
        조회 결과 요약 (총 wafer 수 + LOT_CD/OPER_PARA 분포 + 대표 GROUPKEY 샘플).
    """
    storage = _get_tool_payload()
    defaults = storage.get("_defaults", {})
    lotcd = lotcd or defaults.get("lotcd")
    category = category or defaults.get("category")
    parameter = parameter or defaults.get("parameter")
    start_tm = start_tm or defaults.get("start_tm")
    end_tm = end_tm or defaults.get("end_tm")
    logger.info(
        "[wads_query_wf_list] 호출: lotcd=%s, category=%s, parameter=%s, start_tm=%s, end_tm=%s",
        lotcd, category, parameter, start_tm, end_tm,
    )

    try:
        df = _query_wf_list_data(
            lot_cd=lotcd,
            category=category,
            parameter=parameter,
            start_tm=start_tm,
            end_tm=end_tm,
        )
    except Exception as e:
        logger.error("[wads_query_wf_list] Oracle 오류: %s", e, exc_info=True)
        storage["wf_list"] = [{"error": "DBError", "detail": f"Oracle 연결/조회 오류: {e}"}]
        return f"오류: Oracle 연결/조회에 실패했습니다. ({e})"

    if df.empty:
        logger.info("[wads_query_wf_list] 결과 없음")
        storage["wf_list"] = [{"error": "NoMatch", "detail": "조건에 맞는 wafer 검출 기록이 없습니다."}]
        return "조건에 맞는 wafer 검출 기록이 없습니다."

    result = df.to_dict(orient="records")
    storage["wf_list"] = result
    n = len(result)

    lines = [f"WADS 검출 wafer 조회 완료: 총 {n}개 (DF_WADS_WF_LIST)"]
    if start_tm and end_tm:
        lines.append(f"- 기간: {start_tm} ~ {end_tm}")
    lines.append(f"- LOT_CD 분포: {_top_counts([r.get('lot_cd', '') for r in result])}")
    lines.append(f"- OPER_PARA 분포: {_top_counts([r.get('oper_para', '') for r in result])}")
    sample_keys = [r.get("groupkey") for r in result[:5] if r.get("groupkey")]
    if sample_keys:
        lines.append(f"- 대표 GROUPKEY 5개: {', '.join(map(str, sample_keys))}")
    return "\n".join(lines)


# ── 외부 데이터: Wiki 컨텍스트 lookup ────────────────────────


@tool
@observe(name="wads_lookup_param_context")
def wads_lookup_param_context(
    parameter: str,
    category: Optional[str] = None,
) -> str:
    """PARAMETER(=fail_type) 또는 CATEGORY 의 의미·정의·과거 누적 본문을 wiki 에서 lookup.

    "EASY가 뭐야?", "PT1H_TEST 와 PT1C_TEST 차이?", "왜 늘었지?" 류 해석성 질의에
    한해 1회 호출. 본문 보강용 — artifact 발사 안 함.

    Args:
        parameter: fail_type (예: "EASY", "TWT").
        category: 테스트 카테고리 (선택, 예: "PT1H_TEST").

    Returns:
        wiki 본문(있으면) + alias / 최근 episode 메타 요약. 없으면 빈 결과 안내.
    """
    try:
        import wiki_store
    except Exception as e:
        logger.warning("[wads_lookup_param_context] wiki_store import 실패: %s", e)
        return f"wiki 모듈을 사용할 수 없습니다: {e}"

    lines: list[str] = []

    # 1) super_concept 노드 (가장 정제된 정의)
    try:
        sc = wiki_store.lookup_super_concept(axis="fail_type", axis_value=parameter)
        if sc and (sc.get("body") or "").strip():
            body = sc["body"].strip()
            lines.append(f"[wiki super_concept fail_type={parameter}]")
            lines.append(body[:1200])
    except Exception as e:
        logger.warning("[wads_lookup_param_context] super_concept lookup 실패: %s", e)

    # 2) 일반 lookup — alias / recent episodes
    try:
        query = f"{category} {parameter}".strip() if category else parameter
        res = wiki_store.lookup(query=query, filters={}, max_episodes=3)
        if res.get("concepts"):
            lines.append("[관련 concept]")
            for c in res["concepts"]:
                summary = c.get("summary_1line", "")
                seen = c.get("seen_count", 0)
                lines.append(f"- {c.get('id')} (seen={seen}): {summary}")
        if res.get("aliases"):
            ali = ", ".join(f"{a.get('variant')}→{a.get('canonical')}" for a in res["aliases"][:5])
            lines.append(f"[alias] {ali}")
        if res.get("recent_episodes"):
            lines.append("[최근 episode]")
            for e in res["recent_episodes"][:3]:
                q = e.get("query", "")[:60]
                lines.append(f"- {e.get('id')}: {q}")
    except Exception as e:
        logger.warning("[wads_lookup_param_context] lookup 실패: %s", e)

    if not lines:
        return f"wiki 에 '{parameter}' 관련 정보가 없습니다. (외부 컨텍스트 보강 없이 진행하세요)"
    return "\n".join(lines)


# ── WADS 전용 도구 리스트 ─────────────────────────────────────
WADS_TOOLS = [
    wads_query_data,
    wads_get_html_report,
    wads_query_sql,
    wads_query_wf_list,
    wads_lookup_param_context,
]

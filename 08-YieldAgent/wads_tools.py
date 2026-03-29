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
_ORACLE_TABLE = os.getenv("WADS_TABLE", "WADS_TABLE")
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


# ── Oracle 조회 ───────────────────────────────────────────────
@observe(name="wads_query_oracle")
def _query_wads_data(
    lotcd: Optional[str] = None,
    end_tm: Optional[str] = None,
    start_tm: Optional[str] = None,
    parameter: Optional[str] = None,
    columns: str = "*",
) -> pd.DataFrame:
    """Oracle에서 WADS 데이터 조회 (SQL WHERE + LIKE 필터링)"""
    logger.info("[_query_wads_data] 조회 시작: lotcd=%s, end_tm=%s, start_tm=%s, parameter=%s, columns=%s",
                lotcd, end_tm, start_tm, parameter, columns)
    conditions = []
    bind_vars = {}

    if lotcd:
        conditions.append("UPPER(LOTCD) LIKE UPPER(:lotcd)")
        bind_vars["lotcd"] = f"%{lotcd}%"
    if start_tm and end_tm:
        conditions.append(
            "TRUNC(TO_DATE(SUBSTR(END_TM, 1, 10), 'YYYY-MM-DD')) "
            "BETWEEN TO_DATE(:start_tm, 'YYYY-MM-DD') AND TO_DATE(:end_tm_range, 'YYYY-MM-DD')"
        )
        bind_vars["start_tm"] = start_tm
        bind_vars["end_tm_range"] = end_tm
    elif end_tm:
        conditions.append("END_TM LIKE :end_tm")
        bind_vars["end_tm"] = f"%{end_tm}%"
    if parameter:
        conditions.append("UPPER(CTN_DESC) LIKE UPPER(:parameter)")
        bind_vars["parameter"] = f"%{parameter}%"

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    sql = f"SELECT {columns} FROM {_ORACLE_TABLE} WHERE {where_clause}"
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
) -> str:
    """
    WADS 데이터를 조회합니다. 선택적 필터를 적용하여 매칭되는 데이터의 메타정보를 반환합니다.

    Args:
        lotcd: 랏코드 필터 (예: "5NA", "4SA"). 부분 일치 검색. 미입력시 전체 조회.
        end_tm: 종료시간 필터 (예: "2026-01-01", "18:07"). 부분 일치 검색. 미입력시 전체 조회.
        start_tm: 시작 날짜 필터 (예: "2026-03-19"). end_tm과 함께 사용하면 날짜 범위 조회. 미입력시 end_tm 단일 필터.
        parameter: 스텝 설명 필터 (예: "step01", "step02"). 부분 일치 검색. 미입력시 전체 조회.

    Returns:
        조회 결과 요약 메시지 (실제 데이터는 별도 저장됨)
    """
    logger.info("[wads_query_data] 호출: lotcd=%s, end_tm=%s, start_tm=%s, parameter=%s", lotcd, end_tm, start_tm, parameter)
    storage = _get_tool_payload()

    try:
        filtered_df = _query_wads_data(
            lotcd=lotcd,
            end_tm=end_tm,
            start_tm=start_tm,
            parameter=parameter,
            columns="LOTCD, END_TM, CTN_DESC AS PARAMETER",
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

    result = filtered_df[["lotcd", "end_tm", "parameter"]].to_dict(orient="records")
    storage["query"] = result
    logger.info("[wads_query_data] 완료: %d건", len(result))

    return f"WADS 데이터 조회 완료: 총 {len(result)}건의 데이터가 조회되었습니다. (데이터는 화면에 별도 표시됩니다)"


@tool
def wads_get_html_report(
    lotcd: Optional[str] = None,
    end_tm: Optional[str] = None,
    start_tm: Optional[str] = None,
    parameter: Optional[str] = None,
) -> str:
    """
    WADS HTML 리포트를 조회합니다. 선택적 필터를 적용하여 매칭되는 데이터의 HTML 콘텐츠를 반환합니다.
    여러 번 호출하면 모든 리포트가 누적되어 표시됩니다.

    Args:
        lotcd: 로트코드 필터 (예: "5NA", "4SA"). 부분 일치 검색. 미입력시 전체 조회.
        end_tm: 종료시간 필터 (예: "2026-01-01", "18:07"). 부분 일치 검색. 미입력시 전체 조회.
        start_tm: 시작 날짜 필터 (예: "2026-03-19"). end_tm과 함께 사용하면 날짜 범위 조회. 미입력시 end_tm 단일 필터.
        parameter: 스텝 설명 필터 (예: "step01", "step02"). 부분 일치 검색. 미입력시 전체 조회.

    Returns:
        조회 결과 요약 메시지 (실제 HTML은 별도 저장됨)
    """
    logger.info("[wads_get_html_report] 호출: lotcd=%s, end_tm=%s, start_tm=%s, parameter=%s", lotcd, end_tm, start_tm, parameter)
    storage = _get_tool_payload()

    try:
        filtered_df = _query_wads_data(
            lotcd=lotcd,
            end_tm=end_tm,
            start_tm=start_tm,
            parameter=parameter,
            columns="LOTCD, END_TM, CTN_DESC AS PARAMETER, HTML",
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
                "end_tm": row["end_tm"],
                "parameter": row["parameter"],
                "html": row["html"],
            }
        )

    count = len(filtered_df)
    summary = ", ".join(
        f"lotcd={r['lotcd']} parameter={r['parameter']}"
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


def _validate_sql(sql: str) -> Tuple[bool, str]:
    """
    4계층 SQL 검증. (ok, reason) 반환.
    계층1: 주석 제거, 계층2: SELECT 확인, 계층3: 테이블 허용목록, 금지 키워드
    """
    cleaned = _strip_sql_comments(sql).strip()
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

    # 계층3: 테이블 허용목록
    table_refs = re.findall(r"\bFROM\s+(\w+)", cleaned, re.IGNORECASE)
    table_refs += re.findall(r"\bJOIN\s+(\w+)", cleaned, re.IGNORECASE)
    allowed = _ORACLE_TABLE.upper()
    for tbl in table_refs:
        if tbl.upper() != allowed:
            return False, f"허용되지 않은 테이블: {tbl} (허용: {_ORACLE_TABLE})"

    return True, ""


def _ensure_row_limit(sql: str, limit: int = 500) -> str:
    """FETCH FIRST / ROWNUM 행 제한이 없으면 자동 추가"""
    upper = sql.upper()
    if "FETCH FIRST" in upper or "ROWNUM" in upper:
        return sql
    return f"{sql.rstrip().rstrip(';')} FETCH FIRST {limit} ROWS ONLY"


# ── SQL 생성 프롬프트 (D-4: 도구 내부 캡슐화) ────────────────
_SQL_GEN_PROMPT = """You are an Oracle SQL generator for the {table_name} table.

Schema:
  LOTCD     VARCHAR2  -- lot code (e.g., "4SS", "5NA")
  END_TM    VARCHAR2  -- end time "YYYY-MM-DD HH:MM:SS"
  CTN_DESC  VARCHAR2  -- step description (e.g., "step01", "step02")
  HTML      CLOB      -- report HTML (do NOT select unless explicitly requested)

Rules:
- SELECT statements ONLY
- Do NOT include HTML column unless explicitly requested
- Use UPPER() for case-insensitive LOTCD, CTN_DESC comparisons
- Date filtering: TRUNC(TO_DATE(SUBSTR(END_TM,1,10),'YYYY-MM-DD'))
- Always add FETCH FIRST 500 ROWS ONLY
- Output ONLY the SQL statement, no explanation

Request: {query_description}"""


@tool
@observe(name="wads_query_sql")
def wads_query_sql(query_description: str) -> str:
    """WADS 데이터에 대한 복잡한 SQL 쿼리를 생성하고 실행합니다.
    wads_query_data/wads_get_html_report로 처리할 수 없는 복잡한 조건에만 사용하세요.
    이 도구는 내부 LLM 호출이 추가되어 다른 도구보다 느립니다.

    Args:
        query_description: 조회 내용을 자연어로 설명
            (예: "4SS 로트의 3월 step01, step02 건수를 step별 집계")
            (예: "step03 제외한 전체 스텝 목록")
            (예: "로트별 HIGH severity 건수 비교")

    Returns:
        조회 결과 요약 (건수 + 컬럼 정보). 실제 데이터는 화면에 별도 표시됩니다.
    """
    storage = _get_tool_payload()

    # 1. LLM으로 SQL 생성
    try:
        llm = get_llm(model=_SQL_GEN_MODEL)
        prompt = _SQL_GEN_PROMPT.format(
            table_name=_ORACLE_TABLE,
            query_description=query_description,
        )
        response = llm.invoke(prompt)
        raw_sql = response.content.strip()
        # 코드 블록 마커 제거
        if raw_sql.startswith("```"):
            raw_sql = re.sub(r"^```\w*\n?", "", raw_sql)
            raw_sql = re.sub(r"\n?```$", "", raw_sql)
        raw_sql = raw_sql.strip()
    except Exception as e:
        logger.error("[wads_query_sql] SQL 생성 실패: %s", e)
        return f"SQL 생성 실패: {e}. wads_query_data 도구를 대신 사용하세요."

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
            return f"컬럼/테이블 오류: {e}. 사용 가능 컬럼: LOTCD, END_TM, CTN_DESC, HTML"
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

    # HTML 컬럼 포함 여부에 따라 reports 또는 sql_result에 저장
    if "html" in col_names:
        for r in result:
            storage.setdefault("reports", []).append({
                "lotcd": r.get("lotcd", ""),
                "end_tm": r.get("end_tm", ""),
                "parameter": r.get("ctn_desc", ""),
                "html": r.get("html", ""),
            })
        return f"WADS SQL 리포트 조회 완료: 총 {len(result)}건 (리포트는 화면에 별도 표시됩니다)"
    else:
        storage["sql_result"] = result
        col_info = ", ".join(col_names)
        return f"WADS SQL 쿼리 실행 완료: 총 {len(result)}건 조회됨. 컬럼: {col_info} (데이터는 화면에 별도 표시됩니다)"


# ── WADS 전용 도구 리스트 ─────────────────────────────────────
WADS_TOOLS = [wads_query_data, wads_get_html_report, wads_query_sql]

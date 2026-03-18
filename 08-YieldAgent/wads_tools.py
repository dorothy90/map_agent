"""
WADS @tool 함수 모듈
====================
WADS 데이터 조회/리포트 도구 함수와 ContextVar 격리 로직.
wads_agent.py에서 분리.
"""
from __future__ import annotations

import contextvars
from typing import Any, Dict, Optional

import oracledb
import pandas as pd
from langchain_core.tools import tool
from langfuse import observe

from common import get_oracle_connection as _get_oracle_connection

import os
from dotenv import load_dotenv

load_dotenv(override=True)
_ORACLE_TABLE = os.getenv("WADS_TABLE", "WADS_TABLE")


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
    parameter: Optional[str] = None,
    columns: str = "*",
) -> pd.DataFrame:
    """Oracle에서 WADS 데이터 조회 (SQL WHERE + LIKE 필터링)"""
    conditions = []
    bind_vars = {}

    if lotcd:
        conditions.append("UPPER(LOTCD) LIKE UPPER(:lotcd)")
        bind_vars["lotcd"] = f"%{lotcd}%"
    if end_tm:
        conditions.append("END_TM LIKE :end_tm")
        bind_vars["end_tm"] = f"%{end_tm}%"
    if parameter:
        conditions.append("UPPER(CTN_DESC) LIKE UPPER(:parameter)")
        bind_vars["parameter"] = f"%{parameter}%"

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    sql = f"SELECT {columns} FROM {_ORACLE_TABLE} WHERE {where_clause}"

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
        return df
    finally:
        conn.close()


# ── @tool 함수 ────────────────────────────────────────────────
@tool
def wads_query_data(
    lotcd: Optional[str] = None,
    end_tm: Optional[str] = None,
    parameter: Optional[str] = None,
) -> str:
    """
    WADS 데이터를 조회합니다. 선택적 필터를 적용하여 매칭되는 데이터의 메타정보를 반환합니다.

    Args:
        lotcd: 랏코드 필터 (예: "5NA", "4SA"). 부분 일치 검색. 미입력시 전체 조회.
        end_tm: 종료시간 필터 (예: "2026-01-01", "18:07"). 부분 일치 검색. 미입력시 전체 조회.
        parameter: 스텝 설명 필터 (예: "step01", "step02"). 부분 일치 검색. 미입력시 전체 조회.

    Returns:
        조회 결과 요약 메시지 (실제 데이터는 별도 저장됨)
    """
    storage = _get_tool_payload()

    try:
        filtered_df = _query_wads_data(
            lotcd=lotcd,
            end_tm=end_tm,
            parameter=parameter,
            columns="LOTCD, END_TM, CTN_DESC AS PARAMETER",
        )
    except Exception as e:
        storage["query"] = [
            {
                "error": "DBError",
                "detail": f"Oracle 연결/조회 오류: {e}",
            }
        ]
        return f"오류: Oracle 연결/조회에 실패했습니다. ({e})"

    if filtered_df.empty:
        storage["query"] = [
            {"error": "NoMatch", "detail": "조건에 맞는 WADS 데이터가 없습니다."}
        ]
        return "조건에 맞는 WADS 데이터가 없습니다."

    result = filtered_df[["lotcd", "end_tm", "parameter"]].to_dict(orient="records")
    storage["query"] = result

    return f"WADS 데이터 조회 완료: 총 {len(result)}건의 데이터가 조회되었습니다. (데이터는 화면에 별도 표시됩니다)"


@tool
def wads_get_html_report(
    lotcd: Optional[str] = None,
    end_tm: Optional[str] = None,
    parameter: Optional[str] = None,
    limit: int = 1,
) -> str:
    """
    WADS HTML 리포트를 조회합니다. 선택적 필터를 적용하여 매칭되는 데이터의 HTML 콘텐츠를 반환합니다.
    여러 번 호출하면 모든 리포트가 누적되어 표시됩니다.

    Args:
        lotcd: 로트코드 필터 (예: "5NA", "4SA"). 부분 일치 검색. 미입력시 전체 조회.
        end_tm: 종료시간 필터 (예: "2026-01-01", "18:07"). 부분 일치 검색. 미입력시 전체 조회.
        parameter: 스텝 설명 필터 (예: "step01", "step02"). 부분 일치 검색. 미입력시 전체 조회.
        limit: 하위호환을 위해 받지만, 현재는 무시됩니다. (항상 1개만 반환)

    Returns:
        조회 결과 요약 메시지 (실제 HTML은 별도 저장됨)
    """
    storage = _get_tool_payload()

    try:
        filtered_df = _query_wads_data(
            lotcd=lotcd,
            end_tm=end_tm,
            parameter=parameter,
            columns="LOTCD, END_TM, CTN_DESC AS PARAMETER, HTML",
        )
    except Exception as e:
        return f"오류: Oracle 연결/조회에 실패했습니다. ({e})"

    if filtered_df.empty:
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


# ── WADS 전용 도구 리스트 ─────────────────────────────────────
WADS_TOOLS = [wads_query_data, wads_get_html_report]

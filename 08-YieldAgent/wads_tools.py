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
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

import oracledb
import pandas as pd
from langchain_core.tools import tool
from langfuse import observe

from common import get_oracle_connection as _get_oracle_connection, get_llm
from local_trace import emit_runtime_detail

import os
from dotenv import load_dotenv

load_dotenv(override=True)
_ORACLE_REPORT_TABLE = "DF_WADS_REPORT"
_ORACLE_WF_TABLE = os.getenv("WADS_WF_LIST_TABLE", "DF_WADS_WF_LIST")
_ORACLE_WF_GROUPKEY_COLUMN = os.getenv("WADS_WF_GROUPKEY_COLUMN", "GROUP_KEY")
_ORACLE_TABLE = _ORACLE_REPORT_TABLE
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


def _tool_task_id() -> str:
    storage = _get_tool_payload()
    defaults = storage.get("_defaults", {}) if isinstance(storage, dict) else {}
    return str(defaults.get("task_id") or "")


def _sample_rows(df: pd.DataFrame, *, limit: int = 3) -> list[dict[str, Any]]:
    if df.empty:
        return []
    return df.head(limit).to_dict(orient="records")


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _clean_text(value: Any) -> str:
    if _is_missing(value):
        return ""
    return str(value).strip()


def _compact_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", str(sql or "")).strip()


def _log_wads_sql(
    context: str,
    sql: str,
    bind_vars: dict[str, Any] | None = None,
    **metadata: Any,
) -> None:
    compact_sql = _compact_sql(sql)
    binds = dict(bind_vars or {})
    logger.warning("[%s] SQL: %s", context, compact_sql)
    logger.warning("[%s] SQL binds: %s", context, binds)
    emit_runtime_detail(
        "wads.sql",
        {
            "context": context,
            "sql": compact_sql,
            "binds": binds,
            "bind_keys": sorted(binds),
            **metadata,
        },
        task_id=_tool_task_id(),
    )


def _end_tm_expr(alias: str = "r") -> str:
    return f"TO_CHAR({alias}.END_TM, 'YYYY-MM-DD HH24:MI:SS') AS END_TM"


_END_TM_DATE_JOIN_EXPR = "TRUNC(CAST(w.END_TM AS DATE)) = TRUNC(CAST(r.END_TM AS DATE))"


def _oracle_identifier(name: str, *, fallback: str) -> str:
    text = str(name or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", text):
        logger.warning(
            "Invalid Oracle identifier %r; falling back to %s", name, fallback
        )
        return fallback
    return text


def _wf_groupkey_column() -> str:
    return _oracle_identifier(_ORACLE_WF_GROUPKEY_COLUMN, fallback="GROUP_KEY")


def _wf_groupkey_expr(alias: str = "w") -> str:
    return f"{alias}.{_wf_groupkey_column()} AS GROUP_KEY"


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if hasattr(value, "read"):
        return value.read()
    return value


def _split_groupkey(groupkey: Any) -> tuple[str, str]:
    text = _clean_text(groupkey)
    if not text:
        return "", ""
    if "." not in text:
        return text, ""
    lotid, wf_id = text.rsplit(".", 1)
    return lotid.strip(), wf_id.strip()


def _append_unique(values: list[str], value: Any) -> None:
    text = _clean_text(value)
    if text and text not in values:
        values.append(text)


def _row_groupkey(row: Any) -> str:
    if isinstance(row, pd.Series):
        getter = row.get
    elif isinstance(row, dict):
        getter = row.get
    else:
        return ""
    return _clean_text(getter("groupkey") or getter("group_key"))


def _report_key(row: Any) -> tuple[str, str, str, str]:
    if isinstance(row, pd.Series):
        getter = row.get
    elif isinstance(row, dict):
        getter = row.get
    else:
        return ("", "", "", "")
    return (
        _clean_text(getter("lotcd")),
        _clean_text(getter("category")),
        _clean_text(getter("parameter")),
        _clean_text(getter("end_tm")),
    )


def _category_to_map_oper(category: Any) -> str:
    text = str(category or "").upper()
    if "PT1C" in text:
        return "PT1C"
    if "PT1H" in text:
        return "PT1H"
    return ""


def _wads_join_coverage(df: pd.DataFrame) -> dict[str, Any]:
    rows = df.to_dict(orient="records") if not df.empty else []
    has_groupkey_column = "groupkey" in df.columns or "group_key" in df.columns
    report_keys: list[str] = []
    groupkeys: list[str] = []
    lot_ids: list[str] = []
    wf_ids: list[str] = []
    oper_para_samples: list[str] = []
    groupkey_rows = 0

    for row in rows:
        key_tuple = _report_key(row)
        key = "|".join(key_tuple)
        if key.strip("|"):
            _append_unique(report_keys, key)

        category = _clean_text(row.get("category"))
        parameter = _clean_text(row.get("parameter"))
        if category or parameter:
            _append_unique(oper_para_samples, f"{category}_{parameter}".strip("_"))

        groupkey = _row_groupkey(row)
        if groupkey:
            groupkey_rows += 1
            _append_unique(groupkeys, groupkey)
            lotid, wf_id = _split_groupkey(groupkey)
            _append_unique(lot_ids, lotid)
            _append_unique(wf_ids, wf_id)

    joined_rows = len(rows)
    report_rows = len(report_keys) if report_keys else joined_rows
    missing_groupkey_rows = (
        max(joined_rows - groupkey_rows, 0) if has_groupkey_column else 0
    )
    join_missing = bool(has_groupkey_column and report_rows > 0 and len(groupkeys) == 0)

    return {
        "joined_rows": joined_rows,
        "report_rows": report_rows,
        "wafer_rows": groupkey_rows,
        "unique_groupkeys": len(groupkeys),
        "unique_lot_ids": len(lot_ids),
        "unique_wf_ids": len(wf_ids),
        "missing_groupkey_rows": missing_groupkey_rows,
        "has_groupkey_column": has_groupkey_column,
        "join_missing": join_missing,
        "sample_report_keys": report_keys[:5],
        "sample_groupkeys": groupkeys[:5],
        "sample_oper_para": oper_para_samples[:5],
    }


def _log_join_coverage(context: str, stats: dict[str, Any]) -> None:
    logger.info(
        "[%s] join coverage: report_rows=%s joined_rows=%s wafer_rows=%s "
        "unique_groupkeys=%s missing_groupkey_rows=%s",
        context,
        stats.get("report_rows"),
        stats.get("joined_rows"),
        stats.get("wafer_rows"),
        stats.get("unique_groupkeys"),
        stats.get("missing_groupkey_rows"),
    )
    if stats.get("join_missing"):
        logger.warning(
            "[%s] report rows matched but no wafer GROUPKEY rows. "
            "Check %s join keys LOT_CD, OPER_PARA, END_TM date. "
            "sample_report_keys=%s sample_oper_para=%s",
            context,
            _ORACLE_WF_TABLE,
            stats.get("sample_report_keys"),
            stats.get("sample_oper_para"),
        )


def _wafer_groups_for_reports(
    *,
    lotcd: Optional[str],
    end_tm: Optional[str],
    start_tm: Optional[str],
    parameter: Optional[str],
    category: Optional[str] = None,
) -> dict[tuple[str, str, str, str], dict[str, list[str]]]:
    try:
        wafer_df = _query_wads_data(
            lotcd=lotcd,
            end_tm=end_tm,
            start_tm=start_tm,
            parameter=parameter,
            category=category,
            columns=(
                f"r.LOTCD, r.CATEGORY, r.PARAMETER, {_end_tm_expr('r')}, {_wf_groupkey_expr()}"
            ),
            join_wafers=True,
        )
    except Exception as exc:
        logger.warning("[wads_get_html_report] wafer groupkey 조회 실패: %s", exc)
        return {}

    grouped: dict[tuple[str, str, str, str], dict[str, list[str]]] = {}
    for row in wafer_df.to_dict(orient="records"):
        key = _report_key(row)
        bucket = grouped.setdefault(key, {"groupkeys": [], "lot_ids": [], "wf_ids": []})
        groupkey = _row_groupkey(row)
        _append_unique(bucket["groupkeys"], groupkey)
        lotid, wf_id = _split_groupkey(groupkey)
        _append_unique(bucket["lot_ids"], lotid)
        _append_unique(bucket["wf_ids"], wf_id)
    return grouped


# ── Oracle 조회 ───────────────────────────────────────────────
@observe(name="wads_query_oracle")
def _query_wads_data(
    lotcd: Optional[str] = None,
    end_tm: Optional[str] = None,
    start_tm: Optional[str] = None,
    parameter: Optional[str] = None,
    category: Optional[str] = None,
    columns: str = "*",
    join_wafers: bool = False,
) -> pd.DataFrame:
    """Oracle에서 WADS 데이터 조회 (report 기준 필터 + 필요 시 wafer list 조인)"""
    logger.debug(
        "[_query_wads_data] start filters lotcd=%s start=%s end=%s parameter=%s join_wafers=%s columns=%s",
        bool(lotcd),
        bool(start_tm),
        bool(end_tm),
        bool(parameter),
        join_wafers,
        columns,
    )
    conditions = []
    bind_vars = {}

    if lotcd:
        conditions.append("UPPER(r.LOTCD) LIKE UPPER(:lotcd)")
        bind_vars["lotcd"] = f"%{lotcd}%"
    if start_tm and end_tm:
        conditions.append(
            "TRUNC(CAST(r.END_TM AS DATE)) "
            "BETWEEN TO_DATE(:start_tm, 'YYYY-MM-DD') AND TO_DATE(:end_tm_range, 'YYYY-MM-DD')"
        )
        bind_vars["start_tm"] = start_tm
        bind_vars["end_tm_range"] = end_tm
    elif end_tm:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(end_tm).strip()):
            conditions.append(
                "TRUNC(CAST(r.END_TM AS DATE)) = TO_DATE(:end_tm_date, 'YYYY-MM-DD')"
            )
            bind_vars["end_tm_date"] = str(end_tm).strip()
        else:
            conditions.append("TO_CHAR(r.END_TM, 'YYYY-MM-DD HH24:MI:SS') LIKE :end_tm")
            bind_vars["end_tm"] = f"%{end_tm}%"
    if parameter:
        conditions.append("UPPER(r.PARAMETER) LIKE UPPER(:parameter)")
        bind_vars["parameter"] = f"%{parameter}%"
    if category:
        # "PT1H"/"PT1H_TEST"/"pt1h" 어떤 입력이든 "PT1H"/"PT1C"로 정규화 후 부분일치
        oper = _category_to_map_oper(category)
        if oper:
            conditions.append("UPPER(r.CATEGORY) LIKE :category")
            bind_vars["category"] = f"%{oper}%"

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    from_clause = f"FROM {_ORACLE_REPORT_TABLE} r"
    if join_wafers:
        from_clause += (
            f" LEFT JOIN {_ORACLE_WF_TABLE} w"
            " ON w.LOT_CD = r.LOTCD"
            " AND w.OPER_PARA = r.CATEGORY || '_' || r.PARAMETER"
            f" AND {_END_TM_DATE_JOIN_EXPR}"
        )
    sql = f"SELECT {columns} {from_clause} WHERE {where_clause} ORDER BY r.END_TM DESC"
    logger.debug(
        "[_query_wads_data] SQL prepared len=%d bind_keys=%s",
        len(sql),
        sorted(bind_vars),
    )
    _log_wads_sql(
        "_query_wads_data",
        sql,
        bind_vars,
        join_wafers=join_wafers,
        columns=columns,
        report_table=_ORACLE_REPORT_TABLE,
        wafer_table=_ORACLE_WF_TABLE if join_wafers else "",
    )

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

        safe_rows = [tuple(_json_safe_value(value) for value in row) for row in rows]
        df = pd.DataFrame(safe_rows, columns=col_names)
        logger.info(
            "[_query_wads_data] 조회 완료: %d rows, columns=%s",
            len(df),
            list(col_names),
        )
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
    WADS 데이터를 조회합니다. 선택적 필터를 적용하여 매칭되는 데이터의 메타정보를 반환합니다.

    Args:
        lotcd: 랏코드 필터 (예: "4SA", "4SS"). 부분 일치 검색. 미입력시 전체 조회.
        end_tm: 종료시간 필터 (예: "2026-01-01", "18:07"). 부분 일치 검색. 미입력시 전체 조회.
        start_tm: 시작 날짜 필터 (예: "2026-03-19"). end_tm과 함께 사용하면 날짜 범위 조회. 미입력시 end_tm 단일 필터.
        parameter: fail_type 필터 (예: "EASY", "EASY(W)", "TWT"). 부분 일치 검색. 미입력시 전체 조회.
        category: 공정 필터 ("PT1H" 또는 "PT1C"). CATEGORY(PT1H_TEST/PT1C_TEST)로 필터. 미입력시 전체 조회.

    Returns:
        조회 결과 요약 메시지 (실제 데이터는 별도 저장됨)
    """
    storage = _get_tool_payload()
    defaults = storage.get("_defaults", {})
    lotcd = lotcd or defaults.get("lotcd")
    end_tm = end_tm or defaults.get("end_tm")
    start_tm = start_tm or defaults.get("start_tm")
    parameter = parameter or defaults.get("parameter")
    category = category or defaults.get("category")
    logger.info(
        "[wads_query_data] 호출: lotcd=%s, end_tm=%s, start_tm=%s, parameter=%s, category=%s",
        lotcd,
        end_tm,
        start_tm,
        parameter,
        category,
    )

    try:
        filtered_df = _query_wads_data(
            lotcd=lotcd,
            end_tm=end_tm,
            start_tm=start_tm,
            parameter=parameter,
            category=category,
            columns=(
                f"r.LOTCD, r.CATEGORY, r.PARAMETER, {_end_tm_expr('r')}, {_wf_groupkey_expr()}"
            ),
            join_wafers=True,
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
        stats = _wads_join_coverage(filtered_df)
        storage["query_stats"] = stats
        logger.warning(
            "[wads_query_data] 결과 없음 (NoMatch): lotcd=%s, start_tm=%s, end_tm=%s, parameter=%s",
            lotcd,
            start_tm,
            end_tm,
            parameter,
        )
        emit_runtime_detail(
            "wads.query_data",
            {
                "row_count": 0,
                "filters": {
                    "lotcd": lotcd,
                    "start_tm": start_tm,
                    "end_tm": end_tm,
                    "parameter": parameter,
                },
                "columns": list(filtered_df.columns),
                "sample": [],
                "join_coverage": stats,
            },
            task_id=_tool_task_id(),
        )
        storage["query"] = [
            {"error": "NoMatch", "detail": "조건에 맞는 WADS 데이터가 없습니다."}
        ]
        return "조건에 맞는 WADS 데이터가 없습니다."

    stats = _wads_join_coverage(filtered_df)
    storage["query_stats"] = stats
    _log_join_coverage("wads_query_data", stats)

    result = []
    for row in filtered_df.to_dict(orient="records"):
        groupkey = _row_groupkey(row)
        lotid, wf_id = _split_groupkey(groupkey)
        result.append(
            {
                "lotid": lotid,
                "wf_id": wf_id,
                "groupkey": groupkey,
                "lotcd": _clean_text(row.get("lotcd")),
                "category": _clean_text(row.get("category")),
                "map_oper": _category_to_map_oper(row.get("category")),
                "parameter": _clean_text(row.get("parameter")),
                "end_tm": _clean_text(row.get("end_tm")),
                "has_groupkey": bool(groupkey),
            }
        )
    storage["query"] = result
    logger.info(
        "[wads_query_data] 완료: report_rows=%s joined_rows=%s unique_groupkeys=%s result_rows=%d",
        stats.get("report_rows"),
        stats.get("joined_rows"),
        stats.get("unique_groupkeys"),
        len(result),
    )
    emit_runtime_detail(
        "wads.query_data",
        {
            "row_count": len(result),
            "filters": {
                "lotcd": lotcd,
                "start_tm": start_tm,
                "end_tm": end_tm,
                "parameter": parameter,
            },
            "columns": list(filtered_df.columns),
            "sample": _sample_rows(filtered_df),
            "join_coverage": stats,
        },
        task_id=_tool_task_id(),
    )

    # LLM에게 실제 데이터(LOTID/GROUPKEY 포함)를 반환하여 lot/wafer list 질문에 답변 가능하게 함
    lines = [
        "WADS 데이터 조회 완료: "
        f"리포트 {stats.get('report_rows', len(result))}건 / "
        f"wafer GROUPKEY {stats.get('unique_groupkeys', 0)}건 "
        "(데이터는 화면에 별도 표시됩니다)"
    ]
    if stats.get("join_missing"):
        lines.append(
            "주의: WADS 리포트는 조회됐지만 연결된 wafer GROUPKEY가 없습니다. "
            f"{_ORACLE_WF_TABLE}의 LOT_CD, OPER_PARA, END_TM 날짜 조인 키를 확인하세요."
        )
    for r in result[:50]:
        lines.append(
            "- "
            f"GROUPKEY={r.get('groupkey')}, LOTID={r.get('lotid')}, WF_ID={r.get('wf_id')}, "
            f"LOTCD={r.get('lotcd')}, CATEGORY={r.get('category')}, "
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
    WADS HTML 리포트를 조회합니다. 선택적 필터를 적용하여 매칭되는 데이터의 HTML 콘텐츠를 반환합니다.
    여러 번 호출하면 모든 리포트가 누적되어 표시됩니다.

    Args:
        lotcd: 로트코드 필터 (예: "4SA", "4SS"). 부분 일치 검색. 미입력시 전체 조회.
        end_tm: 종료시간 필터 (예: "2026-01-01", "18:07"). 부분 일치 검색. 미입력시 전체 조회.
        start_tm: 시작 날짜 필터 (예: "2026-03-19"). end_tm과 함께 사용하면 날짜 범위 조회. 미입력시 end_tm 단일 필터.
        parameter: fail_type 필터 (예: "EASY", "EASY(W)", "TWT"). 부분 일치 검색. 미입력시 전체 조회.
        category: 공정 필터 ("PT1H" 또는 "PT1C"). CATEGORY(PT1H_TEST/PT1C_TEST)로 필터. 미입력시 전체 조회.

    Returns:
        조회 결과 요약 메시지 (실제 HTML은 별도 저장됨)
    """
    storage = _get_tool_payload()
    defaults = storage.get("_defaults", {})
    lotcd = lotcd or defaults.get("lotcd")
    end_tm = end_tm or defaults.get("end_tm")
    start_tm = start_tm or defaults.get("start_tm")
    parameter = parameter or defaults.get("parameter")
    category = category or defaults.get("category")
    logger.info(
        "[wads_get_html_report] 호출: lotcd=%s, end_tm=%s, start_tm=%s, parameter=%s, category=%s",
        lotcd,
        end_tm,
        start_tm,
        parameter,
        category,
    )

    try:
        filtered_df = _query_wads_data(
            lotcd=lotcd,
            end_tm=end_tm,
            start_tm=start_tm,
            parameter=parameter,
            category=category,
            columns=(f"r.LOTCD, r.CATEGORY, r.PARAMETER, {_end_tm_expr('r')}, r.HTML"),
        )
    except Exception as e:
        logger.error("[wads_get_html_report] Oracle 오류: %s", e, exc_info=True)
        return f"오류: Oracle 연결/조회에 실패했습니다. ({e})"

    if filtered_df.empty:
        stats = _wads_join_coverage(filtered_df)
        storage["report_stats"] = stats
        logger.warning(
            "[wads_get_html_report] 결과 없음: lotcd=%s, start_tm=%s, end_tm=%s, parameter=%s",
            lotcd,
            start_tm,
            end_tm,
            parameter,
        )
        emit_runtime_detail(
            "wads.report_data",
            {
                "row_count": 0,
                "filters": {
                    "lotcd": lotcd,
                    "start_tm": start_tm,
                    "end_tm": end_tm,
                    "parameter": parameter,
                },
                "columns": list(filtered_df.columns),
                "sample": [],
                "join_coverage": stats,
            },
            task_id=_tool_task_id(),
        )
        return "조건에 맞는 WADS 데이터가 없습니다."

    if "reports" not in storage:
        storage["reports"] = []

    wafer_groups = _wafer_groups_for_reports(
        lotcd=lotcd,
        end_tm=end_tm,
        start_tm=start_tm,
        parameter=parameter,
        category=category,
    )
    report_start_index = len(storage["reports"]) + 1
    for offset, (_, row) in enumerate(filtered_df.iterrows()):
        group_info = wafer_groups.get(_report_key(row), {})
        storage["reports"].append(
            {
                "report_index": report_start_index + offset,
                "lotcd": row["lotcd"],
                "category": row["category"],
                "end_tm": row["end_tm"],
                "parameter": row["parameter"],
                "map_oper": _category_to_map_oper(row["category"]),
                "groupkeys": group_info.get("groupkeys", []),
                "lot_ids": group_info.get("lot_ids", []),
                "wf_ids": group_info.get("wf_ids", []),
                "html": row["html"],
            }
        )

    count = len(filtered_df)
    report_groupkeys: list[str] = []
    reports_without_wafers = 0
    for report in storage["reports"][-count:]:
        groupkeys = report.get("groupkeys") or []
        report_groupkeys.extend(groupkeys)
        if not groupkeys:
            reports_without_wafers += 1
    stats = {
        "report_rows": count,
        "joined_rows": count,
        "wafer_rows": len(report_groupkeys),
        "unique_groupkeys": len(dict.fromkeys(report_groupkeys)),
        "missing_groupkey_rows": reports_without_wafers,
        "join_missing": bool(count and not report_groupkeys),
        "sample_report_keys": [
            "|".join(_report_key(report)) for report in storage["reports"][-count:]
        ][:5],
        "sample_groupkeys": list(dict.fromkeys(report_groupkeys))[:5],
    }
    storage["report_stats"] = stats
    _log_join_coverage("wads_get_html_report", stats)
    emit_runtime_detail(
        "wads.report_data",
        {
            "row_count": count,
            "filters": {
                "lotcd": lotcd,
                "start_tm": start_tm,
                "end_tm": end_tm,
                "parameter": parameter,
            },
            "columns": list(filtered_df.columns),
            "sample": _sample_rows(filtered_df),
            "join_coverage": stats,
        },
        task_id=_tool_task_id(),
    )
    summary = ", ".join(
        f"lotcd={r['lotcd']} category={r.get('category', '')} parameter={r['parameter']}"
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


def _normalize_end_tm_join(sql: str) -> str:
    """Generated WADS joins must match END_TM by date, not exact timestamp."""
    patterns = (
        r"\bw\.END_TM\s*=\s*r\.END_TM\b",
        r"\br\.END_TM\s*=\s*w\.END_TM\b",
    )
    normalized = sql
    for pattern in patterns:
        normalized = re.sub(
            pattern,
            _END_TM_DATE_JOIN_EXPR,
            normalized,
            flags=re.IGNORECASE,
        )
    return normalized


def _normalize_groupkey_column(sql: str) -> str:
    """Map common WADS wafer key spellings to the configured DB column."""
    return re.sub(
        r"\bw\.(?:GROUP_KEY|GROUPKEY)\b",
        f"w.{_wf_groupkey_column()}",
        sql,
        flags=re.IGNORECASE,
    )


def _normalize_cast_parentheses(sql: str) -> str:
    """Repair common Oracle CAST syntax drift from generated SQL."""

    return re.sub(
        r"\bCAST\s+([rw]\.END_TM)\s+AS\s+DATE\b",
        r"CAST(\1 AS DATE)",
        sql,
        flags=re.IGNORECASE,
    )


def _validate_sql(sql: str) -> Tuple[bool, str]:
    """
    4계층 SQL 검증. (ok, reason) 반환.
    계층1: 주석 제거, 계층2: SELECT 확인, 계층3: 테이블 허용목록, 금지 키워드
    """
    cleaned = _strip_sql_comments(sql).strip()
    # E3 fix: trailing 단일 세미콜론은 LLM의 흔한 습관이므로 자동 제거 (multi-statement 검사는 유지)
    cleaned = cleaned.rstrip(";").strip()
    if not cleaned:
        return False, "빈 SQL"

    # 세미콜론 금지 (multi-statement 방지) — trailing은 위에서 strip 했으므로 여기 ;는 multi-statement
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
    allowed = {_ORACLE_REPORT_TABLE.upper(), _ORACLE_WF_TABLE.upper()}
    for tbl in table_refs:
        if tbl.upper() not in allowed:
            return (
                False,
                f"허용되지 않은 테이블: {tbl} (허용: {', '.join(sorted(allowed))})",
            )

    return True, ""


def _ensure_row_limit(sql: str, limit: int = 500) -> str:
    """FETCH FIRST / ROWNUM 행 제한이 없으면 자동 추가"""
    upper = sql.upper()
    if "FETCH FIRST" in upper or "ROWNUM" in upper:
        return sql
    return f"{sql.rstrip().rstrip(';')} FETCH FIRST {limit} ROWS ONLY"


_COUNT_COLUMN_NAMES = {
    "count",
    "cnt",
    "detection_count",
    "detected_count",
    "report_count",
    "wafer_count",
}


def _numeric_sort_value(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _count_column_name(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    names: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            names.extend(str(key) for key in row.keys())
    for name in names:
        normalized = name.lower()
        if normalized in _COUNT_COLUMN_NAMES or normalized.endswith("_count"):
            if any(
                _numeric_sort_value(row.get(name)) is not None
                for row in rows
                if isinstance(row, dict)
            ):
                return name
    return ""


def _sort_direction_from_sql(sql: str, count_col: str) -> str:
    cleaned = _strip_sql_comments(sql)
    matches = list(
        re.finditer(
            r"\border\s+by\b(?P<clause>.*?)(?:\bfetch\s+first\b|\boffset\b|$)",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )
    if not matches:
        return ""

    clause = matches[-1].group("clause").strip()
    if not clause:
        return ""

    first_term = clause.split(",", 1)[0]
    count_col_pattern = rf"\b{re.escape(count_col)}\b"
    if not re.search(count_col_pattern, first_term, flags=re.IGNORECASE):
        if not re.search(r"\bcount\s*\(", first_term, flags=re.IGNORECASE):
            return ""

    if re.search(r"\bdesc\b", first_term, flags=re.IGNORECASE):
        return "desc"
    if re.search(r"\basc\b", first_term, flags=re.IGNORECASE):
        return "asc"
    return "asc"


def _sort_sql_result_rows(
    rows: list[dict[str, Any]],
    *,
    query_description: str,
    sql: str,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Preserve SQL result order and expose count sort metadata from ORDER BY."""

    # 정렬 의도는 SQL 생성 LLM이 ORDER BY로 결정한다. Python은 자연어를 재해석하지 않는다.
    _ = query_description
    count_col = _count_column_name(rows)
    if not count_col:
        return rows, {}
    direction = _sort_direction_from_sql(sql, count_col)
    if not direction:
        return rows, {}
    return rows, {"key": count_col, "direction": direction}


# ── SQL 생성 프롬프트 (D-4: 도구 내부 캡슐화) ────────────────
_SQL_GEN_PROMPT = """You are an Oracle SQL generator for WADS report tables.

Schema:
  {report_table} r
    LOTCD     VARCHAR2   -- product code (e.g., "4SA", "4SS")
    CATEGORY  VARCHAR2   -- "PT1H_TEST" or "PT1C_TEST"
    PARAMETER VARCHAR2   -- fail_type (e.g., "EASY(W)", "TWT(T)", "FMAX(X)")
    END_TM    TIMESTAMP  -- report end time
    HTML      CLOB       -- report HTML (do NOT select unless explicitly requested)

  {wf_table} w
    OPER_PARA VARCHAR2   -- r.CATEGORY || '_' || r.PARAMETER
    {wf_groupkey_column} VARCHAR2   -- lot.wf (e.g., "4SS2DPD.03")
    END_TM    TIMESTAMP  -- same YYYY-MM-DD date as r.END_TM
    LOT_CD    VARCHAR2   -- same value as r.LOTCD

Relationship:
  w.LOT_CD = r.LOTCD
  w.OPER_PARA = r.CATEGORY || '_' || r.PARAMETER
  TRUNC(CAST(w.END_TM AS DATE)) = TRUNC(CAST(r.END_TM AS DATE))

Rules:
- SELECT statements ONLY
- Do NOT include HTML column unless explicitly requested
- Use aliases r and w when joining
- Join END_TM by YYYY-MM-DD date only, not exact timestamp.
- When selecting the wafer identifier, use w.{wf_groupkey_column} AS GROUP_KEY so downstream code receives a GROUP_KEY column.
- For wafer/GROUP_KEY list queries, also select r.CATEGORY and r.PARAMETER so downstream map requests can resolve map_oper and parameter provenance.
- Use UPPER() for case-insensitive LOTCD, CATEGORY, PARAMETER comparisons
- Date filtering: TRUNC(CAST(r.END_TM AS DATE)) = TO_DATE('2026-03-19', 'YYYY-MM-DD') or BETWEEN two TO_DATE literals
- Use TO_CHAR(r.END_TM, 'YYYY-MM-DD HH24:MI:SS') when selecting END_TM for display
- For COUNT/ranking requests, include ORDER BY using the count alias and the requested direction.
  If the request asks for "많이", "상위", "top", "most", or similar, ORDER BY count DESC.
  If the request asks for "적게", "하위", "least", or similar, ORDER BY count ASC.
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
            (예: "4SS의 3월 EASY(W), TWT(T) 건수를 parameter별 집계")
            (예: "PT1H_TEST 리포트의 wafer groupkey 목록")
            (예: "4SA의 CATEGORY별 리포트 건수 비교")

    Returns:
        조회 결과 요약 (건수 + 컬럼 정보). 실제 데이터는 화면에 별도 표시됩니다.
    """
    storage = _get_tool_payload()

    # 1. LLM으로 SQL 생성
    try:
        llm = get_llm(model="z-ai/glm-5.1")
        prompt = _SQL_GEN_PROMPT.format(
            report_table=_ORACLE_REPORT_TABLE,
            wf_table=_ORACLE_WF_TABLE,
            wf_groupkey_column=_wf_groupkey_column(),
            query_description=query_description,
        )
        response = llm.invoke(prompt)
        raw_sql = response.content.strip()
        # 코드 블록 마커 제거
        if raw_sql.startswith("```"):
            raw_sql = re.sub(r"^```\w*\n?", "", raw_sql)
            raw_sql = re.sub(r"\n?```$", "", raw_sql)
        # E3 fix: trailing 단일 세미콜론 제거 — LLM이 SQL 끝에 ;를 자동으로 붙이는 습관 대응
        raw_sql = raw_sql.strip().rstrip(";").strip()
        raw_sql = _normalize_end_tm_join(raw_sql)
        raw_sql = _normalize_groupkey_column(raw_sql)
        raw_sql = _normalize_cast_parentheses(raw_sql)
    except Exception as e:
        logger.error(
            "[wads_query_sql] SQL 생성 실패: %s query_description=%r",
            e,
            query_description,
        )
        return f"SQL 생성 실패: {e}. wads_query_data 도구를 대신 사용하세요."

    _log_wads_sql(
        "wads_query_sql.generated",
        raw_sql,
        {},
        query_description=query_description,
        generated_sql_len=len(raw_sql),
    )

    # 2. SQL 검증
    ok, reason = _validate_sql(raw_sql)
    if not ok:
        logger.warning(
            "[wads_query_sql] SQL 검증 실패: %s (sql_len=%d)", reason, len(raw_sql)
        )
        _log_wads_sql(
            "wads_query_sql.validation_failed",
            raw_sql,
            {},
            query_description=query_description,
            validation_error=reason,
            generated_sql_len=len(raw_sql),
        )
        return f"SQL 검증 실패: {reason}. wads_query_data 도구를 대신 사용하세요."

    # 행 제한 보장
    sql = _ensure_row_limit(raw_sql)
    logger.debug("[wads_query_sql] SQL prepared len=%d", len(sql))
    _log_wads_sql(
        "wads_query_sql",
        sql,
        {},
        query_description=query_description,
        generated_sql_len=len(sql),
    )

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
                f"LOTCD, CATEGORY, PARAMETER, END_TM, HTML, OPER_PARA, "
                f"{_wf_groupkey_column()}, LOT_CD"
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
        logger.warning("[wads_query_sql] 결과 없음: sql_len=%d", len(sql))
        return "조건에 맞는 데이터가 없습니다."

    result = [
        {name: _json_safe_value(value) for name, value in zip(col_names, row)}
        for row in rows
    ]
    for row in result:
        if "group_key" in row and "groupkey" not in row:
            row["groupkey"] = row["group_key"]
        if "category" in row and "map_oper" not in row:
            map_oper = _category_to_map_oper(row.get("category"))
            if map_oper:
                row["map_oper"] = map_oper
    result, sort_info = _sort_sql_result_rows(
        result,
        query_description=query_description,
        sql=sql,
    )

    # HTML 컬럼 포함 여부에 따라 reports 또는 sql_result에 저장
    if "html" in col_names:
        for r in result:
            storage.setdefault("reports", []).append(
                {
                    "lotcd": r.get("lotcd", ""),
                    "category": r.get("category", ""),
                    "end_tm": r.get("end_tm", ""),
                    "parameter": r.get("parameter", ""),
                    "html": r.get("html", ""),
                }
            )
        return f"WADS SQL 리포트 조회 완료: 총 {len(result)}건 (리포트는 화면에 별도 표시됩니다)"
    else:
        storage["sql_result"] = result
        if sort_info:
            storage["sql_result_sort"] = sort_info
        col_info = ", ".join(col_names)
        return f"WADS SQL 쿼리 실행 완료: 총 {len(result)}건 조회됨. 컬럼: {col_info} (데이터는 화면에 별도 표시됩니다)"


# ── WADS 전용 도구 리스트 ─────────────────────────────────────
WADS_TOOLS = [wads_query_data, wads_get_html_report, wads_query_sql]

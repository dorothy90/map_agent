"""
LOT History @tool 함수 모듈
============================
5개 Oracle 테이블에서 LOT_ID로 종합 이력을 조회합니다.
- DF_FDC_ALARM, DF_QTIME_OVER, DF_TROUBLE_LOT, DF_FUTURE_ACTION, DF_SAMPLE_SPLIT
"""
from __future__ import annotations

import contextvars
import logging
from typing import Any, Dict, List

from langchain_core.tools import tool
from langfuse import observe

from common import get_oracle_connection as _get_oracle_connection

logger = logging.getLogger("yield_agent.lot_history_tools")


# ── ContextVar (요청별 격리) ──────────────────────────────────
_tool_payload_var: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "_lot_history_tool_payload"
)


def _get_tool_payload() -> Dict[str, Any]:
    """현재 컨텍스트의 tool payload storage 반환 (없으면 초기화)"""
    try:
        return _tool_payload_var.get()
    except LookupError:
        storage: Dict[str, Any] = {}
        _tool_payload_var.set(storage)
        return storage


# ── 5개 테이블 쿼리 정의 ──────────────────────────────────────
_QUERIES = {
    "fdc_alarm": """
        SELECT LOT_CD, LOT_ID, TRANSFER_TM, OPER_ID, EQP_ID, ITEM_NM,
               ALARM_LEVEL_CD, RSLT_VAL, SPEC_MIN_VAL, SPEC_MAX_VAL
        FROM DF_FDC_ALARM
        WHERE LOT_ID IN ({lot_placeholders})
        ORDER BY LOT_ID, TRANSFER_TM
    """,
    "qtime_over": """
        SELECT LOT_CD, LOT_ID, FROM_OPER, TO_OPER,
               CONTROL_LIMIT, Q_TIME, BAL, EVENT_TM
        FROM DF_QTIME_OVER
        WHERE LOT_ID IN ({lot_placeholders})
        ORDER BY LOT_ID, EVENT_TM
    """,
    "trouble_lot": """
        SELECT LOT_CD, LOT_ID, HOLD_TIME, STEP_DESC, CAUSE_EQ,
               DELAY_TIME, CONTENTS, H_CODE
        FROM DF_TROUBLE_LOT
        WHERE LOT_ID IN ({lot_placeholders})
        ORDER BY LOT_ID, HOLD_TIME
    """,
    "future_action": """
        SELECT LOT_ID, ACTION_TIME, ACTION_STEP, ACTION_COMMENT,
               ACTION_FLAG, REASON_AREA
        FROM DF_FUTURE_ACTION
        WHERE LOT_ID IN ({lot_placeholders})
        ORDER BY LOT_ID, ACTION_TIME
    """,
    "sample_split": """
        SELECT LOT_ID, EVENT, STEP, OPER_DESC,
               SAMPLE_SLOT, SAMPLE_SPLIT_ID, QTY
        FROM DF_SAMPLE_SPLIT
        WHERE LOT_ID IN ({lot_placeholders})
        ORDER BY LOT_ID, STEP
    """,
}

_COLUMN_NAMES = {
    "fdc_alarm": ["lot_cd", "lot_id", "transfer_tm", "oper_id", "eqp_id", "item_nm",
                   "alarm_level_cd", "rslt_val", "spec_min_val", "spec_max_val"],
    "qtime_over": ["lot_cd", "lot_id", "from_oper", "to_oper",
                    "control_limit", "q_time", "bal", "event_tm"],
    "trouble_lot": ["lot_cd", "lot_id", "hold_time", "step_desc", "cause_eq",
                     "delay_time", "contents", "h_code"],
    "future_action": ["lot_id", "action_time", "action_step", "action_comment",
                       "action_flag", "reason_area"],
    "sample_split": ["lot_id", "event", "step", "oper_desc",
                      "sample_slot", "sample_split_id", "qty"],
}


def _parse_lot_ids(lot_ids: str) -> list[str]:
    seen: set[str] = set()
    parsed: list[str] = []
    for raw in lot_ids.split(","):
        lot_id = raw.strip()
        if lot_id and lot_id not in seen:
            seen.add(lot_id)
            parsed.append(lot_id)
    return parsed


def _query_lots(
    conn: Any, lot_ids: list[str]
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    results = {
        lot_id: {source: [] for source in _QUERIES}
        for lot_id in lot_ids
    }
    binds = {f"lot_{index}": lot_id for index, lot_id in enumerate(lot_ids)}
    placeholders = ", ".join(f":lot_{index}" for index in range(len(lot_ids)))
    cur = conn.cursor()
    try:
        for source, sql_template in _QUERIES.items():
            cur.execute(sql_template.format(lot_placeholders=placeholders), binds)
            columns = _COLUMN_NAMES[source]
            for raw_row in cur.fetchall():
                row = dict(zip(columns, raw_row))
                lot_id = str(row.get("lot_id") or "").strip()
                if lot_id in results:
                    results[lot_id][source].append(row)
    finally:
        cur.close()
    return results


@tool
@observe(name="query_lot_history")
def query_lot_history(lot_ids: str) -> str:
    """LOT 종합 이력을 조회합니다. 5개 테이블(FDC알람, Q-TIME초과, Trouble, Future Action, Sample Split)에서 LOT_ID로 검색합니다.

    Args:
        lot_ids: 조회할 LOT_ID (콤마 구분으로 여러 개 가능, 예: "4SS2DPD" 또는 "4SS2DPD,4SSXCEW")

    Returns:
        조회 결과 요약 문자열 (상세 데이터는 화면에 별도 표시)
    """
    lot_list = _parse_lot_ids(lot_ids)
    if not lot_list:
        return "LOT_ID를 입력해주세요."

    logger.info("[query_lot_history] 조회 시작: %s", lot_list)
    storage = _get_tool_payload()

    conn = _get_oracle_connection()
    try:
        all_results = _query_lots(conn, lot_list)
    except Exception as e:
        logger.error("[query_lot_history] Oracle 오류: %s", e, exc_info=True)
        storage["lot_history"] = {"error": str(e)}
        return f"오류: {e}"
    finally:
        conn.close()

    storage["lot_history"] = all_results

    # LLM에 요약만 반환
    summaries = []
    for lot_id, data in all_results.items():
        parts = [
            f"FDC {len(data['fdc_alarm'])}건",
            f"Q-TIME {len(data['qtime_over'])}건",
            f"TROUBLE {len(data['trouble_lot'])}건",
            f"ACTION {len(data['future_action'])}건",
            f"SAMPLE {len(data['sample_split'])}건",
        ]
        summaries.append(f"{lot_id} — {', '.join(parts)}")

    logger.info("[query_lot_history] 조회 완료: %d LOTs", len(lot_list))
    return f"조회 완료 ({len(lot_list)}건):\n" + "\n".join(summaries) + "\n(데이터는 화면에 별도 표시됩니다)"


LOT_HISTORY_TOOLS = [query_lot_history]

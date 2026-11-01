from __future__ import annotations

import asyncio
import threading

from pydantic import Field

from ..types import Contract, EMPTY_RESULT_SUMMARY, ResultTable
from .registry import ToolResult, ToolSpec


class ListTablesInput(Contract):
    table: str | None = Field(default=None, min_length=1)


def _connection():
    from common import get_oracle_connection

    return get_oracle_connection()


def _list(
    args: ListTablesInput,
    cancelled: threading.Event,
    active: dict,
    lock: threading.Lock,
) -> tuple[list[str], list[dict]] | None:
    connection = cursor = None
    try:
        connection = _connection()
        with lock:
            active["connection"] = connection
        if cancelled.is_set():
            return None
        connection.call_timeout = 60_000
        cursor = connection.cursor()
        if args.table is None:
            columns = ["TABLE_NAME"]
            cursor.execute(
                "SELECT table_name FROM USER_TABLES ORDER BY table_name"
            )
        else:
            columns = ["COLUMN_NAME", "DATA_TYPE"]
            cursor.execute(
                "SELECT c.column_name, c.data_type FROM USER_TAB_COLUMNS c "
                "JOIN USER_TABLES t ON t.table_name = c.table_name "
                "WHERE t.table_name = UPPER(:table_name) ORDER BY c.column_id",
                {"table_name": args.table},
            )
        return columns, [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        try:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                if connection is not None:
                    connection.close()
        finally:
            with lock:
                active.pop("connection", None)


async def _run_list(
    args: ListTablesInput,
) -> tuple[list[str], list[dict]] | None:
    cancelled = threading.Event()
    active = {}
    lock = threading.Lock()
    worker = asyncio.create_task(
        asyncio.to_thread(_list, args, cancelled, active, lock)
    )
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancelled.set()
        with lock:
            connection = active.get("connection")
        if connection is not None:
            try:
                connection.cancel()
            except Exception:
                pass
        worker.add_done_callback(
            lambda task: task.exception() if not task.cancelled() else None
        )
        raise


async def list_tables(args: ListTablesInput, ctx) -> ToolResult:
    result = await _run_list(args)
    if result is None:
        raise asyncio.CancelledError
    columns, rows = result
    row_count = len(rows)
    return ToolResult(
        tables=[ResultTable(
            table_id="list_tables",
            title=args.table or "tables",
            columns=columns,
            rows=rows,
            complete=True,
        )],
        summary=EMPTY_RESULT_SUMMARY if not rows else f"결과 {row_count}건",
        status="empty" if not rows else None,
        scope={"list_tables": {
            "columns": columns,
            "rows": rows,
            "row_count": row_count,
            "truncated": False,
        }},
    )


def register(registry):
    registry.add(ToolSpec(
        "list_tables",
        "현재 Oracle 스키마의 물리 테이블명을 반환한다. "
        "table을 지정하면 해당 테이블의 컬럼명과 데이터 타입을 반환한다.",
        ListTablesInput,
        list_tables,
        read_only=True,
        effect="read",
        eager=True,
    ))

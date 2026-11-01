from __future__ import annotations

import asyncio
import csv
import io
import math
import threading
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator

from ..isolation.backend import IsolatedSSHBackend
from ..types import Contract, EMPTY_RESULT_SUMMARY, ResultTable
from .registry import ToolResult, ToolSpec


class Comparison(Contract):
    column: str = Field(min_length=1)
    op: Literal["eq", "ne", "lt", "le", "gt", "ge"] = "eq"
    value: str | int | float | bool | None

    @field_validator("value")
    @classmethod
    def scalar_only(cls, value):
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError("comparison values must be scalar")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("comparison values must be finite")
        return value


class Ordering(Contract):
    column: str = Field(min_length=1)
    direction: Literal["asc", "desc"] = "asc"


class QueryDBInput(Contract):
    table: str = Field(min_length=1)
    columns: list[str] | None = None
    filters: list[Comparison] = Field(default_factory=list)
    order_by: list[Ordering] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=1000)


def _connection():
    from common import get_oracle_connection

    return get_oracle_connection()


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _resolved(names: list[str], available: dict[str, str]) -> list[str]:
    result = []
    for name in names:
        key = name.upper()
        if key not in available:
            raise ValueError(f"Unknown column: {name}")
        if available[key] not in result:
            result.append(available[key])
    return result


def _serialize(value):
    if callable(getattr(value, "read", None)):
        return _serialize(value.read())
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("Unsupported database value: non-finite float")
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    raise TypeError(f"Unsupported database value: {type(value).__name__}")


def _query(args: QueryDBInput, cancelled: threading.Event, active: dict, lock: threading.Lock):
    connection = cursor = None
    try:
        connection = _connection()
        with lock:
            active["connection"] = connection
        if cancelled.is_set():
            return None
        connection.call_timeout = 60_000
        cursor = connection.cursor()

        cursor.execute(
            "SELECT table_name FROM USER_TABLES WHERE table_name = UPPER(:table_name)",
            {"table_name": args.table},
        )
        table_row = cursor.fetchone()
        if table_row is None:
            raise ValueError(f"Unknown table: {args.table}")
        table = table_row[0]

        cursor.execute(
            "SELECT column_name FROM USER_TAB_COLUMNS "
            "WHERE table_name = :table_name ORDER BY column_id",
            {"table_name": table},
        )
        columns = [row[0] for row in cursor.fetchall()]
        available = {name.upper(): name for name in columns}
        selected = columns if args.columns is None else _resolved(args.columns, available)
        if not selected:
            raise ValueError("At least one column is required")

        predicates = []
        binds = {}
        operators = {"eq": "=", "ne": "<>", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}
        for index, item in enumerate(args.filters):
            column = _resolved([item.column], available)[0]
            if item.value is None:
                if item.op not in {"eq", "ne"}:
                    raise ValueError("NULL comparisons support only eq and ne")
                predicates.append(f"{_identifier(column)} IS {'NOT ' if item.op == 'ne' else ''}NULL")
            else:
                name = f"value_{index}"
                predicates.append(f"{_identifier(column)} {operators[item.op]} :{name}")
                binds[name] = item.value

        ordering = []
        for item in args.order_by:
            column = _resolved([item.column], available)[0]
            ordering.append(f"{_identifier(column)} {item.direction.upper()}")
        inner = f"SELECT {', '.join(map(_identifier, selected))} FROM {_identifier(table)}"
        if predicates:
            inner += " WHERE " + " AND ".join(predicates)
        if ordering:
            inner += " ORDER BY " + ", ".join(ordering)
        sql = f"SELECT * FROM ({inner}) WHERE ROWNUM <= :row_limit"
        binds["row_limit"] = args.limit + 1
        cursor.execute(sql, binds)
        raw_rows = cursor.fetchmany(args.limit + 1)
        rows = [
            {column: _serialize(value) for column, value in zip(selected, row)}
            for row in raw_rows[:args.limit]
        ]
        return selected, rows, len(raw_rows) > args.limit
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


async def _run_query(args):
    cancelled = threading.Event()
    active = {}
    lock = threading.Lock()
    worker = asyncio.create_task(asyncio.to_thread(_query, args, cancelled, active, lock))
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
        worker.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        raise


def _csv_bytes(columns, rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


async def query_db(args, ctx):
    result = await _run_query(args)
    if result is None:
        raise asyncio.CancelledError
    columns, rows, truncated = result
    isolation = getattr(ctx.settings, "isolation", None)
    if isolation is None:
        raise RuntimeError("isolated backend is not configured")
    backend = IsolatedSSHBackend(isolation, ctx.run["run_id"], ctx.run["principal_id"])
    path = await backend.upload_bytes(_csv_bytes(columns, rows), timeout=60)
    row_count = len(rows)
    summary = EMPTY_RESULT_SUMMARY if row_count == 0 else f"결과 {row_count}건"
    return ToolResult(
        tables=[ResultTable(
            table_id="query_db", title=args.table, rows=rows, columns=columns,
            complete=not truncated,
            missing_reason="Result exceeded query limit" if truncated else None,
        )],
        summary=summary,
        status="empty" if row_count == 0 else None,
        scope={"query_db": {
            "columns": columns, "row_count": row_count,
            "truncated": truncated, "path": path,
        }},
    )


def register(registry):
    registry.add(ToolSpec(
        "query_db",
        "현재 Oracle 스키마의 물리 테이블 하나를 조회해 전체 제한 결과를 저장하고 격리 환경에 CSV를 만든다. "
        "반환된 absolute CSV path는 실행 간 유지되며 마지막 수정 후 7일 동안 다시 열 수 있다. "
        "후속 Python 분석은 tables[result_id][table_id]를 사용하며 table_id는 이 도구가 반환한다.",
        QueryDBInput, query_db, read_only=True, effect="artifact", eager=True,
    ))

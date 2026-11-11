from __future__ import annotations

import asyncio
import csv
import io
import math
import re
import threading
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator

from ..isolation.backend import IsolatedSSHBackend, isolation_backend
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


class Aggregate(Contract):
    function: Literal["avg", "count", "count_distinct", "min", "max", "sum"]
    column: str | None = Field(default=None, min_length=1)
    alias: str = Field(min_length=1)


class QueryDBInput(Contract):
    table: str = Field(min_length=1)
    columns: list[str] | None = None
    filters: list[Comparison] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    aggregates: list[Aggregate] = Field(default_factory=list)
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


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$"
)


def _temporal_bind(data_type: str, value, bind_name: str):
    if not isinstance(value, str):
        raise ValueError("Temporal comparisons require an ISO string")
    upper_type = data_type.upper()
    if upper_type == "DATE":
        if _DATE.fullmatch(value):
            datetime.strptime(value, "%Y-%m-%d")
            return f"TO_DATE(:{bind_name}, 'YYYY-MM-DD')", value
        match = _DATETIME.fullmatch(value)
        if not match or match.group(2) or match.group(3):
            raise ValueError("DATE comparisons require an ISO date or second-precision datetime")
        datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
        return f"TO_DATE(:{bind_name}, 'YYYY-MM-DD\"T\"HH24:MI:SS')", value

    match = _DATETIME.fullmatch(value)
    if not match:
        raise ValueError("TIMESTAMP comparisons require an ISO datetime")
    datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
    fraction, offset = match.group(2), match.group(3)
    precision_match = re.match(r"TIMESTAMP(?:\((\d+)\))?", upper_type)
    precision = int(precision_match.group(1) or 6) if precision_match else 6
    if fraction and len(fraction) > precision:
        raise ValueError(f"TIMESTAMP precision exceeds {precision} digits")
    timezone_bearing = "TIME ZONE" in upper_type
    if timezone_bearing and not offset:
        raise ValueError("Timezone-bearing TIMESTAMP comparisons require an offset")
    if not timezone_bearing and offset:
        raise ValueError("TIMESTAMP without time zone does not accept an offset")
    normalized = value[:-1] + "+00:00" if offset == "Z" else value
    try:
        datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError("Invalid ISO timestamp or offset") from None
    mask = 'YYYY-MM-DD"T"HH24:MI:SS'
    if fraction:
        mask += f".FF{len(fraction)}"
    if timezone_bearing:
        mask += "TZH:TZM"
        return f"TO_TIMESTAMP_TZ(:{bind_name}, '{mask}')", normalized
    return f"TO_TIMESTAMP(:{bind_name}, '{mask}')", normalized


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
            "SELECT column_name, data_type FROM USER_TAB_COLUMNS "
            "WHERE table_name = :table_name ORDER BY column_id",
            {"table_name": table},
        )
        metadata = cursor.fetchall()
        columns = [row[0] for row in metadata]
        available = {name.upper(): name for name in columns}
        data_types = {row[0].upper(): row[1] for row in metadata}
        grouped = _resolved(args.group_by, available)
        aggregate_mode = bool(grouped or args.aggregates)
        selected = (
            grouped if aggregate_mode and args.columns is None
            else columns if args.columns is None
            else _resolved(args.columns, available)
        )
        if not selected and not args.aggregates:
            raise ValueError("At least one column is required")
        if aggregate_mode and not set(selected).issubset(grouped):
            raise ValueError("Aggregate queries may project only grouped columns")

        aggregate_sql = []
        aliases = {}
        grouped_keys = {name.upper() for name in grouped}
        for item in args.aggregates:
            if item.column is None and item.function != "count":
                raise ValueError(f"{item.function} aggregate requires a column")
            alias_key = item.alias.upper()
            if alias_key in grouped_keys or alias_key in aliases:
                raise ValueError(f"Duplicate or colliding aggregate alias: {item.alias}")
            aliases[alias_key] = item.alias
            source = "*" if item.column is None else _identifier(
                _resolved([item.column], available)[0]
            )
            expression = (
                f"COUNT(DISTINCT {source})"
                if item.function == "count_distinct"
                else f"{item.function.upper()}({source})"
            )
            aggregate_sql.append(f"{expression} AS {_identifier(item.alias)}")

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
                expression = f":{name}"
                value = item.value
                data_type = data_types[column.upper()]
                if data_type.upper() == "DATE" or data_type.upper().startswith("TIMESTAMP"):
                    expression, value = _temporal_bind(data_type, value, name)
                predicates.append(f"{_identifier(column)} {operators[item.op]} {expression}")
                binds[name] = value

        ordering = []
        for item in args.order_by:
            if aggregate_mode and item.column.upper() in aliases:
                column = aliases[item.column.upper()]
            else:
                column = _resolved([item.column], available)[0]
                if aggregate_mode and column not in grouped:
                    raise ValueError("Aggregate ordering requires a grouped column or aggregate alias")
            ordering.append(f"{_identifier(column)} {item.direction.upper()}")
        projections = [*map(_identifier, selected), *aggregate_sql]
        inner = f"SELECT {', '.join(projections)} FROM {_identifier(table)}"
        if predicates:
            inner += " WHERE " + " AND ".join(predicates)
        if grouped:
            inner += " GROUP BY " + ", ".join(map(_identifier, grouped))
        if ordering:
            inner += " ORDER BY " + ", ".join(ordering)
        sql = f"SELECT * FROM ({inner}) WHERE ROWNUM <= :row_limit"
        binds["row_limit"] = args.limit + 1
        cursor.execute(sql, binds)
        raw_rows = cursor.fetchmany(args.limit + 1)
        output_columns = [*selected, *(item.alias for item in args.aggregates)]
        rows = [
            {column: _serialize(value) for column, value in zip(output_columns, row)}
            for row in raw_rows[:args.limit]
        ]
        return output_columns, rows, len(raw_rows) > args.limit
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
    constructor = isolation_backend if getattr(isolation, "backend", None) == "seatbelt" else IsolatedSSHBackend
    backend = constructor(isolation, ctx.run["run_id"], ctx.run["principal_id"])
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
        "현재 Oracle 스키마의 물리 테이블 하나를 안전하게 조회한다. 컬럼 필터, ISO 날짜·타임스탬프 범위, "
        "그룹화와 avg/count/count_distinct/min/max/sum 집계를 지원하며 count_distinct는 column이 필수다. "
        "limit은 원본 행이 아니라 반환 행 또는 그룹에 적용된다. "
        "임의 SQL은 받지 않는다. 조회 결과를 저장하고 격리 환경에 CSV를 만든다. "
        "반환된 absolute CSV path는 실행 간 유지되며 마지막 수정 후 7일 동안 다시 열 수 있다. "
        "후속 Python 분석은 tables[result_id][table_id]를 사용하며 table_id는 이 도구가 반환한다.",
        QueryDBInput, query_db, read_only=True, effect="artifact", eager=True,
    ))

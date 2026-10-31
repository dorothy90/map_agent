"""Ownership, version and result helpers shared by wafer-table tools."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .types import ResultTable
from .tools.registry import ToolResult
from .tools.wafer_set_tools import _version as table_version
from .wafer_set_contracts import TableHandle

MAX_TABLE_ROWS = 250_000
MAX_RESULT_BYTES = 64 * 1024 * 1024
VALID_SOURCE_STATUSES = {"success", "partial", "empty"}


@dataclass(frozen=True)
class ResolvedTable:
    rows: list[dict]
    columns: list[str]
    units: dict[str, str]
    complete: bool
    missing_reason: str | None
    actual_version: str
    observation: Any


@dataclass(frozen=True)
class MembershipMatch:
    cohorts_by_row: list[tuple[Any, ...]]
    identities_by_row: list[tuple[str, str]]
    key_columns: tuple[str, ...]
    matched_members: int
    unmatched_members: int


def json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str, allow_nan=False).encode())


def schema_fingerprint(tables: list[ResultTable]) -> str:
    schema = [{"table_id": t.table_id, "columns": t.columns, "units": t.units} for t in tables]
    return hashlib.sha256(json.dumps(schema, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _identity_token(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def match_memberships(source_rows: list[dict], memberships: list[dict]) -> MembershipMatch:
    """Match cohort membership rows to source rows by references, then preserved keys."""
    reserved = {"cohort_id", "member_id", "measurement_ref"}
    key_columns = tuple(dict.fromkeys(
        key for membership in memberships for key in membership if key not in reserved
    ))
    reference_indexes = {"measurement_ref": {}, "member_id": {}}
    key_index: dict[tuple[str, ...], list[int]] = {}
    identities = []
    for index, row in enumerate(source_rows):
        for field in reference_indexes:
            value = row.get(field)
            if value is not None:
                reference_indexes[field].setdefault(_identity_token(value), []).append(index)
        if key_columns and all(column in row for column in key_columns):
            values = tuple(_identity_token(row[column]) for column in key_columns)
            key_index.setdefault(values, []).append(index)
        if row.get("measurement_ref") is not None:
            identities.append(("measurement_ref", _identity_token(row["measurement_ref"])))
        elif row.get("member_id") is not None:
            identities.append(("member_id", _identity_token(row["member_id"])))
        elif key_columns and all(column in row for column in key_columns):
            values = [row[column] for column in key_columns]
            identities.append(("keys", _identity_token(values)))
        else:
            identities.append(("row", hashlib.sha256(
                json.dumps(row, sort_keys=True, ensure_ascii=False, default=str).encode()
            ).hexdigest()))

    cohorts: list[list[Any]] = [[] for _ in source_rows]
    matched = 0
    for membership in memberships:
        candidates: list[int] = []
        for field in ("measurement_ref", "member_id"):
            value = membership.get(field)
            if value is not None:
                candidates = reference_indexes[field].get(_identity_token(value), [])
            if candidates:
                break
        if not candidates and key_columns and all(column in membership for column in key_columns):
            key = tuple(_identity_token(membership[column]) for column in key_columns)
            candidates = key_index.get(key, [])
        if not candidates:
            continue
        matched += 1
        cohort_id = membership.get("cohort_id")
        for index in candidates:
            if cohort_id not in cohorts[index]:
                cohorts[index].append(cohort_id)
    return MembershipMatch(
        cohorts_by_row=[tuple(values) for values in cohorts],
        identities_by_row=identities,
        key_columns=key_columns,
        matched_members=matched,
        unmatched_members=len(memberships) - matched,
    )


async def resolve_handle(ctx, handle: TableHandle) -> ResolvedTable:
    principal = ctx.run["principal_id"]
    session = ctx.run["session_id"]
    obs = await ctx.store.observation(principal, session, handle.result_id)
    if obs.status not in VALID_SOURCE_STATUSES:
        raise ValueError("Source result status is not readable")
    if obs.scope.get("handle_contract") != "wafer-table/v1":
        raise ValueError("Unsupported handle contract")
    table = next((item for item in obs.tables if item.table_id == handle.table_id), None)
    if table is None:
        raise ValueError("Unknown table_id")
    rows = await ctx.store.rows(principal, session, handle.result_id, table_id=handle.table_id)
    if len(rows) > MAX_TABLE_ROWS:
        raise ValueError("Source table row limit exceeded")
    if json_size(rows) > MAX_RESULT_BYTES:
        raise ValueError("Source table byte limit exceeded")
    actual = table_version(rows)
    declared = obs.scope.get("table_versions", {}).get(handle.table_id)
    if declared is None or declared != actual:
        raise ValueError("Stored declared table version does not match rows")
    if handle.version is not None and handle.version != actual:
        raise ValueError("Table version mismatch")
    columns = list(dict.fromkeys([*table.columns, *(key for row in rows for key in row)]))
    return ResolvedTable(rows=rows, columns=columns, units=dict(table.units),
                         complete=table.complete, missing_reason=table.missing_reason,
                         actual_version=actual, observation=obs)


async def resolve_many(ctx, handles: list[TableHandle]) -> list[ResolvedTable]:
    cache: dict[tuple[str, str], ResolvedTable] = {}
    output = []
    for handle in handles:
        key = (handle.result_id, handle.table_id)
        if key not in cache:
            cache[key] = await resolve_handle(ctx, handle)
        elif handle.version is not None and handle.version != cache[key].actual_version:
            raise ValueError("Table version mismatch")
        output.append(cache[key])
    return output


def build_result(tables: list[ResultTable], *, source_result_ids: list[str] | None = None,
                 limitations: list[dict] | None = None, scope: dict | None = None,
                 summary: str = "", status: str | None = None, data_origin: str = "live",
                 artifacts: list[dict] | None = None) -> ToolResult:
    tables = [ResultTable.model_validate(table) for table in tables]
    if sum(len(table.rows) for table in tables) > MAX_TABLE_ROWS:
        raise ValueError("Result row limit exceeded")
    if json_size([table.model_dump() for table in tables]) > MAX_RESULT_BYTES:
        raise ValueError("Result byte limit exceeded")
    limitations = limitations or []
    complete = all(table.complete for table in tables)
    data_tables = [table for table in tables if not table.table_id.endswith(("_summary", "_audit", "_meta"))]
    has_data = any(table.rows for table in data_tables or tables)
    resolved_status = status or ("partial" if not complete else "success" if has_data else "empty")
    result_scope = {**(scope or {}), "handle_contract": "wafer-table/v1",
                    "table_versions": {table.table_id: table_version(table.rows) for table in tables},
                    "schema_fingerprint": schema_fingerprint(tables), "limitations": limitations}
    return ToolResult(tables=tables, summary=summary, scope=result_scope,
                      source_result_ids=list(dict.fromkeys(source_result_ids or [])),
                      status=resolved_status, data_origin=data_origin, artifacts=artifacts or [])


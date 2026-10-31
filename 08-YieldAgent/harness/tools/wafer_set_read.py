"""Compatible handle-aware adapter for the existing result reader."""
from __future__ import annotations

import json

from pydantic import Field, model_validator

from ..types import Contract, ResultPage, ResultTable
from ..wafer_set_contracts import TableHandle
from ..wafer_set_core import build_result, resolve_handle
from .python_tools import ReadInput

PAGE_BYTES = 1024 * 1024


class CompatibleReadInput(Contract):
    result_id: str | None = None
    source: TableHandle | None = Field(default=None, description="버전을 검증할 저장 표 핸들")
    table_id: str | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=1000)
    columns: list[str] | None = None

    @model_validator(mode="after")
    def one_path(self):
        if (self.result_id is None) == (self.source is None):
            raise ValueError("Supply exactly one read path")
        if self.source is not None and self.table_id is not None:
            raise ValueError("table_id belongs to the legacy path")
        if self.source is not None and self.columns is not None and len(self.columns) > 100:
            raise ValueError("columns exceeds the handle path limit")
        return self


def _bounded_page(rows: list[dict], offset: int, limit: int) -> tuple[list[dict], int | None, bool]:
    selected, used = [], 2
    for row in rows[offset:offset + limit]:
        size = len(json.dumps(row, ensure_ascii=False, default=str, allow_nan=False).encode())
        if not selected and size + 2 > PAGE_BYTES:
            raise ValueError("A single row exceeds the page byte limit; select fewer columns")
        extra = size + (1 if selected else 0)
        if used + extra > PAGE_BYTES:
            break
        selected.append(row)
        used += extra
    consumed = offset + len(selected)
    truncated = offset > 0 or consumed < len(rows)
    return selected, consumed if consumed < len(rows) else None, truncated


async def read_result(args: CompatibleReadInput, ctx):
    resolved = await resolve_handle(ctx, args.source)
    if args.columns is not None:
        unknown = [column for column in args.columns if column not in resolved.columns]
        if unknown:
            raise ValueError(f"Unknown column: {unknown[0]}")
        source_rows = [{column: row.get(column) for column in args.columns} for row in resolved.rows]
        columns = list(args.columns)
    else:
        source_rows, columns = resolved.rows, resolved.columns
    page, next_offset, truncated = _bounded_page(source_rows, args.offset, args.limit)
    complete = resolved.complete and not truncated
    reasons = []
    if not resolved.complete:
        reasons.append(resolved.missing_reason or "Source table is incomplete")
    if truncated:
        reasons.append("Only a page of the source table was read")
    reason = "; ".join(reasons) or None
    page_table = ResultTable(table_id="result_page", title="Result page", rows=page, columns=columns,
        units={key: value for key, value in resolved.units.items() if key in columns},
        complete=complete, missing_reason=reason)
    meta = {"source_result_id": args.source.result_id, "source_table_id": args.source.table_id,
        "source_version": resolved.actual_version, "source_total_rows": len(resolved.rows),
        "offset": args.offset, "returned_rows": len(page), "next_offset": next_offset,
        "source_complete": resolved.complete, "page_complete": complete}
    meta_table = ResultTable(table_id="page_meta", title="Page metadata", rows=[meta], columns=list(meta),
                             complete=True, missing_reason=None)
    limitations = []
    if not resolved.complete:
        limitations.append({"code": "SOURCE_INCOMPLETE", "table_id": "result_page",
            "affected_count": len(resolved.rows), "requested_count": len(resolved.rows),
            "detail": resolved.missing_reason or "Source table is incomplete",
            "source_handle": args.source.model_dump()})
    if truncated:
        limitations.append({"code": "PAGE_ONLY", "table_id": "result_page", "affected_count": len(page),
            "requested_count": len(resolved.rows), "detail": "Only a page of the source table was read",
            "source_handle": args.source.model_dump()})
    projection = ResultPage(table_id="result_page", source_total_rows=len(resolved.rows),
                            offset=args.offset, limit=args.limit)
    result = build_result([page_table, meta_table], source_result_ids=[args.source.result_id],
        limitations=limitations, scope={"source_version": resolved.actual_version},
        summary=projection.summary(len(page)),
        data_origin=resolved.observation.provenance.get("data_origin", "unknown"))
    result.page = projection
    return result


async def dispatch_read(args: CompatibleReadInput, ctx, *, legacy_handler, legacy_schema=ReadInput):
    if args.source is None:
        legacy = legacy_schema.model_validate({"result_id": args.result_id, "table_id": args.table_id,
            "offset": args.offset, "limit": args.limit, "columns": args.columns})
        return await legacy_handler(legacy, ctx)
    return await read_result(args, ctx)


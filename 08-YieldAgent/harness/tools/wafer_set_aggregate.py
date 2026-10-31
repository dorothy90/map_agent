"""Aggregation and deterministic pivoting of normalized stored metrics."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ..runtime.domain_process import run_blocking
from ..types import Contract, ResultTable
from ..wafer_set_contracts import TableHandle
from ..wafer_set_core import build_result, resolve_many
from ..wafer_set_statistics import aggregate_rows, pivot_rows
from .registry import ToolSpec


REQUIRED_METRIC_COLUMNS = ("metric_id", "definition_id", "value", "numerator", "denominator", "unit")


class AggregateMetricsInput(Contract):
    sources: list[TableHandle] = Field(min_length=1, max_length=10, description="입력 지표 표")
    group_by: list[str] = Field(default_factory=list, max_length=6, description="집계 그룹 열")
    operation: Literal["mean", "sum", "min", "max", "median", "std", "ratio_of_sums", "identity"] = Field(
        description="적용할 집계 연산")
    period: Literal["none", "daily", "weekly", "monthly"] = Field(default="none", description="선택적 달력 기간")
    time_column: str | None = Field(default=None, min_length=1, max_length=128, description="기간을 계산할 시각 열")
    time_zone: Literal["Asia/Seoul"] = Field(default="Asia/Seoul", description="달력 기간의 시각 기준")
    output_shape: Literal["long", "pivot"] = Field(default="long", description="출력 표 배열 방식")
    column_catalog: TableHandle | None = Field(default=None, description="피벗 열 메타데이터 표")
    cohort_overlap: Literal["error", "deduplicate", "count_memberships"] = Field(
        default="error", description="겹치는 소속의 집계 방식")
    max_groups: int = Field(default=100, ge=1, le=100, description="허용할 최대 집계 그룹 수")

    @model_validator(mode="after")
    def valid_period(self):
        if (self.period == "none") != (self.time_column is None):
            raise ValueError("time_column is required exactly when period is used")
        return self


async def aggregate_metrics(args, ctx):
    handles = [*args.sources, *([args.column_catalog] if args.column_catalog else [])]
    resolved = await resolve_many(ctx, handles)
    sources = resolved[:len(args.sources)]
    catalog = resolved[-1] if args.column_catalog else None
    for source in sources:
        missing = [column for column in REQUIRED_METRIC_COLUMNS if column not in source.columns]
        if missing:
            required = ", ".join(REQUIRED_METRIC_COLUMNS)
            raise ValueError(
                f"sources는 compute_metrics가 만든 지표 표여야 합니다 (필요 열: {required})"
                ". 먼저 read_facts 또는 select_wafers의 표를 compute_metrics에 전달하고, "
                "반환된 metrics 표 핸들을 sources에 넣으세요."
            )
    rows = [row for source in sources for row in source.rows]
    output = await run_blocking(aggregate_rows, rows, group_by=args.group_by,
        operation=args.operation, period=args.period, time_column=args.time_column,
        overlap=args.cohort_overlap, max_groups=args.max_groups)
    source_complete = all(source.complete for source in sources) and (catalog.complete if catalog else True)
    complete = source_complete and output.audit["excluded_null_rows"] == 0
    reason = next((source.missing_reason for source in sources if source.missing_reason), None)
    if not complete and reason is None: reason = "Null metric values were excluded"
    aggregate_columns = list(dict.fromkeys(key for row in output.rows for key in row)) or [
        "group_id", *args.group_by, "period_start", "period_end", "metric_id", "definition_id",
        "operation", "value", "numerator_sum", "denominator_sum", "value_sum", "value_count",
        "measurement_count", "unique_wafer_count", "unit", "aggregation_level"]
    tables = [ResultTable(table_id="aggregates", title="Aggregates", rows=output.rows,
        columns=aggregate_columns, units={"value": "per_row_unit"}, complete=complete, missing_reason=reason)]
    if args.output_shape == "pivot":
        pivot, columns = await run_blocking(pivot_rows, output.rows, catalog.rows if catalog else [])
        tables.extend([
            ResultTable(table_id="yield_pivot", title="Metric pivot", rows=pivot,
                        columns=list(dict.fromkeys(key for row in pivot for key in row)),
                        complete=complete, missing_reason=reason),
            ResultTable(table_id="yield_columns", title="Pivot columns", rows=columns,
                        columns=["column_id", "display_label", "group_id", "group_label", "display_order",
                                 "source_id", "process", "parameter", "unit", "better_when", "included",
                                 "availability"], complete=complete, missing_reason=reason)])
    tables.append(ResultTable(table_id="aggregation_audit", title="Aggregation audit", rows=[output.audit],
                              columns=list(output.audit), complete=True, missing_reason=None))
    limitations = [] if complete else [{"code": "SOURCE_INCOMPLETE", "table_id": "aggregates",
        "affected_count": output.audit["excluded_null_rows"], "requested_count": len(rows),
        "detail": reason, "source_handle": args.sources[0].model_dump()}]
    origins = {source.observation.provenance.get("data_origin", "unknown") for source in sources}
    origin = origins.pop() if len(origins) == 1 else "unknown"
    return build_result(tables, source_result_ids=[handle.result_id for handle in handles],
        limitations=limitations, scope={"source_versions": [source.actual_version for source in sources],
            "operation": args.operation, "period": args.period, "output_shape": args.output_shape},
        summary=f"지표를 {len(output.rows)}개 그룹으로 집계했습니다.", data_origin=origin)


def register(registry):
    registry.add(ToolSpec("aggregate_metrics", "최대 10개의 저장 지표 표와 그룹·기간·연산을 입력받아 집계 표 및 선택적 피벗·열 메타데이터 표를 반환한다. 집계 그룹은 최대 100개다.",
                          AggregateMetricsInput, aggregate_metrics, timeout=30))

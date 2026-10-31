"""Coordinate-preserving reduction of immutable stored map payloads."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ..types import Contract, ResultTable
from ..wafer_set_arrays import parse_maps, reduce_map_metrics
from ..wafer_set_contracts import TableHandle
from ..wafer_set_core import build_result, match_memberships, resolve_handle, resolve_many
from .registry import ToolSpec
from .wafer_set_metrics import MetricSpec, _process_links


class ReduceMapsInput(Contract):
    source: TableHandle = Field(description="입력 저장 측정 표")
    metrics: list[MetricSpec] = Field(min_length=1, max_length=25, description="좌표별로 계산할 지표")
    definitions: TableHandle | None = Field(default=None, description="저장된 지표 정의 표")
    cohorts: TableHandle | None = Field(default=None, description="선택적 저장 소속 표")
    coordinate_policy: Literal["same_product_process", "explicit_same_grid"] = Field(
        default="same_product_process", description="좌표 그룹 호환 정책")
    grid_id: str | None = Field(default=None, min_length=1, max_length=100, description="명시적인 좌표계 식별자")
    max_cells: int = Field(default=250_000, ge=1, le=250_000, description="허용할 최대 좌표 출력 행 수")

    @model_validator(mode="after")
    def valid_reduction(self):
        if any(spec.mode not in {"bin", "symbol"} for spec in self.metrics):
            raise ValueError("Map reduction accepts bin or symbol metrics")
        if any(spec.mode == "symbol" for spec in self.metrics) and self.definitions is None:
            raise ValueError("symbol metrics require stored definitions")
        if any(spec.mode == "bin" and spec.quantity == "fail_rate"
               for spec in self.metrics):
            raise ValueError(
                "Failure metrics require symbol definitions. "
                "Reselect with symbol_bin_parameters and use a symbol metric."
            )
        if (self.coordinate_policy == "explicit_same_grid") != (self.grid_id is not None):
            raise ValueError("grid_id is required exactly for explicit_same_grid")
        names = [spec.metric_id for spec in self.metrics]
        if len(names) != len(set(names)): raise ValueError("metric_id values must be unique")
        return self


def _namespace(ctx):
    run = ctx.run
    return (run["principal_id"], run["session_id"], run.get("run_id"), run.get("epoch"))


async def _payloads(ctx, source):
    table_ids = {table.table_id for table in source.observation.tables}
    if "map_payloads" not in table_ids:
        raise ValueError(
            "Stored map payload table is required"
            ". 먼저 read_facts(source_id='map', include_payload=True)를 실행하고, "
            "map_payloads가 함께 저장된 결과의 map_measurements 표 핸들을 "
            "source에 전달하세요."
        )
    version = source.observation.scope.get("table_versions", {}).get("map_payloads")
    return await resolve_handle(ctx, TableHandle(result_id=source.observation.result_id,
                                                  table_id="map_payloads", version=version))


async def reduce_maps(args, ctx):
    handles = [args.source, *([args.definitions] if args.definitions else []),
               *([args.cohorts] if args.cohorts else [])]
    resolved = await resolve_many(ctx, handles)
    source, offset = resolved[0], 1
    definitions = resolved[offset] if args.definitions else None; offset += bool(args.definitions)
    cohorts = resolved[offset] if args.cohorts else None
    process_links = await _process_links(ctx, args.definitions, definitions) if definitions else None
    payloads = await _payloads(ctx, source)
    parsed = parse_maps(payloads.rows, namespace=_namespace(ctx))
    membership_match = match_memberships(source.rows, cohorts.rows if cohorts else [])
    cells, summaries, members, calculation_incomplete = reduce_map_metrics(
        source.rows, parsed, args.metrics, definitions.rows if definitions else [],
        cohorts.rows if cohorts else [], process_links=process_links.rows if process_links else [],
        membership_match=membership_match, coordinate_policy=args.coordinate_policy, grid_id=args.grid_id)
    unmatched_members = membership_match.unmatched_members if cohorts else 0
    if len(cells) > args.max_cells: raise ValueError("Map cell output exceeds max_cells")
    complete = source.complete and payloads.complete and (definitions.complete if definitions else True) and \
        (cohorts.complete if cohorts else True) and not calculation_incomplete and unmatched_members == 0
    unmatched_reason = None
    if cohorts and unmatched_members:
        unmatched_reason = "No cohort members matched the source table" if not members \
            else f"{unmatched_members} cohort membership rows did not match the source table"
    reason = source.missing_reason or payloads.missing_reason or \
        (cohorts.missing_reason if cohorts else None) or unmatched_reason or \
        ("INVALID_BIN_PAIR or ZERO_DENOMINATOR" if calculation_incomplete else None)
    tables = [
        ResultTable(table_id="map_cells", title="Reduced map cells", rows=cells,
            columns=["cohort_id", "lot_cd", "process", "grid_id", "metric_id", "definition_id",
                     "row", "col", "numerator", "denominator", "value", "unit"],
            units={"value": "percent", "numerator": "count", "denominator": "count"},
            complete=complete, missing_reason=reason),
        ResultTable(table_id="map_summary", title="Map reduction summary", rows=summaries,
            columns=["cohort_id", "metric_id", "unique_wafer_count", "measurement_count",
                     "coordinate_count", "die_observations", "pooled_value", "mean_coordinate_value",
                     "unmatched_cells"], units={"pooled_value": "percent", "mean_coordinate_value": "percent"},
            complete=complete, missing_reason=reason),
        ResultTable(table_id="reduction_members", title="Reduction members", rows=members,
            columns=["cohort_id", "measurement_ref", "payload_hash"], complete=complete, missing_reason=reason)]
    limitations = [] if complete else [{"code": "UNMATCHED_JOIN" if unmatched_members else
        "INVALID_BIN_PAIR" if calculation_incomplete else "SOURCE_INCOMPLETE",
        "table_id": "map_cells", "affected_count": unmatched_members or
        sum(row["unmatched_cells"] for row in summaries),
        "requested_count": len(cohorts.rows) if cohorts and unmatched_members else len(source.rows),
        "detail": reason, "source_handle": args.source.model_dump()}]
    origin = source.observation.provenance.get("data_origin", "unknown")
    return build_result(tables, source_result_ids=[handle.result_id for handle in handles],
        limitations=limitations, scope={"source_version": source.actual_version,
            "payload_version": payloads.actual_version, "coordinate_policy": args.coordinate_policy,
            "grid_id": args.grid_id, "unmatched_members": unmatched_members},
        summary=f"저장 맵에서 좌표 지표 {len(cells)}행을 계산했습니다.",
        data_origin=origin)


def register(registry):
    registry.add(ToolSpec("reduce_maps", "저장 맵 측정과 지표 명세 및 선택적 정의·소속 표를 입력받아 좌표별 분자·분모·비율 표를 반환한다. 지표는 최대 25개, 출력은 최대 250,000좌표다.",
                          ReduceMapsInput, reduce_maps, timeout=45))

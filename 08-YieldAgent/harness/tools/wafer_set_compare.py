"""Comparison of two immutable stored metric tables."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ..runtime.domain_process import run_blocking
from ..types import Contract, ResultTable
from ..wafer_set_contracts import TableHandle
from ..wafer_set_core import build_result, resolve_many
from ..wafer_set_statistics import compare_rows
from .registry import ToolSpec


class CompareMetricsInput(Contract):
    baseline: TableHandle = Field(description="기준 저장 지표 표")
    current: TableHandle = Field(description="대상 저장 지표 표")
    keys: list[str] = Field(min_length=1, max_length=8, description="행을 대응시킬 열")
    value_column: str = Field(default="value", min_length=1, max_length=128, description="비교할 값 열")
    methods: list[Literal["difference", "relative_percent", "ratio"]] = Field(
        default_factory=lambda: ["difference"], min_length=1, max_length=3, description="계산할 비교 방식")
    paired: bool = Field(default=False, description="개별 관측 대응 여부")
    pair_keys: list[str] = Field(default_factory=list, max_length=6, description="개별 관측 대응 열")
    missing: Literal["preserve", "error"] = Field(default="preserve", description="대응 없는 행 처리 방식")
    output_shape: Literal["long", "yield_changes"] = Field(default="long", description="출력 표 이름 방식")

    @model_validator(mode="after")
    def valid_methods(self):
        if len(self.methods) != len(set(self.methods)): raise ValueError("Comparison methods must be unique")
        if self.paired != bool(self.pair_keys): raise ValueError("pair_keys are required exactly for paired comparison")
        return self


async def compare_metrics(args, ctx):
    baseline, current = await resolve_many(ctx, [args.baseline, args.current])
    output = await run_blocking(compare_rows, baseline.rows, current.rows, keys=args.keys,
        value_column=args.value_column, methods=args.methods, paired=args.paired,
        pair_keys=args.pair_keys, missing=args.missing)
    complete = baseline.complete and current.complete and not output.incomplete
    reason = baseline.missing_reason or current.missing_reason or ("Unmatched rows or invalid denominators" if output.incomplete else None)
    table_id = "yield_changes" if args.output_shape == "yield_changes" else "comparisons"
    columns = list(dict.fromkeys(key for row in output.rows for key in row)) or [*args.keys,
        "baseline_value", "current_value", "delta", "delta_unit", "relative_percent", "ratio",
        "baseline_n", "current_n", "paired_n", "metric_id", "definition_id", "unit", "better_when",
        "direction", "match_status", "missing_reason"]
    tables = [ResultTable(table_id=table_id, title="Metric comparisons", rows=output.rows,
        columns=columns, complete=complete, missing_reason=reason),
        ResultTable(table_id="comparison_audit", title="Comparison audit", rows=[output.audit],
                    columns=list(output.audit), complete=True, missing_reason=None)]
    limitations = [] if complete else [{"code": "MISSING_REFERENCE", "table_id": table_id,
        "affected_count": output.audit["baseline_only"] + output.audit["current_only"],
        "requested_count": len(output.rows), "detail": reason, "source_handle": args.baseline.model_dump()}]
    origins = {baseline.observation.provenance.get("data_origin", "unknown"),
               current.observation.provenance.get("data_origin", "unknown")}
    origin = origins.pop() if len(origins) == 1 else "unknown"
    return build_result(tables, source_result_ids=[args.baseline.result_id, args.current.result_id],
        limitations=limitations, scope={"baseline_version": baseline.actual_version,
            "current_version": current.actual_version, "methods": args.methods, "paired": args.paired},
        summary=f"기준과 대상에서 비교 행 {len(output.rows)}개를 만들었습니다.", data_origin=origin)


def register(registry):
    registry.add(ToolSpec("compare_metrics", "두 저장 지표 표와 대응 키 및 변화 방식을 입력받아 차이·상대 백분율·비율 중 선택한 비교 표와 감사 표를 반환한다.",
                          CompareMetricsInput, compare_metrics, timeout=30))

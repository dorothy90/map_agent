"""Stored-table chart and pivot rendering tool."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, model_validator

from ..runtime.domain_process import run_blocking
from ..types import Contract, ResultTable
from ..wafer_set_contracts import TableHandle
from ..wafer_set_core import build_result, resolve_many
from ..wafer_set_rendering import (RENDERER_VERSION, chart_png, content_sha256,
                                   pivot_html)
from .registry import ToolSpec


def sha256_bytes(value: bytes) -> str:
    return content_sha256(value)


class ChartSpec(Contract):
    kind: Literal["line", "bar", "scatter", "histogram", "heatmap"] = Field(
        description="표시할 차트 방식")
    x: str = Field(min_length=1, max_length=128, description="가로축 또는 분포 값 열")
    y: str | None = Field(default=None, min_length=1, max_length=128,
                          description="세로축 또는 좌표 열")
    series: str | None = Field(default=None, min_length=1, max_length=128,
                               description="계열을 구분할 열")
    facet: str | None = Field(default=None, min_length=1, max_length=128,
                              description="그림을 나눌 열")
    value: str | None = Field(default=None, min_length=1, max_length=128,
                              description="좌표 셀에 표시할 값 열")
    bins: int = Field(default=30, ge=1, le=200, description="분포 구간 수")

    @model_validator(mode="after")
    def valid_shape(self):
        if self.kind == "heatmap" and (self.y is None or self.value is None):
            raise ValueError("heatmap requires y and value")
        if self.kind not in ("histogram", "heatmap") and self.y is None:
            raise ValueError("chart requires y")
        if self.kind != "heatmap" and self.value is not None:
            raise ValueError("value belongs to heatmap")
        return self


class RenderViewInput(Contract):
    source: TableHandle = Field(description="표시할 저장 표 핸들")
    view_type: Literal["chart", "pivot_table"] = Field(description="표시 결과 형식")
    chart: ChartSpec | None = Field(default=None, description="차트 열과 표시 방식")
    columns_source: TableHandle | None = Field(default=None, description="선택적 열 메타데이터 표")
    changes_source: TableHandle | None = Field(default=None, description="선택적 비교 결과 표")
    title: str = Field(default="", max_length=160, description="표시 제목")
    display_precision: int = Field(default=3, ge=0, le=10, description="숫자 표시 소수 자릿수")
    emit_html: bool = Field(default=False, description="표의 HTML 파일 생성 여부")
    max_rows: int = Field(default=10000, ge=1, le=20000, description="표시에 허용할 최대 행 수")

    @model_validator(mode="after")
    def matching_view(self):
        if (self.view_type == "chart") != (self.chart is not None):
            raise ValueError("chart configuration must match view_type")
        if self.view_type == "chart" and self.emit_html:
            raise ValueError("emit_html belongs to pivot_table")
        return self


def _required_columns(args: RenderViewInput) -> list[str]:
    if not args.chart:
        return []
    return list(dict.fromkeys(value for value in (args.chart.x, args.chart.y,
        args.chart.series, args.chart.facet, args.chart.value) if value is not None))


async def render_view(args: RenderViewInput, ctx):
    handles = [args.source, *(h for h in (args.columns_source, args.changes_source) if h)]
    resolved = await resolve_many(ctx, handles)
    source = resolved[0]
    if len(source.rows) > args.max_rows:
        raise ValueError("Source row limit exceeded; filter or aggregate before rendering")
    columns = _required_columns(args)
    unknown = [column for column in columns if column not in source.columns]
    if unknown:
        raise ValueError(f"Unknown chart column: {unknown[0]}")
    title = args.title or ("Chart" if args.view_type == "chart" else "Table")
    artifacts = []
    chart_kind = args.chart.kind if args.chart else None
    if args.view_type == "chart":
        payload = await run_blocking(chart_png, source.rows, args.chart, title=title)
        artifacts.append({"title": title, "mime": "image/png", "data": payload})
    elif args.emit_html:
        payload = pivot_html(source.rows, source.columns, title=title,
                             precision=args.display_precision)
        artifacts.append({"title": title, "mime": "text/html", "data": payload})
    artifact_hash = content_sha256(artifacts[0]["data"]) if artifacts else None
    comparison = args.changes_source.model_dump() if args.changes_source else None
    manifest = {"source_result_id": args.source.result_id,
        "source_table_id": args.source.table_id, "source_version": source.actual_version,
        "source_content_sha256": content_sha256(source.rows), "renderer_version": RENDERER_VERSION,
        "view_type": args.view_type, "chart_kind": chart_kind,
        "columns_used_json": json.dumps(columns, ensure_ascii=False),
        "source_rows": len(source.rows), "rows_used": len(source.rows), "omitted_rows": 0,
        "display_precision": args.display_precision,
        "comparison_handle_json": json.dumps(comparison, sort_keys=True) if comparison else None,
        "artifact_index": 0 if artifacts else None, "artifact_title": title if artifacts else None,
        "artifact_content_sha256": artifact_hash}
    complete = all(item.complete for item in resolved)
    reason = next((item.missing_reason for item in resolved if not item.complete), None)
    table = ResultTable(table_id="render_manifest", title="Render manifest", rows=[manifest],
        columns=list(manifest), complete=complete, missing_reason=reason)
    origins = {item.observation.provenance.get("data_origin", "unknown") for item in resolved}
    origin = origins.pop() if len(origins) == 1 else "unknown"
    return build_result([table], source_result_ids=[handle.result_id for handle in handles],
        scope={"source_version": source.actual_version}, artifacts=artifacts,
        summary=f"저장 표 {len(source.rows)}행을 {args.view_type} 형식으로 표시했습니다.",
        data_origin=origin)


def register(registry):
    registry.add(ToolSpec("render_view", "저장 표와 열·표시 설정을 입력받아 차트 이미지 또는 표 보기와 원본 해시가 포함된 manifest를 반환한다. 입력은 최대 20,000행이다.",
                          RenderViewInput, render_view, read_only=False, effect="artifact", timeout=30))

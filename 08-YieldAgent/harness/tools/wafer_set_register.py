"""Assemble the implemented wafer-table tools behind the opt-in flag."""
from __future__ import annotations

from dataclasses import replace
from functools import partial

from . import (wafer_set_aggregate, wafer_set_compare, wafer_set_discovery,
               wafer_set_facts, wafer_set_members, wafer_set_metrics,
               wafer_set_reduce, wafer_set_tools, wafer_set_views)
from .wafer_set_export import CompatibleExportInput, dispatch_export
from .wafer_set_python import CompatiblePythonInput, dispatch_python
from .wafer_set_read import CompatibleReadInput, dispatch_read

WAFER_SET_TOOL_NAMES = {
    "describe_sources", "list_values", "inspect_coverage", "select_wafers",
    "read_facts", "join_facts", "filter_set", "make_cohorts", "rank_members",
    "compute_metrics", "aggregate_metrics", "reduce_maps", "compare_metrics",
    "render_maps", "render_view", "export_report", "read_result", "run_python",
}
REPLACES = {
    "describe_sources": ("inspect_yield_coverage",),
    "inspect_coverage": ("inspect_yield_coverage",),
    "select_wafers": (
        "query_wafer_yield", "query_yield", "query_defect_yield",
    ),
    "read_facts": (
        "query_wafer_yield", "query_yield", "query_defect_yield",
    ),
    "compute_metrics": (
        "query_wafer_yield", "query_yield", "query_defect_yield",
        "analyze_yield",
    ),
    "aggregate_metrics": ("analyze_yield",),
    "compare_metrics": ("analyze_yield",),
    "render_maps": ("render_wafer_map",),
    "reduce_maps": ("compare_wafer_maps",),
    "render_view": ("render_yield_scatter",),
}
LIVE_EVAL_TOOL_NAMES = WAFER_SET_TOOL_NAMES - {"export_report", "run_python"}
TOOL_EFFECTS = {name: ("artifact" if name in {"render_view", "export_report", "run_python"}
                      else "read") for name in WAFER_SET_TOOL_NAMES}


def register(registry):
    for name, spec in list(registry.tools.items()):
        registry.tools[name] = replace(spec, group="legacy")
    registry.hidden_groups.add("legacy")

    wafer_set_tools.register(registry)
    wafer_set_discovery.register(registry)
    wafer_set_facts.register(registry)
    wafer_set_members.register(registry)
    wafer_set_metrics.register(registry)
    wafer_set_aggregate.register(registry)
    wafer_set_reduce.register(registry)
    wafer_set_compare.register(registry)
    wafer_set_views.register(registry)

    original = registry.tools["read_result"]
    registry.tools["read_result"] = replace(
        original,
        description=original.description + " 저장 표 핸들을 입력하면 최대 1,000행·1 MiB의 원본 페이지와 페이지 메타데이터를 반환한다.",
        schema=CompatibleReadInput,
        handler=partial(dispatch_read, legacy_handler=original.handler, legacy_schema=original.schema),
    )
    original = registry.tools["export_report"]
    registry.tools["export_report"] = replace(
        original,
        description=original.description + " 저장 표·파일 핸들을 입력하면 선택 내용을 최대 200슬라이드의 PPTX와 내보내기 manifest로 반환한다.",
        schema=CompatibleExportInput,
        handler=partial(dispatch_export, legacy_handler=original.handler, legacy_schema=original.schema),
    )
    original = registry.tools["run_python"]
    registry.tools["run_python"] = replace(
        original,
        description=original.description + " 저장 표 핸들과 코드를 입력하면 최대 10개·총 250,000행의 표와 최대 3개 차트 및 실행 manifest를 반환한다.",
        schema=CompatiblePythonInput,
        handler=partial(dispatch_python, legacy_handler=original.handler, legacy_schema=original.schema),
    )

    for name in WAFER_SET_TOOL_NAMES:
        registry.tools[name] = replace(
            registry.tools[name],
            group="wafer_set",
            replaces=REPLACES.get(name, ()),
        )


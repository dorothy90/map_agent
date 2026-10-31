"""Discovery tools for allowlisted sources and immutable result tables."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from typing import Literal

from pydantic import Field, model_validator

from ..runtime.domain_process import run_blocking
from ..types import Contract, ResultTable
from ..wafer_set_catalog import RELATIONS, SOURCES, physical_name, source_spec
from ..wafer_set_contracts import SourceQuery, TableHandle
from ..wafer_set_core import build_result, resolve_handle
from ..wafer_set_predicates import EMPTY_FILTER_HINT, empty_filter_limitation, has_string_filter
from ..wafer_set_sql import (inspect_catalog, normalize_source_query, query_coverage,
                             query_distinct_values, query_link_keys)
from ..wafer_set_symbols import load_definition_snapshot
from .registry import ToolSpec


class DescribeSourcesInput(Contract):
    source_ids: list[str] = Field(default_factory=list, max_length=20, description="설명할 소스 식별자")
    lot_cd: list[str] = Field(default_factory=list, max_length=20, description="정의 범위를 제한할 제품 식별자")
    process: list[str] = Field(default_factory=list, max_length=20, description="정의 범위를 제한할 공정 식별자")
    include_definitions: bool = Field(default=True,
        description="계산 정의 포함 여부. False이면 metric_definitions는 빈 목록이며 question_guides는 유지됩니다.")
    refresh: bool = Field(default=False, description="캐시를 사용하지 않고 정의를 다시 읽을지 여부")


class ListValuesInput(Contract):
    source_id: str | None = Field(default=None, description="조회할 등록 소스 식별자")
    source: TableHandle | None = Field(default=None, description="조회할 저장 표 핸들")
    field: str = Field(min_length=1, max_length=128, description="고유값을 셀 열")
    query: SourceQuery | None = Field(default=None, description="등록 소스의 선택적 범위")
    include_null: bool = Field(default=True, description="누락값 그룹 포함 여부")
    order: Literal["count_desc", "value_asc"] = Field(default="count_desc", description="결과 정렬 방식")
    limit: int = Field(default=100, ge=1, le=1000, description="반환할 최대 고유값 수")

    @model_validator(mode="after")
    def one_source(self):
        if (self.source_id is None) == (self.source is None):
            raise ValueError("Supply exactly one source path")
        if self.source is not None and self.query is not None:
            raise ValueError("query is available only for registered sources. query와 query.where 필터는 source_id와 함께 사용하세요. 기존 표 핸들은 filter_set 또는 read_result로 처리하세요.")
        return self


DEFAULT_CHECKS = ["rows", "nulls", "duplicates", "time_range"]


class InspectCoverageInput(Contract):
    source_id: str | None = Field(default=None, description="검사할 등록 소스 식별자")
    source: TableHandle | None = Field(default=None, description="검사할 저장 표 핸들")
    query: SourceQuery | None = Field(default=None, description="등록 소스의 선택적 범위")
    columns: list[str] = Field(default_factory=list, max_length=20, description="누락을 검사할 열")
    key_columns: list[str] = Field(default_factory=list, max_length=6, description="중복을 검사할 키 열")
    time_column: str | None = Field(default=None, min_length=1, max_length=128,
                                    description="범위를 검사할 시각 열")
    checks: list[Literal["rows", "nulls", "duplicates", "time_range", "links"]] = Field(
        default_factory=lambda: list(DEFAULT_CHECKS), description="수행할 검사")
    against: TableHandle | None = Field(default=None, description="연결률의 비교 저장 표 핸들")
    relation_id: str | None = Field(default=None, description="연결률에 사용할 등록 관계")

    @model_validator(mode="after")
    def valid_paths(self):
        if (self.source_id is None) == (self.source is None):
            raise ValueError("Supply exactly one source path")
        if self.source is not None and self.query is not None:
            raise ValueError("query is available only for registered sources. query와 query.where 필터는 source_id와 함께 사용하세요. 기존 표 핸들은 filter_set 또는 read_result로 처리하세요.")
        if "links" in self.checks and (self.against is None or self.relation_id is None):
            raise ValueError("links requires against and relation_id")
        if "links" not in self.checks and (self.against is not None or self.relation_id is not None):
            raise ValueError("against and relation_id are links-only")
        return self


def _general_table(table_id, title, rows, columns, *, complete=True, reason=None, units=None):
    return ResultTable(table_id=table_id, title=title, rows=rows, columns=columns, units=units or {},
                       complete=complete, missing_reason=reason)


async def describe_sources(args, ctx):
    selected = args.source_ids or list(SOURCES)
    for source_id in selected:
        source_spec(source_id)
    metadata = await run_blocking(inspect_catalog, selected)
    snapshot = await run_blocking(load_definition_snapshot, args.lot_cd, args.process, args.refresh, selected)
    definitions = {**snapshot,
        "metric_definitions": snapshot.get("metric_definitions", []) if args.include_definitions else []}
    definition_version = definitions.get("definition_version")
    if args.include_definitions and definition_version is None:
        definition_version = hashlib.sha256(json.dumps({
            "metric_definitions": definitions["metric_definitions"],
            "process_links": definitions["process_links"]}, sort_keys=True,
            ensure_ascii=False, default=str).encode()).hexdigest()[:16]
    source_rows, column_rows = [], []
    reasons = []
    for source_id in selected:
        spec = source_spec(source_id)
        state = metadata[source_id]
        state_reason = state.get("missing_reason")
        if not state["available"]:
            reasons.append(f"{source_id}: {state_reason or 'unavailable'}")
        source_rows.append({"source_id": source_id, "physical_name": physical_name(spec), "grain": spec.grain,
            "available": state["available"], "time_columns_json": json.dumps(spec.time_columns),
            "key_columns_json": json.dumps(spec.key_columns),
            "semantic_status": "available" if state["available"] else "unavailable",
            "missing_reason": state_reason})
        for public, physical in spec.columns.items():
            actual = state["columns"].get(physical.upper(), {}) if physical is not None else {}
            binary = public in spec.binary_columns
            column_rows.append({"source_id": source_id, "column": public, "physical_column": physical,
                "data_type": actual.get("data_type"), "nullable": actual.get("nullable"),
                "unit": _column_unit(source_id, public, snapshot.get("metric_definitions", [])),
                "semantic_status": "derived" if physical is None else "available" if actual else "unavailable",
                "filter_ops_json": json.dumps([] if binary or physical is None else
                    ["eq", "ne", "lt", "le", "gt", "ge", "in", "not_in", "between", "is_null", "not_null"])})
    complete = not reasons
    reason = "; ".join(reasons) or None
    tables = [
        _general_table("sources", "Sources", source_rows,
            ["source_id", "physical_name", "grain", "available", "time_columns_json", "key_columns_json",
             "semantic_status", "missing_reason"], complete=complete, reason=reason),
        _general_table("columns", "Columns", column_rows,
            ["source_id", "column", "physical_column", "data_type", "nullable", "unit",
             "semantic_status", "filter_ops_json"], complete=complete, reason=reason),
        _general_table("relations", "Relations", list(RELATIONS),
            ["relation_id", "left_grain", "right_grain", "keys_json", "cardinality", "temporal_policy", "confidence"]),
        _general_table("metric_definitions", "Metric definitions", definitions["metric_definitions"],
            ["definition_id", "source_id", "lot_cd", "process", "parameter", "mode",
             "regex_pattern", "param_kind", "unit", "value_basis", "better_when", "included",
             "display_order", "definition_version"]),
        _general_table("process_links", "Process links", definitions["process_links"],
            ["source_id", "source_process", "definition_process", "mapping_basis", "mapping_version"]),
        _general_table("question_guides", "Question guides", definitions.get("question_guides", []),
            ["source_id", "question_type", "guidance"]),
    ]
    limitations = [{"code": "SOURCE_INCOMPLETE", "table_id": "sources",
        "affected_count": len(reasons), "requested_count": len(selected), "detail": reason,
        "source_handle": None}] if reasons else []
    limitations.extend({"code": item["code"], "table_id": "metric_definitions",
        "affected_count": None, "requested_count": None, "detail": item.get("detail"),
        "source_handle": None} for item in definitions.get("metadata_limitations", []))
    return build_result(tables, limitations=limitations,
        summary=f"등록 소스 {len(selected)}개와 열·관계·정의를 확인했습니다.",
        data_origin="live", scope={"source_ids": selected, "definition_version": definition_version})


def _column_unit(source_id, public, definitions):
    if public != "value" or source_id != "wafer_parameter":
        return "unknown"
    units = {row.get("unit") for row in definitions
             if row.get("source_id") == source_id and row.get("unit")}
    if len(units) == 1:
        return units.pop()
    return "per_row_unit" if units else "unspecified"


def _value_key(value):
    return (value is None, type(value).__name__, str(value))


def _stored_values(rows, field, include_null, order, limit):
    typed = {}
    for row in rows:
        value = row.get(field)
        key = (type(value).__name__, json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
        if key not in typed:
            typed[key] = [value, 0]
        typed[key][1] += 1
    null_count = next((count for (kind, _), (_, count) in typed.items() if kind == "NoneType"), 0)
    items = [(value, count) for value, count in typed.values() if include_null or value is not None]
    if order == "count_desc":
        items.sort(key=lambda item: (-item[1], _value_key(item[0])))
    else:
        items.sort(key=lambda item: _value_key(item[0]))
    return {"rows": [{"value": value, "count": count, "is_null": value is None}
                     for value, count in items[:limit]], "source_row_count": len(rows),
            "distinct_count": len(items), "null_count": null_count}


async def list_values(args, ctx):
    source_ids, origin, source_complete, source_reason = [], "live", True, None
    requested_query = args.query
    effective_query = args.query
    notices = []
    if args.source is not None:
        resolved = await resolve_handle(ctx, args.source)
        if args.field not in resolved.columns:
            raise ValueError(f"Unknown field: {args.field}")
        data = _stored_values(resolved.rows, args.field, args.include_null, args.order, args.limit)
        source_ids = [args.source.result_id]
        origin = resolved.observation.provenance.get("data_origin", "unknown")
        source_complete, source_reason = resolved.complete, resolved.missing_reason
        source_version = resolved.actual_version
    else:
        data = await run_blocking(query_distinct_values, args.source_id, args.field, effective_query,
                                  args.include_null, args.order, args.limit)
        if data["source_row_count"] == 0 and args.query is not None:
            normalized, notices = await run_blocking(normalize_source_query, args.source_id, args.query)
            if normalized != args.query:
                effective_query = normalized
                data = await run_blocking(query_distinct_values, args.source_id, args.field, effective_query,
                                          args.include_null, args.order, args.limit)
        source_version = None
    returned = len(data["rows"])
    omitted = max(0, data["distinct_count"] - returned)
    values_complete = source_complete and omitted == 0
    value_reason = source_reason or (f"{omitted} distinct values omitted" if omitted else None)
    summary = {"source_row_count": data["source_row_count"], "distinct_count": data["distinct_count"],
        "returned_distinct_count": returned, "omitted_distinct_count": omitted,
        "null_count": data["null_count"], "exact": True}
    tables = [_general_table("values", "Values", data["rows"], ["value", "count", "is_null"],
        complete=values_complete, reason=value_reason),
        _general_table("value_summary", "Value summary", [summary], list(summary),
                       complete=source_complete, reason=source_reason)]
    limitations = list(notices)
    if not values_complete:
        limitations.append({"code": "SOURCE_INCOMPLETE" if not source_complete else "ROW_LIMIT",
            "table_id": "values", "affected_count": omitted, "requested_count": data["distinct_count"],
            "detail": value_reason, "source_handle": args.source.model_dump() if args.source else None})
    result_summary = f"고유값 {returned}개를 반환했습니다."
    if args.source_id is not None and data["source_row_count"] == 0 and has_string_filter(effective_query):
        result_summary += " " + EMPTY_FILTER_HINT
        limitations.append(empty_filter_limitation("values", 0))
    return build_result(tables, source_result_ids=source_ids, limitations=limitations,
        scope={"field": args.field, "source_version": source_version,
               "requested_query": requested_query.model_dump(mode="json") if requested_query else None,
               "effective_query": effective_query.model_dump(mode="json") if effective_query else None},
        summary=result_summary, data_origin=origin)


def _stored_coverage(rows, columns, key_columns, time_column, checks, population_complete, reason):
    total, output = len(rows), []
    common = {"denominator": total, "exact": True, "population_complete": population_complete,
              "missing_reason": reason}
    if "rows" in checks:
        output.append({"check": "rows", "field": "*", "numerator": total, "value": total,
                       "minimum": None, "maximum": None, **common})
    if "nulls" in checks:
        for field in columns:
            count = sum(row.get(field) is None for row in rows)
            output.append({"check": "nulls", "field": field, "numerator": count,
                           "value": count / total if total else None, "minimum": None, "maximum": None, **common})
    if "duplicates" in checks and key_columns:
        frequencies = Counter(tuple(row.get(key) for key in key_columns) for row in rows)
        groups = sum(count > 1 for count in frequencies.values())
        duplicate_rows = sum(max(0, count - 1) for count in frequencies.values())
        label = ",".join(key_columns)
        for check, count in (("duplicate_groups", groups), ("duplicate_rows", duplicate_rows)):
            output.append({"check": check, "field": label, "numerator": count, "value": count,
                           "minimum": None, "maximum": None, **common})
    if "time_range" in checks and time_column:
        values = [row.get(time_column) for row in rows if row.get(time_column) is not None]
        output.append({"check": "time_range", "field": time_column, "numerator": None, "value": None,
                       "minimum": min(values) if values else None, "maximum": max(values) if values else None, **common})
    return output


async def inspect_coverage(args, ctx):
    source_ids, origin, source_version = [], "live", None
    requested_query = args.query
    effective_query = args.query
    notices = []
    if args.source is not None:
        resolved = await resolve_handle(ctx, args.source)
        requested = [*args.columns, *args.key_columns, *([args.time_column] if args.time_column else [])]
        if any(field not in resolved.columns for field in requested):
            raise ValueError("Unknown coverage column")
        rows = _stored_coverage(resolved.rows, args.columns, args.key_columns, args.time_column,
                                args.checks, resolved.complete, resolved.missing_reason)
        source_ids, source_version = [args.source.result_id], resolved.actual_version
        origin = resolved.observation.provenance.get("data_origin", "unknown")
        complete, reason = resolved.complete, resolved.missing_reason
        source_population_count = len(resolved.rows)
    else:
        coverage_checks = list(dict.fromkeys([*args.checks, "rows"]))
        rows = await run_blocking(query_coverage, args.source_id, effective_query, args.columns,
                                  args.key_columns, args.time_column, coverage_checks)
        population_count = next((int(row.get("denominator") or 0) for row in rows
                                 if row.get("check") == "rows"), 0)
        if population_count == 0 and args.query is not None:
            normalized, notices = await run_blocking(normalize_source_query, args.source_id, args.query)
            if normalized != args.query:
                effective_query = normalized
                rows = await run_blocking(query_coverage, args.source_id, effective_query, args.columns,
                                          args.key_columns, args.time_column, coverage_checks)
                population_count = next((int(row.get("denominator") or 0) for row in rows
                                         if row.get("check") == "rows"), 0)
        source_population_count = population_count
        if "rows" not in args.checks:
            rows = [row for row in rows if row.get("check") != "rows"]
        for row in rows:
            row.update(population_complete=True, missing_reason=None)
        complete, reason = True, None
    if "links" in args.checks:
        relation = next((item for item in RELATIONS if item["relation_id"] == args.relation_id), None)
        if relation is None:
            raise ValueError("Unknown relation_id")
        against = await resolve_handle(ctx, args.against)
        against_origin = against.observation.provenance.get("data_origin", "unknown")
        origin = "fixture" if "fixture" in (origin, against_origin) else \
            "live" if origin == against_origin == "live" else "unknown"
        keys = json.loads(relation["keys_json"])
        if any(key not in against.columns for key in keys):
            raise ValueError("Relation keys are unavailable")
        if args.source is not None:
            if any(key not in resolved.columns for key in keys):
                raise ValueError("Relation keys are unavailable")
            left_keys = [tuple(row.get(key) for key in keys) for row in resolved.rows]
        else:
            left_keys = await run_blocking(query_link_keys, args.source_id, effective_query, keys)
        right = {tuple(row.get(key) for key in keys) for row in against.rows
                 if all(row.get(key) is not None for key in keys)}
        numerator = sum(key in right for key in left_keys if all(value is not None for value in key))
        denominator = len(left_keys)
        complete = complete and against.complete
        reason = reason or against.missing_reason
        rows.append({"check": "links", "field": args.relation_id, "numerator": numerator,
            "denominator": denominator, "value": numerator / denominator if denominator else None,
            "minimum": None, "maximum": None, "exact": True, "population_complete": complete,
            "missing_reason": reason})
        source_ids.append(args.against.result_id)
    columns = ["check", "field", "numerator", "denominator", "value", "minimum", "maximum",
               "exact", "population_complete", "missing_reason"]
    table = _general_table("coverage", "Coverage", rows, columns, complete=complete, reason=reason)
    limitations = list(notices)
    limitations += [{"code": "SOURCE_INCOMPLETE", "table_id": "coverage", "affected_count": len(rows),
        "requested_count": len(rows), "detail": reason, "source_handle": args.source.model_dump()}] \
        if not complete else []
    result_summary = f"커버리지 검사 {len(rows)}개를 반환했습니다."
    if args.source_id is not None and source_population_count == 0 and has_string_filter(effective_query):
        result_summary += " " + EMPTY_FILTER_HINT
        limitations.append(empty_filter_limitation("coverage", 0))
    return build_result([table], source_result_ids=source_ids, limitations=limitations,
        scope={"source_version": source_version,
               "requested_query": requested_query.model_dump(mode="json") if requested_query else None,
               "effective_query": effective_query.model_dump(mode="json") if effective_query else None},
        summary=result_summary,
        data_origin=origin)


def register(registry):
    registry.add(ToolSpec("describe_sources",
        "소스 식별자와 제품·공정 범위 및 정의 포함 여부를 입력받아 등록 데이터 소스의 열·관계·계산 정의와 사용 가능 상태 표를 반환한다. 소스 식별자·제품·공정은 각각 최대 20개다.",
        DescribeSourcesInput, describe_sources, timeout=30, eager=True))
    registry.add(ToolSpec("list_values",
        "등록 소스 식별자 또는 저장 표 핸들과 열을 입력받아 고유값·빈도·요약 표를 반환한다. 고유값은 최대 1,000개다.",
        ListValuesInput, list_values, timeout=20))
    registry.add(ToolSpec("inspect_coverage",
        "등록 소스 식별자 또는 저장 표 핸들과 검사 항목을 입력받아 행·누락·중복·시각 범위 및 선택적 연결률 표를 반환한다.",
        InspectCoverageInput, inspect_coverage, timeout=30))


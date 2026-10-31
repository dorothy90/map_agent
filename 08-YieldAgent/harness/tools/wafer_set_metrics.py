"""Metric calculation over immutable stored measurements and facts."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Literal

from pydantic import Field, model_validator

from ..types import Contract, ResultTable
from ..wafer_set_arrays import compute_map_metrics, parse_maps
from ..wafer_set_contracts import TableHandle
from ..wafer_set_core import build_result, match_memberships, resolve_handle, resolve_many
from .registry import ToolSpec


class MetricSpec(Contract):
    metric_id: str = Field(min_length=1, max_length=100, description="출력 지표 식별자")
    mode: Literal["stored_selection", "bin", "symbol", "source_value"] = Field(description="계산 입력 방식")
    quantity: Literal["pass_rate", "fail_rate", "fail_yield", "value"] = Field(description="계산할 수량")
    bin_side: Literal["left_bin", "right_bin"] | None = Field(default=None, description="판정에 사용할 값 위치")
    pass_bin: str | None = Field(default=None, description="통과로 계산할 값")
    parameters: list[str] = Field(default_factory=list, max_length=25, description="정의 기반 계산 항목")
    combine: Literal["separate", "union"] = Field(default="separate", description="여러 정의의 결합 방식")
    value_column: str | None = Field(default=None, min_length=1, max_length=128, description="원본 값 열")
    better_when: Literal["higher", "lower", "neutral", "unknown"] = Field(
        default="unknown", description="값의 선호 방향")

    @model_validator(mode="after")
    def valid_mode(self):
        if self.mode == "bin" and (self.bin_side is None or self.pass_bin is None):
            raise ValueError("bin mode requires a value position and pass value")
        if self.mode == "symbol" and not self.parameters:
            raise ValueError("symbol mode requires parameters")
        if self.mode == "source_value" and self.value_column is None:
            raise ValueError("source_value mode requires value_column. metrics 항목에 value_column=<입력 표의 숫자 열 이름>을 지정하고 quantity='value'를 사용하세요.")
        if self.mode != "bin" and (self.bin_side is not None or self.pass_bin is not None):
            raise ValueError("bin fields are available only in bin mode")
        if self.mode != "symbol" and self.parameters:
            raise ValueError("parameters are available only in symbol mode")
        if self.mode != "source_value" and self.value_column is not None:
            raise ValueError("value_column is available only in source_value mode")
        if self.mode != "symbol" and self.combine != "separate":
            raise ValueError("union combination is available only in symbol mode")
        if self.mode == "source_value" and self.quantity != "value":
            raise ValueError("source_value mode requires value quantity")
        if self.mode != "source_value" and self.quantity == "value":
            raise ValueError("value quantity requires source_value mode")
        return self


class ComputeMetricsInput(Contract):
    source: TableHandle = Field(description="입력 측정 또는 사실 표")
    metrics: list[MetricSpec] = Field(min_length=1, max_length=25, description="계산할 지표 명세")
    definitions: TableHandle | None = Field(default=None, description="저장된 지표 정의 표")
    cohorts: TableHandle | None = Field(default=None, description="선택적 저장 소속 표")
    max_rows: int = Field(default=100_000, ge=1, le=250_000, description="허용할 최대 출력 행 수")

    @model_validator(mode="after")
    def unique_metrics(self):
        names = [item.metric_id for item in self.metrics]
        if len(names) != len(set(names)): raise ValueError("metric_id values must be unique")
        if any(item.mode == "symbol" for item in self.metrics) and self.definitions is None:
            raise ValueError("symbol metrics require stored definitions")
        return self


def _namespace(ctx):
    run = ctx.run
    return (run["principal_id"], run["session_id"], run.get("run_id"), run.get("epoch"))


def _definition_subset(rows, source_row, spec, process_links=None):
    selected = [row for row in rows if row.get("parameter") in spec.parameters
                and row.get("lot_cd") == source_row.get("lot_cd")]
    target_process = source_row.get("process")
    links = [row for row in (process_links or []) if row.get("source_process") == target_process]
    if len({row.get("definition_process") for row in links}) > 1:
        raise ValueError("Stored process relation is ambiguous")
    if links: target_process = links[0].get("definition_process")
    selected = [row for row in selected if row.get("process") == target_process]
    for parameter in spec.parameters:
        matches = [row for row in selected if row.get("parameter") == parameter]
        if len(matches) != 1:
            raise ValueError(f"Stored definitions are missing or ambiguous for parameter: {parameter}")
    return selected


def _base(row, cohort_id):
    return {"member_id": row.get("member_id"), "measurement_ref": row.get("measurement_ref"),
        "fact_id": row.get("fact_id"), "cohort_id": cohort_id, "lot_id": row.get("lot_id"),
        "wf_id": row.get("wf_id"), "lot_cd": row.get("lot_cd"), "process": row.get("process"),
        "time_value": row.get("time_value") or row.get("measurement_start")}


def _stored_metric(row, spec):
    denominator = row.get("die_count")
    passed = row.get("pass_die_count")
    if denominator is None or passed is None:
        raise ValueError("Stored selection is missing count columns")
    failure_quantity = spec.quantity == "fail_rate"
    if failure_quantity and row.get("calc_mode") != "symbol":
        raise ValueError(
            "Failure metrics require symbol definitions. "
            "Reselect with symbol_bin_parameters and use that handle."
        )
    failed = denominator - passed if row.get("calc_mode") == "symbol" else None
    numerator = failed if failure_quantity else passed
    value = None if denominator == 0 else 100.0 * numerator / denominator
    raw = {"mode": spec.mode, "quantity": spec.quantity, "source": row.get("calc_mode"),
           "pass_bin": row.get("calc_pass_bin"), "bin_side": row.get("calc_bin_side"),
           "parameters": row.get("calc_symbol_parameters")}
    definition = "stored:" + hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {"metric_id": spec.metric_id, "definition_id": definition, "value": value,
        "numerator": numerator, "denominator": denominator, "unit": "percent",
        "pass_die_count": passed, "fail_die_count": failed,
        "unmatched_cells": row.get("unmatched_cells", 0), "better_when": spec.better_when}


def _source_metric(row, spec, units):
    if spec.value_column not in row: raise ValueError(f"Unknown source value column: {spec.value_column}")
    value = row.get(spec.value_column)
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                              or not math.isfinite(value)):
        raise ValueError("Source metric value must be a finite number")
    declared_unit = units.get(spec.value_column)
    unit = row.get("unit", "unspecified") if declared_unit in (None, "per_row_unit") else declared_unit
    direction = row.get("better_when", "unknown") if spec.better_when == "unknown" else spec.better_when
    raw = {"mode": spec.mode, "column": spec.value_column, "parameter": row.get("parameter"),
           "unit": unit, "better_when": direction}
    definition = "source:" + hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()[:16]
    return {"metric_id": spec.metric_id, "definition_id": definition, "value": value,
        "numerator": None, "denominator": None, "unit": unit, "pass_die_count": None,
        "fail_die_count": None, "unmatched_cells": 0, "better_when": direction,
        "source_id": row.get("source_id"), "parameter": row.get("parameter"),
        "param_kind": row.get("param_kind"), "value_basis": row.get("value_basis")}


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


async def _process_links(ctx, handle, resolved):
    if not any(table.table_id == "process_links" for table in resolved.observation.tables): return None
    version = resolved.observation.scope.get("table_versions", {}).get("process_links")
    return await resolve_handle(ctx, TableHandle(result_id=handle.result_id,
                                                  table_id="process_links", version=version))


async def compute_metrics(args, ctx):
    handles = [args.source, *([args.definitions] if args.definitions else []),
               *([args.cohorts] if args.cohorts else [])]
    resolved = await resolve_many(ctx, handles)
    source, offset = resolved[0], 1
    definitions = resolved[offset] if args.definitions else None; offset += bool(args.definitions)
    cohorts = resolved[offset] if args.cohorts else None
    process_links = await _process_links(ctx, args.definitions, definitions) if definitions else None
    map_specs = [spec for spec in args.metrics if spec.mode in {"bin", "symbol"}]
    payloads = await _payloads(ctx, source) if map_specs else None
    parsed = parse_maps(payloads.rows, namespace=_namespace(ctx)) if payloads else {}
    membership_match = match_memberships(source.rows, cohorts.rows if cohorts else [])
    output, definition_output, incomplete = [], {}, False
    seen = set()
    for index, row in enumerate(source.rows):
        identity = membership_match.identities_by_row[index]
        cohort_ids = membership_match.cohorts_by_row[index] if cohorts else [None]
        for cohort_id in cohort_ids:
            if (identity, cohort_id) in seen: continue
            seen.add((identity, cohort_id))
            for spec in args.metrics:
                if spec.mode in {"bin", "symbol"}:
                    parsed_map = parsed.get(row.get("payload_sha256")) or parsed.get(row.get("payload_hash"))
                    if parsed_map is None: raise ValueError("Stored payload reference is unavailable")
                    definition_rows = _definition_subset(definitions.rows, row, spec,
                        process_links.rows if process_links else []) if spec.mode == "symbol" else []
                    calculated, partial = compute_map_metrics(parsed_map, [spec], definition_rows)
                    incomplete |= partial
                elif spec.mode == "stored_selection":
                    calculated = [_stored_metric(row, spec)]
                    incomplete |= calculated[0]["value"] is None or bool(calculated[0]["unmatched_cells"])
                else:
                    calculated = [_source_metric(row, spec, source.units)]
                    incomplete |= calculated[0]["value"] is None
                for item in calculated:
                    if len(output) >= args.max_rows: raise ValueError("Metric output exceeds max_rows")
                    output.append({**_base(row, cohort_id), **item,
                        **{key: row.get(key) for key in ("calc_mode", "calc_pass_bin", "calc_bin_side",
                                                        "calc_symbol_parameters") if key in row}})
                    definition_output[item["definition_id"]] = {"definition_id": item["definition_id"],
                        "metric_id": item["metric_id"], "formula": spec.quantity, "unit": item["unit"],
                        "definition_version": item["definition_id"].split(":")[-1],
                        "parameters_json": json.dumps(spec.parameters, ensure_ascii=False),
                        "bin_side": spec.bin_side, "pass_bin": spec.pass_bin,
                        "denominator_policy": "valid_observed_cells" if spec.mode in {"bin", "symbol"} else None,
                        **{key: item.get(key) for key in
                           ("source_id", "parameter", "param_kind", "value_basis", "better_when")}}
    unmatched_members = membership_match.unmatched_members if cohorts else 0
    complete = source.complete and (definitions.complete if definitions else True) and \
        (cohorts.complete if cohorts else True) and not incomplete and unmatched_members == 0
    unmatched_reason = None
    if cohorts and unmatched_members:
        unmatched_reason = "No cohort members matched the source table" if not membership_match.matched_members \
            else f"{unmatched_members} cohort membership rows did not match the source table"
    reason = source.missing_reason or (cohorts.missing_reason if cohorts else None) or unmatched_reason or \
        ("ZERO_DENOMINATOR or INVALID_BIN_PAIR" if incomplete else None)
    columns = ["metric_id", "definition_id", "member_id", "measurement_ref", "fact_id", "cohort_id",
        "lot_id", "wf_id", "lot_cd", "process", "time_value", "value", "numerator", "denominator",
        "unit", "pass_die_count", "fail_die_count", "unmatched_cells", "better_when",
        "source_id", "parameter", "param_kind", "value_basis"]
    tables = [ResultTable(table_id="metrics", title="Metrics", rows=output,
        columns=list(dict.fromkeys([*columns, *(key for row in output for key in row)])),
        units={"value": "per_row_unit", "numerator": "count", "denominator": "count"},
        complete=complete, missing_reason=reason),
        ResultTable(table_id="metric_definitions", title="Metric definitions",
        rows=list(definition_output.values()), columns=["definition_id", "metric_id", "formula", "unit",
        "definition_version", "parameters_json", "bin_side", "pass_bin", "denominator_policy",
        "source_id", "parameter", "param_kind", "value_basis", "better_when"],
        complete=(definitions.complete if definitions else True),
        missing_reason=(definitions.missing_reason if definitions else None))]
    source_ids = [handle.result_id for handle in handles]
    limitations = []
    if not complete:
        unmatched_cells = sum(row.get("unmatched_cells") or 0 for row in output)
        zero_denominators = sum(row.get("denominator") == 0 for row in output)
        code = "UNMATCHED_JOIN" if unmatched_members else \
            "SOURCE_INCOMPLETE" if not source.complete else \
            "INVALID_BIN_PAIR" if unmatched_cells else "ZERO_DENOMINATOR"
        limitations.append({"code": code, "table_id": "metrics",
            "affected_count": unmatched_members or unmatched_cells or zero_denominators,
            "requested_count": len(cohorts.rows) if cohorts and unmatched_members else len(output),
            "detail": reason, "source_handle": args.source.model_dump()})
    origin = source.observation.provenance.get("data_origin", "unknown")
    return build_result(tables, source_result_ids=source_ids, limitations=limitations,
        scope={"source_version": source.actual_version,
               "definition_version": definitions.actual_version if definitions else None,
               "matched_members": membership_match.matched_members if cohorts else None,
               "unmatched_members": unmatched_members,
               "membership_key_columns": list(membership_match.key_columns) if cohorts else []},
        summary=f"측정 및 사실에서 지표 {len(output)}행을 계산했습니다.", data_origin=origin)


def register(registry):
    registry.add(ToolSpec("compute_metrics", "저장 측정 또는 사실 표와 지표 명세 및 선택적 정의·소속 표를 입력받아 분자·분모가 있는 지표 표를 반환한다. 지표는 최대 25개, 출력은 최대 250,000행이다.",
                          ComputeMetricsInput, compute_metrics, timeout=45))

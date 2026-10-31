"""Stored fact reading and registered relation joins."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from ..runtime.domain_process import run_blocking
from ..types import Contract, ResultTable
from ..wafer_set_contracts import SourceQuery, TableHandle
from ..wafer_set_core import build_result, resolve_handle, resolve_many
from ..wafer_set_catalog import source_spec
from ..wafer_set_map_source import fetch_map_range, fetch_maps_by_refs
from ..wafer_set_predicates import (EMPTY_FILTER_HINT, empty_filter_limitation,
                                     evaluate_predicate, has_string_filter)
from ..wafer_set_relations import join_tables
from ..wafer_set_sources import SourceBatch, fetch_source
from ..wafer_set_sql import normalize_source_query
from .registry import ToolSpec

_EMPTY_SELECTION = object()


class ReadFactsInput(Contract):
    source_id: str = Field(min_length=1, description="읽을 등록 소스 식별자")
    members: TableHandle | None = Field(default=None, description="범위를 제한할 저장 멤버 표")
    query: SourceQuery | None = Field(default=None, description="소스에 적용할 선택 범위")
    columns: list[str] = Field(default_factory=list, max_length=40, description="반환할 공개 열")
    measurement_policy: Literal["all", "latest_per_entity"] = Field(
        default="all", description="여러 측정의 선택 방식")
    include_payload: bool = Field(default=False, description="큰 원본 내용 포함 여부")
    max_rows: int = Field(default=50_000, ge=1, le=250_000, description="반환할 최대 사실 행 수")
    max_measurements: int = Field(default=500, ge=1, le=1000, description="읽을 최대 측정 수")
    max_bytes: int = Field(default=67_108_864, ge=1_048_576, le=67_108_864,
                           description="반환할 원본 내용의 최대 바이트 수")

    @model_validator(mode="after")
    def needs_scope(self):
        if self.source_id not in ("symbol_definition", "parameter_catalog") \
                and self.members is None and self.query is None:
            raise ValueError("members or query is required")
        if self.include_payload and self.source_id != "map":
            raise ValueError("Payload output is available only for payload-bearing measurements")
        return self


class JoinFactsInput(Contract):
    left: TableHandle = Field(description="왼쪽 저장 표")
    right: TableHandle = Field(description="오른쪽 저장 표")
    relation_id: str = Field(min_length=1, description="사용할 등록 관계")
    how: Literal["left", "inner", "full"] = Field(default="left", description="조인 포함 방식")
    expected_cardinality: Literal["one_to_one", "one_to_many", "many_to_one"] = Field(
        description="허용할 관계 카디널리티")
    time_policy: Literal["none", "exact", "latest_before"] = Field(default="none", description="시각 대응 방식")
    left_time: str | None = Field(default=None, min_length=1, max_length=128, description="왼쪽 시각 열")
    right_time: str | None = Field(default=None, min_length=1, max_length=128, description="오른쪽 시각 열")
    tolerance_seconds: int | None = Field(default=None, ge=0, le=2_678_400, description="허용 시각 차이")
    allow_approximate: bool = Field(default=False, description="근사 관계 사용 동의 여부")
    max_expansion: float = Field(default=20, ge=1, le=100, description="허용할 최대 행 증식 배수")
    max_rows: int = Field(default=100_000, ge=1, le=250_000, description="허용할 최대 출력 행 수")

    @model_validator(mode="after")
    def valid_time(self):
        if self.time_policy == "none" and any(value is not None for value in
                                                (self.left_time, self.right_time, self.tolerance_seconds)):
            raise ValueError("Time fields require a temporal policy")
        if self.time_policy != "none" and (self.left_time is None or self.right_time is None):
            raise ValueError("Temporal policy requires both time columns")
        return self


def _table(table_id, title, rows, *, complete=True, reason=None, units=None, columns=None):
    columns = columns or list(dict.fromkeys(key for row in rows for key in row))
    return ResultTable(table_id=table_id, title=title, rows=rows, columns=columns, units=units or {},
                       complete=complete, missing_reason=reason)


def _query_from_members(query, rows):
    from ..wafer_set_contracts import SourceQuery, WaferPair
    if not rows: return _EMPTY_SELECTION
    base = query or SourceQuery()
    common_process = list(dict.fromkeys(str(row["process"]) for row in rows if row.get("process") is not None))
    process = [value for value in base.process if value in common_process] if base.process and common_process \
        else common_process or base.process
    if base.process and common_process and not process: return _EMPTY_SELECTION
    if all(row.get("lot_id") is not None and row.get("wf_id") is not None for row in rows):
        pairs = list(dict.fromkeys((str(row["lot_id"]), int(row["wf_id"])) for row in rows))
        if base.wafer_pairs:
            allowed = {(item.lot_id, item.wf_id) for item in base.wafer_pairs}
            pairs = [item for item in pairs if item in allowed]
        if base.lot_ids:
            allowed = set(base.lot_ids)
            pairs = [item for item in pairs if item[0] in allowed]
        if not pairs: return _EMPTY_SELECTION
        return base.model_copy(update={"lot_ids": [], "wafer_pairs": [WaferPair(lot_id=lot, wf_id=wf)
                                      for lot, wf in pairs], "process": process})
    if all(row.get("lot_id") is not None for row in rows):
        lots = list(dict.fromkeys(str(row["lot_id"]) for row in rows))
        if base.wafer_pairs:
            pairs = [item for item in base.wafer_pairs if item.lot_id in set(lots)]
            if not pairs: return _EMPTY_SELECTION
            return base.model_copy(update={"lot_ids": [], "wafer_pairs": pairs, "process": process})
        if base.lot_ids: lots = [lot for lot in lots if lot in set(base.lot_ids)]
        if not lots: return _EMPTY_SELECTION
        return base.model_copy(update={"lot_ids": lots, "process": process})
    if all(row.get("lot_cd") is not None for row in rows):
        products = list(dict.fromkeys(str(row["lot_cd"]) for row in rows))
        if base.lot_cd: products = [value for value in products if value in set(base.lot_cd)]
        if not products: return _EMPTY_SELECTION
        return base.model_copy(update={"lot_cd": products, "process": process})
    raise ValueError("Members do not provide a common source identity")


def _filter_references(rows, query):
    if query is _EMPTY_SELECTION: return []
    output = list(rows)
    if query.wafer_pairs:
        pairs = {(item.lot_id, item.wf_id) for item in query.wafer_pairs}
        output = [row for row in output if (str(row.get("lot_id")), int(row.get("wf_id"))) in pairs]
    if query.lot_ids:
        allowed = set(query.lot_ids)
        output = [row for row in output if str(row.get("lot_id")) in allowed]
    if query.lot_cd:
        allowed = set(query.lot_cd)
        output = [row for row in output if str(row.get("lot_cd")) in allowed]
    if query.process:
        allowed = set(query.process)
        output = [row for row in output if str(row.get("process")) in allowed]
    if query.time:
        column = query.time.column
        output = [row for row in output if row.get(column) is not None
                  and query.time.start <= __import__("datetime").datetime.fromisoformat(str(row[column])) < query.time.end]
    if query.where:
        output = [row for row, keep in zip(output, evaluate_predicate(output, query.where, "exclude")) if keep]
    return output


async def read_facts(args, ctx):
    resolved = await resolve_handle(ctx, args.members) if args.members else None
    source_ids = [args.members.result_id] if args.members else []
    source_complete = resolved.complete if resolved else True
    source_reason = resolved.missing_reason if resolved else None
    origin = resolved.observation.provenance.get("data_origin", "unknown") if resolved else "live"
    query = _query_from_members(args.query, resolved.rows) if resolved else args.query
    empty_selection = query is _EMPTY_SELECTION
    requested_query = None if empty_selection or query is None else query.model_copy(deep=True)
    effective_query = query
    notices = []

    async def normalized_query():
        nonlocal notices
        if args.query is None:
            return query
        normalized, notices = await run_blocking(normalize_source_query, args.source_id, args.query)
        if normalized == args.query:
            return query
        return _query_from_members(normalized, resolved.rows) if resolved else normalized
    if args.source_id == "map":
        if empty_selection:
            from ..wafer_set_map_source import MapBatch
            batch = MapBatch([], [], 0, 0, 0, 2, True, None)
            refs = "already_loaded"
        elif resolved and all({"lot_id", "wf_id", "process", "time_column", "time_value", "payload_hash"}
                            .issubset(row) for row in resolved.rows):
            if args.measurement_policy != "all":
                raise ValueError("Stored measurement references require measurement_policy='all'")
            refs = _filter_references(resolved.rows, query)
        elif resolved and not resolved.rows:
            from ..wafer_set_map_source import MapBatch
            batch = MapBatch([], [], 0, 0, 0, 2, True, None)
            refs = "already_loaded"
        else:
            batch = await run_blocking(fetch_map_range, query, measurement_policy=args.measurement_policy,
                include_payload=args.include_payload, max_rows=args.max_rows,
                max_measurements=args.max_measurements, max_bytes=args.max_bytes)
            if batch.complete and batch.requested_count == 0:
                retry_query = await normalized_query()
                if retry_query is not _EMPTY_SELECTION and retry_query != query:
                    effective_query = retry_query
                    batch = await run_blocking(fetch_map_range, retry_query,
                        measurement_policy=args.measurement_policy,
                        include_payload=args.include_payload, max_rows=args.max_rows,
                        max_measurements=args.max_measurements, max_bytes=args.max_bytes)
            refs = "already_loaded"
            source_complete = source_complete and batch.complete
            source_reason = source_reason or batch.missing_reason
        if refs == "already_loaded":
            pass
        else:
            batch = await run_blocking(fetch_maps_by_refs, refs, include_payload=args.include_payload,
                                       max_measurements=args.max_measurements, max_bytes=args.max_bytes)
        complete = source_complete and batch.complete
        reason = source_reason or batch.missing_reason
        payload_version = hashlib.sha256(json.dumps(batch.payloads, sort_keys=True, ensure_ascii=False,
            default=str).encode()).hexdigest()[:16]
        measurements = [{**row, "payload_version": payload_version} for row in batch.measurements]
        measurement_columns = list(dict.fromkeys(key for row in measurements for key in row)) or [
            "lot_id", "wf_id", "lot_cd", "fab_id", "process", "start_tm", "end_tm",
            "time_column", "time_value", "measurement_ref", "member_id", "payload_hash",
            "payload_sha256", "payload_table_id", "payload_version"]
        tables = [_table("map_measurements", "Map measurements", measurements, complete=complete,
                         reason=reason, columns=measurement_columns)]
        if args.include_payload:
            tables.append(_table("map_payloads", "Map payloads", batch.payloads, complete=complete,
                reason=reason, columns=["payload_hash", "payload_sha256", "map_val_json"]))
        audit = {"requested_count": batch.requested_count, "fetched_count": len(batch.measurements),
            "returned_count": len(batch.measurements), "omitted_count": 0,
            "duplicate_count": batch.duplicate_count, "invalid_identity_count": batch.invalid_identity_count,
            "bytes_read": batch.bytes_read, "complete": complete, "missing_reason": reason}
    else:
        if empty_selection:
            batch = SourceBatch([], 0, 0, 0, 0, 2, True, None)
        else:
            batch = await run_blocking(fetch_source, args.source_id, query, args.columns,
                                       args.measurement_policy, args.max_rows, args.max_bytes)
            if batch.complete and batch.requested_count == 0:
                retry_query = await normalized_query()
                if retry_query is not _EMPTY_SELECTION and retry_query != query:
                    effective_query = retry_query
                    batch = await run_blocking(fetch_source, args.source_id, retry_query, args.columns,
                                               args.measurement_policy, args.max_rows, args.max_bytes)
        complete = source_complete and batch.complete
        reason = source_reason or batch.missing_reason
        default_columns = args.columns or [name for name in source_spec(args.source_id).columns
                                           if name not in source_spec(args.source_id).binary_columns]
        fact_columns = list(dict.fromkeys(key for row in batch.rows for key in row)) or [
            "source_id", "fact_id", "source_row_hash", *default_columns]
        tables = [_table("facts", "Facts", batch.rows, complete=complete, reason=reason,
                         units={"value": "per_row_unit"} if args.source_id == "wafer_parameter" else {},
                         columns=fact_columns)]
        audit = {"requested_count": batch.requested_count, "fetched_count": batch.requested_count,
            "returned_count": len(batch.rows), "omitted_count": batch.omitted_count,
            "duplicate_count": batch.duplicate_count, "invalid_identity_count": batch.invalid_identity_count,
            "bytes_read": batch.bytes_read, "complete": complete, "missing_reason": reason}
    tables.append(_table("read_audit", "Read audit", [audit], columns=list(audit)))
    limitations = list(notices)
    limitations += [{"code": "SOURCE_INCOMPLETE", "table_id": tables[0].table_id,
        "affected_count": audit["omitted_count"], "requested_count": audit["requested_count"],
        "detail": reason, "source_handle": args.members.model_dump() if args.members else None}] if not complete else []
    if args.source_id != "map":
        limitations.extend({"code": item["code"], "table_id": tables[0].table_id,
            "affected_count": item.get("affected_count"), "requested_count": audit["requested_count"],
            "detail": item.get("detail"), "source_handle": args.members.model_dump() if args.members else None}
            for item in batch.metadata_limitations)
    result_summary = f"사실 {audit['returned_count']}행을 읽었습니다."
    if complete and audit["returned_count"] == 0 and not empty_selection and has_string_filter(effective_query):
        result_summary += " " + EMPTY_FILTER_HINT
        limitations.append(empty_filter_limitation(tables[0].table_id, audit["requested_count"],
            args.members.model_dump() if args.members else None))
    return build_result(tables, source_result_ids=source_ids, limitations=limitations,
        scope={"source_id": args.source_id, "source_version": resolved.actual_version if resolved else None,
               "metadata_definition_version": getattr(batch, "metadata_definition_version", None),
               "requested_query": requested_query.model_dump(mode="json") if requested_query else None,
               "effective_query": effective_query.model_dump(mode="json")
                   if effective_query is not _EMPTY_SELECTION and effective_query else None},
        summary=result_summary, data_origin=origin)


async def join_facts(args, ctx):
    left, right = await resolve_many(ctx, [args.left, args.right])
    output = await run_blocking(join_tables, left.rows, right.rows, relation_id=args.relation_id,
        how=args.how, expected_cardinality=args.expected_cardinality, time_policy=args.time_policy,
        left_time=args.left_time, right_time=args.right_time, tolerance_seconds=args.tolerance_seconds,
        allow_approximate=args.allow_approximate, max_expansion=args.max_expansion, max_rows=args.max_rows)
    complete = left.complete and right.complete and output.audit["unmatched_left"] == 0
    reason = left.missing_reason or right.missing_reason or ("UNMATCHED_JOIN" if not complete else None)
    joined_columns = [*left.columns, *(f"right__{column}" for column in right.columns),
        "left_fact_id", "right_fact_id", "relation_id", "match_status", "time_distance_seconds"]
    joined_units = {**left.units, **{f"right__{key}": value for key, value in right.units.items()}}
    tables = [_table("joined", "Joined facts", output.joined, complete=complete, reason=reason,
                     columns=list(dict.fromkeys(joined_columns)), units=joined_units),
              _table("join_audit", "Join audit", [output.audit]),
              _table("unmatched", "Unmatched facts", output.unmatched, complete=complete, reason=reason,
                     columns=["side", "fact_id", "member_id", "reason"])]
    limitations = [] if complete else [{"code": "UNMATCHED_JOIN", "table_id": "joined",
        "affected_count": len(output.unmatched), "requested_count": len(left.rows), "detail": reason,
        "source_handle": args.left.model_dump()}]
    origins = {left.observation.provenance.get("data_origin", "unknown"),
               right.observation.provenance.get("data_origin", "unknown")}
    origin = origins.pop() if len(origins) == 1 else "unknown"
    return build_result(tables, source_result_ids=[args.left.result_id, args.right.result_id],
        limitations=limitations, scope={"relation_id": args.relation_id,
        "left_version": left.actual_version, "right_version": right.actual_version},
        summary=f"관계에 따라 {len(output.joined)}행을 연결했습니다.", data_origin=origin)


def register(registry):
    registry.add(ToolSpec("read_facts", "등록 소스와 선택 범위 또는 저장 멤버 표를 입력받아 식별자와 출처가 있는 사실 표를 반환한다. 출력은 최대 250,000행·1,000측정·64 MiB 원본 내용이다.",
                          ReadFactsInput, read_facts, timeout=45))
    registry.add(ToolSpec("join_facts", "두 저장 표와 등록 관계 및 시각 정책을 입력받아 결과·감사·미연결 표를 반환한다. 출력은 최대 250,000행이며 행 증식은 최대 100배다.",
                          JoinFactsInput, join_facts, timeout=30))

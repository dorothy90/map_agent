"""Immutable filtering and cohort construction tools."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, model_validator

from ..runtime.domain_process import run_blocking
from ..types import Contract, ResultTable
from ..wafer_set_contracts import PredicateGroup, TableHandle
from ..wafer_set_core import build_result, resolve_handle, resolve_many
from ..wafer_set_predicates import (EMPTY_FILTER_HINT, empty_filter_limitation,
                                     filter_rows, has_string_filter, make_cohorts)
from .registry import ToolSpec


class FilterSetInput(Contract):
    source: TableHandle = Field(description="입력 저장 표")
    operation: Literal["where", "intersection", "union", "difference"] = Field(
        default="where", description="적용할 선택 또는 집합 연산")
    predicate: PredicateGroup | None = Field(default=None, description="행 선택 조건")
    other: TableHandle | None = Field(default=None, description="집합 연산의 다른 저장 표")
    key_columns: list[str] = Field(min_length=1, max_length=6, description="행 동일성을 판단할 열")
    null_policy: Literal["exclude", "include", "error"] = Field(default="exclude", description="누락 키 처리 방식")
    max_rows: int = Field(default=100_000, ge=1, le=250_000, description="허용할 최대 출력 행 수")

    @model_validator(mode="after")
    def valid_operation(self):
        if self.operation == "where" and (self.predicate is None or self.other is not None):
            raise ValueError("where requires predicate only")
        if self.operation != "where" and (self.predicate is not None or self.other is None):
            raise ValueError("set operations require other only")
        return self


class CohortRule(Contract):
    name: str = Field(min_length=1, max_length=100, description="집합 표시 이름")
    predicate: PredicateGroup = Field(description="집합에 포함할 행 조건")


class MakeCohortsInput(Contract):
    source: TableHandle = Field(description="입력 저장 표")
    key_columns: list[str] = Field(min_length=1, max_length=6, description="멤버 식별 열")
    group_by: list[str] = Field(default_factory=list, max_length=4, description="그룹을 만들 열")
    rules: list[CohortRule] = Field(default_factory=list, max_length=25, description="이름 있는 집합 조건")
    combine: Literal["independent", "cross"] = Field(default="independent", description="그룹과 조건의 결합 방식")
    null_group: Literal["include", "exclude", "error"] = Field(default="include", description="누락 그룹 처리 방식")
    max_cohorts: int = Field(default=100, ge=1, le=100, description="허용할 최대 집합 수")
    max_memberships: int = Field(default=250_000, ge=1, le=250_000, description="허용할 최대 소속 행 수")

    @model_validator(mode="after")
    def valid_groups(self):
        if not self.group_by and not self.rules: raise ValueError("group_by or rules is required")
        names = [rule.name for rule in self.rules]
        if len(names) != len(set(names)): raise ValueError("Cohort rule names must be unique")
        if self.combine == "cross" and (not self.group_by or not self.rules):
            raise ValueError("cross requires groups and rules")
        return self


def _table(table_id, title, rows, *, complete=True, reason=None, columns=None):
    columns = columns or list(dict.fromkeys(key for row in rows for key in row))
    return ResultTable(table_id=table_id, title=title, rows=rows, columns=columns,
                       complete=complete, missing_reason=reason)


async def filter_set(args, ctx):
    resolved = await resolve_many(ctx, [args.source, *([args.other] if args.other else [])])
    source, other = resolved[0], resolved[1] if args.other else None
    output = await run_blocking(filter_rows, source.rows, operation=args.operation,
        key_columns=args.key_columns, null_policy=args.null_policy, predicate=args.predicate,
        other=other.rows if other else None)
    if len(output.rows) > args.max_rows: raise ValueError("filter output exceeds max_rows")
    complete = source.complete and (other.complete if other else True) and output.null_predicate_rows == 0
    reason = source.missing_reason or (other.missing_reason if other else None) or \
        ("NULL values were excluded" if output.null_predicate_rows else None)
    audit = {"input_rows": len(source.rows), "output_rows": len(output.rows),
        "null_predicate_rows": output.null_predicate_rows, "duplicate_keys": output.duplicate_keys,
        "operation": args.operation, "complete": complete}
    tables = [_table("members", "Members", output.rows, complete=complete, reason=reason,
                     columns=source.columns), _table("filter_audit", "Filter audit", [audit])]
    tables[0].units = dict(source.units)
    source_ids = [args.source.result_id] + ([args.other.result_id] if args.other else [])
    limitations = [] if complete else [{"code": "SOURCE_INCOMPLETE", "table_id": "members",
        "affected_count": output.null_predicate_rows, "requested_count": len(source.rows),
        "detail": reason, "source_handle": args.source.model_dump()}]
    summary = f"입력 {len(source.rows)}행에서 {len(output.rows)}행을 선택했습니다."
    if args.operation == "where" and source.rows and not output.rows and has_string_filter(args.predicate):
        summary += " " + EMPTY_FILTER_HINT + " 저장 표의 실제 값은 list_values(source=source, field=field)로 확인하세요."
        limitations.append(empty_filter_limitation("members", len(source.rows), args.source.model_dump()))
    return build_result(tables, source_result_ids=source_ids, limitations=limitations,
        scope={"operation": args.operation, "source_version": source.actual_version,
               "other_version": other.actual_version if other else None},
        summary=summary,
        data_origin=source.observation.provenance.get("data_origin", "unknown"))


async def make_cohorts_handler(args, ctx):
    source = await resolve_handle(ctx, args.source)
    output = await run_blocking(make_cohorts, source.rows, key_columns=args.key_columns,
        group_by=args.group_by, rules=args.rules, combine=args.combine, null_group=args.null_group,
        max_cohorts=args.max_cohorts, max_memberships=args.max_memberships)
    complete = source.complete and output.audit["excluded_null_members"] == 0
    reason = source.missing_reason or ("Null groups were excluded" if output.audit["excluded_null_members"] else None)
    for row in output.cohorts: row["complete"] = complete
    membership_columns = ["cohort_id", "member_id", "measurement_ref", *args.key_columns]
    tables = [_table("cohorts", "Cohorts", output.cohorts, complete=complete, reason=reason,
                     columns=["cohort_id", "label", "dimensions_json", "predicate_json",
                              "member_count", "measurement_count", "complete"]),
              _table("memberships", "Memberships", output.memberships, complete=complete,
                     reason=reason, columns=membership_columns),
              _table("cohort_audit", "Cohort audit", [output.audit])]
    limitations = [] if complete else [{"code": "SOURCE_INCOMPLETE", "table_id": "memberships",
        "affected_count": output.audit["excluded_null_members"], "requested_count": output.audit["input_members"],
        "detail": reason, "source_handle": args.source.model_dump()}]
    summary = f"집합 {len(output.cohorts)}개와 소속 {len(output.memberships)}행을 만들었습니다."
    if source.rows and args.rules and not output.memberships and \
            any(has_string_filter(rule.predicate) for rule in args.rules):
        summary += " " + EMPTY_FILTER_HINT + " 저장 표의 실제 값은 list_values(source=source, field=field)로 확인하세요."
        limitations.append(empty_filter_limitation("memberships", output.audit["input_members"],
                                                   args.source.model_dump()))
    return build_result(tables, source_result_ids=[args.source.result_id], limitations=limitations,
        scope={"source_version": source.actual_version, "combine": args.combine,
               "group_by_json": json.dumps(args.group_by, ensure_ascii=False)},
        summary=summary,
        data_origin=source.observation.provenance.get("data_origin", "unknown"))


def register(registry):
    registry.add(ToolSpec("filter_set", "저장 표와 조건 또는 집합 연산을 입력받아 원본 열을 보존한 멤버 표와 감사 표를 반환한다. 출력은 최대 250,000행이다.",
                          FilterSetInput, filter_set, timeout=20))
    registry.add(ToolSpec("make_cohorts", "저장 멤버 표와 그룹 열 또는 이름 있는 조건을 입력받아 집합 표와 소속 표를 반환한다. 집합은 최대 100개, 소속은 최대 250,000행이다.",
                          MakeCohortsInput, make_cohorts_handler, timeout=20))

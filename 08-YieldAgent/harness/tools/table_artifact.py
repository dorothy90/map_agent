import asyncio
import math
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from ..types import Contract, ResultTable
from .registry import ToolResult, ToolSpec
from .yield_report_data import (
    ReportPeriod, PeriodReportData, GRAINS, fetch_parameter_metadata as _fetch_parameter_metadata,
    fetch_period_report, aggregate_groups,
)
from yield_viz import GMS_COLUMNS, _build_html_table


class YieldReportRow(Contract):
    period: str = Field(min_length=1, description="Arbitrary group name.")
    process: str = Field(min_length=1)
    param: str = Field(min_length=1)
    value: int | float | None = None


class YieldReportGms(Contract):
    cum0: int | float | None = None
    cum2: int | float | None = None
    fab: int | float | None = None
    prb: int | float | None = None
    pnt: int | float | None = None


class YieldReportPeriod(Contract):
    period: str = Field(min_length=1)
    label: str | None = Field(default=None, min_length=1)
    lot_count: int | None = Field(default=None, ge=0)
    wf_count: int | None = Field(default=None, ge=0)
    gms: YieldReportGms = Field(default_factory=YieldReportGms)


class ReportComparison(Contract):
    current: str = Field(min_length=1)
    baseline: str = Field(min_length=1)


class _ReportInput(Contract):
    """Private presentation structure; never exposed as a tool schema."""
    title: str = "Yield report"
    product_label: str | None = None
    group_gms_unavailable: bool = False
    rows: list[YieldReportRow] = Field(default_factory=list)
    periods: list[YieldReportPeriod] = Field(default_factory=list)
    period_label: str | None = None
    comparison: ReportComparison | None = None

    @model_validator(mode="after")
    def validate_rows(self):
        declared = {_period_key(self, p.period) for p in self.periods}
        if len(declared) != len(self.periods):
            raise ValueError("duplicate normalized report period")
        labels = [(p.label or p.period).strip() for p in self.periods]
        if not all(labels) or len(set(labels)) != len(labels):
            raise ValueError("group labels must be unique")
        seen = set()
        for row in self.rows:
            key = (_period_key(self, row.period), _normalize_process(row.process), row.param)
            if row.value is not None and not math.isfinite(row.value):
                raise ValueError("report values must be finite")
            if key in seen:
                raise ValueError("duplicate normalized report row")
            if declared and key[0] not in declared:
                raise ValueError(f"undeclared group ID: {key[0]}")
            seen.add(key)
        for period in self.periods:
            if any(v is not None and not math.isfinite(v) for v in period.gms.model_dump().values()):
                raise ValueError("GMS values must be finite")
        if self.comparison:
            current, baseline = self.comparison.current, self.comparison.baseline
            if current == baseline:
                raise ValueError("comparison current and baseline must differ")
            if not {current, baseline} <= (declared | {r.period for r in self.rows}):
                raise ValueError("comparison refers to an unknown group")
        return self


Identifier = Annotated[str, StringConstraints(strict=True, min_length=1)]


class TableMembership(Contract):
    kind: Literal["table"]
    member_grain: Literal["lot", "wafer"] = "lot"
    result_id: Identifier
    table_id: Identifier | None = None


class InlineGroup(Contract):
    group_id: Identifier
    lot_ids: list[Identifier]


class InlineMembership(Contract):
    kind: Literal["inline"]
    groups: list[InlineGroup] = Field(min_length=1)


class GroupLabel(Contract):
    id: Identifier
    label: Identifier | None = None


class ReportGrouping(Contract):
    membership: Annotated[TableMembership | InlineMembership, Field(discriminator="kind")]
    groups: list[GroupLabel] | None = Field(default=None, min_length=1)
    coverage: Literal["disjoint_subsets", "partition"] = "disjoint_subsets"
    definition: str | None = None


class YieldTableInput(Contract):
    """A scope plus optional lot membership; report values are always tool-owned."""
    lot_cd: Identifier | None = None
    period: ReportPeriod | None = None
    report_scope_id: Identifier | None = None
    title: str = Field(default="수율 리포트", min_length=1, max_length=200)
    layout: Literal["period", "group", "group_period"] | None = None
    grouping: ReportGrouping | None = None
    comparison: ReportComparison | None = None

    @model_validator(mode="after")
    def validate_scope(self):
        if self.report_scope_id is not None:
            if self.lot_cd is not None or self.period is not None:
                raise ValueError("Choose exactly one scope: lot_cd+period OR report_scope_id")
            if not self.report_scope_id.startswith("yield-report:"):
                raise ValueError("report_scope_id must be a yield-report: reference returned by this tool")
        elif self.lot_cd is None or not self.lot_cd.strip() or self.period is None:
            raise ValueError("Missing scope: pass report_scope_id from the earlier render_yield_table result (or lot_cd+period).")
        if self.lot_cd:
            self.lot_cd = self.lot_cd.strip()
        if self.layout is None:
            self.layout = "group" if self.grouping else "period"
        if self.grouping is None and (self.layout != "period" or self.comparison is not None):
            raise ValueError("period layout forbids group comparison; group/group_period requires grouping")
        if self.grouping is not None and self.layout == "period":
            raise ValueError("grouping requires layout group or group_period")
        if self.comparison and self.comparison.current == self.comparison.baseline:
            raise ValueError("comparison current and baseline must differ")
        return self


def _period_key(args: _ReportInput, value: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("group ID must be a nonempty string without surrounding whitespace")
    return value


def _normalize_process(process: str) -> Literal["pt1h", "pt1c"]:
    normalized = " ".join(process.strip().casefold().split())
    aliases = {
        "pt1h": "pt1h",
        "pt1h test": "pt1h",
        "pt1c": "pt1c",
        "pt1c test": "pt1c",
    }
    if normalized not in aliases:
        raise ValueError("process must be PT1H TEST, PT1C TEST, pt1h, or pt1c")
    return aliases[normalized]


def _group_periods(args: _ReportInput) -> list[YieldReportPeriod]:
    return args.periods or [
        YieldReportPeriod(period=period)
        for period in dict.fromkeys(row.period for row in args.rows)
    ]


def _pivot_report_rows(args: _ReportInput, parameter_columns: dict[str, list[str]]) -> list[dict]:
    ordered = [p.period for p in _group_periods(args)]
    parameter_keys = [*parameter_columns["pt1h"],
                      *(f"pt1c_{p}" for p in parameter_columns["pt1c"])]
    pivoted = {period: {
        "week": period, "lotcount": "-", "wfCount": "-",
        **{key: "-" for key in parameter_keys},
        **{f"gms_{param}": "-" for param in GMS_COLUMNS},
    } for period in ordered}

    for metadata in args.periods:
        row = pivoted[_period_key(args, metadata.period)]
        if metadata.label is not None:
            row["week"] = metadata.label
        row["lotcount"] = metadata.lot_count if metadata.lot_count is not None else "-"
        row["wfCount"] = metadata.wf_count if metadata.wf_count is not None else "-"
        for param in GMS_COLUMNS:
            value = getattr(metadata.gms, param)
            row[f"gms_{param}"] = value if value is not None else "-"

    for source in args.rows:
        process = _normalize_process(source.process)
        if source.param in parameter_columns[process]:
            key = source.param if process == "pt1h" else f"pt1c_{source.param}"
            pivoted[_period_key(args, source.period)][key] = source.value
    return list(pivoted.values())


@dataclass(frozen=True)
class _ReportRenderData:
    rows: list[dict]
    column_keys: list[str]
    parameter_keys: list[str]
    parameter_columns: dict[str, list[str]]


def _report_render_data(args: _ReportInput, parameter_columns: dict[str, list[str]]) -> _ReportRenderData:
    parameter_keys = [*parameter_columns["pt1h"],
                      *(f"pt1c_{p}" for p in parameter_columns["pt1c"])]
    return _ReportRenderData(
        rows=_pivot_report_rows(args, parameter_columns),
        column_keys=["week", "lotcount", "wfCount", *parameter_keys,
                     *(f"gms_{p}" for p in GMS_COLUMNS)],
        parameter_keys=parameter_keys,
        parameter_columns=parameter_columns,
    )


def _html(args: _ReportInput, report_data: _ReportRenderData,
          displayed_rows: list[dict] | None = None, *, unit="weekly") -> str:
    ids = [p.period for p in _group_periods(args)]
    comparison = None
    if args.comparison:
        comparison = (ids.index(_period_key(args, args.comparison.baseline)),
                      ids.index(_period_key(args, args.comparison.current)))
    # Caller formats remain accepted, but the report contract determines units.
    formats = {key: ("scaled", 1) for key in report_data.parameter_keys}
    formats.update({f"gms_{p}": ("number", 1) for p in GMS_COLUMNS})
    return _build_html_table(
        report_data.rows, args.product_label or "", unit=unit,
        parameter_columns=report_data.parameter_columns,
        parameter_formats=formats, comparison=comparison,
        period_label=args.period_label or "GROUP",
        displayed_rows=displayed_rows,
        unavailable_delta_keys=frozenset(f"gms_{p}" for p in GMS_COLUMNS) if args.group_gms_unavailable else frozenset(),
    )


async def render_yield_table(args: YieldTableInput, ctx) -> ToolResult:
    from .report_policy import ReportContractError
    from .yield_report_scope import load_scope, save_scope, attach_population

    if args.report_scope_id:
        data, original_period, scope_id = await load_scope(args.report_scope_id, ctx)
    else:
        try:
            data = await asyncio.to_thread(fetch_period_report, args.lot_cd, args.period)
        except ValueError as exc:
            raise ReportContractError("REPORT_SCOPE_UNAVAILABLE", str(exc)) from exc
        original_period = args.period.model_dump(exclude_none=True)
        scope_id = None
    source_ids = []
    if args.grouping:
        membership, groups, origin, uncovered, uncovered_wafers = await _membership(args, data, ctx)
        member_grain = getattr(args.grouping.membership, "member_grain", "lot")
        result = _render_groups(args, data, membership, groups)
        result.scope.update(membership=origin, coverage=args.grouping.coverage,
                            definition=args.grouping.definition, uncovered_lot_count=uncovered,
                            member_grain=member_grain)
        if member_grain == "wafer":
            result.scope["uncovered_wafer_count"] = uncovered_wafers
        result.tables.append(ResultTable(table_id="group_membership", title=f"Accepted {member_grain} membership",
                                        rows=membership, columns=["group_id", "lot_id"] +
                                        (["wf_id"] if member_grain == "wafer" else []), complete=True))
        if origin.get("result_id"):
            source_ids.append(origin["result_id"])
    else:
        # Reused scopes keep their original declarative period; never resolve recent again.
        period_args = args.model_copy(update={"period": original_period})
        result = _render_period_report(period_args, data)
    if scope_id is None:
        scope_id = await save_scope(data, original_period, ctx)
    result.scope.update(report_scope_id=scope_id, layout=args.layout,
                        window=data.window, period=original_period,
                        query_consistency="Single connection, statement-level reads; no cross-statement snapshot guarantee",
                        scope_reusable=ctx is not None)
    if ctx is not None:
        source_ids.insert(0, scope_id.removeprefix("yield-report:"))
    result.source_result_ids = list(dict.fromkeys(source_ids))
    attach_population(result, data, scope_id)
    return result


async def _membership(args, data, ctx):
    from .report_policy import ReportContractError
    grouping = args.grouping
    source = grouping.membership
    member_grain = getattr(source, "member_grain", "lot")
    if member_grain == "wafer" and data.wafer_parameter_stats is None:
        raise ReportContractError("WAFER_CAPTURE_REQUIRED", "call again with lot_cd+period")
    if isinstance(source, TableMembership):
        if ctx is None:
            raise ReportContractError("MEMBERSHIP_UNAVAILABLE", "Saved membership requires a result store")
        try:
            obs = await ctx.store.observation(ctx.run["principal_id"], ctx.run["session_id"], source.result_id)
        except (PermissionError, KeyError) as exc:
            raise ReportContractError("MEMBERSHIP_UNAVAILABLE", "Membership result unavailable") from exc
        choices = [t.table_id for t in obs.tables]
        table_id = source.table_id
        if table_id is None:
            if len(choices) != 1:
                raise ReportContractError("MEMBERSHIP_TABLE_REQUIRED", {
                    "message": "Provide table_id; available tables: " + ", ".join(choices), "table_ids": choices})
            table_id = choices[0]
        table = next((t for t in obs.tables if t.table_id == table_id), None)
        if table is None:
            raise ReportContractError("MEMBERSHIP_TABLE_REQUIRED", {
                "message": "Unknown table_id; available tables: " + ", ".join(choices), "table_ids": choices})
        if obs.status not in ("success", "partial", "empty") or not table.complete:
            raise ReportContractError("INCOMPLETE_MEMBERSHIP", "Membership table must be complete")
        columns = ["group_id", "lot_id"] + (["wf_id"] if member_grain == "wafer" else [])
        if not set(columns) <= set(table.columns):
            raise ReportContractError("MEMBERSHIP_COLUMNS", "Membership table requires " + ", ".join(columns) + " columns")
        try:
            raw = await ctx.store.rows(ctx.run["principal_id"], ctx.run["session_id"], source.result_id, table_id=table_id)
        except (PermissionError, KeyError, ValueError) as exc:
            raise ReportContractError("MEMBERSHIP_UNAVAILABLE", "Cannot read complete membership table") from exc
        if len(raw) != table.total_rows:
            raise ReportContractError("INCOMPLETE_MEMBERSHIP", "Complete membership row count does not match stored table")
        if member_grain == "lot" and any(
                r.get("wf_id") is not None and (not isinstance(r["wf_id"], str) or r["wf_id"].strip()) for r in raw):
            raise ReportContractError("MEMBERSHIP_GRAIN_MISMATCH", 'use member_grain "wafer" or drop the column')
        rows = [{key: r.get(key) for key in columns} for r in raw]
        if member_grain == "wafer" and any(
                not isinstance(r["wf_id"], str) or not r["wf_id"].strip() or r["wf_id"] != r["wf_id"].strip() for r in rows):
            raise ReportContractError("INVALID_MEMBERSHIP", 'wf_id must be a nonempty unpadded str; cast to str exactly as in WFID, e.g. "7"')
        inferred = list(dict.fromkeys(r["group_id"] for r in rows if isinstance(r["group_id"], str)))
        origin = {"kind": "table", "result_id": source.result_id, "table_id": table_id}
    else:
        inferred = [g.group_id for g in source.groups]
        if len(inferred) != len(set(inferred)):
            raise ReportContractError("DUPLICATE_GROUP", "duplicate inline group_id")
        rows = [{"group_id": g.group_id, "lot_id": lot} for g in source.groups for lot in g.lot_ids]
        origin = {"kind": "inline", "evidence_table_id": "group_membership"}
    for row in rows:
        if any(not isinstance(value, str) or not value.strip() or value != value.strip()
               for value in (row["group_id"], row["lot_id"])):
            raise ReportContractError("INVALID_MEMBERSHIP", "group_id and lot_id must be nonempty, unpadded strings")
    groups = grouping.groups or [GroupLabel(id=identity) for identity in inferred]
    ids = [g.id for g in groups]
    labels = [(g.label or g.id).strip() for g in groups]
    if not ids:
        raise ReportContractError("GROUPS_REQUIRED", "Empty membership requires declared groups")
    if len(ids) != len(set(ids)) or any(not i.strip() or i != i.strip() for i in ids):
        raise ReportContractError("INVALID_GROUP_ID", "group IDs must be unique, nonempty and unpadded")
    if set(inferred) - set(ids):
        raise ReportContractError("UNDECLARED_GROUP", "Membership contains undeclared group IDs")
    if len(labels) != len(set(labels)) or not all(labels):
        raise ReportContractError("INVALID_GROUP_LABEL", "group labels must be unique and nonempty")
    if args.comparison and not {args.comparison.current, args.comparison.baseline} <= set(ids):
        raise ReportContractError("UNKNOWN_COMPARISON", "comparison refers to unknown group IDs")
    population = {r["lot_id"] for r in data.lot_population}
    if member_grain == "wafer":
        selectable = {(r["lot_id"], r["wf_id"]) for r in
                      [*data.lot_wafer_members, *data.wafer_parameter_stats] if r["grain"] == "all"}
        assigned_wafers = set()
        for row in rows:
            key = (row["lot_id"], row["wf_id"])
            if key not in selectable:
                raise ReportContractError("UNKNOWN_WAFER", f"unknown wafer key: {key}")
            if key in assigned_wafers:
                raise ReportContractError("INVALID_MEMBERSHIP", f"duplicate wafer membership: {key}")
            assigned_wafers.add(key)
        uncovered_wafers = len(selectable - assigned_wafers)
        if grouping.coverage == "partition" and uncovered_wafers:
            raise ReportContractError("INCOMPLETE_PARTITION", f"partition leaves {uncovered_wafers} wafers uncovered")
        return rows, groups, origin, len(population - {r["lot_id"] for r in rows}), uncovered_wafers
    assigned = {}
    for row in rows:
        group, lot = row["group_id"], row["lot_id"]
        if any(not isinstance(v, str) or not v.strip() or v != v.strip() for v in (group, lot)):
            raise ReportContractError("INVALID_MEMBERSHIP", "group_id and lot_id must be nonempty, unpadded strings")
        if group not in ids:
            raise ReportContractError("UNDECLARED_GROUP", f"undeclared group_id: {group}")
        if lot not in population:
            raise ReportContractError("UNKNOWN_LOT", f"unknown lot_id in official population: {lot}")
        if lot in assigned:
            reason = "duplicate" if assigned[lot] == group else "overlap"
            raise ReportContractError("INVALID_MEMBERSHIP", f"{reason} membership for lot_id: {lot}")
        assigned[lot] = group
    uncovered = len(population - assigned.keys())
    if grouping.coverage == "partition" and uncovered:
        raise ReportContractError("INCOMPLETE_PARTITION", f"partition leaves {uncovered} lots uncovered")
    return rows, groups, origin, uncovered, None


def _render_groups(args, data, membership, groups):
    comparison = args.comparison
    comparison_source = "caller" if comparison else None
    if comparison is None and len(groups) > 1:
        comparison = ReportComparison(current=groups[-1].id, baseline=groups[0].id)
        comparison_source = "default_last_minus_first"
    delta_definition = "Δ = last group − first group (default)" if comparison_source == "default_last_minus_first" else None
    member_grain = getattr(args.grouping.membership, "member_grain", "lot")
    rendered, counts, values, cells = [], [], [], []
    slices = [("all", "")]
    if args.layout == "group_period":
        slices = [(grain, bucket) for grain, (key, _) in GRAINS.items()
                  for bucket in data.window.get(key, [])]
    for grain, bucket in slices:
        aggregated = aggregate_groups(data, membership, [g.id for g in groups], grain=grain, bucket=bucket, member_grain=member_grain)
        by_group = {r["period"]: r for r in aggregated["counts"]}
        presentation = _ReportInput(title=args.title, product_label=data.lot_cd, group_gms_unavailable=True,
            period_label="GROUP" if grain == "all" else f"{grain.upper()} {bucket} / GROUP",
            rows=aggregated["rows"],
            periods=[YieldReportPeriod(period=g.id, label=g.label,
                lot_count=by_group[g.id]["lot_count"], wf_count=by_group[g.id]["wf_count"]) for g in groups],
            comparison=comparison)
        part = _render_report(presentation, data.lot_cd, data.metadata, [],
                              delta_definition=delta_definition, allow_empty_metadata=True)
        rendered.append(part)
        counts.extend({"grain": grain, "bucket": bucket, **r} for r in aggregated["counts"])
        values.extend({"grain": grain, "bucket": bucket, **r} for r in aggregated["rows"])
        cells.extend({"grain": grain, "bucket": bucket, **r} for r in part.tables[0].rows)
    result = rendered[0]
    html = result.artifacts[0]["data"]
    for other in rendered[1:]:
        body = other.artifacts[0]["data"].split("<body>", 1)[1].rsplit("</body>", 1)[0]
        html = html.replace("</body>", body + "</body>")
    result.artifacts[0]["data"] = html
    metadata_table = result.tables[-1]
    result.tables = [
        ResultTable(table_id="group_counts", title="Official group counts", rows=counts,
                    columns=["grain", "bucket", "period", "lot_count", "wf_count", "has_yield_data"], complete=True),
        ResultTable(table_id="group_yield_long", title="Official group parameter averages", rows=values,
                    columns=["grain", "bucket", "period", "process", "param", "value"], complete=True),
        ResultTable(table_id="displayed_yield_table", title=args.title, rows=cells,
                    columns=["grain", "bucket", "row", "column", "raw", "shown"], complete=True),
        metadata_table,
    ]
    missing = [{"grain": r["grain"], "bucket": r["bucket"], "group_id": r["period"]}
               for r in counts if not r["has_yield_data"]]
    result.scope.update(row_count=len(counts), groups=[g.model_dump() for g in groups],
        missing_group_buckets=missing, default_table_id="group_counts",
        gms_unavailable={"reason": "not_available_at_lot_group_granularity"},
        aggregation="Source-row AVG(VALUE); not an average of lot means",
        comparison=comparison.model_dump() if comparison else None,
        comparison_source=comparison_source)
    result.status = "empty" if not any(r["lot_count"] for r in counts) else "partial" if missing else "success"
    if member_grain == "wafer":
        result.scope["aggregation"] = "Source-row AVG(VALUE) over selected (LOT_ID, WFID); not an average of wafer means"
        result.status = "empty" if not any(r["lot_count"] or r["has_yield_data"] for r in counts) else "partial" if missing else "success"
    result.summary = (f"LOT_CD={data.lot_cd} {data.window['start']}~{data.window['end']}: "
                      f"{len(groups)} groups; counts in group_counts; {result.scope['selection_rule']}; "
                      "group GMS unavailable; selection meaning requires verification")
    return result


def _render_report(args, lot_cd, metadata, source_result_ids, *, delta_definition=None, allow_empty_metadata=False, unit="weekly"):
    metadata_columns = ["PROC_GBN_CD", "PARAM_NM", "PARAM_SEQNO", "PARAM_KIND_CD", "YIELD_TABLE"]
    metadata_rows = [
        {**dict(zip(metadata_columns, row)), "displayed": str(row[4]).strip() == "o"}
        for row in metadata
    ]
    displayed_metadata = [row for row in metadata_rows if row["displayed"]]
    if not displayed_metadata and not allow_empty_metadata:
        return ToolResult(status="error", source_result_ids=source_result_ids,
                          summary=f"LOT_CD={lot_cd!r}: no YIELD_TABLE='o' parameters found in DF_ALL_PARA_PRB")
    parameter_columns = {"pt1h": [], "pt1c": []}
    for row in displayed_metadata:
        parameter_columns[_normalize_process(row["PROC_GBN_CD"])].append(row["PARAM_NM"])
    report_data = _report_render_data(args, parameter_columns)
    displayed_rows = []
    html = _html(args, report_data, displayed_rows, unit=unit)
    displayed_columns = ["lot", "wf",
                         *(f"PT1H TEST/{p}" for p in parameter_columns["pt1h"]),
                         *(f"PT1C TEST/{p}" for p in parameter_columns["pt1c"]),
                         *(f"GMS/{p}" for p in GMS_COLUMNS)]
    row_count = len(report_data.rows)
    column_count = len(report_data.column_keys)
    parameter_count = len(report_data.parameter_keys)
    selection_counts = {flag: sum(str(row["YIELD_TABLE"]).strip() == flag for row in metadata_rows)
                        for flag in ("o", "x")}
    selection_rule = f"DF_ALL_PARA_PRB LOT_CD={lot_cd}: TRIM(YIELD_TABLE)='o' shown / 'x' excluded"
    selection_order = "PT1H then PT1C by PARAM_SEQNO"
    if delta_definition is None:
        delta_definition = "Δ = current − baseline" if args.comparison else "Δ absent (no declared comparison)"
    delta_definition += " (parameters ×100, GMS raw)"
    table_ids = ["displayed_yield_table", "column_metadata"]
    scope = {
        "title": args.title, "row_count": row_count,
        "column_count": column_count, "parameter_count": parameter_count,
        "lot_cd": lot_cd, "displayed_columns": displayed_columns,
        "selection_rule": selection_rule, "selection_order": selection_order,
        "selection_counts": selection_counts, "delta_definition": delta_definition,
        "comparison": args.comparison.model_dump() if args.comparison else None,
        "tables_available": table_ids, "default_table_id": "displayed_yield_table",
    }
    return ToolResult(
        summary=(
            f"{selection_rule}; {delta_definition}; {selection_order}; "
            f"o={selection_counts['o']}, x={selection_counts['x']}; "
            f"tables: {', '.join(table_ids)}"
        ),
        scope=scope,
        tables=[ResultTable(table_id="displayed_yield_table", title=args.title,
                            rows=displayed_rows, columns=["row", "column", "raw", "shown"], complete=True),
                ResultTable(table_id="column_metadata", title="Column metadata",
                            rows=metadata_rows, columns=[*metadata_columns, "displayed"], complete=True)],
        artifacts=[{"title": args.title, "mime": "text/html", "data": html}],
        source_result_ids=source_result_ids,
    )


def _render_period_report(args: YieldTableInput, data: PeriodReportData) -> ToolResult:
    """Render each grain through the same pivot/HTML path and attach shared evidence."""
    rendered, count_tables, gms_tables, displayed_rows = [], [], [], []
    missing, missing_gms, deltas = {}, {}, {}
    totals = {
        "lot_cd": data.lot_cd, "start": data.window["start"], "end": data.window["end"],
        "counts_process": "PT1H TEST", "period_lot_count": data.period_lot_count,
        "period_wafer_count": data.period_wafer_count,
        "definition": "period_* = distinct over resolved window; bucket counts overlap, do not add them",
    }
    sql_filters = {
        "DF_ALL_PARA_PRB": {"LOT_CD": data.lot_cd},
        "DF_WF_ALL_PRB_V2": {"LOT_CD": data.lot_cd, "processes": ["PT1H TEST", "PT1C TEST"],
                              "window": data.window, "yield_parameters": "TRIM(YIELD_TABLE)='o'",
                              "counts_process": "PT1H TEST"},
    }
    for grain, (key, unit) in GRAINS.items():
        if grain not in data.counts:
            continue
        buckets, counts = data.window[key], data.counts[grain]
        gms = {row[grain]: row for row in data.gms[grain]}
        render_args = _ReportInput(
            title=args.title, product_label=data.lot_cd,
            period_label={"month": "MONTH", "week": "WEEK", "day": "DATE"}[grain],
            rows=[YieldReportRow(period=row["bucket"], process=row["process"],
                                 param=row["param"], value=row["value"])
                  for row in data.yield_long if row["grain"] == grain],
            periods=[YieldReportPeriod(
                period=row[grain], lot_count=row["lot_count"], wf_count=row["wafer_count"],
                gms=YieldReportGms(**{k: gms.get(row[grain], {}).get(k) for k in ("fab", "prb", "cum0")}),
            ) for row in counts],
            comparison=ReportComparison(current=buckets[-1], baseline=buckets[-2]) if len(buckets) > 1 else None,
        )
        result = _render_report(render_args, data.lot_cd, data.metadata, [],
                                delta_definition=f"Δ = latest {grain} − previous {grain}" if len(buckets) > 1 else "Δ absent (single bucket)",
                                allow_empty_metadata=True, unit=unit)
        rendered.append(result)
        deltas[grain] = result.scope["delta_definition"]
        displayed_rows.extend({"grain": grain, **row} for row in result.tables[0].rows)
        totals.update({key: ",".join(buckets), f"{grain}_count": len(buckets),
                       f"sum_of_{unit}_lot_count": sum(row["lot_count"] or 0 for row in counts),
                       f"sum_of_{unit}_wafer_count": sum(row["wafer_count"] or 0 for row in counts)})
        count_tables.append(ResultTable(table_id=f"{unit}_counts", title=f"{unit.title()} counts", rows=counts,
                                        columns=[grain, "lot_count", "wafer_count", "has_yield_data", "has_gms"], complete=True))
        gms_tables.append(ResultTable(table_id=f"gms_{unit}", title=f"{unit.title()} GMS", rows=data.gms[grain],
                                      columns=[grain, "period_date", "fab", "prb", "cum0"], complete=True))
        missing[grain] = [r[grain] for r in counts if not r["has_yield_data"]]
        missing_gms[grain] = [r[grain] for r in counts if not r["has_gms"]]
        sql_filters[f"DF_GMS_YIELD_{unit.upper()}"] = {
            "LOTCODE": data.lot_cd, "PERIOD_DATE": [b.replace("-", "") for b in buckets]}
    result = rendered[0]
    # Reuse the first document's existing styles and append the other table bodies.
    html = result.artifacts[0]["data"]
    for other in rendered[1:]:
        body = other.artifacts[0]["data"].split("<body>", 1)[1].rsplit("</body>", 1)[0]
        html = html.replace("</body>", body + "</body>")
    result.artifacts[0]["data"] = html
    result.tables = [
        ResultTable(table_id="report_totals", title="Period distinct totals", rows=[totals], columns=list(totals), complete=True),
        *count_tables,
        ResultTable(table_id="yield_long", title="Displayed parameter averages", rows=data.yield_long,
                    columns=["grain", "bucket", *data.counts, "process", "param", "value"], complete=True),
        *gms_tables,
        ResultTable(table_id="displayed_yield_table", title=args.title, rows=displayed_rows,
                    columns=["grain", "row", "column", "raw", "shown"], complete=True),
        result.tables[-1],
    ]
    result.summary = (
        f"LOT_CD={data.lot_cd} {data.window['start']}~{data.window['end']}: "
        f"기간 distinct lot {data.period_lot_count}, wafer {data.period_wafer_count}; "
        f"{result.scope['selection_rule']}; {'; '.join(deltas.values())}; "
        "기간 수는 report_totals, 버킷 수는 grain counts 표만 인용; 더하지 않는다"
    )
    result.scope.update({
        "period": args.period if isinstance(args.period, dict) else args.period.model_dump(exclude_none=True), "window": data.window,
        "sql_filters": sql_filters, "missing_buckets": missing, "missing_gms": missing_gms,
        "row_count": sum(len(rows) for rows in data.counts.values()),
        "delta_definitions": deltas,
        "delta_definition": "; ".join(deltas.values()),
        "comparisons": {grain: {"current": data.window[GRAINS[grain][0]][-1],
                                 "baseline": data.window[GRAINS[grain][0]][-2]}
                        if len(data.window[GRAINS[grain][0]]) > 1 else None for grain in data.counts},
        "query_consistency": "Single connection, statement-level reads; no cross-statement snapshot guarantee",
        "gms_source_columns": ["FAB_YIELD", "PRB_YIELD", "CUM0_YIELD"],
        "gms_unavailable": "CUM2/PNT have no source column; shown as '-'; missing GMS buckets also shown as '-'",
        "gms_bucket_scope": "GMS is the source table's whole-bucket value; yield/counts use the resolved date window (including clipped edge buckets)",
        "tables_available": [table.table_id for table in result.tables],
        "default_table_id": "report_totals",
    })
    if "weeks" in data.window:
        result.scope.update(resolved_weeks=data.window["weeks"], missing_weeks=missing["week"])
    result.status = ("empty" if not data.yield_long and not any(
        r["lot_count"] or r["wafer_count"] for rows in data.counts.values() for r in rows
    ) else "partial" if any(missing.values()) else "success")
    return result


def register(registry):
    registry.add(ToolSpec(
        "render_yield_table",
        "표·리포트를 요청한 경우 사용하는 표준 수율 리포트다. lot_cd+period 또는 이전 report_scope_id 중 하나를 전달한다. "
        "period는 recent(weeks/months/days 중 하나의 count, 선택적 grains) 또는 명시적 weeks/months/days 목록이다. "
        "최근 기간은 PT1H TEST 최신 DB 측정일 기준이며 오늘 날짜를 계산하지 않는다. ISO 주차와 빈 버킷을 보존한다. "
        "결과 lot_population의 전체 lot_id, wafer_count, pt1h_mean, pt1h_value_count로 Python에서 그룹을 선택한다. "
        "grouping.membership은 {kind:'table',result_id,table_id}로 group_id,lot_id 표를 참조한다. "
        "wafer 멤버십은 member_grain:'wafer'와 group_id,lot_id,wf_id 열(PRB WFID 원문 문자열)을 사용한다. "
        "멤버십은 query_db/run_python으로 구성하고 OPER_ID별 EQP_ID 등 공식 그룹 수율 표는 이 도구로 렌더링한다. "
        "동일 OPER_ID의 wafer가 여러 EQP 행에 매칭되면 임의 중복 제거 없이 clarify한다. "
        "단일 표이면 table_id 생략 가능, 여러 표이면 반드시 지정한다. 소수 lot은 {kind:'inline',groups:[{group_id,lot_ids}]}도 가능하다. "
        "그룹 호출은 이전 render_yield_table의 report_scope_id(또는 lot_cd+period)와 grouping.membership이 필수다. "
        "grouping.groups=[{id,label}] 생략 시 멤버십 순서를 사용한다. grouping.coverage 기본 disjoint_subsets는 단일 부분집합도 허용하며 "
        "partition은 선택한 member_grain 전체 모집단의 정확한 분할이다. definition은 선택 기준 설명이며 도구는 해석하지 않는다. "
        "layout은 period/group/group_period이며 group_period는 같은 그룹의 버킷별 추이다. "
        "comparison={current,baseline}은 특정 비교 요청 시에만 전달하며 방향은 current−baseline이다. "
        "생략 시 2개 이상 그룹은 표시 순서의 마지막−첫 그룹 Δ를 추가하며 group_period는 버킷마다 적용한다. "
        "도구가 distinct LOT_ID/GORUPKEY, source-row AVG(VALUE), DF_ALL_PARA_PRB YIELD_TABLE='o' 열, "
        "PT1H 다음 PT1C PARAM_SEQNO 순서와 ×100 소수 1자리(% 없음)를 계산한다. 수율 셀이나 열 매핑을 만들지 않는다. "
        "기간 합계는 report_totals이며 버킷 수를 더하지 않는다. 그룹 GMS는 적용 불가로 '-'이다. "
        "반환 report_scope_id는 저장된 범위/집계/모집단을 재사용하며 recent를 다시 계산하지 않는다.",
        YieldTableInput, render_yield_table, read_only=True, effect="artifact", eager=True,
    ))

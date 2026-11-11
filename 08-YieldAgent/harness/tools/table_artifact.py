import asyncio
import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from ..types import Contract, ResultTable
from .registry import ToolResult, ToolSpec
from yield_viz import GMS_COLUMNS, _build_html_table


class YieldReportRow(Contract):
    period: str = Field(min_length=1, description="Arbitrary group name by default; ISO week only when period_kind='week'.")
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


class ReportParameterFormat(Contract):
    process: str
    param: str
    kind: Literal["number", "ratio", "percent"] = "number"
    decimals: int = Field(default=2, ge=0, le=8)


class YieldLongColumns(Contract):
    period: str = "period"
    process: str = "process"
    param: str = "param"
    value: str = "value"


class YieldCountsColumns(Contract):
    period: str = "period"
    lot: str = "lot_count"
    wf: str = "wf_count"


class YieldGmsColumns(Contract):
    period: str = "period"
    cum0: str = "cum0"
    cum2: str = "cum2"
    fab: str = "fab"
    prb: str = "prb"
    pnt: str = "pnt"


class YieldTableInput(Contract):
    title: str = Field(min_length=1, max_length=200)
    product_label: str | None = Field(
        default=None,
        description="Optional product/lot label passed to the legacy yield report builder.",
    )
    lot_cd: str | None = Field(default=None, description="Metadata LOT_CD; defaults to stripped product_label.")
    rows: list[YieldReportRow] = Field(
        default_factory=list,
        description=(
            "Inline report LONG rows shaped as "
            "{period, process, param, value}; long_result_id is the saved-result alternative."
        )
    )
    periods: list[YieldReportPeriod] = Field(
        default_factory=list,
        description=(
            "Optional report metadata shaped as "
            "{period, label, lot_count, wf_count, gms:{cum0,cum2,fab,prb,pnt}}. When omitted, group names and order come from first appearance in LONG rows, then counts/GMS. When supplied, it declares all groups in display order; LONG rows use matching period IDs. label optionally overrides the displayed group name."
        ),
    )
    period_kind: Literal["week", "group"] = Field(
        default="group",
        description="Defaults to group: period is an arbitrary group name, without date parsing. Use explicit week for ISO-week normalization, chronological sorting and the existing weekly report comparison.",
    )
    period_label: str | None = Field(default=None, min_length=1)
    comparison: ReportComparison | None = Field(
        default=None, description="Report delta is current minus baseline using period IDs. Group reports show no delta unless this is supplied.",
    )
    parameter_formats: list[ReportParameterFormat] = Field(
        default_factory=list,
        description="Accepted for compatibility; ignored in week and group reports. Metadata parameter values always use x100, one decimal, no percent sign.",
    )
    long_result_id: str | None = Field(
        default=None,
        description="Saved query_db result containing report LONG rows.",
    )
    long_columns: YieldLongColumns = Field(default_factory=YieldLongColumns)
    counts_result_id: str | None = Field(
        default=None,
        description="Saved query_db result containing period, LOT count, and WF count rows.",
    )
    counts_columns: YieldCountsColumns = Field(default_factory=YieldCountsColumns)
    gms_result_id: str | None = Field(
        default=None,
        description="Saved query_db result containing period and GMS value rows.",
    )
    gms_columns: YieldGmsColumns = Field(default_factory=YieldGmsColumns)

    @model_validator(mode="after")
    def validate_presentation(self):
        for columns_field, result_id_field in (
            ("long_columns", "long_result_id"),
            ("counts_columns", "counts_result_id"),
            ("gms_columns", "gms_result_id"),
        ):
            if (
                columns_field in self.model_fields_set
                and getattr(self, result_id_field) is None
            ):
                raise ValueError(f"{columns_field} requires {result_id_field}")

        if self.rows and self.long_result_id:
            raise ValueError("provide rows or long_result_id, not both")
        declared = {_period_key(self, p.period) for p in self.periods}
        if len(declared) != len(self.periods):
            raise ValueError("duplicate normalized report period")
        parsed_rows = self.rows
        if self.comparison:
            current = _period_key(self, self.comparison.current)
            baseline = _period_key(self, self.comparison.baseline)
            if current == baseline:
                raise ValueError("comparison current and baseline must differ")
            known = declared | {_period_key(self, row.period) for row in parsed_rows}
            if not any((self.long_result_id, self.counts_result_id, self.gms_result_id)) and not {current, baseline} <= known:
                raise ValueError("comparison refers to an unknown period")
        formats = set()
        for fmt in self.parameter_formats:
            key = (_normalize_process(fmt.process), fmt.param)
            if key in formats:
                raise ValueError("duplicate parameter format")
            formats.add(key)
        seen_rows: set[tuple[str, str, str]] = set()
        for row in parsed_rows:
            if row.value is not None and not math.isfinite(row.value):
                raise ValueError("report values must be finite")
            key = (
                _period_key(self, row.period),
                _normalize_process(row.process),
                row.param,
            )
            if self.period_kind == "group" and self.periods:
                if key[0] not in declared:
                    raise ValueError(f"undeclared group ID: {key[0]}")
            if key in seen_rows:
                raise ValueError("duplicate normalized report row")
            seen_rows.add(key)

        if not self.long_result_id:
            present = {(process, param) for _, process, param in seen_rows}
            if formats - present:
                raise ValueError(f"parameter format refers to missing parameter: {sorted(formats - present)[0]}")
        labels = [(p.label or p.period).strip() for p in self.periods]
        if self.period_kind == "group" and (not all(labels) or len(set(labels)) != len(labels)):
            raise ValueError("group labels must be unique")
        seen_periods: set[str] = set()
        for period in self.periods:
            key = _period_key(self, period.period)
            if any(value is not None and not math.isfinite(value) for value in period.gms.model_dump().values()):
                raise ValueError("GMS values must be finite")
            if key in seen_periods:
                raise ValueError("duplicate normalized report period")
            seen_periods.add(key)
        return self


def _period_key(args: YieldTableInput, value: str) -> str:
    if args.period_kind == "week":
        return _normalize_week(value)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("group ID must be a nonempty string without surrounding whitespace")
    return value


def _normalize_week(period: str) -> str:
    value = period.strip()
    match = re.fullmatch(r"(\d{4})-W(\d{2})", value, re.IGNORECASE)
    if match is None:
        match = re.fullmatch(r"(\d{4})-(\d{2})", value)
    if match is None:
        match = re.fullmatch(r"(\d{4})(\d{2})", value)
    if match is None:
        raise ValueError("period must be YYYY-Www, YYYY-ww, or YYYYww")
    year, week = (int(part) for part in match.groups())
    try:
        date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise ValueError(f"invalid ISO week: {period}") from exc
    return f"{year:04d}-W{week:02d}"


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


def _group_periods(args: YieldTableInput) -> list[YieldReportPeriod]:
    return args.periods or [
        YieldReportPeriod(period=period)
        for period in dict.fromkeys(row.period for row in args.rows)
    ]


def _fetch_parameter_metadata(lot_cd: str) -> list[tuple]:
    """Read report columns using the same connection factory as query_db."""
    from .query_db import _connection

    connection = cursor = None
    try:
        connection = _connection()
        connection.call_timeout = 60_000
        cursor = connection.cursor()
        cursor.execute(
            "SELECT PROC_GBN_CD, PARAM_NM, PARAM_SEQNO, PARAM_KIND_CD, YIELD_TABLE FROM DF_ALL_PARA_PRB "
            "WHERE LOT_CD=:lot "
            "ORDER BY CASE PROC_GBN_CD WHEN 'PT1H' THEN 0 WHEN 'PT1C' THEN 1 ELSE 2 END, PARAM_SEQNO",
            {"lot": lot_cd},
        )
        return cursor.fetchall()
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            if connection is not None:
                connection.close()


def _pivot_report_rows(args: YieldTableInput, parameter_columns: dict[str, list[str]]) -> list[dict]:
    periods = {_period_key(args, row.period) for row in args.rows}
    periods.update(_period_key(args, period.period) for period in args.periods)
    ordered = [p.period for p in _group_periods(args)] if args.period_kind == "group" else sorted(periods)
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


def _report_render_data(args: YieldTableInput, parameter_columns: dict[str, list[str]]) -> _ReportRenderData:
    parameter_keys = [*parameter_columns["pt1h"],
                      *(f"pt1c_{p}" for p in parameter_columns["pt1c"])]
    return _ReportRenderData(
        rows=_pivot_report_rows(args, parameter_columns),
        column_keys=["week", "lotcount", "wfCount", *parameter_keys,
                     *(f"gms_{p}" for p in GMS_COLUMNS)],
        parameter_keys=parameter_keys,
        parameter_columns=parameter_columns,
    )


def _html(args: YieldTableInput, report_data: _ReportRenderData,
          displayed_rows: list[dict] | None = None) -> str:
    ids = ([p.period for p in _group_periods(args)] if args.period_kind == "group" else
           sorted({_period_key(args, row.period) for row in args.rows} |
                  {_period_key(args, p.period) for p in args.periods}))
    comparison = None
    if args.comparison:
        comparison = (ids.index(_period_key(args, args.comparison.baseline)),
                      ids.index(_period_key(args, args.comparison.current)))
    elif args.period_kind == "week" and len(ids) >= 2:
        comparison = (len(ids) - 2, len(ids) - 1)
    # Caller formats remain accepted, but the report contract determines units.
    formats = {key: ("scaled", 1) for key in report_data.parameter_keys}
    formats.update({f"gms_{p}": ("number", 1) for p in GMS_COLUMNS})
    return _build_html_table(
        report_data.rows, args.product_label or "", unit="weekly",
        parameter_columns=report_data.parameter_columns,
        parameter_formats=formats, comparison=comparison,
        period_label=args.period_label or ("GROUP" if args.period_kind == "group" else "WEEK"),
        displayed_rows=displayed_rows,
    )


async def _saved_rows(result_id: str, columns: dict[str, str], ctx) -> list[dict]:
    try:
        page = await ctx.store.read_result(
            ctx.run["principal_id"],
            ctx.run["session_id"],
            result_id,
            offset=0,
            limit=1000,
            columns=list(columns.values()),
        )
    except PermissionError:
        raise PermissionError(f"Result unavailable: {result_id}") from None
    except ValueError as exc:
        if str(exc) != "Unknown column":
            raise
        available_page = await ctx.store.read_result(
            ctx.run["principal_id"],
            ctx.run["session_id"],
            result_id,
            offset=0,
            limit=1,
        )
        available = available_page["columns"]
        missing_key, missing_column = next(
            (key, column)
            for key, column in columns.items()
            if column not in available
        )
        available_text = ", ".join(available) if available else "(none)"
        raise ValueError(
            f"result {result_id} mapping key '{missing_key}' refers to unknown "
            f"column '{missing_column}'; available columns: {available_text}"
        ) from None
    if page.get("complete") is False or page.get("truncated") or page.get("total_rows", len(page["rows"])) > len(page["rows"]):
        raise ValueError(f"result {result_id} is incomplete or exceeds 1000 rows; aggregate it before rendering")
    return [
        {target: row.get(source) for target, source in columns.items()}
        for row in page["rows"]
    ]


async def _resolve_report_results(args: YieldTableInput, ctx) -> YieldTableInput:
    rows = list(args.rows)
    periods = {
        _period_key(args, period.period): period.model_dump()
        for period in args.periods
    }

    if args.long_result_id:
        rows = [YieldReportRow.model_validate(row) for row in await _saved_rows(
            args.long_result_id, args.long_columns.model_dump(), ctx,
        )]
    if args.period_kind == "group" and not args.periods:
        for row in rows:
            periods.setdefault(row.period, {"period": row.period, "gms": {}})
    # Week reports show only weeks present in yield rows or declared periods, like the
    # legacy report. Extra count/GMS weeks are dropped. Without either, keep source weeks.
    report_weeks = None
    if args.period_kind == "week" and (rows or periods):
        report_weeks = set(periods) | {_period_key(args, row.period) for row in rows}
    if args.counts_result_id:
        seen_counts = set()
        for source in await _saved_rows(
            args.counts_result_id, args.counts_columns.model_dump(), ctx,
        ):
            key = _period_key(args, source["period"])
            if report_weeks is not None and key not in report_weeks:
                continue
            period = periods.setdefault(key, {"period": source["period"], "gms": {}})
            if key in seen_counts:
                raise ValueError(f"duplicate counts period: {key}")
            seen_counts.add(key)
            if args.period_kind == "group" and args.periods and key not in {p.period for p in args.periods}:
                raise ValueError(f"undeclared group ID: {key}")
            for field, value in (("lot_count", source["lot"]), ("wf_count", source["wf"])):
                if period.get(field) is not None and period[field] != value:
                    raise ValueError(f"conflicting {field} for period: {key}")
            period["lot_count"] = source["lot"]
            period["wf_count"] = source["wf"]
    if args.gms_result_id:
        seen_gms = set()
        gms_mapping = args.gms_columns.model_dump()
        explicitly_mapped = args.gms_columns.model_fields_set
        if len(explicitly_mapped) < len(gms_mapping):
            schema = await ctx.store.read_result(
                ctx.run["principal_id"], ctx.run["session_id"],
                args.gms_result_id, offset=0, limit=1,
            )
            available = schema["columns"]
            for field in gms_mapping:
                if field in explicitly_mapped:
                    continue
                standard = "PERIOD_DATE" if field == "period" else f"{field.upper()}_YIELD"
                if standard in available:
                    gms_mapping[field] = standard
            gms_mapping = {
                key: value for key, value in gms_mapping.items()
                if key == "period" or key in explicitly_mapped or value in available
            }
        for source in await _saved_rows(
            args.gms_result_id, gms_mapping, ctx,
        ):
            key = _period_key(args, source["period"])
            if report_weeks is not None and key not in report_weeks:
                continue
            period = periods.setdefault(key, {"period": source["period"], "gms": {}})
            if key in seen_gms:
                raise ValueError(f"duplicate GMS period: {key}")
            seen_gms.add(key)
            if args.period_kind == "group" and args.periods and key not in {p.period for p in args.periods}:
                raise ValueError(f"undeclared group ID: {key}")
            for name, value in source.items():
                if name != "period" and period.get("gms", {}).get(name) is not None and period["gms"][name] != value:
                    raise ValueError(f"conflicting GMS {name} for period: {key}")
            period["gms"] = {
                name: source.get(name)
                for name in ("cum0", "cum2", "fab", "prb", "pnt")
            }

    return YieldTableInput.model_validate({
        **args.model_dump(exclude={
            "long_columns", "counts_columns", "gms_columns",
        }),
        "rows": rows,
        "periods": list(periods.values()),
        "long_result_id": None,
        "counts_result_id": None,
        "gms_result_id": None,
    })


async def render_yield_table(args: YieldTableInput, ctx) -> ToolResult:
    source_result_ids = [
        result_id for result_id in (
            args.long_result_id, args.counts_result_id, args.gms_result_id,
        ) if result_id
    ]
    if source_result_ids:
        try:
            args = await _resolve_report_results(args, ctx)
        except PermissionError as exc:
            return ToolResult(
                summary=str(exc),
                status="error",
                source_result_ids=source_result_ids,
            )
    lot_cd = (args.lot_cd if args.lot_cd is not None else args.product_label or "").strip()
    metadata = await asyncio.to_thread(_fetch_parameter_metadata, lot_cd)
    metadata_columns = ["PROC_GBN_CD", "PARAM_NM", "PARAM_SEQNO", "PARAM_KIND_CD", "YIELD_TABLE"]
    metadata_rows = [
        {**dict(zip(metadata_columns, row)), "displayed": str(row[4]).strip() == "o"}
        for row in metadata
    ]
    displayed_metadata = [row for row in metadata_rows if row["displayed"]]
    if not displayed_metadata:
        return ToolResult(status="error", source_result_ids=source_result_ids,
                          summary=f"LOT_CD={lot_cd!r}: no YIELD_TABLE='o' parameters found in DF_ALL_PARA_PRB")
    parameter_columns = {"pt1h": [], "pt1c": []}
    for row in displayed_metadata:
        parameter_columns[_normalize_process(row["PROC_GBN_CD"])].append(row["PARAM_NM"])
    report_data = _report_render_data(args, parameter_columns)
    displayed_rows = []
    html = _html(args, report_data, displayed_rows)
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
    if args.period_kind == "week" and not args.comparison:
        delta_definition = "Δ = latest week − previous week"
    elif args.comparison:
        delta_definition = "Δ = current − baseline"
    else:
        delta_definition = "Δ absent (no declared comparison)"
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


def register(registry):
    registry.add(ToolSpec(
        "render_yield_table",
        "기존 pt1h·pt1c·gms 수율 리포트 전용 렌더러다. 행은 주차/그룹이고 열은 파라미터다. 기존 디자인의 리포트를 요청하면 이 렌더러로 시각 결과를 "
        "생성한다. Python은 조회 결과의 정리·그룹 집계·계산에 사용할 수 있다. 기본 period_kind='group'이며 "
        "LONG rows의 period에 임의 그룹 이름을 바로 넣는다. 별도 periods는 선택 사항이고, 생략하면 LONG rows, counts, GMS의 "
        "첫 등장 순서로 표시한다. periods를 주면 모든 그룹의 ID와 표시 순서를 선언하며 label로 표시 이름을 바꿀 수 있다. "
        "comparison={current,baseline}을 지정할 때만 그룹 Δ를 표시한다. parameter_formats는 호환용으로 받지만 무시한다. "
        "그룹 멤버십과 날짜 범위는 원본 조회로 확인하고 라벨로 추측하지 않는다. 주차 보고서는 period_kind='week'를 명시하고 "
        "DF_WF_ALL_PRB_V2에서 PARAM_NM을 필터링하지 않고 모든 PARAM_NM을 "
        "WEEK_HYNIX·PROCESS·PARAM_NM별 AVG(VALUE) LONG rows로 집계한다. 최근 N주는 기존 리포트와 같이 기준일이 속한 "
        "주를 포함하고 오래된 주부터 정렬한다. PT1H 기준 주차별 distinct LOT_ID 수 및 distinct GORUPKEY 수(wafer 수), "
        "DF_GMS_YIELD_WEEKLY의 주차별 GMS를 전달한다. 주차 보고서 행은 LONG rows나 periods에 있는 주차만 표시하며 그 밖의 counts·GMS 주차는 버린다. 긴 rows를 붙여넣지 말고 query_db의 long_result_id, "
        "counts_result_id, gms_result_id와 필요시 각 columns 매핑을 전달한다. inline rows와 periods도 계속 지원한다. "
        "gms_columns의 명시적 매핑을 우선하며, 미지정 필드는 결과에 존재하는 PERIOD_DATE 및 "
        "CUM0_YIELD, CUM2_YIELD, FAB_YIELD, PRB_YIELD, PNT_YIELD로 자동 매핑한다. "
        "주차·그룹 모두 렌더러가 lot_cd(생략 시 product_label)의 DF_ALL_PARA_PRB YIELD_TABLE='o'를 조회하여 "
        "PT1H 다음 PT1C, 각각 PARAM_SEQNO 순서의 정확한 PARAM_NM 열을 표시한다. 파라미터 값은 x100 소수 1자리, % 없이 표시하고 "
        "GMS는 원값 소수 1자리, lot/wafer 수는 정수다. 답변은 전체 파라미터가 아니라 YIELD_TABLE='o' 파라미터라고 설명한다. "
        "사용자가 표·아티팩트·리포트 형태를 요청한 경우에만 호출하고 일반 수율 조회에는 호출하지 않는다.",
        YieldTableInput,
        render_yield_table,
        read_only=True,
        effect="artifact",
        eager=True,
    ))

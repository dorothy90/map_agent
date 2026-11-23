"""Read wafer MAP payloads for a raw die table or an on-demand cumulative map."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
from itertools import groupby
import json
from operator import itemgetter
import re
from typing import Annotated, Literal
from zoneinfo import ZoneInfo
import uuid

from pydantic import Field, model_validator

from bin_mapping import PASS_BIN
from ..runtime.domain_process import run_blocking
from ..types import Contract, ResultTable
from .map_aggregate import CummapAggregation
from .registry import ToolResult, ToolSpec


DIE_COLUMNS = [
    "measurement_id", "lot_id", "wf_id", "lot_cd", "oper", "start_tm", "end_tm",
    "row", "col", "left_bin", "right_bin", "bin", "fail_bin_name",
]


def map_measurements(sources, *, members=None):
    """Summarize complete, contiguous normalized measurements, optionally selected by source member identity."""
    preview = []
    count = 0
    pass_pair = PASS_BIN * 2
    for source_id, dies in sources.items():
        for identity, group in groupby(dies, key=itemgetter("measurement_id")):
            first = next(group)
            key = (source_id, first["lot_id"], str(first["wf_id"]), identity)
            if members is not None and key not in members:
                continue
            count += 1
            if len(preview) >= 10:
                continue
            total, passed = 1, int(first["bin"] == pass_pair)
            for row in group:
                total += 1
                passed += row["bin"] == pass_pair
            values = {name: first.get(name) for name in (
                "lot_cd", "lot_id", "wf_id", "oper", "start_tm", "end_tm", "measurement_id")}
            if source_id is not None:
                values["source_result_id"] = source_id
            preview.append(dict(values, pass_die_count=passed, die_count=total, map_pass_fraction=passed / total))
    if members is not None and count != len(members):
        return {"selection_status": "unverified", "preview_rows": []}
    return {
        "source_table": "wafer_map_dies", "grain": "MAP measurement with observed dies", "measurement_count": count,
        "preview_rows": preview, "truncated": count > len(preview),
        "metric": {"name": "map_pass_fraction", "unit": "fraction",
                   "numerator": "pass_die_count", "denominator": "die_count"},
        "wafer_yield_lookup": {"source": "DF_WF_ALL_PRB_V2", "value_column": "VALUE",
            "identity_columns": ["LOT_CD", "LOT_ID", "WFID", "PROCESS", "PARAM_NM", "MEASURETIME_START"],
            "time_reference": "For a corresponding test, MAP.END_TM equals MEASURETIME_START.",
            "status": "requires independent PRB query"},
    }


class RawMapPage(Contract):
    measurement_offset: int = Field(default=0, ge=0, le=1_000_000)
    max_measurements: int = Field(default=200, ge=1, le=1_000)
    max_dies: int = Field(default=500_000, ge=1, le=5_000_000)


class WaferGroup(Contract):
    label: str = Field(min_length=1, max_length=100)
    lot_id: str = Field(min_length=1, max_length=100)
    wf_ids: list[Annotated[int, Field(strict=True, ge=0)]] = Field(min_length=1, max_length=100)


class LoadWaferMapsInput(Contract):
    wafer_groups: list[WaferGroup] = Field(default_factory=list, max_length=100, description=(
        "For individual maps of named or remembered groups, send each label and its exact LOT/wafer pairs. "
        "Repeat the label for different lots in the same group."
    ))
    aggregate: CummapAggregation | None = Field(default=None, description=(
        "For a cumulative map supply the requested grouping. Scans all matching MAP measurements "
        "and returns the finished chart and per-coordinate counts in one call."
    ))
    lot_ids: list[str] = Field(default_factory=list, max_length=100, description=(
        "For raw binmaps supply lot_ids or wafer_groups. For cumulative maps, omit for the whole selected population; "
        "pass lot IDs only for an explicitly selected subset."
    ))
    lot_cd: str | None = Field(default=None, min_length=1, max_length=100)
    wf_ids: list[int] = Field(default_factory=list, max_length=100)
    oper: str | None = Field(default=None, min_length=1, max_length=100, description=(
        "The measured test on the source wafer maps, exact MAP.OPER_DET_DESC for this lot_cd. "
        "For manufacturing-process groupings, put the named manufacturing operation in aggregate.group_oper."))
    recent_weeks: int | None = Field(default=None, strict=True, gt=0, description=(
        "Count of calendar weeks ending at the latest MAP measurement for this product and test."))
    all_time: bool = Field(default=False, strict=True, description="True only for an explicitly unbounded period.")
    time_column: Literal["start_tm", "end_tm"] = "end_tm"
    start_time: datetime | None = None
    end_time: datetime | None = None
    raw_page: RawMapPage | None = Field(default=None, description=(
        "Only for individual raw binmaps. Omit when using aggregate, which scans the whole selection."
    ))

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_raw_page(cls, value):
        if not isinstance(value, dict):
            return value
        keys = value.keys() & RawMapPage.model_fields.keys()
        if not keys:
            return value
        if value.get("aggregate") is not None or "raw_page" in value:
            raise ValueError(f"Conflicting raw paging parameters: {', '.join(sorted(keys))}")
        raw = dict(value)
        raw["raw_page"] = {key: raw.pop(key) for key in keys}
        return raw

    @model_validator(mode="after")
    def valid_range(self):
        if self.aggregate is None and not (self.lot_ids or self.wafer_groups):
            raise ValueError("lot_ids or wafer_groups is required for raw wafer maps; use aggregate for a cumulative map.")
        if self.wafer_groups and (self.aggregate is not None or self.lot_ids or self.wf_ids):
            raise ValueError("Use wafer_groups alone to select individual LOT/wafer pairs.")
        dated = self.start_time is not None or self.end_time is not None
        if sum((self.recent_weeks is not None, self.all_time, dated)) > 1:
            raise ValueError("Choose one period: recent_weeks, start_time/end_time, or all_time.")
        if self.recent_weeks is not None and (not self.lot_cd or not self.oper or self.time_column != "end_tm"):
            raise ValueError("recent_weeks requires lot_cd, oper and time_column=end_tm.")
        if self.aggregate is not None and not self.lot_ids:
            if not self.lot_cd or not self.oper or not (self.recent_weeks or dated or self.all_time):
                raise ValueError("For a population cummap supply lot_cd, oper and its requested period "
                                 "(recent_weeks, start_time/end_time, or explicit all_time).")
        if self.aggregate is not None and self.raw_page is not None:
            raise ValueError("raw_page cannot be supplied for an aggregate")
        if self.start_time and self.end_time:
            if (self.start_time.utcoffset() is None) != (self.end_time.utcoffset() is None):
                raise ValueError("Use the same timezone convention for both time bounds.")
            if self.end_time <= self.start_time:
                raise ValueError("end_time must be after start_time")
        if len(set(self.lot_ids)) != len(self.lot_ids):
            raise ValueError("lot_ids must not contain duplicates")
        if len(set(self.wf_ids)) != len(self.wf_ids):
            raise ValueError("wf_ids must not contain duplicates")
        if self.aggregate is None and self.raw_page is None:
            self.raw_page = RawMapPage()
        return self

    @property
    def max_dies(self):
        return self.raw_page.max_dies

    @property
    def max_measurements(self):
        return self.raw_page.max_measurements

    @property
    def measurement_offset(self):
        return self.raw_page.measurement_offset


@dataclass(frozen=True)
class NormalizedMaps:
    die_rows: list[dict]
    measurement_count: int
    facts: dict


def _text(value):
    if callable(getattr(value, "read", None)):
        value = value.read()
    return value


def _time(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return None if value is None else str(value)


def _measurement_id(row: dict, payload: str) -> str:
    identity = [
        row.get("lot_id"), row.get("wf_id"), row.get("lot_cd"), row.get("oper"),
        _time(row.get("start_tm")), _time(row.get("end_tm")), payload,
    ]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, default=str).encode()).hexdigest()[:24]


def _parse_payload(payload) -> tuple[str, list[tuple[int, int, str, str]]]:
    payload = _text(payload)
    try:
        raw = json.loads(payload) if isinstance(payload, str) else payload
    except (TypeError, ValueError) as exc:
        raise ValueError("Malformed MAP_VAL_JSON") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("MAP"), list):
        raise ValueError("MAP_VAL_JSON must contain a MAP array")
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    cells, coordinates = [], set()
    for entry in raw["MAP"]:
        if not isinstance(entry, str):
            raise ValueError("MAP cells must be strings")
        parts = entry.split(",")
        if len(parts) < 4:
            raise ValueError("MAP cells require row, col, left bin and right bin")
        try:
            coordinate = (int(parts[0]), int(parts[1]))
        except (TypeError, ValueError) as exc:
            raise ValueError("MAP coordinates must be integers") from exc
        if coordinate in coordinates:
            raise ValueError(f"MAP contains duplicate coordinate {coordinate}")
        coordinates.add(coordinate)
        cells.append((*coordinate, parts[2], parts[3]))
    return canonical, cells


def normalize_map_rows(
    rows: list[dict], *, symbol_patterns: dict | None = None,
    max_measurements: int, max_dies: int, measurement_offset: int = 0,
    source_has_more: bool = False,
) -> NormalizedMaps:
    """Pure conversion of MAP source rows into complete-measurement die rows."""
    compiled = {
        key: [(name, re.compile(pattern)) for name, pattern in patterns.items()]
        for key, patterns in (symbol_patterns or {}).items()
    }
    die_rows, seen_measurements = [], set()
    duplicate_measurements = 0
    truncation_reason = None
    included_measurements = 0
    for source in rows:
        if included_measurements >= max_measurements:
            truncation_reason = "max_measurements"
            break
        canonical, cells = _parse_payload(source.get("map_val_json"))
        measurement_id = _measurement_id(source, canonical)
        if measurement_id in seen_measurements:
            duplicate_measurements += 1
            continue
        if len(die_rows) + len(cells) > max_dies:
            truncation_reason = "max_dies"
            break
        seen_measurements.add(measurement_id)
        included_measurements += 1
        lot_cd = None if source.get("lot_cd") is None else str(source["lot_cd"])
        oper = None if source.get("oper") is None else str(source["oper"])
        definitions = compiled.get((lot_cd, oper), [])
        for row, col, left_bin, right_bin in cells:
            bin_value = left_bin + right_bin
            fail_bin_name = next(
                (name for name, pattern in definitions if pattern.fullmatch(bin_value)), None
            )
            die_rows.append({
                "measurement_id": measurement_id,
                "lot_id": str(source["lot_id"]),
                "wf_id": int(source["wf_id"]),
                "lot_cd": lot_cd,
                "oper": oper,
                "start_tm": _time(source.get("start_tm")),
                "end_tm": _time(source.get("end_tm")),
                "row": row,
                "col": col,
                "left_bin": left_bin,
                "right_bin": right_bin,
                "bin": bin_value,
                "fail_bin_name": fail_bin_name,
            })

    if truncation_reason is None and source_has_more:
        truncation_reason = "max_measurements"
    truncated = truncation_reason is not None
    lots = sorted({row["lot_id"] for row in die_rows})
    wafers = sorted({row["wf_id"] for row in die_rows})
    wafer_keys = {(row["lot_id"], row["wf_id"]) for row in die_rows}
    row_values = [row["row"] for row in die_rows]
    col_values = [row["col"] for row in die_rows]
    facts = {
        "wafer_count": len(wafer_keys),
        "measurement_count": included_measurements,
        "die_count": len(die_rows),
        "row_range": [min(row_values), max(row_values)] if row_values else None,
        "col_range": [min(col_values), max(col_values)] if col_values else None,
        "bin_value_counts": dict(sorted(Counter(row["bin"] for row in die_rows).items())),
        "lots_found": lots,
        "wafers_found": wafers,
        "duplicate_measurement_count": duplicate_measurements,
        "truncated": truncated,
        "truncation_reason": truncation_reason,
        "measurement_offset": measurement_offset,
        "next_measurement_offset": measurement_offset + included_measurements if truncated else None,
    }
    return NormalizedMaps(die_rows, included_measurements, facts)


def _connection():
    from common import get_oracle_connection

    connection = get_oracle_connection()
    if hasattr(connection, "call_timeout"):
        connection.call_timeout = 60_000
    return connection


def recent_map_window(lot_cd: str, oper: str, weeks: int) -> tuple[datetime, datetime]:
    """Anchor the requested week count at this product and test's latest MAP measurement."""
    with _connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT TRUNC(MAX(END_TM)) FROM MAP WHERE LOT_CD = :lot_cd AND OPER_DET_DESC = :oper",
            {"lot_cd": lot_cd, "oper": oper},
        )
        anchor, = cursor.fetchone()
        if anchor is None:
            cursor.execute(
                "SELECT DISTINCT OPER_DET_DESC FROM MAP WHERE LOT_CD = :lot_cd AND END_TM IS NOT NULL "
                "AND OPER_DET_DESC IS NOT NULL ORDER BY OPER_DET_DESC FETCH FIRST 12 ROWS ONLY",
                {"lot_cd": lot_cd},
            )
            options = [name for (name,) in cursor.fetchall()]
            raise ValueError(f"No MAP measurement date for product {lot_cd} and test {oper}. "
                             f"Available MAP.OPER_DET_DESC for this product (up to 12): "
                             f"{json.dumps(options, ensure_ascii=False)}")
    return anchor - timedelta(weeks=weeks - 1, days=anchor.weekday()), anchor + timedelta(days=1)


def _configure_cursor(cursor):
    cursor.arraysize = 64
    try:
        import oracledb
    except ImportError:
        return

    def output_type_handler(active_cursor, metadata):
        if metadata.type_code is oracledb.DB_TYPE_CLOB:
            return active_cursor.var(oracledb.DB_TYPE_LONG, arraysize=active_cursor.arraysize)
        return None

    cursor.outputtypehandler = output_type_handler


def _filters(args: LoadWaferMapsInput):
    binds, predicates = {}, []
    if args.wafer_groups:
        pair_predicates = []
        for i, group in enumerate(args.wafer_groups):
            binds[f"group_lot_{i}"] = group.lot_id
            slots = []
            for j, wf_id in enumerate(group.wf_ids):
                name = f"group_wf_{i}_{j}"
                binds[name] = wf_id
                slots.append(f":{name}")
            pair_predicates.append(f"(LOT_ID = :group_lot_{i} AND WF_ID IN ({', '.join(slots)}))")
        predicates.append("(" + " OR ".join(pair_predicates) + ")")
    lot_slots = []
    for index, value in enumerate(args.lot_ids):
        key = f"lot_{index}"
        binds[key] = value
        lot_slots.append(f":{key}")
    if lot_slots:
        predicates.append(f"LOT_ID IN ({', '.join(lot_slots)})")
    if args.lot_cd is not None:
        predicates.append("LOT_CD = :lot_cd")
        binds["lot_cd"] = args.lot_cd
    if args.wf_ids:
        wafer_slots = []
        for index, value in enumerate(args.wf_ids):
            key = f"wf_{index}"
            binds[key] = value
            wafer_slots.append(f":{key}")
        predicates.append(f"WF_ID IN ({', '.join(wafer_slots)})")
    if args.oper is not None:
        binds["oper"] = args.oper
        predicates.append("OPER_DET_DESC = :oper")
    time_column = args.time_column.upper()
    if args.start_time is not None:
        binds["start_time"] = args.start_time
        predicates.append(f"{time_column} >= :start_time")
    if args.end_time is not None:
        binds["end_time"] = args.end_time
        predicates.append(f"{time_column} < :end_time")
    return " AND ".join(predicates) or "1=1", binds


def _load_symbol_patterns(cursor, rows: list[dict]) -> dict:
    pairs = sorted({(str(row["lot_cd"]), str(row["oper"])) for row in rows if row.get("lot_cd") and row.get("oper")})
    if not pairs:
        return {}
    lot_codes = sorted({lot for lot, _ in pairs})
    processes = sorted({value for _, oper in pairs for value in (oper, oper.split(maxsplit=1)[0])})
    binds, lot_slots, process_slots = {}, [], []
    for index, value in enumerate(lot_codes):
        key = f"symbol_lot_{index}"
        binds[key] = value
        lot_slots.append(f":{key}")
    for index, value in enumerate(processes):
        key = f"symbol_process_{index}"
        binds[key] = value
        process_slots.append(f":{key}")
    cursor.execute(
        "SELECT LOT_CD, PROCESS, PARAM_NM, REGEX_PATTERN FROM DF_SYMBOL_BIN "
        f"WHERE LOT_CD IN ({', '.join(lot_slots)}) AND PROCESS IN ({', '.join(process_slots)}) "
        "ORDER BY LOT_CD, PROCESS, PARAM_NM",
        binds,
    )
    definitions = cursor.fetchall()
    by_source = {}
    for lot_cd, process, name, pattern in definitions:
        pattern = _text(pattern)
        by_source.setdefault((str(lot_cd), str(process)), {})[str(name)] = str(pattern)
    result = {}
    for lot_cd, oper in pairs:
        available = by_source.get((lot_cd, oper)) or by_source.get((lot_cd, oper.split(maxsplit=1)[0]))
        if available:
            result[(lot_cd, oper)] = available
    return result


def _load_rows_and_patterns(args: LoadWaferMapsInput):
    where, binds = _filters(args)
    binds.update(stop_row=args.measurement_offset + args.max_measurements + 1, offset=args.measurement_offset)
    inner = (
        "SELECT LOT_ID, WF_ID, LOT_CD, OPER_DET_DESC AS OPER, START_TM, END_TM, MAP_VAL_JSON "
        f"FROM MAP WHERE {where} ORDER BY LOT_ID, WF_ID, {args.time_column.upper()}, START_TM"
    )
    paged = (
        "SELECT LOT_ID, WF_ID, LOT_CD, OPER, START_TM, END_TM, MAP_VAL_JSON FROM "
        f"(SELECT source_rows.*, ROWNUM AS RN FROM ({inner}) source_rows WHERE ROWNUM <= :stop_row) "
        "WHERE RN > :offset"
    )
    connection = _connection()
    cursor = None
    try:
        cursor = connection.cursor()
        _configure_cursor(cursor)
        cursor.execute(paged, binds)
        names = [item[0].lower() for item in cursor.description]
        raw = cursor.fetchmany(args.max_measurements + 1)
        has_more = len(raw) > args.max_measurements
        rows = [dict(zip(names, values)) for values in raw[:args.max_measurements + 1]]
        for row in rows:
            row["map_val_json"] = _text(row["map_val_json"])

        coverage_binds = {key: value for key, value in binds.items() if key not in {"stop_row", "offset"}}
        cursor.execute(f"SELECT DISTINCT LOT_ID, WF_ID FROM MAP WHERE {where}", coverage_binds)
        coverage = cursor.fetchall()
        found = {
            "lot_ids": sorted({str(row[0]) for row in coverage}),
            "wf_ids": sorted({int(row[1]) for row in coverage}),
        }
        try:
            patterns = _load_symbol_patterns(cursor, rows)
        except Exception:
            patterns = {}
        return rows, patterns, has_more, found
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            connection.close()


def _load(args: LoadWaferMapsInput, source_id="oracle:MAP") -> ToolResult:
    rows, patterns, has_more, found = _load_rows_and_patterns(args)
    normalized = normalize_map_rows(
        rows,
        symbol_patterns=patterns,
        max_measurements=args.max_measurements,
        max_dies=args.max_dies,
        measurement_offset=args.measurement_offset,
        source_has_more=has_more,
    )
    missing = {
        "lot_ids": sorted(set(args.lot_ids) - set(found["lot_ids"])),
        "wf_ids": sorted(set(args.wf_ids) - set(found["wf_ids"])),
    }
    filters = args.model_dump(mode="json")
    artifacts, evidence_tables = [], []
    if args.wafer_groups:
        wafer_groups = filters["wafer_groups"]
        selected = {(g["lot_id"], wf) for g in wafer_groups for wf in g["wf_ids"]}
        observed = {(row["lot_id"], row["wf_id"]) for row in normalized.die_rows}
        missing["lot_wafers"] = [list(pair) for pair in sorted(selected - observed)]
        if normalized.die_rows:
            from .map_bin_plot import grouped_binmaps

            artifact, evidence_tables = grouped_binmaps(normalized.die_rows, wafer_groups, source_id)
            artifacts.append(artifact)
    facts = {**normalized.facts, "lots_found": found["lot_ids"], "wafers_found": found["wf_ids"],
             "missing_requests": missing}
    incomplete = facts["truncated"]
    missing_any = any(missing.values())
    status = "empty" if normalized.measurement_count == 0 and not incomplete else (
        "partial" if incomplete or missing_any else "success"
    )
    reason = facts["truncation_reason"] if incomplete else None
    return ToolResult(
        tables=[ResultTable(
            table_id="wafer_map_dies",
            title="Normalized wafer map dies",
            rows=normalized.die_rows,
            columns=DIE_COLUMNS,
            complete=not incomplete,
            missing_reason=reason,
        ), *evidence_tables],
        artifacts=artifacts,
        summary=(
            f"measurements={facts['measurement_count']}, wafers={facts['wafer_count']}, "
            f"dies={facts['die_count']}, truncated={facts['truncated']}"
        ),
        scope={"wafer_maps": facts, "filters": filters,
               "map_measurements": map_measurements({None: normalized.die_rows})},
        status=status,
    )


def _query_maps(args, source_id, timezone):
    if args.recent_weeks is not None:
        start, end = recent_map_window(args.lot_cd, args.oper, args.recent_weeks)
        args = args.model_copy(update={"start_time": start, "end_time": end})
    else:
        # Oracle DATE uses local wall time; convert explicit offsets before binding.
        local_bounds = {name: value.astimezone(ZoneInfo(timezone)).replace(tzinfo=None)
                        for name in ("start_time", "end_time")
                        if (value := getattr(args, name)) is not None and value.tzinfo is not None}
        if local_bounds:
            args = args.model_copy(update=local_bounds)
    if args.aggregate is not None:
        from .map_aggregate import aggregate_wafer_maps

        return aggregate_wafer_maps(args, source_id)
    return _load(args, source_id)


async def load_wafer_maps(args, ctx):
    source_id = "oracle:MAP"
    if getattr(ctx, "invocation_id", None):
        source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, ctx.run["run_id"] + ":" + ctx.invocation_id))
    timezone = getattr(getattr(ctx, "settings", None), "timezone", None) or "Asia/Seoul"
    return await run_blocking(_query_maps, args, source_id, timezone)


def register(registry):
    registry.add(ToolSpec(
        "load_wafer_maps",
        "누적 맵(cummap)은 aggregate에 요청한 집계 그룹을 지정해 한 번 호출한다. 공정 이력별 그룹은 "
        "aggregate.group_by에 이력 열(recipe=RCP/RECIPE_ID, equipment=EQP_ID, chamber=EQP_CH, "
        "stepper_recipe=STEPPER_RECIPE_ID)을 선택하고 aggregate.group_oper에 DF_WF_LEGEND 공정명 또는 공정 ID를 넣는다. "
        "oper는 원본 웨이퍼 맵을 측정한 검사(MAP.OPER_DET_DESC)다. 도구가 전체 원본을 "
        "조회해 좌표별 불량·관측 횟수와 완성된 맵을 반환하므로 lot 목록 사전 조회, 페이지 순회, Python 재집계가 필요 없다. "
        "모집단 누적 맵에는 확정된 lot_cd, MAP의 정확한 OPER_DET_DESC(oper), 기간을 직접 전달한다. "
        "최근 N주는 recent_weeks=N, 명시한 범위는 start_time/end_time, 전체 기간은 all_time=true다. "
        "lot_ids/wf_ids는 특정 웨이퍼를 지정할 때의 선택 필터다. 개별 binmap은 aggregate를 생략하고 lot_ids로 "
        "원본 die 표와 측정별 MAP 통과 비율을 읽는다. 명명한 개별 맵은 wafer_groups에 "
        "각 label, lot_id, wf_ids를 함께 전달하면 해당 조합의 완성된 맵을 반환한다. "
        "독립적인 wafer 수율은 DF_WF_ALL_PRB_V2에서 조회한다.",
        LoadWaferMapsInput,
        load_wafer_maps,
        read_only=True,
        timeout=90,
        effect="artifact",
        eager=True,
    ))

"""Read wafer MAP payloads for a raw die table or an on-demand cumulative map."""
from __future__ import annotations

from collections import Counter
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
from itertools import groupby, islice
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
from .map_page_result import finalize_map_pages, map_summary, run_map_query
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
    lot_result_id: str | None = Field(default=None, min_length=1, description=(
        "Use the result_id of a LOT history lookup in this chat to select every LOT in its "
        "complete lot_summary table and show individual binmaps. Only LOT membership carries over; "
        "MAP dates filter measurement times and should be set only when separately requested. "
        "The authoritative membership key is lot_summary.lot_id = MAP.LOT_ID (exact); "
        "lot_map_coverage covers the full selection regardless of the raw map page size."
    ))
    wafer_item_id: str | None = Field(default=None, min_length=1, max_length=100, description=(
        "For a saved Kanban defect item, pass its task ID from the board. Reads all of its saved "
        "LOT/wafer pairs directly. Use oper for the requested test; omit other wafer selectors."
    ))
    wafer_groups: list[WaferGroup] = Field(default_factory=list, max_length=100, description=(
        "For individual maps of named or remembered groups, send each label and its exact LOT/wafer pairs. "
        "Repeat the label for different lots in the same group."
    ))
    aggregate: CummapAggregation | None = Field(default=None, description=(
        "For one grouping dimension use group_by. For two dimensions use matrix.rows and matrix.columns "
        "to return one cumulative map per observed pair in a single call. MAP.WF_ID groups the same "
        "wafer number across LOTs; group_by=wafer keeps LOT and wafer together. "
        "list_tables can show the physical fields of MAP or DF_WF_LEGEND when a field is unknown."
    ))
    lot_ids: list[str] = Field(default_factory=list, max_length=1000, description=(
        "For raw binmaps supply lot_ids or wafer_groups. For cumulative maps, omit for the whole selected population; "
        "pass lot IDs only for an explicitly selected subset."
    ))
    render_maps: bool = Field(default=False, description=(
        "Show individual wafer charts when passing explicit lot_ids. With lot_result_id, wafer_groups or "
        "wafer_item_id charts are already included. Data-only calls can omit this."
    ))
    lot_cd: str | None = Field(default=None, min_length=1, max_length=100)
    wf_ids: list[int] = Field(default_factory=list, max_length=100)
    oper: str | None = Field(default=None, min_length=1, max_length=100, description=(
        "The measured test on the source wafer maps, exact MAP.OPER_DET_DESC for this lot_cd. "
        "Scope the manufacturing operation separately in group_oper or in each legend axis's operation."))
    recent_weeks: int | None = Field(default=None, strict=True, gt=0, description=(
        "Count of calendar weeks ending at the latest MAP measurement for this product and test."))
    all_time: bool = Field(default=False, strict=True, description="True only for an explicitly unbounded period.")
    time_column: Literal["start_tm", "end_tm"] = "end_tm"
    start_time: datetime | None = Field(default=None, description="Inclusive start of the requested measurement period.")
    end_time: datetime | None = Field(default=None, description=(
        "Exclusive end of the measurement period. For an inclusive last calendar day use the following midnight."))
    raw_page: RawMapPage | None = Field(default=None, description=(
        "Optional batch sizes for reading individual maps. Starting at offset zero reads the full selection. "
        "Use a positive measurement_offset only to request a single continuation page. Omit for aggregate."
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
        if self.lot_result_id and (self.lot_ids or self.wafer_groups or self.wafer_item_id):
            raise ValueError("Choose only one LOT selection source.")
        if self.wafer_item_id and (self.wafer_groups or self.lot_ids or self.wf_ids or self.aggregate):
            raise ValueError("wafer_item_id is the complete wafer selection; omit other selectors and aggregate.")
        if self.aggregate is None and not (self.lot_ids or self.wafer_groups or self.wafer_item_id or self.lot_result_id):
            raise ValueError("lot_ids or wafer_groups or a saved selection is required for raw wafer maps; use aggregate for a cumulative map.")
        if self.wafer_groups and (self.aggregate is not None or self.lot_ids or self.wf_ids):
            raise ValueError("Use wafer_groups alone to select individual LOT/wafer pairs.")
        dated = self.start_time is not None or self.end_time is not None
        if sum((self.recent_weeks is not None, self.all_time, dated)) > 1:
            raise ValueError("Choose one period: recent_weeks, start_time/end_time, or all_time.")
        if self.recent_weeks is not None and (self.time_column != "end_tm" or
                (not self.wafer_item_id and not (self.lot_cd and self.oper))):
            raise ValueError("recent_weeks requires lot_cd, oper and time_column=end_tm.")
        if self.aggregate is not None and not (self.lot_ids or self.lot_result_id):
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
    source_has_more: bool = False, seen_measurements: set[str] | None = None,
    allow_oversized_measurement: bool = False,
) -> NormalizedMaps:
    """Pure conversion of MAP source rows into complete-measurement die rows."""
    compiled = {
        key: [(name, re.compile(pattern)) for name, pattern in patterns.items()]
        for key, patterns in (symbol_patterns or {}).items()
    }
    die_rows = []
    if seen_measurements is None:
        seen_measurements = set()
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
        if len(die_rows) + len(cells) > max_dies and (included_measurements or not allow_oversized_measurement):
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
        "next_measurement_offset": measurement_offset + included_measurements + duplicate_measurements if truncated else None,
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
        # A stored result can exceed Oracle's 1,000 expressions per IN clause.
        clauses = [f"LOT_ID IN ({', '.join(lot_slots[i:i + 1000])})" for i in range(0, len(lot_slots), 1000)]
        predicates.append("(" + " OR ".join(clauses) + ")")
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


@contextmanager
def _open_map_source(args):
    where, binds = _filters(args)
    with closing(_connection()) as connection, closing(connection.cursor()) as cursor:
        _configure_cursor(cursor)
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(f"SELECT DISTINCT LOT_ID, WF_ID, LOT_CD, OPER_DET_DESC FROM MAP WHERE {where}", binds)
        coverage = cursor.fetchall()
        if args.lot_result_id and args.oper and not coverage:
            cursor.execute("SELECT 1 FROM MAP WHERE OPER_DET_DESC = :oper AND ROWNUM = 1", {"oper": args.oper})
            if cursor.fetchone() is None:
                cursor.execute("SELECT DISTINCT OPER_DET_DESC FROM MAP WHERE OPER_DET_DESC IS NOT NULL "
                               "ORDER BY OPER_DET_DESC FETCH FIRST 12 ROWS ONLY")
                options = [name for name, in cursor.fetchall()]
                if options:
                    raise ValueError(f"Unknown MAP test {args.oper!r}. Available exact MAP.OPER_DET_DESC "
                                     f"values (up to 12): {json.dumps(options, ensure_ascii=False)}.")
        found = {
            "lot_ids": sorted({str(row[0]) for row in coverage}),
            "wf_ids": sorted({int(row[1]) for row in coverage}),
        }
        pairs = {(row[2], row[3]) for row in coverage}
        try:
            patterns = _load_symbol_patterns(cursor, [{"lot_cd": prod, "oper": oper} for prod, oper in pairs])
        except Exception:
            patterns = {}
        sql = (
            "SELECT LOT_ID, WF_ID, LOT_CD, OPER_DET_DESC AS OPER, START_TM, END_TM, MAP_VAL_JSON "
            f"FROM MAP WHERE {where} ORDER BY LOT_ID, WF_ID, {args.time_column.upper()}, START_TM, ROWID"
        )
        if args.measurement_offset:
            sql += " OFFSET :offset ROWS"
            binds["offset"] = args.measurement_offset
        cursor.execute(sql, binds)
        names = [item[0].lower() for item in cursor.description]

        def source_rows():
            for values in cursor:
                row = dict(zip(names, values))
                row["map_val_json"] = _text(row["map_val_json"])
                yield row

        yield source_rows(), patterns, found


def _map_pages(args, source_id, *, added_groups=None):
    with _open_map_source(args) as (source, patterns, found):
        offset = args.measurement_offset
        pending = []
        seen = set()
        while True:
            pending.extend(islice(source, args.max_measurements + 1 - len(pending)))
            has_more = len(pending) > args.max_measurements
            normalized = normalize_map_rows(
                pending[:args.max_measurements], symbol_patterns=patterns,
                max_measurements=args.max_measurements, max_dies=args.max_dies,
                measurement_offset=offset, source_has_more=has_more,
                seen_measurements=seen, allow_oversized_measurement=not args.measurement_offset,
            )
            next_offset = normalized.facts["next_measurement_offset"]
            yield _map_page_result(args, source_id, normalized, found, added_groups=added_groups)
            if next_offset is None or args.measurement_offset:
                return
            del pending[:next_offset - offset]
            offset = next_offset
            del normalized


def _map_page_result(args, source_id, normalized, found, *, added_groups=None):
    missing = {
        "lot_ids": sorted(set(args.lot_ids) - set(found["lot_ids"])),
        "wf_ids": sorted(set(args.wf_ids) - set(found["wf_ids"])),
    }
    filters = args.model_dump(mode="json")
    artifacts, evidence_tables = [], []
    wafer_groups = filters["wafer_groups"]
    if args.wafer_groups:
        selected = {(g["lot_id"], wf) for g in wafer_groups for wf in g["wf_ids"]}
        observed = {(row["lot_id"], row["wf_id"]) for row in normalized.die_rows}
        missing["lot_wafers"] = [list(pair) for pair in sorted(selected - observed)]
    elif args.lot_result_id or args.render_maps:
        by_lot = {}
        for die in normalized.die_rows:
            by_lot.setdefault(die["lot_id"], set()).add(die["wf_id"])
        wafer_groups = [dict(label=lot, lot_id=lot, wf_ids=sorted(wafers)) for lot, wafers in by_lot.items()]
    if wafer_groups and normalized.die_rows:
        from .map_bin_plot import grouped_binmaps

        artifact, evidence_tables = grouped_binmaps(normalized.die_rows, wafer_groups, source_id,
                                                   added_groups=added_groups)
        artifacts.append(artifact)
    facts = {**normalized.facts, "lots_found": found["lot_ids"], "wafers_found": found["wf_ids"],
             "missing_requests": missing}
    incomplete = facts["truncated"]
    missing_any = any(missing.values())
    status = "empty" if normalized.measurement_count == 0 and not incomplete else (
        "partial" if incomplete or missing_any else "success"
    )
    reason = facts["truncation_reason"] if incomplete else None
    if args.lot_result_id:
        matched = set(found["lot_ids"])
        evidence_tables.append(ResultTable(
            table_id="lot_map_coverage", title="LOT별 MAP 조회 결과", columns=["lot_id", "has_map"],
            rows=[{"lot_id": lot, "has_map": lot in matched} for lot in args.lot_ids], complete=True,
        ))
        # The complete, query-wide membership is readable from the table, even on a paged die result.
        filters.pop("lot_ids")
        facts.pop("lots_found")
        facts["missing_requests"] = {"lot_count": len(missing["lot_ids"]), "wf_ids": missing["wf_ids"]}
        facts["matched_lot_count"] = len(matched)
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
        summary=map_summary(args, facts),
        scope={"wafer_maps": facts, "filters": filters,
               "map_measurements": map_measurements({None: normalized.die_rows})},
        status=status,
    )


def _load(args, source_id="oracle:MAP", *, added_groups=None, spool_dir=None):
    with closing(_map_pages(args, source_id, added_groups=added_groups)) as pages:
        if spool_dir is None or args.measurement_offset:
            return next(pages)
        return finalize_map_pages(pages, args, spool_dir=spool_dir)


def _query_maps(args, source_id, timezone, board_scope=None, *, spool_dir=None):
    item = None
    if args.wafer_item_id:
        from ..web_kanban import read_wafer_item

        item = read_wafer_item(*board_scope, args.wafer_item_id)
        if not item["wafer_groups"]:
            raise ValueError("This item has no registered wafers.")
        args = LoadWaferMapsInput.model_validate({
            **args.model_dump(exclude={"wafer_item_id"}), "wafer_groups": item["wafer_groups"],
            "lot_cd": args.lot_cd or item.get("lot_cd"), "oper": args.oper or item.get("oper"),
        })
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

        return aggregate_wafer_maps(args, source_id, spool_dir=spool_dir)
    if item is not None:
        result = _load(args, source_id, added_groups=item["added_wafer_groups"], spool_dir=spool_dir)
        if args.oper and result.status == "empty":
            where, binds = _filters(args.model_copy(update={"oper": None}))
            with _connection() as conn, conn.cursor() as cursor:
                cursor.execute(f"SELECT DISTINCT OPER_DET_DESC FROM MAP WHERE {where} "
                               "AND OPER_DET_DESC IS NOT NULL ORDER BY OPER_DET_DESC", binds)
                available = [name for name, in cursor.fetchall()]
            if available and args.oper not in available:
                raise ValueError(f"No MAP test {args.oper!r} for this item's registered wafers. "
                                 f"Available exact MAP.OPER_DET_DESC values: {json.dumps(available, ensure_ascii=False)}. "
                                 "Keep wafer_item_id and pass the matching oper. Clarify if ambiguous.")
        result.scope["wafer_item_id"] = item["id"]
        return result
    return _load(args, source_id, spool_dir=spool_dir)


async def _run_maps(args, source_id, timezone, board_scope=None):
    spool = args.aggregate.matrix is not None if args.aggregate else not args.measurement_offset
    if spool:
        return await run_map_query(run_blocking, _query_maps, args, source_id, timezone, board_scope)
    return await run_blocking(_query_maps, args, source_id, timezone, board_scope)


async def load_wafer_maps(args, ctx):
    if args.aggregate and getattr(ctx, "web_map_jobs_enabled", False):
        from ..map_jobs import dispatch_map_job
        return await dispatch_map_job(args, ctx, MAP_CALL_TIMEOUT)
    source_id = "oracle:MAP"
    if getattr(ctx, "invocation_id", None):
        source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, ctx.run["run_id"] + ":" + ctx.invocation_id))
    timezone = getattr(getattr(ctx, "settings", None), "timezone", None) or "Asia/Seoul"
    if args.lot_result_id:
        owner = (ctx.run["principal_id"], ctx.run["session_id"], args.lot_result_id)
        obs = await ctx.store.observation(*owner)
        summary = next((table for table in obs.tables if table.table_id == "lot_summary"), None)
        if (obs.tool_name != "query_lot_history" or obs.status not in {"success", "empty"}
                or summary is None or not summary.complete or "lot_id" not in summary.columns):
            raise ValueError("Use a complete LOT history result from this chat.")
        lots = [row["lot_id"] async for row in ctx.store.iter_table_rows(*owner, table_id="lot_summary")]
        if len(lots) != summary.total_rows or any(not isinstance(lot, str) or not lot for lot in lots):
            raise ValueError("LOT history result has an invalid LOT selection.")
        if not lots:
            return ToolResult(status="empty", summary="The LOT history selection is empty; no MAP measurements requested.",
                              source_result_ids=[args.lot_result_id], scope={"selected_lot_count": 0})
        result = await _run_maps(args.model_copy(update={"lot_ids": lots}), source_id, timezone)
        result.source_result_ids = [args.lot_result_id]
        result.scope["selected_lot_count"] = len(lots)
        return result
    if args.wafer_item_id:
        from ..native_hermes import profile_path

        board_scope = (profile_path(ctx.run["principal_id"]), ctx.store.db.name)
        return await _run_maps(args, source_id, timezone, board_scope)
    return await _run_maps(args, source_id, timezone)


MAP_CALL_TIMEOUT = 90


def register(registry):
    registry.add(ToolSpec(
        "load_wafer_maps",
        "공정별 누적 맵(cummap)은 공정명·ID의 오타까지 원문 그대로 먼저 조회하고, 공정이 하나로 식별되지 않을 때만 목록을 확인한다. "
        "누적 맵은 aggregate에 요청한 집계 그룹을 지정해 한 번 호출한다. 공정 이력별 그룹은 "
        "aggregate.group_by에 이력 열(recipe=RCP/RECIPE_ID, equipment=EQP_ID, chamber=EQP_CH, "
        "stepper_recipe=STEPPER_RECIPE_ID)을 선택하고 aggregate.group_oper에 DF_WF_LEGEND 공정명 또는 공정 ID를 넣는다. "
        "두 기준을 교차할 때는 aggregate.matrix의 rows와 columns에 각각 원본과 필드를 지정한다. "
        "map은 MAP의 측정값, legend는 지정한 operation에서의 DF_WF_LEGEND 값이다. "
        "oper는 원본 웨이퍼 맵을 측정한 검사(MAP.OPER_DET_DESC)다. 도구가 전체 원본을 "
        "조회해 좌표별 불량·관측 횟수와 완성된 맵을 반환하므로 lot 목록 사전 조회, 페이지 순회, Python 재집계가 필요 없다. "
        "모집단 누적 맵에는 확정된 lot_cd, MAP의 정확한 OPER_DET_DESC(oper), 기간을 직접 전달한다. "
        "최근 N주는 recent_weeks=N, 명시한 범위는 start_time/end_time, 전체 기간은 all_time=true다. "
        "다른 LOT 이력 조회에서 찾은 LOT들의 개별 맵은 lot_result_id에 그 결과 ID를 넣는다. "
        "이력 발생 기간은 맵 측정 기간과 독립적이다. 기본 개별 맵 조회는 동일 조건의 마지막 측정까지 도구 안에서 읽는다. "
        "lot_ids/wf_ids는 특정 웨이퍼를 지정할 때의 선택 필터다. 개별 binmap은 aggregate를 생략하고 lot_ids로 "
        "원본 die 표와 측정별 MAP 통과 비율을 읽는다. 명명한 개별 맵은 wafer_groups에 "
        "각 label, lot_id, wf_ids를 함께 전달하면 해당 조합의 완성된 맵을 반환한다. "
        "저장된 칸반 불량 아이템의 개별 맵은 해당 카드 ID(wafer_item_id)로 전체 등록 웨이퍼를 읽는다. "
        "독립적인 wafer 수율은 DF_WF_ALL_PRB_V2에서 조회한다.",
        LoadWaferMapsInput,
        load_wafer_maps,
        read_only=True,
        timeout=MAP_CALL_TIMEOUT,
        effect="artifact",
        eager=True,
    ))

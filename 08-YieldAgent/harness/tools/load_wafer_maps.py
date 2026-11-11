"""Load MAP payloads into a stored, normalized die table."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Literal

from pydantic import Field, model_validator

from ..runtime.domain_process import run_blocking
from ..types import Contract, ResultTable
from .registry import ToolResult, ToolSpec


DIE_COLUMNS = [
    "measurement_id", "lot_id", "wf_id", "lot_cd", "oper", "start_tm", "end_tm",
    "row", "col", "left_bin", "right_bin", "bin", "fail_bin_name",
]


class LoadWaferMapsInput(Contract):
    lot_ids: list[str] = Field(min_length=1, max_length=100)
    wf_ids: list[int] = Field(default_factory=list, max_length=100)
    oper: str | None = Field(default=None, min_length=1, max_length=100)
    time_column: Literal["start_tm", "end_tm"] = "end_tm"
    start_time: datetime | None = None
    end_time: datetime | None = None
    measurement_offset: int = Field(default=0, ge=0, le=1_000_000)
    max_measurements: int = Field(default=200, ge=1, le=1_000)
    max_dies: int = Field(default=500_000, ge=1, le=5_000_000)

    @model_validator(mode="after")
    def valid_range(self):
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if len(set(self.lot_ids)) != len(self.lot_ids):
            raise ValueError("lot_ids must not contain duplicates")
        if len(set(self.wf_ids)) != len(self.wf_ids):
            raise ValueError("wf_ids must not contain duplicates")
        return self


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
    lot_slots = []
    for index, value in enumerate(args.lot_ids):
        key = f"lot_{index}"
        binds[key] = value
        lot_slots.append(f":{key}")
    predicates.append(f"LOT_ID IN ({', '.join(lot_slots)})")
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
    return " AND ".join(predicates), binds


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


def _load(args: LoadWaferMapsInput) -> ToolResult:
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
        )],
        summary=(
            f"measurements={facts['measurement_count']}, wafers={facts['wafer_count']}, "
            f"dies={facts['die_count']}, truncated={facts['truncated']}"
        ),
        scope={"wafer_maps": facts, "filters": args.model_dump(mode="json")},
        status=status,
    )


async def load_wafer_maps(args, ctx):
    return await run_blocking(_load, args)


def register(registry):
    registry.add(ToolSpec(
        "load_wafer_maps",
        "MAP의 요청 lot/wafer/공정/기간 측정을 표준 die 표로 정규화해 result_id로 저장한다. "
        "lot_ids/wf_ids는 원본 선택 필터이며 이후 누적의 집계 그룹과는 별개다. "
        "그림이나 분석 없이 개수, 좌표 범위, bin 빈도, 누락과 paging 사실만 반환한다.",
        LoadWaferMapsInput,
        load_wafer_maps,
        read_only=True,
        timeout=90,
        effect="artifact",
        eager=True,
    ))

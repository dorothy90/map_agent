"""Exact-reference batch reader for immutable map payload snapshots."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json

from .wafer_set_sql import build_where

MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_DIE_PER_MEASUREMENT = 100_000
MAX_DIE_OBSERVATIONS = 5_000_000
MAX_COORDINATE = 100_000


@dataclass(frozen=True)
class MapBatch:
    measurements: list[dict]
    payloads: list[dict]
    requested_count: int
    duplicate_count: int
    invalid_identity_count: int
    bytes_read: int
    complete: bool
    missing_reason: str | None


def _connection():
    from map_agent import _get_oracle_connection_common
    connection = _get_oracle_connection_common()
    if hasattr(connection, "call_timeout"):
        connection.call_timeout = min(int(getattr(connection, "call_timeout", 15_000) or 15_000), 15_000)
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


def _map_table():
    from map_agent import ORACLE_TABLE
    value = str(ORACLE_TABLE).upper()
    if not value.replace("_", "").isalnum(): raise ValueError("Configured source name is invalid")
    return value


def _time(value):
    if isinstance(value, datetime): value = value.isoformat(sep=" ")
    return str(value)[:19]


def _validate_payload(payload: str) -> tuple[str, str, int]:
    encoded = payload.encode()
    if len(encoded) > MAX_PAYLOAD_BYTES: raise ValueError("Map payload exceeds 1 MiB")
    try: entries = json.loads(payload)["MAP"]
    except (TypeError, ValueError, KeyError) as exc: raise ValueError("Invalid map payload") from exc
    if not isinstance(entries, list) or len(entries) > MAX_DIE_PER_MEASUREMENT:
        raise ValueError("Map die limit exceeded")
    coordinates = set()
    for entry in entries:
        parts = str(entry).split(",")
        if len(parts) < 4: raise ValueError("Invalid map cell")
        try: coordinate = (int(parts[0]), int(parts[1]))
        except ValueError as exc: raise ValueError("Invalid map coordinate") from exc
        if max(abs(coordinate[0]), abs(coordinate[1])) > MAX_COORDINATE:
            raise ValueError("Map coordinate limit exceeded")
        if coordinate in coordinates: raise ValueError("Map contains duplicate coordinates")
        coordinates.add(coordinate)
    full = hashlib.sha256(encoded).hexdigest()
    return full[:16], full, len(entries)


def _measurement_ref(row):
    value = {key: row[key] for key in ("lot_id", "wf_id", "process", "time_column", "time_value", "payload_hash")}
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def snapshot_map_rows(rows: list[dict], *, time_column: str, measurement_policy: str,
                      include_payload: bool, max_measurements: int, max_bytes: int) -> MapBatch:
    if time_column not in ("start_tm", "end_tm"): raise ValueError("Unsupported time column")
    prepared = []
    for source in rows:
        payload = source.get("map_val_json")
        if payload is None: raise ValueError("Map selection requires payload identity")
        short, full, die_count = _validate_payload(payload)
        reference = {key: value for key, value in source.items()
                     if key not in ("map_val_json", "source_id", "fact_id", "source_row_hash")}
        reference.update(time_column=time_column, time_value=_time(source.get(time_column)),
                         payload_hash=short, payload_sha256=full, die_count=die_count,
                         map_val_json=payload)
        prepared.append(reference)
    if measurement_policy == "latest_per_entity":
        grouped = {}
        for row in prepared:
            key = (row["lot_id"], int(row["wf_id"]))
            prior = grouped.get(key)
            if prior is None or row[time_column] > prior[time_column]: grouped[key] = row
            elif row[time_column] == prior[time_column] and row["payload_sha256"] != prior["payload_sha256"]:
                raise ValueError("Latest measurement has conflicting same-time payloads")
        prepared = list(grouped.values())
    if len(prepared) > max_measurements: raise ValueError("max_measurements exceeded")
    measurements, payloads, seen, total_bytes, total_die = [], [], set(), 2, 0
    for row in prepared:
        total_die += row["die_count"]
        if total_die > MAX_DIE_OBSERVATIONS: raise ValueError("Map die observation limit exceeded")
        if include_payload and row["payload_sha256"] not in seen:
            payload_row = {"payload_hash": row["payload_hash"], "payload_sha256": row["payload_sha256"],
                           "map_val_json": row["map_val_json"]}
            encoded = len(json.dumps(payload_row, ensure_ascii=False).encode()) + 1
            if total_bytes + encoded > max_bytes: raise ValueError("Map payload byte limit exceeded")
            total_bytes += encoded
            payloads.append(payload_row)
            seen.add(row["payload_sha256"])
        reference = {key: value for key, value in row.items() if key not in ("map_val_json", "die_count")}
        reference.update(measurement_ref=row.get("measurement_ref") or _measurement_ref(row),
                         member_id=row.get("member_id") or json.dumps([row["lot_id"], row["wf_id"]]),
                         payload_table_id="map_payloads", payload_version=row["payload_sha256"][:16])
        measurements.append(reference)
    return MapBatch(measurements, payloads, len(rows), 0, 0, total_bytes, True, None)


def fetch_map_range(query, *, measurement_policy: str, include_payload: bool,
                    max_rows: int, max_measurements: int, max_bytes: int) -> MapBatch:
    time_column = query.time.column if query and query.time else "end_tm"
    where, binds = build_where("map", query)
    sql = ("SELECT LOT_ID,WF_ID,LOT_CD,FAB_ID,OPER_DET_DESC AS PROCESS,START_TM,END_TM,MAP_VAL_JSON" +
           f" FROM {_map_table()}{where} ORDER BY LOT_ID,WF_ID,{time_column}")
    connection = _connection()
    cursor = None
    fetched, bytes_read, die_read, limited_reason = [], 2, 0, None
    try:
        cursor = connection.cursor()
        _configure_cursor(cursor)
        cursor.execute(sql, binds)
        names = [item[0].lower() for item in cursor.description]
        while len(fetched) <= max_rows:
            batch = cursor.fetchmany(min(64, max_rows + 1 - len(fetched)))
            if not batch: break
            for values in batch:
                row = dict(zip(names, values))
                value = row["map_val_json"]
                payload = value.read() if hasattr(value, "read") else value
                short, full, die_count = _validate_payload(payload)
                encoded = len(payload.encode())
                if bytes_read + encoded > max_bytes:
                    limited_reason = "BYTE_LIMIT"
                    break
                die_read += die_count
                if die_read > MAX_DIE_OBSERVATIONS:
                    limited_reason = "DIE_LIMIT"
                    break
                bytes_read += encoded
                row.update(payload_hash=short, payload_sha256=full, die_count=die_count)
                if include_payload:
                    row["map_val_json"] = payload
                else:
                    row.pop("map_val_json", None)
                row["start_tm"], row["end_tm"] = _time(row["start_tm"]), _time(row["end_tm"])
                fetched.append(row)
            if limited_reason or len(fetched) > max_rows: break
    finally:
        try:
            if cursor is not None: cursor.close()
        finally:
            connection.close()
    over_rows = len(fetched) > max_rows
    boundary = fetched[max_rows] if over_rows else None
    fetched = fetched[:max_rows]
    if measurement_policy == "latest_per_entity":
        if boundary is not None:
            boundary_key = (boundary["lot_id"], int(boundary["wf_id"]))
            fetched = [row for row in fetched if (row["lot_id"], int(row["wf_id"])) != boundary_key]
        grouped = {}
        for row in fetched:
            key = (row["lot_id"], int(row["wf_id"]))
            prior = grouped.get(key)
            if prior is None or row[time_column] > prior[time_column]: grouped[key] = row
            elif row[time_column] == prior[time_column] and row["payload_sha256"] != prior["payload_sha256"]:
                raise ValueError("Latest measurement has conflicting same-time payloads")
        fetched = list(grouped.values())
    if len(fetched) > max_measurements:
        fetched = fetched[:max_measurements]
        limited_reason = limited_reason or "ROW_LIMIT"
    measurements, payloads, seen = [], [], set()
    for row in fetched:
        reference = {key: value for key, value in row.items()
                     if key not in ("map_val_json", "payload_sha256", "die_count")}
        identity = {**reference, "time_column": time_column,
                    "time_value": row[time_column], "payload_hash": row["payload_hash"]}
        reference.update(time_column=time_column, time_value=row[time_column],
            measurement_ref=_measurement_ref(identity),
            member_id=json.dumps([row["lot_id"], int(row["wf_id"])]),
            payload_sha256=row["payload_sha256"], payload_table_id="map_payloads",
            payload_version=row["payload_sha256"][:16])
        measurements.append(reference)
        if include_payload and row["payload_sha256"] not in seen:
            payloads.append({"payload_hash": row["payload_hash"],
                             "payload_sha256": row["payload_sha256"],
                             "map_val_json": row["map_val_json"]})
            seen.add(row["payload_sha256"])
    reason = limited_reason or ("ROW_LIMIT" if over_rows else None)
    return MapBatch(measurements, payloads, len(fetched) + (1 if over_rows else 0), 0, 0,
                    bytes_read, reason is None, reason)


def fetch_maps_by_refs(refs: list[dict], *, include_payload: bool,
                       max_measurements: int, max_bytes: int) -> MapBatch:
    if len(refs) > max_measurements: raise ValueError("max_measurements exceeded")
    required = {"lot_id", "wf_id", "process", "time_column", "time_value", "payload_hash"}
    if any(not required.issubset(row) for row in refs): raise ValueError("Incomplete measurement reference")
    grouped = defaultdict(list)
    for row in refs:
        if row["time_column"] not in ("start_tm", "end_tm"): raise ValueError("Unsupported time column")
        grouped[(row["process"], row["time_column"])].append(row)
    connection = _connection()
    cursor = None
    found = defaultdict(list)
    fetched_payload_bytes = 0
    try:
        cursor = connection.cursor()
        _configure_cursor(cursor)
        for (process, time_column), items in grouped.items():
            for start in range(0, len(items), 128):
                chunk = items[start:start + 128]
                binds, clauses = {"process": process}, []
                for index, row in enumerate(chunk):
                    binds[f"lot_{index}"] = row["lot_id"]
                    binds[f"wf_{index}"] = row["wf_id"]
                    binds[f"time_{index}"] = row["time_value"]
                    clauses.append(f"(LOT_ID=:lot_{index} AND WF_ID=:wf_{index} AND "
                        f"{time_column}>=TO_TIMESTAMP(:time_{index},'YYYY-MM-DD HH24:MI:SS') AND "
                        f"{time_column}<TO_TIMESTAMP(:time_{index},'YYYY-MM-DD HH24:MI:SS')+INTERVAL '1' SECOND)")
                sql = ("SELECT LOT_ID,WF_ID,LOT_CD,FAB_ID,OPER_DET_DESC AS PROCESS,START_TM,END_TM," +
                       "MAP_VAL_JSON "
                       f"FROM {_map_table()} WHERE OPER_DET_DESC=:process AND (" + " OR ".join(clauses) + ")")
                cursor.execute(sql, binds)
                names = [item[0].lower() for item in cursor.description]
                candidate_count, candidate_limit = 0, len(chunk) * 10
                while candidate_count <= candidate_limit:
                    request_count = min(64, candidate_limit + 1 - candidate_count)
                    values_batch = cursor.fetchmany(request_count)
                    if not values_batch: break
                    for values in values_batch:
                        candidate_count += 1
                        if candidate_count > candidate_limit:
                            raise ValueError("Map source returned excessive duplicate measurements")
                        raw = dict(zip(names, values))
                        value = raw["map_val_json"]
                        payload = value.read() if hasattr(value, "read") else value
                        short, full, die_count = _validate_payload(payload)
                        fetched_payload_bytes += len(payload.encode())
                        if fetched_payload_bytes > max_bytes:
                            raise ValueError("Map payload byte limit exceeded")
                        raw.update(payload_hash=short, payload_sha256=full, die_count=die_count)
                        if include_payload:
                            raw["map_val_json"] = payload
                        else:
                            raw.pop("map_val_json", None)
                        found[(str(raw["lot_id"]), int(raw["wf_id"]), str(raw["process"]))].append(raw)
                    if len(values_batch) < request_count: break
    finally:
        try:
            if cursor is not None: cursor.close()
        finally:
            connection.close()
    measurements, payload_by_sha, total_bytes, total_die = [], {}, 2, 0
    for reference in refs:
        candidates = found.get((str(reference["lot_id"]), int(reference["wf_id"]), str(reference["process"])), [])
        candidates = [row for row in candidates if _time(row[reference["time_column"]]) == _time(reference["time_value"])]
        matching = [row for row in candidates
                    if row["payload_hash"] == str(reference["payload_hash"]).lower()]
        if not matching: raise ValueError("Measurement payload is unavailable or changed")
        fabs = {row.get("fab_id") for row in matching}
        if len(fabs) > 1: raise ValueError("Measurement reference resolves to multiple FAB values")
        contents = {row["payload_sha256"] for row in matching}
        if len(contents) > 1: raise ValueError("Measurement reference resolves to conflicting payloads")
        row = matching[0]
        total_die += row["die_count"]
        if total_die > MAX_DIE_OBSERVATIONS: raise ValueError("Map die observation limit exceeded")
        if include_payload and row["payload_sha256"] not in payload_by_sha:
            payload_row = {"payload_hash": row["payload_hash"], "payload_sha256": row["payload_sha256"],
                           "map_val_json": row["map_val_json"]}
            encoded = len(json.dumps(payload_row, ensure_ascii=False).encode()) + 1
            if total_bytes + encoded > max_bytes: raise ValueError("Map payload byte limit exceeded")
            total_bytes += encoded
            payload_by_sha[row["payload_sha256"]] = payload_row
        measurement = {**reference, "lot_cd": row.get("lot_cd"), "fab_id": row.get("fab_id"),
            "start_tm": _time(row.get("start_tm")), "end_tm": _time(row.get("end_tm")),
            "measurement_ref": reference.get("measurement_ref") or _measurement_ref(reference),
            "member_id": reference.get("member_id") or json.dumps([reference["lot_id"], reference["wf_id"]]),
            "payload_sha256": row["payload_sha256"], "payload_table_id": "map_payloads",
            "payload_version": row["payload_sha256"][:16]}
        measurements.append(measurement)
    return MapBatch(measurements, list(payload_by_sha.values()), len(refs), 0, 0,
                    fetched_payload_bytes + 2, True, None)

"""Allowlisted source readers and source-row normalization."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import math

from .wafer_set_catalog import physical_name, source_spec
from .wafer_set_contracts import SourceQuery
from .wafer_set_sql import build_where, source_query_chunks


@dataclass(frozen=True)
class SourceBatch:
    rows: list[dict]
    requested_count: int
    omitted_count: int
    duplicate_count: int
    invalid_identity_count: int
    bytes_read: int
    complete: bool
    missing_reason: str | None
    metadata_limitations: list[dict] = field(default_factory=list)
    metadata_definition_version: str | None = None


def _connection():
    from common import get_oracle_connection
    connection = get_oracle_connection()
    if hasattr(connection, "call_timeout"):
        connection.call_timeout = min(int(getattr(connection, "call_timeout", 15_000) or 15_000), 15_000)
    return connection


def _configure_cursor(cursor, arraysize):
    cursor.arraysize = arraysize
    try:
        import oracledb
    except ImportError:
        return
    def output_type_handler(active_cursor, metadata):
        if metadata.type_code is oracledb.DB_TYPE_CLOB:
            return active_cursor.var(oracledb.DB_TYPE_LONG, arraysize=active_cursor.arraysize)
        return None
    cursor.outputtypehandler = output_type_handler


def _scalar(value):
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value): raise ValueError("Source contains a non-finite number")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite(): raise ValueError("Source contains a non-finite number")
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime): return value.isoformat(sep=" ")
    if isinstance(value, date): return value.isoformat()
    if hasattr(value, "read"): return value.read()
    raise ValueError(f"Unsupported source value type: {type(value).__name__}")


def _derived(source_id, row):
    if "source_wf_id" in row:
        raw = row["source_wf_id"]
        try:
            if isinstance(raw, bool): raise ValueError
            value = int(str(raw).strip())
            if value < 0: raise ValueError
            row["wf_id"] = value
        except (TypeError, ValueError):
            row["wf_id"] = None
    if source_id.startswith("gms_"):
        kind = source_id.split("_", 1)[1]
        row["period_kind"] = kind
        raw = str(row.get("period_raw") or "")
        try:
            if kind == "daily": start = datetime.strptime(raw, "%Y%m%d")
            elif kind == "monthly": start = datetime.strptime(raw, "%Y%m")
            else: start = datetime.strptime(raw + "-1", "%G%V-%u")
            if kind == "daily": end = start.replace(day=start.day) + __import__("datetime").timedelta(days=1)
            elif kind == "weekly": end = start + __import__("datetime").timedelta(days=7)
            else:
                end = datetime(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
            row["period_start"], row["period_end"] = start.isoformat(sep=" "), end.isoformat(sep=" ")
        except ValueError:
            row["period_start"] = row["period_end"] = None
    if source_id == "bmw_image":
        row["image_available"] = bool(row.get("image_available"))
        row["pdf_available"] = bool(row.get("pdf_available"))
    if source_id == "wads_report" and row.get("report_id") is None:
        row["report_id"] = hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:24]
        row["html_available"] = bool(row.get("html_available"))
    return row


def _identity(source_id, row, occurrence):
    canonical = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False)
    source_hash = hashlib.sha256(canonical.encode()).hexdigest()
    fact_id = hashlib.sha256(f"{source_id}:{source_hash}:{occurrence}".encode()).hexdigest()[:24]
    output = {"source_id": source_id, "fact_id": fact_id, "source_row_hash": source_hash, **row}
    if row.get("lot_id") is not None and row.get("wf_id") is not None:
        output["member_id"] = json.dumps([row["lot_id"], row["wf_id"]], ensure_ascii=False)
    return output


def fetch_source(source_id: str, query: SourceQuery | None, columns: list[str],
                 measurement_policy: str, max_rows: int, max_bytes: int) -> SourceBatch:
    spec = source_spec(source_id)
    selected = columns or [name for name in spec.columns if not (source_id == "map" and name == "map_val_json")]
    unknown = [name for name in selected if name not in spec.columns]
    if unknown: raise ValueError("Unknown source columns: " + ", ".join(unknown))
    fetch_selected = list(selected)
    if source_id == "wafer_parameter":
        for name in ("lot_cd", "process", "parameter"):
            if name not in fetch_selected:
                fetch_selected.append(name)
    physical = [(name, spec.columns[name]) for name in fetch_selected if spec.columns[name] is not None]
    projections = []
    for public, column in physical:
        if public in spec.binary_columns and source_id != "map":
            projections.append(f"CASE WHEN {column} IS NOT NULL THEN 1 ELSE 0 END AS {public}")
        else:
            projections.append(f"{column} AS {public}")
    sql_columns = ", ".join(projections)
    chunks = source_query_chunks(source_id, query)
    connection = _connection()
    cursor = None
    raw_rows, fetch_bytes, fetch_byte_limited = [], 2, False
    metadata_snapshot = None
    try:
        cursor = connection.cursor()
        _configure_cursor(cursor, 64 if source_id == "map" and "map_val_json" in selected else 1000)
        for chunk in chunks:
            where, binds = build_where(source_id, chunk)
            sql = f"SELECT {sql_columns} FROM {physical_name(spec)}{where}"
            order = [spec.columns[key] for key in spec.key_columns if spec.columns.get(key)]
            if order: sql += " ORDER BY " + ", ".join(order)
            cursor.execute(sql, binds)
            names = [item[0].lower() for item in cursor.description]
            while len(raw_rows) <= max_rows:
                request_count = min(cursor.arraysize, max_rows + 1 - len(raw_rows))
                values_batch = cursor.fetchmany(request_count)
                if not values_batch: break
                for values in values_batch:
                    row = dict(zip(names, values))
                    for name, value in list(row.items()):
                        if hasattr(value, "read"):
                            row[name] = value.read()
                    encoded = len(json.dumps(row, ensure_ascii=False, default=str).encode()) + 1
                    if fetch_bytes + encoded > max_bytes:
                        fetch_byte_limited = True
                        break
                    fetch_bytes += encoded
                    raw_rows.append(row)
                if fetch_byte_limited or len(values_batch) < request_count: break
            if len(raw_rows) > max_rows: break
        if source_id == "wafer_parameter":
            from .wafer_set_param_meta import read_param_metadata
            metadata_snapshot = read_param_metadata(connection,
                lot_cd=sorted({str(row.get("lot_cd")) for row in raw_rows if row.get("lot_cd") is not None}),
                source_ids=[source_id])
    finally:
        try:
            if cursor is not None: cursor.close()
        finally:
            connection.close()
    requested = len(raw_rows)
    over_limit = len(raw_rows) > max_rows
    raw_rows = raw_rows[:max_rows]
    if measurement_policy == "latest_per_entity":
        if not spec.time_columns:
            raise ValueError("Source does not expose a measurement time")
        time_column = spec.time_columns[0]
        required = [*spec.key_columns, time_column]
        if any(column not in selected for column in required):
            raise ValueError(f"Latest policy requires source key and time columns. columns에 {required!r}을 모두 포함하거나 measurement_policy='all'을 사용하세요.")
        grouped = {}
        for row in raw_rows:
            key = tuple(row.get(column) for column in spec.key_columns)
            prior = grouped.get(key)
            if prior is None or row.get(time_column) > prior.get(time_column):
                grouped[key] = row
            elif row.get(time_column) == prior.get(time_column):
                current = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
                previous = json.dumps(prior, sort_keys=True, ensure_ascii=False, default=str)
                if current != previous:
                    raise ValueError("Latest measurement has a same-time content conflict")
        raw_rows = list(grouped.values())
    normalized, occurrences, duplicate_count, invalid = [], {}, 0, 0
    size = 2
    byte_limited = fetch_byte_limited
    for raw in raw_rows:
        row = {name: _scalar(raw.get(name)) for name in selected if name in raw}
        row = _derived(source_id, row)
        invalid += row.get("source_wf_id") is not None and row.get("wf_id") is None
        fingerprint = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False)
        occurrence = occurrences.get(fingerprint, 0)
        occurrences[fingerprint] = occurrence + 1
        duplicate_count += occurrence > 0
        fact = _identity(source_id, row, occurrence)
        if metadata_snapshot is not None:
            from .wafer_set_param_meta import resolve_param_meta
            identity = {name: _scalar(raw.get(name)) for name in ("lot_cd", "process", "parameter")}
            metadata = resolve_param_meta(metadata_snapshot, source_id=source_id,
                lot_cd=identity.get("lot_cd"), process=identity.get("process"),
                parameter=identity.get("parameter"))
            fact.update({key: metadata.get(key) for key in
                ("unit", "better_when", "param_kind", "value_basis", "definition_version")})
        encoded = len(json.dumps(fact, ensure_ascii=False, default=str, allow_nan=False).encode()) + 1
        if size + encoded > max_bytes:
            byte_limited = True
            break
        size += encoded
        normalized.append(fact)
    complete = not over_limit and not byte_limited
    reasons = []
    if over_limit: reasons.append("ROW_LIMIT")
    if byte_limited: reasons.append("BYTE_LIMIT")
    metadata_limitations = list(metadata_snapshot.get("limitations", [])) if metadata_snapshot else []
    missing_metadata = sum(row.get("param_kind") == "unspecified" for row in normalized)
    if missing_metadata:
        metadata_limitations.append({"code": "PARAM_METADATA_NOT_FOUND",
            "detail": f"No matching metadata for {missing_metadata} fact rows",
            "affected_count": missing_metadata})
    return SourceBatch(normalized, requested, max(0, requested - len(normalized)), duplicate_count,
                       invalid, size, complete, ", ".join(reasons) or None, metadata_limitations,
                       metadata_snapshot.get("definition_version") if metadata_snapshot else None)

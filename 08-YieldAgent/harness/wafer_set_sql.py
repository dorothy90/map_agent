"""Bound, read-only SQL helpers for allowlisted wafer-set sources."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
import json
import math
from typing import Any

from .wafer_set_catalog import SOURCES, physical_name, source_spec
from .wafer_set_contracts import PredicateGroup, SourceQuery

MAX_METADATA_ROWS = 10_000
MAX_BINDS = 900
NUMERIC_FIELDS = {"wf_id", "year", "month", "value", "display_order", "fab_yield", "prb_yield",
                  "cum0_yield", "rslt_val", "spec_min_val", "spec_max_val", "control_limit", "q_time",
                  "bal", "delay_time", "qty"}


def _connection():
    from common import get_oracle_connection
    connection = get_oracle_connection()
    if hasattr(connection, "call_timeout"):
        connection.call_timeout = min(int(getattr(connection, "call_timeout", 15_000) or 15_000), 15_000)
    return connection


def _close(connection, cursor):
    try:
        if cursor is not None:
            cursor.close()
    finally:
        connection.close()


def _rows(cursor):
    names = [item[0].lower() for item in cursor.description]
    return [dict(zip(names, values)) for values in cursor.fetchall()]


def _scalar(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Source contains a non-finite number")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Source contains a non-finite number")
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    raise ValueError(f"Unsupported source value type: {type(value).__name__}")


def inspect_catalog(source_ids: list[str]) -> dict[str, dict]:
    selected = source_ids or list(SOURCES)
    specs = [source_spec(item) for item in selected]
    table_to_sources: dict[str, list[str]] = defaultdict(list)
    for spec in specs:
        table_to_sources[physical_name(spec)].append(spec.source_id)
    binds, slots = {}, []
    for index, name in enumerate(table_to_sources):
        binds[f"table_{index}"] = name.upper()
        slots.append(f":table_{index}")
    sql = f"""SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, NULLABLE
              FROM USER_TAB_COLUMNS
              WHERE TABLE_NAME IN ({', '.join(slots)})
              ORDER BY TABLE_NAME, COLUMN_ID"""
    connection = _connection()
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(sql, binds)
        values = cursor.fetchmany(MAX_METADATA_ROWS + 1)
        if len(values) > MAX_METADATA_ROWS:
            raise ValueError("Metadata row limit exceeded")
        names = [item[0].lower() for item in cursor.description]
        physical = defaultdict(dict)
        for value in values:
            row = dict(zip(names, value))
            physical[str(row["table_name"]).upper()][str(row["column_name"]).upper()] = {
                "data_type": str(row["data_type"]), "nullable": str(row["nullable"]).upper() == "Y"}
        output = {}
        for spec in specs:
            name = physical_name(spec).upper()
            found = physical.get(name, {})
            missing = [column for column in dict.fromkeys(spec.columns.values())
                       if column is not None and column.upper() not in found]
            available = bool(found) and not missing
            output[spec.source_id] = {"available": available, "columns": found,
                "missing_reason": None if available else
                    ("source not found" if not found else "required columns missing: " + ", ".join(missing))}
        return output
    finally:
        _close(connection, cursor)


def _bind_list(column: str, values: list, prefix: str, binds: dict, *, numeric: bool = False) -> str:
    if len(values) > 500:
        raise ValueError("IN list limit exceeded")
    if len(binds) + len(values) > MAX_BINDS:
        key = f"{prefix}_json_{len(binds)}"
        binds[key] = json.dumps(values, ensure_ascii=False, allow_nan=False)
        value_type = "NUMBER" if numeric else "VARCHAR2(4000)"
        return (f"{column} IN (SELECT value FROM JSON_TABLE(:{key}, '$[*]' "
                f"COLUMNS(value {value_type} PATH '$')))" )
    slots = []
    for value in values:
        key = f"{prefix}_{len(binds)}"
        binds[key] = value
        slots.append(f":{key}")
    return f"{column} IN ({', '.join(slots)})"


def _predicate_sql(group: PredicateGroup, spec, binds: dict) -> str:
    clauses = []
    for atom in group.items:
        if atom.column not in spec.columns or atom.column in spec.binary_columns:
            raise ValueError(f"Unsupported predicate column: {atom.column}")
        column = spec.columns[atom.column]
        if column is None:
            raise ValueError(f"Derived columns are not directly filterable: {atom.column}")
        values = atom.value if isinstance(atom.value, list) else [atom.value]
        values = [value for value in values if value is not None]
        if atom.column in NUMERIC_FIELDS and any(isinstance(value, bool) or not isinstance(value, (int, float))
                                                  for value in values):
            raise ValueError(f"Predicate value type does not match numeric column: {atom.column}")
        if atom.op == "mod_eq":
            raise ValueError("mod_eq is available only for stored tables")
        if atom.op in ("is_null", "not_null"):
            clauses.append(f"{column} IS {'NOT ' if atom.op == 'not_null' else ''}NULL")
        elif atom.op in ("in", "not_in"):
            clause = _bind_list(column, atom.value, "predicate", binds,
                                numeric=atom.column in NUMERIC_FIELDS)
            clauses.append(clause.replace(" IN ", " NOT IN ", 1) if atom.op == "not_in" else clause)
        elif atom.op == "between":
            low, high = f"p_{len(binds)}", f"p_{len(binds) + 1}"
            binds[low], binds[high] = atom.value
            clauses.append(f"{column} BETWEEN :{low} AND :{high}")
        else:
            operators = {"eq": "=", "ne": "<>", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}
            key = f"p_{len(binds)}"
            binds[key] = atom.value
            clauses.append(f"{column} {operators[atom.op]} :{key}")
    if len(binds) > MAX_BINDS:
        raise ValueError("Bind limit exceeded")
    return "(" + (" AND " if group.mode == "all" else " OR ").join(clauses) + ")"


def build_where(source_id: str, query: SourceQuery | None) -> tuple[str, dict]:
    if query is None:
        return "", {}
    spec = source_spec(source_id)
    clauses, binds = [], {}
    for public, values in (("lot_cd", query.lot_cd), ("process", query.process), ("lot_id", query.lot_ids)):
        if values:
            if public not in spec.columns:
                raise ValueError(f"Source does not expose {public}")
            clauses.append(_bind_list(spec.columns[public], values, public, binds,
                                      numeric=public in NUMERIC_FIELDS))
    if query.wafer_pairs:
        if not {"lot_id", "wf_id"}.issubset(spec.columns):
            raise ValueError("Source does not expose paired identity")
        pairs = []
        for pair in query.wafer_pairs:
            lot_key, wf_key = f"pair_lot_{len(binds)}", f"pair_wf_{len(binds) + 1}"
            binds[lot_key], binds[wf_key] = pair.lot_id, pair.wf_id
            pairs.append(f"({spec.columns['lot_id']} = :{lot_key} AND {spec.columns['wf_id']} = :{wf_key})")
        clauses.append("(" + " OR ".join(pairs) + ")")
    if query.time:
        if query.time.column not in spec.time_columns:
            raise ValueError("Unsupported time column")
        column = spec.columns[query.time.column]
        binds["time_start"], binds["time_end"] = query.time.start, query.time.end
        clauses.append(f"{column} >= :time_start AND {column} < :time_end")
    if query.where:
        clauses.append(_predicate_sql(query.where, spec, binds))
    if len(binds) > MAX_BINDS:
        raise ValueError("Bind limit exceeded")
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", binds


def normalize_source_query(
    source_id: str, query: SourceQuery
) -> tuple[SourceQuery, list[dict]]:
    build_where(source_id, query)
    spec = source_spec(source_id)
    catalog = inspect_catalog([source_id])[source_id]
    character_types = {"CHAR", "VARCHAR2", "NCHAR", "NVARCHAR2", "CLOB", "NCLOB", "LONG"}
    eligible = {
        public for public, physical in spec.columns.items()
        if physical is not None
        and public not in spec.binary_columns
        and public not in NUMERIC_FIELDS
        and public not in spec.time_columns
        and str(catalog.get("columns", {}).get(physical.upper(), {}).get("data_type") or "").upper()
            in character_types
    }
    cache: dict[str, list[str] | None] = {}
    notices: list[dict] = []

    def candidates(column: str) -> list[str] | None:
        if column not in cache:
            data = query_distinct_values(source_id, column, None, False, "value_asc", 1000)
            if data["distinct_count"] > len(data["rows"]):
                cache[column] = None
            else:
                cache[column] = list(dict.fromkeys(
                    row["value"] for row in data["rows"] if isinstance(row.get("value"), str)
                ))
        return cache[column]

    def resolve(column: str, value):
        if column not in eligible or not isinstance(value, str) or not value:
            return value
        values = candidates(column)
        if values is None or any(candidate.casefold() == value.casefold() for candidate in values):
            return value
        matches = [candidate for candidate in values
                   if candidate.casefold().startswith(value.casefold() + " ")]
        if len(matches) != 1:
            return value
        candidate = matches[0]
        notices.append({
            "code": "FILTER_VALUE_NORMALIZED",
            "detail": f"{column} {value!r} 을(를) DB 값 {candidate!r}로 해석",
        })
        return candidate

    updates = {}
    for field, column in (("lot_cd", "lot_cd"), ("process", "process"), ("lot_ids", "lot_id")):
        updates[field] = list(dict.fromkeys(resolve(column, value) for value in getattr(query, field)))
    if query.where is not None:
        atoms = []
        for atom in query.where.items:
            if atom.op == "eq":
                value = resolve(atom.column, atom.value)
            elif atom.op == "in":
                value = list(dict.fromkeys(resolve(atom.column, item) for item in atom.value))
            else:
                value = atom.value
            atoms.append(atom.model_copy(update={"value": value}, deep=True))
        updates["where"] = query.where.model_copy(update={"items": atoms}, deep=True)
    return query.model_copy(update=updates, deep=True), notices


def source_query_chunks(source_id: str, query: SourceQuery | None, *, reserved_binds: int = 0) -> list[SourceQuery | None]:
    if query is None or not query.wafer_pairs:
        return [query]
    base = query.model_copy(update={"wafer_pairs": []})
    _, fixed_binds = build_where(source_id, base)
    capacity = min(128, (MAX_BINDS - reserved_binds - len(fixed_binds)) // 2)
    if capacity < 1:
        raise ValueError("Bind limit leaves no room for exact pairs")
    return [query.model_copy(update={"wafer_pairs": query.wafer_pairs[index:index + capacity]})
            for index in range(0, len(query.wafer_pairs), capacity)]


def query_distinct_values(source_id: str, field: str, query: SourceQuery | None,
                          include_null: bool, order: str, limit: int) -> dict:
    spec = source_spec(source_id)
    if field not in spec.columns:
        raise ValueError(f"Unknown field: {field}")
    if spec.columns[field] is None:
        raise ValueError("Derived fields are not directly queryable")
    if field in spec.binary_columns:
        raise ValueError("Distinct values are unavailable for binary or large-object fields")
    column = spec.columns[field]
    direction = "cnt DESC, value ASC NULLS LAST" if order == "count_desc" else "value ASC NULLS LAST"
    distinct_expression = "COUNT(*)" if include_null else "COUNT(CASE WHEN value IS NOT NULL THEN 1 END)"
    display_filter = "" if include_null else " WHERE value IS NOT NULL"
    chunks = source_query_chunks(source_id, query, reserved_binds=1)
    connection = _connection()
    cursor = None
    try:
        cursor = connection.cursor()
        if len(chunks) == 1:
            where, binds = build_where(source_id, chunks[0])
            base = f"SELECT {column} AS value FROM {physical_name(spec)}{where}"
            binds["value_limit"] = limit
            sql = f"""WITH grouped AS (
                          SELECT value, COUNT(*) cnt FROM ({base}) GROUP BY value
                      ), stats AS (
                          SELECT COALESCE(SUM(cnt), 0) source_rows, {distinct_expression} distinct_count,
                                 COALESCE(SUM(CASE WHEN value IS NULL THEN cnt ELSE 0 END), 0) null_count
                          FROM grouped
                      ), ranked AS (
                          SELECT value, cnt, ROW_NUMBER() OVER (ORDER BY {direction}) rn
                          FROM grouped{display_filter}
                      )
                      SELECT ranked.value, ranked.cnt, stats.source_rows, stats.distinct_count,
                             stats.null_count, ranked.rn
                      FROM stats LEFT JOIN ranked ON ranked.rn <= :value_limit
                      ORDER BY ranked.rn"""
            cursor.execute(sql, binds)
            fetched = _rows(cursor)
            stats = fetched[0] if fetched else {"source_rows": 0, "distinct_count": 0, "null_count": 0}
            values = [row for row in fetched if row.get("cnt") is not None]
            source_rows, distinct_count, null_count = (int(stats["source_rows"] or 0),
                int(stats["distinct_count"] or 0), int(stats["null_count"] or 0))
        else:
            combined: dict[tuple[str, str], list] = {}
            source_rows = 0
            for chunk in chunks:
                where, binds = build_where(source_id, chunk)
                cursor.execute(f"SELECT {column} value, COUNT(*) cnt FROM {physical_name(spec)}{where} "
                               f"GROUP BY {column}", binds)
                for row in _rows(cursor):
                    value = _scalar(row["value"])
                    key = (type(value).__name__, json.dumps(value, ensure_ascii=False, default=str))
                    combined.setdefault(key, [value, 0])[1] += int(row["cnt"])
                    source_rows += int(row["cnt"])
            pairs = [(value, count) for value, count in combined.values() if include_null or value is not None]
            if order == "count_desc":
                pairs.sort(key=lambda item: (-item[1], item[0] is None, type(item[0]).__name__, str(item[0])))
            else:
                pairs.sort(key=lambda item: (item[0] is None, type(item[0]).__name__, str(item[0])))
            null_count = next((count for value, count in combined.values() if value is None), 0)
            distinct_count = len(pairs)
            values = [{"value": value, "cnt": count} for value, count in pairs[:limit]]
        return {"rows": [{"value": _scalar(row["value"]), "count": int(row["cnt"]),
                           "is_null": row["value"] is None} for row in values],
                "source_row_count": source_rows, "distinct_count": distinct_count, "null_count": null_count}
    finally:
        _close(connection, cursor)


def query_coverage(source_id: str, query: SourceQuery | None, columns: list[str],
                   key_columns: list[str], time_column: str | None, checks: list[str]) -> list[dict]:
    spec = source_spec(source_id)
    requested = [*columns, *key_columns, *([time_column] if time_column else [])]
    if any(field not in spec.columns for field in requested):
        raise ValueError("Unknown coverage column")
    if any(spec.columns[field] is None for field in requested):
        raise ValueError("Derived fields are not directly inspectable")
    expressions = ["COUNT(*) AS row_count"]
    for index, field in enumerate(columns):
        expressions.append(f"SUM(CASE WHEN {spec.columns[field]} IS NULL THEN 1 ELSE 0 END) AS null_{index}")
    if time_column:
        expressions.extend([f"MIN({spec.columns[time_column]}) AS time_min",
                            f"MAX({spec.columns[time_column]}) AS time_max"])
    chunks = source_query_chunks(source_id, query)
    connection = _connection()
    cursor = None
    try:
        cursor = connection.cursor()
        aggregate = {"row_count": 0, **{f"null_{index}": 0 for index in range(len(columns))},
                     "time_min": None, "time_max": None}
        for chunk in chunks:
            where, binds = build_where(source_id, chunk)
            cursor.execute(f"SELECT {', '.join(expressions)} FROM {physical_name(spec)}{where}", binds)
            part = _rows(cursor)[0]
            aggregate["row_count"] += int(part["row_count"] or 0)
            for index in range(len(columns)):
                aggregate[f"null_{index}"] += int(part[f"null_{index}"] or 0)
            if time_column:
                if part["time_min"] is not None and (aggregate["time_min"] is None or part["time_min"] < aggregate["time_min"]):
                    aggregate["time_min"] = part["time_min"]
                if part["time_max"] is not None and (aggregate["time_max"] is None or part["time_max"] > aggregate["time_max"]):
                    aggregate["time_max"] = part["time_max"]
        total = aggregate["row_count"]
        output = []
        if "rows" in checks:
            output.append({"check": "rows", "field": "*", "numerator": total, "denominator": total,
                           "value": total, "minimum": None, "maximum": None, "exact": True})
        if "nulls" in checks:
            for index, field in enumerate(columns):
                count = int(aggregate[f"null_{index}"] or 0)
                output.append({"check": "nulls", "field": field, "numerator": count,
                               "denominator": total, "value": count / total if total else None,
                               "minimum": None, "maximum": None, "exact": True})
        if "time_range" in checks and time_column:
            output.append({"check": "time_range", "field": time_column, "numerator": None,
                           "denominator": total, "value": None, "minimum": _scalar(aggregate["time_min"]),
                           "maximum": _scalar(aggregate["time_max"]), "exact": True})
        if "duplicates" in checks and key_columns:
            keys = ", ".join(spec.columns[field] for field in key_columns)
            frequencies = Counter()
            for chunk in chunks:
                where, binds = build_where(source_id, chunk)
                cursor.execute(f"SELECT {keys}, COUNT(*) cnt FROM {physical_name(spec)}{where} GROUP BY {keys}", binds)
                for values in cursor.fetchall():
                    frequencies[tuple(_scalar(value) for value in values[:-1])] += int(values[-1])
            groups_count = sum(count > 1 for count in frequencies.values())
            duplicate_rows = sum(max(0, count - 1) for count in frequencies.values())
            label = ",".join(key_columns)
            output.extend([
                {"check": "duplicate_groups", "field": label, "numerator": groups_count,
                 "denominator": total, "value": groups_count, "minimum": None,
                 "maximum": None, "exact": True},
                {"check": "duplicate_rows", "field": label, "numerator": duplicate_rows,
                 "denominator": total, "value": duplicate_rows, "minimum": None,
                 "maximum": None, "exact": True},
            ])
        return output
    finally:
        _close(connection, cursor)


def query_link_keys(source_id: str, query: SourceQuery | None, keys: list[str],
                    max_rows: int = 250_000) -> list[tuple]:
    spec = source_spec(source_id)
    if not keys or any(key not in spec.columns or spec.columns[key] is None for key in keys):
        raise ValueError("Relation keys are unavailable for the source")
    columns = ", ".join(spec.columns[key] for key in keys)
    chunks = source_query_chunks(source_id, query)
    connection = _connection()
    cursor = None
    try:
        cursor = connection.cursor()
        rows = []
        for chunk in chunks:
            where, binds = build_where(source_id, chunk)
            cursor.execute(f"SELECT {columns} FROM {physical_name(spec)}{where}", binds)
            rows.extend(cursor.fetchmany(max_rows - len(rows) + 1))
            if len(rows) > max_rows:
                raise ValueError("Link population row limit exceeded")
        return [tuple(_scalar(value) for value in row) for row in rows]
    finally:
        _close(connection, cursor)


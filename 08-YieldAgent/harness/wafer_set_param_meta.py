"""Database-owned metadata for source-value wafer parameters."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json


MAX_METADATA_ROWS = 10_000
_KINDS = {"item", "bin", "yield"}
_DIRECTIONS = {"higher", "lower", "neutral", "unknown"}


def _read(value):
    return value.read() if hasattr(value, "read") else value


def _filters(values_by_column):
    binds, clauses = {}, []
    for prefix, column, values in values_by_column:
        if not values:
            continue
        slots = []
        for value in sorted(set(values)):
            name = f"{prefix}_{len(binds)}"
            binds[name] = value
            slots.append(f":{name}")
        clauses.append(f"{column} IN ({', '.join(slots)})")
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), binds


def match_definition_process(process: str, candidates) -> str | None:
    process = str(process)
    unique = sorted({str(candidate) for candidate in candidates})
    if process in unique:
        return process
    matches = [candidate for candidate in unique
               if process.startswith(candidate) and len(process) > len(candidate)
               and process[len(candidate)].isspace()]
    if len(matches) > 1:
        raise ValueError(f"ambiguous metadata process for {process!r}: {matches}")
    return matches[0] if matches else None


def read_param_metadata(connection, *, lot_cd: list[str], source_ids: list[str],
                        process: list[str] | None = None) -> dict:
    with closing(connection.cursor()) as cursor:
        table_binds = {"table_0": "DF_PARAM_META", "table_1": "DF_SOURCE_GUIDE"}
        cursor.execute("SELECT TABLE_NAME FROM USER_TABLES "
                       "WHERE TABLE_NAME IN (:table_0, :table_1)", table_binds)
        existing = {str(row[0]).upper() for row in cursor.fetchmany(3)}

    limitations = []
    if "DF_PARAM_META" not in existing:
        limitations.append({"code": "PARAM_METADATA_TABLE_MISSING", "detail": "DF_PARAM_META is unavailable"})
    if "DF_SOURCE_GUIDE" not in existing:
        limitations.append({"code": "SOURCE_GUIDE_TABLE_MISSING", "detail": "DF_SOURCE_GUIDE is unavailable"})

    metadata = []
    if "DF_PARAM_META" in existing:
        where, binds = _filters((
            ("source", "SOURCE_ID", source_ids),
            ("lot", "LOT_CD", lot_cd),
            ("process", "PROCESS", process),
        ))
        with closing(connection.cursor()) as cursor:
            cursor.execute("SELECT SOURCE_ID,LOT_CD,PROCESS,PARAM_NM,PARAM_KIND,UNIT,VALUE_BASIS,BETTER_WHEN "
                           "FROM DF_PARAM_META" + where +
                           " ORDER BY SOURCE_ID,LOT_CD,PROCESS,PARAM_NM", binds)
            rows = cursor.fetchmany(MAX_METADATA_ROWS + 1)
        if len(rows) > MAX_METADATA_ROWS:
            raise ValueError("Parameter metadata row limit exceeded")
        seen = set()
        for source, lot, process, parameter, kind, unit, basis, direction in rows:
            row = {"source_id": str(source), "lot_cd": str(lot), "process": str(process),
                   "parameter": str(parameter), "param_kind": str(kind), "unit": str(unit),
                   "value_basis": str(_read(basis)), "better_when": str(direction)}
            if row["param_kind"] not in _KINDS:
                raise ValueError(f"Invalid PARAM_KIND in DF_PARAM_META: {row['param_kind']!r}")
            if row["better_when"] not in _DIRECTIONS:
                raise ValueError(f"Invalid BETTER_WHEN in DF_PARAM_META: {row['better_when']!r}")
            row["definition_version"] = hashlib.sha256(json.dumps(
                row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
            key = (row["source_id"], row["lot_cd"], row["process"], row["parameter"])
            if key in seen:
                raise ValueError(f"Duplicate parameter metadata: {key}")
            seen.add(key)
            metadata.append(row)

    guides = []
    if "DF_SOURCE_GUIDE" in existing:
        where, binds = _filters((("source", "SOURCE_ID", source_ids),))
        with closing(connection.cursor()) as cursor:
            cursor.execute("SELECT SOURCE_ID,QUESTION_TYPE,GUIDANCE FROM DF_SOURCE_GUIDE" + where +
                           " ORDER BY SOURCE_ID,QUESTION_TYPE", binds)
            rows = cursor.fetchmany(MAX_METADATA_ROWS + 1)
        if len(rows) > MAX_METADATA_ROWS:
            raise ValueError("Source guide row limit exceeded")
        seen = set()
        for source, question_type, guidance in rows:
            row = {"source_id": str(source), "question_type": str(question_type),
                   "guidance": str(_read(guidance))}
            key = (row["source_id"], row["question_type"])
            if key in seen:
                raise ValueError(f"Duplicate source guide: {key}")
            seen.add(key)
            guides.append(row)

    availability = {"param_metadata": "DF_PARAM_META" in existing,
                    "source_guides": "DF_SOURCE_GUIDE" in existing}
    version_payload = {"param_metadata": metadata, "question_guides": guides,
                       "metadata_availability": availability, "limitations": limitations}
    version = hashlib.sha256(json.dumps(version_payload, sort_keys=True, ensure_ascii=False,
                                        default=str).encode()).hexdigest()[:16]
    return {**version_payload, "definition_version": version}


def resolve_param_meta(snapshot, *, source_id, lot_cd, process, parameter) -> dict:
    candidates = [row for row in snapshot.get("param_metadata", [])
                  if row["source_id"] == source_id and row["lot_cd"] == lot_cd
                  and row["parameter"] == parameter]
    matched_process = match_definition_process(process, [row["process"] for row in candidates])
    matches = [row for row in candidates if row["process"] == matched_process]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous parameter metadata for {(source_id, lot_cd, process, parameter)}")
    if matches:
        return {**matches[0], "metadata_limitation": None}
    return {"source_id": source_id, "lot_cd": lot_cd, "process": process, "parameter": parameter,
            "param_kind": "unspecified", "unit": "unspecified", "value_basis": None,
            "better_when": "unknown", "definition_version": snapshot.get("definition_version"),
            "metadata_limitation": "PARAM_METADATA_NOT_FOUND"}

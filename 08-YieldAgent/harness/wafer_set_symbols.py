"""Definition snapshot loading with a small expiring cache."""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from .wafer_set_cache import ByteLRU

_CACHE = ByteLRU(max_bytes=8 * 1024 * 1024, max_entries=256, ttl_seconds=60)
MAX_DEFINITION_ROWS = 10_000
_CACHE_SCHEMA_VERSION = 3


def _read(value):
    return value.read() if hasattr(value, "read") else value


def load_definition_snapshot(lot_cd: list[str], process: list[str], refresh: bool = False,
                             source_ids: list[str] | None = None) -> dict:
    source_ids = list(source_ids or ["wafer_parameter"])
    cache_key = ("definitions", _CACHE_SCHEMA_VERSION, tuple(sorted(lot_cd)), tuple(sorted(process)),
                 tuple(sorted(source_ids)))
    if not refresh:
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return json.loads(cached)
    from symbol_bin_db import _normalize_process
    from .wafer_set_sql import _connection
    process_map = {}
    for value in process:
        try:
            process_map[value] = _normalize_process(value)
        except ValueError:
            process_map[value] = value
    definition_processes = list(dict.fromkeys(process_map.values()))
    connection = _connection()
    cursor = None
    try:
        cursor = connection.cursor()
        binds, filters = {}, []
        for field, values, column in (("lot", lot_cd, "LOT_CD"), ("process", definition_processes, "PROCESS")):
            if values:
                slots = []
                for value in values:
                    name = f"{field}_{len(binds)}"
                    binds[name] = value
                    slots.append(f":{name}")
                filters.append(f"{column} IN ({', '.join(slots)})")
        where = " WHERE " + " AND ".join(filters) if filters else ""
        cursor.execute("SELECT LOT_CD, PROCESS, PARAM_NM, REGEX_PATTERN, DBLOADTIME "
                       "FROM DF_SYMBOL_BIN" + where + " ORDER BY LOT_CD, PROCESS, PARAM_NM", binds)
        raw_symbols = cursor.fetchmany(MAX_DEFINITION_ROWS + 1)
        if len(raw_symbols) > MAX_DEFINITION_ROWS:
            raise ValueError("Definition row limit exceeded")
        symbol_rows = [(str(a), str(b), str(c), str(_read(d)), e) for a, b, c, d, e in raw_symbols]
        symbol_keys = [(row[0], row[1], row[2]) for row in symbol_rows]
        duplicates = [key for key, count in Counter(symbol_keys).items() if count > 1]
        if duplicates:
            raise ValueError(f"Duplicate symbol definitions: {duplicates[:10]}")
        from symbol_bin import compile_symbol_patterns
        for lot, proc, parameter, pattern, _ in symbol_rows:
            compile_symbol_patterns({f"{lot}/{proc}/{parameter}": pattern})

        catalog_binds, catalog_filters = {}, []
        for field, values, column in (("lot", lot_cd, "LOT_CD"), ("process", definition_processes, "PROC_GBN_CD")):
            if values:
                slots = []
                for value in values:
                    name = f"{field}_{len(catalog_binds)}"
                    catalog_binds[name] = value
                    slots.append(f":{name}")
                catalog_filters.append(f"{column} IN ({', '.join(slots)})")
        catalog_where = " WHERE " + " AND ".join(catalog_filters) if catalog_filters else ""
        cursor.execute("SELECT LOT_CD, PROC_GBN_CD, YIELD_TABLE, PARAM_NM, PARAM_SEQNO "
                       "FROM DF_ALL_PARA_PRB" + catalog_where +
                       " ORDER BY LOT_CD, PROC_GBN_CD, PARAM_SEQNO", catalog_binds)
        raw_catalog = cursor.fetchmany(MAX_DEFINITION_ROWS + 1)
        if len(symbol_rows) + len(raw_catalog) > MAX_DEFINITION_ROWS:
            raise ValueError("Definition row limit exceeded")
        catalog_rows = [(str(a), str(b), str(d), str(c), int(e)) for a, b, c, d, e in raw_catalog]
        catalog_keys = [(row[0], row[1], row[2]) for row in catalog_rows]
        duplicates = [key for key, count in Counter(catalog_keys).items() if count > 1]
        if duplicates:
            raise ValueError(f"Duplicate parameter definitions: {duplicates[:10]}")
        catalog = {(lot, proc, parameter): (included.strip().lower() in {"o", "y", "1", "true"}, order)
                   for lot, proc, parameter, included, order in catalog_rows}
        symbols = {(lot, proc, parameter): (pattern, loaded)
                   for lot, proc, parameter, pattern, loaded in symbol_rows}
        definitions = []
        for lot, proc, parameter in sorted(set(symbols) | set(catalog)):
            pattern, loaded = symbols.get((lot, proc, parameter), (None, None))
            raw = {"lot_cd": lot, "process": proc, "parameter": parameter, "regex_pattern": pattern,
                   "dbloadtime": str(loaded) if loaded is not None else None,
                   "catalog": catalog.get((lot, proc, parameter))}
            version = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
            included, order = catalog.get((lot, proc, parameter), (None, None))
            mode = "symbol" if pattern is not None else "source_value"
            definitions.append({"definition_id": f"{mode}:{version}", "lot_cd": lot, "process": proc,
                "parameter": parameter, "mode": mode, "regex_pattern": pattern, "unit": "unknown",
                "better_when": "unknown", "included": included, "display_order": order,
                "definition_version": version})
        from .wafer_set_param_meta import match_definition_process, read_param_metadata
        metadata = read_param_metadata(connection, lot_cd=lot_cd, source_ids=source_ids,
                                       process=definition_processes)
        metadata_rows = metadata["param_metadata"]
        metadata_keys = [(row["lot_cd"], row["process"], row["parameter"])
                         for row in metadata_rows]
        duplicates = [key for key, count in Counter(metadata_keys).items() if count > 1]
        if duplicates:
            raise ValueError(f"Duplicate parameter metadata definitions: {duplicates[:10]}")
        definition_by_key = {
            (row["lot_cd"], row["process"], row["parameter"]): row for row in definitions}
        merged_keys = set()
        for row in metadata_rows:
            candidates = [item["process"] for item in definitions
                          if item["lot_cd"] == row["lot_cd"]
                          and item["parameter"] == row["parameter"]]
            matched_process = match_definition_process(row["process"], candidates)
            definition_key = (row["lot_cd"], matched_process or row["process"], row["parameter"])
            if definition_key in merged_keys:
                raise ValueError(f"Duplicate parameter metadata definitions: {[definition_key]}")
            merged_keys.add(definition_key)
            if definition_key in definition_by_key:
                definition_by_key[definition_key].update({name: row[name] for name in
                    ("source_id", "param_kind", "unit", "value_basis", "better_when")})
                continue
            raw = {key: row[key] for key in ("source_id", "lot_cd", "process", "parameter",
                                               "param_kind", "unit", "value_basis", "better_when")}
            version = hashlib.sha256(json.dumps(raw, sort_keys=True,
                ensure_ascii=False).encode()).hexdigest()[:16]
            included, order = catalog.get((row["lot_cd"], row["process"], row["parameter"]),
                                           (None, None))
            definitions.append({"definition_id": f"source_value:{row['source_id']}:{version}",
                **raw, "mode": "source_value", "regex_pattern": None, "included": included,
                "display_order": order, "definition_version": version})
        definitions.sort(key=lambda row: (row["lot_cd"], row["process"], row["parameter"],
                                          row.get("source_id", "")))
        available_processes = {row[1] for row in symbol_rows} | {item[1] for item in catalog}
        mappings = [(source, target) for source, target in process_map.items() if target in available_processes]
        if not process:
            mappings = [(value, value) for value in sorted(available_processes)]
        links = [{"source_id": "symbol_definition", "source_process": source,
                  "definition_process": target, "mapping_basis": "exact" if source == target else "validated_normalization",
                  "mapping_version": hashlib.sha256(f"{source}:{target}".encode()).hexdigest()[:16]}
                 for source, target in mappings]
        links.sort(key=lambda row: (row["source_process"], row["definition_process"]))
        snapshot = {"metric_definitions": definitions, "process_links": links,
                    "question_guides": metadata["question_guides"],
                    "metadata_availability": metadata["metadata_availability"],
                    "metadata_limitations": metadata["limitations"]}
        definition_version = hashlib.sha256(json.dumps(
            snapshot, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]
        result = {**snapshot, "definition_version": definition_version}
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        _CACHE.put(cache_key, encoded)
        return json.loads(encoded)
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            connection.close()


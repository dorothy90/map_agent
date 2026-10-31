"""Oracle loader for DF_SYMBOL_BIN definitions."""
from __future__ import annotations

from collections import Counter

from symbol_bin import compile_symbol_patterns


def _normalize_process(process):
    normalized = str(process or '').strip().split(maxsplit=1)[0] if process else ''
    if normalized not in {'PT1H', 'PT1C'}:
        raise ValueError("process must normalize to 'PT1H' or 'PT1C'")
    return normalized


def load_symbol_patterns(lot_cd, process, param_names, connection_factory=None):
    """Load exactly the requested named patterns using bound query values."""
    normalized_process = _normalize_process(process)
    requested = list(dict.fromkeys(param_names))
    if not requested:
        raise ValueError('param_names must not be empty')
    if connection_factory is None:
        from common import get_oracle_connection
        connection_factory = get_oracle_connection

    binds = {'lot_cd': lot_cd, 'process': normalized_process}
    placeholders = []
    for index, name in enumerate(requested):
        key = f'param_{index}'
        binds[key] = name
        placeholders.append(f':{key}')
    sql = f"""
        SELECT LOT_CD, PROCESS, PARAM_NM, REGEX_PATTERN
        FROM DF_SYMBOL_BIN
        WHERE LOT_CD = :lot_cd
          AND PROCESS = :process
          AND PARAM_NM IN ({', '.join(placeholders)})
    """

    connection = connection_factory()
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(sql, binds)
        rows = cursor.fetchall()
        names = [str(row[2]) for row in rows]
        duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
        if duplicates:
            raise ValueError(f'Duplicate DF_SYMBOL_BIN rows for {duplicates}')
        patterns = {}
        for row in rows:
            value = row[3].read() if hasattr(row[3], 'read') else row[3]
            patterns[str(row[2])] = str(value)
        missing = [name for name in requested if name not in patterns]
        if missing:
            raise ValueError(f'Missing DF_SYMBOL_BIN rows for {missing}')
        compile_symbol_patterns(patterns)
        return patterns
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            connection.close()


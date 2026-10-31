from __future__ import annotations

from enum import Enum
from typing import Any

_ENVELOPE_METADATA = {
    "complete", "handle", "limitations", "missing_reason", "provenance",
    "scope", "status", "summary", "version", "warnings",
}


class PrimaryResult(Enum):
    ZERO = "zero"
    NONZERO = "nonzero"
    UNKNOWN = "unknown"


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _cardinality_rows(rows: list[dict]) -> PrimaryResult | None:
    if not rows:
        return PrimaryResult.ZERO

    if all("member_count" in row for row in rows):
        counts = [_nonnegative_int(row.get("member_count")) for row in rows]
        if any(count is None for count in counts):
            return PrimaryResult.UNKNOWN
        return PrimaryResult.NONZERO if any(count for count in counts) else PrimaryResult.ZERO

    if len(rows) != 1:
        return None
    row = rows[0]
    if len(row) == 1 and next(iter(row)) in {"count", "rows"}:
        count = _nonnegative_int(next(iter(row.values())))
        if count is None:
            return PrimaryResult.UNKNOWN
        return PrimaryResult.ZERO if count == 0 else PrimaryResult.NONZERO

    payload_keys = [key for key, value in row.items()
                    if isinstance(value, list) and key not in _ENVELOPE_METADATA]
    other_keys = set(row) - set(payload_keys)
    if len(payload_keys) == 1 and other_keys <= _ENVELOPE_METADATA:
        payload = row[payload_keys[0]]
        return PrimaryResult.ZERO if not payload else PrimaryResult.NONZERO
    return None


def _manifest_count(rows: list[dict], key: str) -> PrimaryResult:
    if not rows:
        return PrimaryResult.ZERO
    counts = [_nonnegative_int(row.get(key)) for row in rows]
    if any(count is None for count in counts):
        return PrimaryResult.UNKNOWN
    return PrimaryResult.NONZERO if any(count for count in counts) else PrimaryResult.ZERO


def classify_primary(result) -> PrimaryResult:
    """Classify primary cardinality from generic result envelopes only."""
    tables = list(result.tables)
    if tables:
        first = tables[0]
        rows = first.rows

        if first.table_id == "render_manifest":
            return _manifest_count(rows, "rows_used")
        if first.table_id == "export_manifest":
            return (PrimaryResult.NONZERO if any(row.get("item_type") == "output" for row in rows)
                    else PrimaryResult.ZERO)
        if first.table_id == "coverage":
            return _manifest_count(rows, "denominator")
        manifests = [table for table in tables if table.table_id == "execution_manifest"]
        if manifests:
            outputs = [table for table in tables if table.table_id != "execution_manifest"]
            if len(manifests) != 1 or len(manifests[0].rows) != 1:
                return PrimaryResult.UNKNOWN
            output_count = _nonnegative_int(manifests[0].rows[0].get("output_table_count"))
            if output_count in (None, 0) or output_count != len(outputs):
                return PrimaryResult.UNKNOWN
            return (PrimaryResult.NONZERO if any(table.rows for table in outputs)
                    else PrimaryResult.ZERO)

        cardinality = _cardinality_rows(rows)
        return cardinality if cardinality is not None else PrimaryResult.NONZERO

    if result.rows:
        cardinality = _cardinality_rows(result.rows)
        return cardinality if cardinality is not None else PrimaryResult.NONZERO
    if result.artifacts:
        return PrimaryResult.UNKNOWN
    return PrimaryResult.ZERO

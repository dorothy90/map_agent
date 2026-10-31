from __future__ import annotations

from datetime import datetime
import hashlib
import json
from types import SimpleNamespace

import pytest


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.offset = 0
        self.calls = []
        self.description = [(name,) for name in ("LOT_ID", "WF_ID", "LOT_CD", "FAB_ID", "PROCESS",
                                                  "START_TM", "END_TM", "MAP_VAL_JSON")]
        self.closed = False

    def execute(self, sql, binds):
        self.calls.append((sql, dict(binds)))

    def fetchall(self):
        return list(self.rows)

    def fetchmany(self, count):
        result = list(self.rows)[self.offset:self.offset + count]
        self.offset += len(result)
        return result

    def close(self):
        self.closed = True


def connection(cursor):
    value = SimpleNamespace(cursor=lambda: cursor, close=lambda: setattr(value, "closed", True),
                            closed=False, call_timeout=30_000)
    return value


def payload(mark="A"):
    return json.dumps({"MAP": [f"0,0,{mark},B"]}, separators=(",", ":"))


def ref(index, body=None):
    body = body or payload(str(index % 10))
    return {"lot_id": f"L{index}", "wf_id": index, "lot_cd": "P", "fab_id": "F",
            "process": "PROC", "start_tm": "2026-01-01 00:00:00",
            "end_tm": "2026-01-02 00:00:00", "time_column": "end_tm",
            "time_value": "2026-01-02 00:00:00",
            "payload_hash": hashlib.sha256(body.encode()).hexdigest()[:16]}


def test_map_batch_uses_one_select_for_100_exact_refs_and_reads_lob_once(monkeypatch):
    from harness import wafer_set_map_source as maps

    reads = []
    class Lob:
        def __init__(self, value): self.value = value
        def read(self): reads.append(self.value); return self.value

    refs = [ref(i) for i in range(100)]
    rows = [(r["lot_id"], r["wf_id"], r["lot_cd"], r["fab_id"], r["process"],
             datetime(2026, 1, 1), datetime(2026, 1, 2), Lob(payload(str(i % 10))))
            for i, r in enumerate(refs)]
    cursor = Cursor(rows)
    conn = connection(cursor)
    monkeypatch.setattr(maps, "_connection", lambda: conn)
    monkeypatch.setattr(maps, "_map_table", lambda: "SAFE_MAP")
    result = maps.fetch_maps_by_refs(refs, include_payload=True, max_measurements=500,
                                     max_bytes=64 * 1024 * 1024)
    assert len(cursor.calls) == 1 and len(result.measurements) == 100
    sql, binds = cursor.calls[0]
    assert "L0" not in sql and binds["lot_0"] == "L0"
    assert "DBMS_CRYPTO" not in sql.upper()
    assert all(not key.startswith("hash_") for key in binds)
    assert len(reads) == 100 and len(result.payloads) == 10
    assert cursor.closed and conn.closed


def test_map_batch_rejects_hash_change_duplicate_fab_and_payload_limits(monkeypatch):
    from harness import wafer_set_map_source as maps

    requested = ref(1, payload("A"))
    bad = payload("B")
    cursor = Cursor([("L1", 1, "P", "F", "PROC", datetime(2026, 1, 1),
                      datetime(2026, 1, 2), bad)])
    monkeypatch.setattr(maps, "_connection", lambda: connection(cursor))
    monkeypatch.setattr(maps, "_map_table", lambda: "SAFE_MAP")
    with pytest.raises(ValueError, match="payload"):
        maps.fetch_maps_by_refs([requested], include_payload=True, max_measurements=10,
                                max_bytes=64 * 1024 * 1024)

    requested = ref(1, bad)
    cursor = Cursor([("L1", 1, "P", "F1", "PROC", datetime(2026, 1, 1), datetime(2026, 1, 2), bad),
                     ("L1", 1, "P", "F2", "PROC", datetime(2026, 1, 1), datetime(2026, 1, 2), bad)])
    monkeypatch.setattr(maps, "_connection", lambda: connection(cursor))
    with pytest.raises(ValueError, match="FAB"):
        maps.fetch_maps_by_refs([requested], include_payload=True, max_measurements=10,
                                max_bytes=64 * 1024 * 1024)

    oversized = "x" * (1024 * 1024 + 1)
    requested = ref(1, oversized)
    cursor = Cursor([("L1", 1, "P", "F", "PROC", datetime(2026, 1, 1), datetime(2026, 1, 2), oversized)])
    monkeypatch.setattr(maps, "_connection", lambda: connection(cursor))
    with pytest.raises(ValueError, match="1 MiB"):
        maps.fetch_maps_by_refs([requested], include_payload=True, max_measurements=10,
                                max_bytes=64 * 1024 * 1024)


def test_map_metadata_read_hashes_clob_in_python_without_returning_payload(monkeypatch):
    from harness import wafer_set_map_source as maps

    body = payload("A")
    full = hashlib.sha256(body.encode()).hexdigest()
    requested = ref(1, body)
    reads = []
    class Lob:
        def read(self):
            reads.append(body)
            return body
    cursor = Cursor([("L1", 1, "P", "F", "PROC", datetime(2026, 1, 1),
                      datetime(2026, 1, 2), Lob())])
    monkeypatch.setattr(maps, "_connection", lambda: connection(cursor))
    monkeypatch.setattr(maps, "_map_table", lambda: "SAFE_MAP")
    result = maps.fetch_maps_by_refs([requested], include_payload=False, max_measurements=10,
                                     max_bytes=64 * 1024 * 1024)
    sql, binds = cursor.calls[0]
    assert "DBMS_CRYPTO" not in sql.upper() and "MAP_VAL_JSON" in sql.upper()
    assert "hash_algorithm" not in binds
    assert all(not key.startswith("hash_") for key in binds)
    assert reads == [body]
    assert result.payloads == [] and result.measurements[0]["payload_sha256"] == full


def test_map_range_without_payload_hashes_clob_in_python_and_enforces_byte_limit(monkeypatch):
    from harness import wafer_set_map_source as maps

    body = payload("A")
    full = hashlib.sha256(body.encode()).hexdigest()
    reads = []
    class Lob:
        def read(self):
            reads.append(body)
            return body
    cursor = Cursor([("L", 1, "P", "F", "PROC", datetime(2026, 1, 1),
                      datetime(2026, 1, 2), Lob())])
    monkeypatch.setattr(maps, "_connection", lambda: connection(cursor))
    monkeypatch.setattr(maps, "_map_table", lambda: "SAFE_MAP")
    result = maps.fetch_map_range(None, measurement_policy="all", include_payload=False,
                                  max_rows=10, max_measurements=10, max_bytes=10_000)
    sql, binds = cursor.calls[0]
    assert "DBMS_CRYPTO" not in sql.upper() and "MAP_VAL_JSON" in sql.upper()
    assert "hash_algorithm" not in binds and reads == [body]
    assert result.payloads == []
    assert result.measurements[0]["payload_sha256"] == full
    assert "map_val_json" not in result.measurements[0]
    assert result.bytes_read == len(body.encode()) + 2

    too_small = len(body.encode()) - 1
    cursor = Cursor([("L", 1, "P", "F", "PROC", datetime(2026, 1, 1),
                      datetime(2026, 1, 2), body)])
    monkeypatch.setattr(maps, "_connection", lambda: connection(cursor))
    limited = maps.fetch_map_range(None, measurement_policy="all", include_payload=False,
                                   max_rows=10, max_measurements=10, max_bytes=too_small)
    assert limited.complete is False and limited.missing_reason == "BYTE_LIMIT"


def test_map_payload_range_does_not_send_unused_hash_bind(monkeypatch):
    from harness import wafer_set_map_source as maps

    body = payload("A")
    cursor = Cursor([("L", 1, "P", "F", "PROC", datetime(2026, 1, 1), datetime(2026, 1, 2), body)])
    monkeypatch.setattr(maps, "_connection", lambda: connection(cursor))
    monkeypatch.setattr(maps, "_map_table", lambda: "SAFE_MAP")
    result = maps.fetch_map_range(None, measurement_policy="all", include_payload=True,
                                  max_rows=10, max_measurements=10, max_bytes=10_000)
    assert "hash_algorithm" not in cursor.calls[0][1]
    assert len(result.payloads) == 1

def test_general_source_normalizes_identity_and_preserves_duplicate_occurrences(monkeypatch):
    from harness import wafer_set_sources as sources
    from harness.wafer_set_contracts import SourceQuery

    rows = [("P", "2026-01", 2026, 1, "L", "07", "S", 0.25, "PROC", datetime(2026, 1, 1)),
            ("P", "2026-01", 2026, 1, "L", "07", "S", 0.25, "PROC", datetime(2026, 1, 1))]
    cursor = SimpleNamespace(description=[(n,) for n in ("LOT_CD", "WEEK", "YEAR", "MONTH", "LOT_ID",
        "SOURCE_WF_ID", "WF_ID", "PARAMETER", "VALUE", "PROCESS", "MEASUREMENT_START")],
        execute=lambda sql, binds: None, fetchmany=lambda count: rows[:count], close=lambda: None)
    monkeypatch.setattr(sources, "_connection", lambda: connection(cursor))
    result = sources.fetch_source("wafer_parameter", SourceQuery(lot_ids=["L"]), [], "all", 10, 10_000)
    assert [row["wf_id"] for row in result.rows] == [7, 7]
    assert result.rows[0]["fact_id"] != result.rows[1]["fact_id"]
    assert result.duplicate_count == 1
    assert all(row["unit"] == "unspecified" for row in result.rows)


def test_general_source_latest_policy_selects_latest_and_rejects_tie_conflict(monkeypatch):
    from harness import wafer_set_sources as sources

    names = ("LOT_CD", "WEEK", "YEAR", "MONTH", "LOT_ID", "SOURCE_WF_ID", "WF_ID",
             "PARAMETER", "VALUE", "PROCESS", "MEASUREMENT_START")
    rows = [("P", "2026-01", 2026, 1, "L", "1", "1", "S", 0.1, "PROC", datetime(2026, 1, 1)),
            ("P", "2026-01", 2026, 1, "L", "1", "1", "S", 0.2, "PROC", datetime(2026, 1, 2))]
    cursor = SimpleNamespace(description=[(name,) for name in names], execute=lambda sql, binds: None,
                             fetchmany=lambda count: rows, close=lambda: None)
    monkeypatch.setattr(sources, "_connection", lambda: connection(cursor))
    result = sources.fetch_source("wafer_parameter", None, [], "latest_per_entity", 10, 10_000)
    assert len(result.rows) == 1 and result.rows[0]["value"] == 0.2

    conflict = [rows[-1], (*rows[-1][:7], "S", 0.3, "PROC", datetime(2026, 1, 2))]
    cursor.fetchmany = lambda count: conflict
    with pytest.raises(ValueError, match="same-time"):
        sources.fetch_source("wafer_parameter", None, [], "latest_per_entity", 10, 10_000)


def test_source_lob_is_read_before_connection_closes(monkeypatch):
    from harness import wafer_set_sources as sources

    state = {"closed": False, "reads": 0}
    class Lob:
        def read(self):
            assert not state["closed"]
            state["reads"] += 1
            return payload()
    cursor = SimpleNamespace(description=[("LOT_ID",), ("WF_ID",), ("LOT_CD",), ("FAB_ID",),
        ("PROCESS",), ("START_TM",), ("END_TM",), ("MAP_VAL_JSON",)],
        execute=lambda sql, binds: None,
        fetchmany=lambda count: [("L", 1, "P", "F", "PROC", datetime(2026, 1, 1),
                                  datetime(2026, 1, 2), Lob())], close=lambda: None)
    conn = SimpleNamespace(cursor=lambda: cursor, call_timeout=30_000,
                           close=lambda: state.update(closed=True))
    monkeypatch.setattr(sources, "_connection", lambda: conn)
    result = sources.fetch_source("map", None,
        ["lot_id", "wf_id", "lot_cd", "fab_id", "process", "start_tm", "end_tm", "map_val_json"],
        "all", 10, 10_000)
    assert state == {"closed": True, "reads": 1}
    assert result.rows[0]["map_val_json"].startswith("{")


def test_binary_catalog_fields_are_projected_as_availability(monkeypatch):
    from harness import wafer_set_sources as sources

    class PresenceCursor:
        description = [("ALIAS_LOT_ID",), ("SOURCE_WF_ID",), ("WF_ID",), ("IMAGE_TYPE",),
                       ("DATE_RAW",), ("IMAGE_AVAILABLE",), ("PDF_AVAILABLE",)]
        def __init__(self): self.sql = ""
        def execute(self, sql, binds): self.sql = sql
        def fetchmany(self, count): return [("L", "1", "1", "I", "20260101", 1, 1)]
        def close(self): pass
    cursor = PresenceCursor()
    monkeypatch.setattr(sources, "_connection", lambda: connection(cursor))
    result = sources.fetch_source("bmw_image", None,
        ["alias_lot_id", "source_wf_id", "wf_id", "image_type", "date_raw",
         "image_available", "pdf_available"], "all", 10, 10_000)
    assert "CASE WHEN IMAGE_B64 IS NOT NULL" in cursor.sql
    assert "CASE WHEN PDF_FILE_PATH IS NOT NULL" in cursor.sql
    assert result.rows[0]["image_available"] is True and result.rows[0]["pdf_available"] is True
    assert "/" not in json.dumps(result.rows[0])


def test_binary_availability_fields_are_in_default_projection(monkeypatch):
    from harness import wafer_set_sources as sources

    class PresenceCursor:
        description = [("ALIAS_LOT_ID",), ("SOURCE_WF_ID",), ("WF_ID",), ("IMAGE_TYPE",),
                       ("DATE_RAW",), ("IMAGE_AVAILABLE",), ("PDF_AVAILABLE",)]
        def execute(self, sql, binds): self.sql = sql
        def fetchmany(self, count): return [("L", "1", "1", "I", "20260101", 0, 1)]
        def close(self): pass
    cursor = PresenceCursor()
    monkeypatch.setattr(sources, "_connection", lambda: connection(cursor))
    result = sources.fetch_source("bmw_image", None, [], "all", 10, 10_000)
    assert result.rows[0]["image_available"] is False
    assert result.rows[0]["pdf_available"] is True


@pytest.mark.asyncio
async def test_disjoint_member_and_query_scope_does_not_read_entire_source(monkeypatch):
    from harness.tools import wafer_set_facts
    from harness.wafer_set_contracts import TableHandle
    from harness.wafer_set_core import ResolvedTable, table_version

    rows = [{"lot_id": "L1", "wf_id": 1, "member_id": "m1"}]
    resolved = ResolvedTable(rows, list(rows[0]), {}, True, None, table_version(rows),
                             SimpleNamespace(provenance={"data_origin": "fixture"}))
    async def resolve(*args): return resolved
    async def inline(function, *args, **kwargs): return function(*args, **kwargs)
    monkeypatch.setattr(wafer_set_facts, "resolve_handle", resolve)
    monkeypatch.setattr(wafer_set_facts, "run_blocking", inline)
    monkeypatch.setattr(wafer_set_facts, "fetch_source",
                        lambda *args, **kwargs: pytest.fail("disjoint scope must not query the source"))
    result = await wafer_set_facts.read_facts(wafer_set_facts.ReadFactsInput(
        source_id="wafer_parameter", members=TableHandle(result_id="r", table_id="members"),
        query={"wafer_pairs": [{"lot_id": "L2", "wf_id": 2}]}), SimpleNamespace())
    assert result.status == "empty" and result.tables[0].rows == []


@pytest.mark.asyncio
async def test_select_filter_cohort_read_facts_chain(monkeypatch):
    from harness.tools import wafer_set_facts, wafer_set_members
    from harness.wafer_set_core import ResolvedTable, table_version

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(wafer_set_facts, "run_blocking", inline)
    monkeypatch.setattr(wafer_set_members, "run_blocking", inline)

    selected = [{"lot_id": "L", "wf_id": 12, "lot_cd": "P", "process": "PROC",
                 "time_column": "end_tm", "time_value": "2026-01-02 00:00:00",
                 "payload_hash": hashlib.sha256(payload().encode()).hexdigest()[:16],
                 "member_id": "L.12", "measurement_ref": "ref-12"}]
    resolved = ResolvedTable(selected, list(selected[0]), {}, True, None, table_version(selected),
                             SimpleNamespace(provenance={"data_origin": "fixture"}))
    async def resolve(*args): return resolved
    monkeypatch.setattr(wafer_set_members, "resolve_handle", resolve)
    async def resolve_many(*args): return [resolved]
    monkeypatch.setattr(wafer_set_members, "resolve_many", resolve_many)
    filtered = await wafer_set_members.filter_set(wafer_set_members.FilterSetInput(
        source={"result_id": "source", "table_id": "wafers"}, operation="where",
        predicate={"items": [{"column": "wf_id", "op": "ge", "value": 1}]},
        key_columns=["lot_id", "wf_id"]), SimpleNamespace())
    assert filtered.tables[0].rows == selected

    filtered_resolved = ResolvedTable(filtered.tables[0].rows, filtered.tables[0].columns, {}, True, None,
        table_version(filtered.tables[0].rows), SimpleNamespace(provenance={"data_origin": "fixture"}))
    async def resolve_filtered(*args): return filtered_resolved
    monkeypatch.setattr(wafer_set_members, "resolve_handle", resolve_filtered)
    cohorts = await wafer_set_members.make_cohorts_handler(wafer_set_members.MakeCohortsInput(
        source={"result_id": "filtered", "table_id": "members"}, key_columns=["lot_id", "wf_id"],
        rules=[{"name": "even", "predicate": {"items": [
            {"column": "wf_id", "op": "mod_eq", "divisor": 2, "remainder": 0}]}}]), SimpleNamespace())
    assert cohorts.tables[1].rows[0]["member_id"] == "L.12"

    monkeypatch.setattr(wafer_set_facts, "resolve_handle", resolve)
    map_result = SimpleNamespace(measurements=[{**selected[0], "payload_sha256": "full",
        "payload_table_id": "map_payloads", "payload_version": "v"}],
        payloads=[{"payload_hash": selected[0]["payload_hash"], "payload_sha256": "full",
                   "map_val_json": payload()}], bytes_read=len(payload()), complete=True,
        missing_reason=None, requested_count=1, duplicate_count=0, invalid_identity_count=0)
    monkeypatch.setattr(wafer_set_facts, "fetch_maps_by_refs", lambda *a, **kw: map_result)
    result = await wafer_set_facts.read_facts(wafer_set_facts.ReadFactsInput(
        source_id="map", members={"result_id": "source", "table_id": "wafers"},
        include_payload=True), SimpleNamespace())
    assert [table.table_id for table in result.tables] == ["map_measurements", "map_payloads", "read_audit"]
    assert result.source_result_ids == ["source"]


def test_fact_descriptions_are_general():
    from harness.tools import wafer_set_facts
    from harness.tools.registry import ToolRegistry

    registry = ToolRegistry()
    wafer_set_facts.register(registry)
    bad = ("PT1H", "PT1C", "?", "LANGGRAPH", "MAP.", "DF_")
    for spec in registry.tools.values():
        payload_text = json.dumps(spec.schema.model_json_schema(), ensure_ascii=False)
        assert all(token not in spec.description and token not in payload_text for token in bad)

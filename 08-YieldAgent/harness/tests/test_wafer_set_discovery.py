import json
import builtins
import sys
from types import SimpleNamespace

import pytest


def context():
    return SimpleNamespace(run={"principal_id": "owner", "session_id": "session", "run_id": "run", "epoch": 1})


@pytest.fixture
def inline(monkeypatch):
    from harness.tools import wafer_set_discovery as tool
    async def blocking(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(tool, "run_blocking", blocking)


def test_contract_predicates_and_source_query_reject_unsafe_shapes():
    from harness.wafer_set_contracts import PredicateAtom, SourceQuery, TimeRange

    with pytest.raises(ValueError):
        PredicateAtom(column="x", op="in", value=list(range(501)))
    with pytest.raises(ValueError):
        PredicateAtom(column="x", op="between", value=[2, 1])
    with pytest.raises(ValueError):
        PredicateAtom(column="x", op="mod_eq", divisor=0, remainder=0)
    with pytest.raises(ValueError):
        SourceQuery(lot_ids=["a"], wafer_pairs=[{"lot_id": "a", "wf_id": 1}])
    with pytest.raises(ValueError):
        TimeRange(column="time", start="2026-01-02", end="2026-01-01")


@pytest.mark.asyncio
async def test_describe_sources_metadata_definitions_and_partial(monkeypatch, inline):
    from harness.tools import wafer_set_discovery as tool

    monkeypatch.setattr(tool, "inspect_catalog", lambda source_ids: {
        "map": {"available": True, "columns": {
            "LOT_ID": {"data_type": "VARCHAR2", "nullable": False},
            "WF_ID": {"data_type": "NUMBER", "nullable": False},
        }},
        "wafer_parameter": {"available": False, "columns": {}, "missing_reason": "not found"},
    })
    monkeypatch.setattr(tool, "load_definition_snapshot", lambda lot_cd, process, refresh=False, source_ids=None: {
        "metric_definitions": [{"definition_id": "d1", "lot_cd": "P", "process": "PROC",
            "parameter": "PARAM", "mode": "symbol", "regex_pattern": "^..$", "unit": "unknown",
            "better_when": "unknown", "included": True, "display_order": 1,
            "definition_version": "v1"}],
        "process_links": [{"source_id": "symbol_definition", "source_process": "PROC",
            "definition_process": "PROC", "mapping_basis": "exact", "mapping_version": "v1"}],
        "question_guides": [{"source_id": "wafer_parameter", "question_type": "numeric_yield_fail",
                             "guidance": "Use stored parameter definitions."}],
        "metadata_availability": {"param_metadata": True, "source_guides": True},
        "metadata_limitations": [],
    })
    args = tool.DescribeSourcesInput(source_ids=["map", "wafer_parameter"], lot_cd=["P"], process=["PROC"])
    result = await tool.describe_sources(args, context())
    assert result.status == "partial"
    assert {row["source_id"] for row in result.tables[0].rows} == {"map", "wafer_parameter"}
    assert result.tables[3].rows[0]["definition_id"] == "d1"
    assert result.tables[5].rows[0]["question_type"] == "numeric_yield_fail"
    assert result.scope["definition_version"]
    assert not result.tables[0].complete
    assert result.scope["table_versions"]

    without_definitions = await tool.describe_sources(tool.DescribeSourcesInput(
        source_ids=["map", "wafer_parameter"], lot_cd=["P"], process=["PROC"],
        include_definitions=False), context())
    assert without_definitions.tables[3].rows == []
    assert without_definitions.tables[5].rows[0]["question_type"] == "numeric_yield_fail"


@pytest.mark.asyncio
async def test_describe_sources_exposes_exact_yield_guide_without_definitions(monkeypatch, inline):
    from harness.tools import wafer_set_discovery as tool

    guidance = ("For yield (수율), use parameter PT1H unless a process is specified; then use "
                "the yield-kind parameter for that process.")
    monkeypatch.setattr(tool, "inspect_catalog", lambda source_ids: {
        "wafer_parameter": {"available": True, "columns": {}}})
    monkeypatch.setattr(tool, "load_definition_snapshot", lambda *args, **kwargs: {
        "metric_definitions": [], "process_links": [],
        "question_guides": [{"source_id": "wafer_parameter",
            "question_type": "yield_parameter_selection", "guidance": guidance}],
        "metadata_availability": {"param_metadata": True, "source_guides": True},
        "metadata_limitations": [], "definition_version": "snapshot"})

    result = await tool.describe_sources(tool.DescribeSourcesInput(
        source_ids=["wafer_parameter"], include_definitions=False), context())

    assert result.tables[3].rows == []
    assert result.tables[5].rows == [{"source_id": "wafer_parameter",
        "question_type": "yield_parameter_selection", "guidance": guidance}]


def test_column_unit_uses_enriched_symbol_rows_and_reports_mixed_units():
    from harness.tools.wafer_set_discovery import _column_unit

    symbol = {"source_id": "wafer_parameter", "mode": "symbol", "unit": "fraction"}
    assert _column_unit("wafer_parameter", "value", [symbol]) == "fraction"
    assert _column_unit("wafer_parameter", "value", [symbol, {
        "source_id": "wafer_parameter", "mode": "source_value", "unit": "volt"}]) == "per_row_unit"


@pytest.mark.asyncio
async def test_list_values_db_and_stored_table_paths(monkeypatch, inline):
    from harness.tools import wafer_set_discovery as tool
    from harness.wafer_set_core import ResolvedTable, table_version

    monkeypatch.setattr(tool, "query_distinct_values", lambda *a, **kw: {
        "rows": [{"value": "a", "count": 2, "is_null": False}], "source_row_count": 3,
        "distinct_count": 2, "null_count": 1,
    })
    db = await tool.list_values(tool.ListValuesInput(source_id="map", field="lot_id", limit=1), context())
    assert db.tables[0].rows == [{"value": "a", "count": 2, "is_null": False}]
    assert not db.tables[0].complete and db.tables[1].complete

    rows = [{"x": 2}, {"x": 1}, {"x": 2}, {"x": None}]
    resolved = ResolvedTable(rows=rows, columns=["x"], units={}, complete=True, missing_reason=None,
                             actual_version=table_version(rows), observation=SimpleNamespace(provenance={}))
    async def resolve(*a):
        return resolved
    monkeypatch.setattr(tool, "resolve_handle", resolve)
    stored = await tool.list_values(tool.ListValuesInput(
        source={"result_id": "source", "table_id": "facts"}, field="x", order="count_desc"), context())
    assert stored.tables[0].rows[0] == {"value": 2, "count": 2, "is_null": False}
    assert stored.source_result_ids == ["source"]


@pytest.mark.asyncio
async def test_inspect_coverage_stored_table_exact_counts(monkeypatch, inline):
    from harness.tools import wafer_set_discovery as tool
    from harness.wafer_set_core import ResolvedTable, table_version

    rows = [{"id": 1, "x": None, "time": "2026-01-02"},
            {"id": 1, "x": 3, "time": "2026-01-01"}, {"id": 2, "x": 4, "time": None}]
    resolved = ResolvedTable(rows=rows, columns=["id", "x", "time"], units={}, complete=False,
                             missing_reason="limited", actual_version=table_version(rows),
                             observation=SimpleNamespace(provenance={"data_origin": "fixture"}))
    async def resolve(*a):
        return resolved
    monkeypatch.setattr(tool, "resolve_handle", resolve)
    args = tool.InspectCoverageInput(source={"result_id": "source", "table_id": "facts"},
        columns=["x"], key_columns=["id"], time_column="time")
    result = await tool.inspect_coverage(args, context())
    checks = {(row["check"], row["field"]): row for row in result.tables[0].rows}
    assert checks[("rows", "*")]["numerator"] == 3
    assert checks[("nulls", "x")]["numerator"] == 1
    assert checks[("duplicate_groups", "id")]["numerator"] == 1
    assert checks[("duplicate_rows", "id")]["numerator"] == 1
    assert checks[("time_range", "time")]["minimum"] == "2026-01-01"
    assert not checks[("rows", "*")]["population_complete"]


def test_discovery_descriptions_are_general():
    from harness.tools import wafer_set_discovery as tool
    from harness.tools.registry import ToolRegistry

    registry = ToolRegistry()
    tool.register(registry)
    bad = ("PT1H", "PT1C", "?", "LANGGRAPH", "MAP.", "DF_")
    for spec in registry.tools.values():
        payload = json.dumps(spec.schema.model_json_schema(), ensure_ascii=False)
        assert all(value not in spec.description and value not in payload for value in bad)
    description = tool.DescribeSourcesInput.model_fields["include_definitions"].description
    assert description == "계산 정의 포함 여부. False이면 metric_definitions는 빈 목록이며 question_guides는 유지됩니다."


class Cursor:
    def __init__(self, batches):
        self.batches = iter(batches)
        self.calls = []
        self.closed = False

    def execute(self, sql, binds):
        self.calls.append((sql, dict(binds)))
        description, rows = next(self.batches)
        self.description = [(name,) for name in description]
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchmany(self, count):
        return list(self._rows)[:count]

    def close(self):
        self.closed = True


def connection(cursor):
    value = SimpleNamespace(cursor=lambda: cursor, closed=False, call_timeout=30_000)
    def close():
        value.closed = True
    value.close = close
    return value


def test_catalog_metadata_uses_bound_allowlisted_query(monkeypatch):
    from harness import wafer_set_sql as sql

    cursor = Cursor([(("TABLE_NAME", "COLUMN_NAME", "DATA_TYPE", "NULLABLE"), [
        ("DF_SYMBOL_BIN", "LOT_CD", "VARCHAR2", "N"),
        ("DF_SYMBOL_BIN", "PROCESS", "VARCHAR2", "N"),
        ("DF_SYMBOL_BIN", "PARAM_NM", "VARCHAR2", "N"),
        ("DF_SYMBOL_BIN", "REGEX_PATTERN", "VARCHAR2", "N"),
        ("DF_SYMBOL_BIN", "DBLOADTIME", "TIMESTAMP", "N"),
    ])])
    conn = connection(cursor)
    monkeypatch.setattr(sql, "_connection", lambda: conn)
    result = sql.inspect_catalog(["symbol_definition"])
    query, binds = cursor.calls[0]
    assert result["symbol_definition"]["available"]
    assert "DF_SYMBOL_BIN" not in query and binds == {"table_0": "DF_SYMBOL_BIN"}
    assert cursor.closed and conn.closed and conn.call_timeout == 30_000


def test_map_catalog_physical_name_uses_map_runtime_configuration(monkeypatch):
    from harness.wafer_set_catalog import physical_name, source_spec

    monkeypatch.setenv("ORACLE_TABLE", "environment_map")
    monkeypatch.setitem(sys.modules, "map_agent", SimpleNamespace(ORACLE_TABLE="runtime_map"))

    assert physical_name(source_spec("map")) == "RUNTIME_MAP"


def test_map_catalog_physical_name_falls_back_when_map_agent_import_fails(monkeypatch):
    from harness.wafer_set_catalog import physical_name, source_spec

    monkeypatch.setenv("ORACLE_TABLE", "fallback_map")
    original_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "map_agent":
            raise ModuleNotFoundError("map_agent unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    assert physical_name(source_spec("map")) == "FALLBACK_MAP"


def test_map_catalog_physical_name_uses_default_after_import_and_environment_fallback(monkeypatch):
    from harness.wafer_set_catalog import physical_name, source_spec

    monkeypatch.delenv("ORACLE_TABLE", raising=False)
    original_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "map_agent":
            raise ModuleNotFoundError("map_agent unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    assert physical_name(source_spec("map")) == "LANGGRAPH_DATA"


def test_wads_catalog_physical_name_uses_loaded_environment(monkeypatch):
    from harness.wafer_set_catalog import physical_name, source_spec

    monkeypatch.setenv("WADS_WF_LIST_TABLE", "runtime_wads")
    assert physical_name(source_spec("wads_wafer")) == "RUNTIME_WADS"


def test_map_catalog_metadata_uses_runtime_table_case_insensitively(monkeypatch):
    from harness import wafer_set_sql as sql

    monkeypatch.setitem(sys.modules, "map_agent", SimpleNamespace(ORACLE_TABLE="runtime_map"))
    required = [physical for physical in sql.source_spec("map").columns.values() if physical]
    cursor = Cursor([(("TABLE_NAME", "COLUMN_NAME", "DATA_TYPE", "NULLABLE"), [
        ("RUNTIME_MAP", column.lower(), "VARCHAR2", "Y") for column in required
    ])])
    conn = connection(cursor)
    monkeypatch.setattr(sql, "_connection", lambda: conn)

    result = sql.inspect_catalog(["map"])
    query, binds = cursor.calls[0]

    assert result["map"]["available"]
    assert binds == {"table_0": "RUNTIME_MAP"}
    assert "RUNTIME_MAP" not in query


def test_distinct_sql_binds_values_and_rejects_large_objects(monkeypatch):
    from harness import wafer_set_sql as sql
    from harness.wafer_set_contracts import SourceQuery

    cursor = Cursor([(("VALUE", "CNT", "SOURCE_ROWS", "DISTINCT_COUNT", "NULL_COUNT"),
                      [("P", 2, 3, 2, 1)])])
    conn = connection(cursor)
    monkeypatch.setattr(sql, "_connection", lambda: conn)
    result = sql.query_distinct_values("symbol_definition", "parameter",
        SourceQuery(lot_cd=["P' OR 1=1"]), True, "count_desc", 10)
    query, binds = cursor.calls[0]
    assert "P' OR 1=1" not in query and "DF_SYMBOL_BIN" in query
    assert binds["lot_cd_0"] == "P' OR 1=1" and result["source_row_count"] == 3
    with pytest.raises(ValueError, match="large-object"):
        sql.query_distinct_values("map", "map_val_json", None, True, "count_desc", 10)


def test_distinct_null_exclusion_preserves_exact_source_stats(monkeypatch):
    from harness import wafer_set_sql as sql

    cursor = Cursor([(("VALUE", "CNT", "SOURCE_ROWS", "DISTINCT_COUNT", "NULL_COUNT", "RN"),
                      [(None, None, 3, 0, 3, None)])])
    conn = connection(cursor)
    monkeypatch.setattr(sql, "_connection", lambda: conn)
    result = sql.query_distinct_values("symbol_definition", "parameter", None, False, "count_desc", 10)
    assert result == {"rows": [], "source_row_count": 3, "distinct_count": 0, "null_count": 3}


def test_definition_snapshot_combines_catalog_and_symbol(monkeypatch):
    from harness import wafer_set_symbols as symbols

    symbols._CACHE.clear()
    cursor = Cursor([
        (("LOT_CD", "PROCESS", "PARAM_NM", "REGEX_PATTERN", "DBLOADTIME"),
         [("P' OR 1=1", "PROC' OR 1=1", "S", "^..$", "2026-01-01"),
          ("P' OR 1=1", "PROC' OR 1=1", "N", "^A.$", "2026-01-01")]),
        (("LOT_CD", "PROC_GBN_CD", "YIELD_TABLE", "PARAM_NM", "PARAM_SEQNO"),
         [("P' OR 1=1", "PROC' OR 1=1", "o", "S", 1),
          ("P' OR 1=1", "PROC' OR 1=1", "o", "V", 2),
          ("P' OR 1=1", "PROC' OR 1=1", "o", "N", 3)]),
        (("TABLE_NAME",), [("DF_PARAM_META",), ("DF_SOURCE_GUIDE",)]),
        (("SOURCE_ID", "LOT_CD", "PROCESS", "PARAM_NM", "PARAM_KIND", "UNIT",
          "VALUE_BASIS", "BETTER_WHEN"),
         [("wafer_parameter", "P' OR 1=1", "PROC' OR 1=1", "S", "bin", "fraction",
           "symbol fraction", "lower"),
          ("wafer_parameter", "P' OR 1=1", "PROC' OR 1=1", "V", "yield", "ratio",
           "catalog value", "higher"),
          ("wafer_parameter", "P' OR 1=1", "PROC' OR 1=1", "M", "item", "volt",
           "metadata only", "neutral")]),
        (("SOURCE_ID", "QUESTION_TYPE", "GUIDANCE"), []),
    ])
    conn = connection(cursor)
    import sys
    monkeypatch.setitem(sys.modules, "common", SimpleNamespace(get_oracle_connection=lambda: conn))
    result = symbols.load_definition_snapshot(["P' OR 1=1"], ["PROC' OR 1=1"], refresh=True)
    rows = result["metric_definitions"]
    assert len(rows) == 4
    assert len({(row["lot_cd"], row["process"], row["parameter"]) for row in rows}) == len(rows)
    by_name = {row["parameter"]: row for row in rows}
    assert by_name["S"]["mode"] == "symbol" and by_name["S"]["included"]
    assert by_name["V"]["mode"] == "source_value" and by_name["V"]["regex_pattern"] is None
    assert by_name["S"]["regex_pattern"] == "^..$"
    assert by_name["S"]["definition_id"].startswith("symbol:")
    assert by_name["S"]["unit"] == "fraction" and by_name["S"]["better_when"] == "lower"
    assert by_name["V"]["definition_id"].startswith("source_value:")
    assert not by_name["V"]["definition_id"].startswith("source_value:wafer_parameter:")
    assert by_name["V"]["display_order"] == 2 and by_name["V"]["unit"] == "ratio"
    assert by_name["M"]["definition_id"].startswith("source_value:wafer_parameter:")
    assert by_name["M"]["included"] is None and by_name["M"]["unit"] == "volt"
    assert by_name["N"]["unit"] == "unknown" and by_name["N"]["better_when"] == "unknown"
    assert all("P' OR 1=1" not in sql for sql, _ in cursor.calls)
    assert all("PROC' OR 1=1" not in sql for sql, _ in cursor.calls)
    metadata_sql, metadata_binds = cursor.calls[3]
    assert "PROCESS IN" in metadata_sql
    assert "PROC' OR 1=1" in metadata_binds.values()
    assert len(result["definition_version"]) == 16


@pytest.mark.parametrize("symbol_rows, catalog_rows, metadata_rows, message", [
    ([("P", "X", "S", "^..$", "1"), ("P", "X", "S", "^A.$", "2")], [], [],
     "Duplicate symbol definitions"),
    ([], [("P", "X", "o", "V", 1), ("P", "X", "o", "V", 2)], [],
     "Duplicate parameter definitions"),
    ([], [], [("one", "P", "X", "M", "item", "volt", "a", "neutral"),
              ("two", "P", "X", "M", "item", "volt", "b", "neutral")],
     "Duplicate parameter metadata definitions"),
])
def test_definition_snapshot_rejects_duplicate_triples(
        monkeypatch, symbol_rows, catalog_rows, metadata_rows, message):
    from harness import wafer_set_symbols as symbols

    symbols._CACHE.clear()
    batches = [
        (("LOT_CD", "PROCESS", "PARAM_NM", "REGEX_PATTERN", "DBLOADTIME"), symbol_rows),
    ]
    if "symbol" not in message:
        batches.append((("LOT_CD", "PROC_GBN_CD", "YIELD_TABLE", "PARAM_NM", "PARAM_SEQNO"),
                        catalog_rows))
    if "metadata" in message:
        batches.extend([
            (("TABLE_NAME",), [("DF_PARAM_META",)]),
            (("SOURCE_ID", "LOT_CD", "PROCESS", "PARAM_NM", "PARAM_KIND", "UNIT",
              "VALUE_BASIS", "BETTER_WHEN"), metadata_rows),
        ])
    cursor = Cursor(batches)
    conn = connection(cursor)
    monkeypatch.setitem(sys.modules, "common", SimpleNamespace(get_oracle_connection=lambda: conn))

    with pytest.raises(ValueError, match=message):
        symbols.load_definition_snapshot(["P"], ["X"], refresh=True, source_ids=["one", "two"])


def test_definition_snapshot_hashes_final_merge_deterministically(monkeypatch):
    from harness import wafer_set_symbols as symbols
    from harness.tools.wafer_set_tools import _version

    def load(pattern="^..$", basis="first", reverse=False, refresh=True):
        symbol_rows = [("P", "PT1H", "S", pattern, "2026-01-01")]
        catalog_rows = [("P", "PT1H", "o", "S", 1)]
        metadata_rows = [
            ("wafer_parameter", "P", "PT1H", "S", "bin", "fraction", basis, "lower"),
            ("wafer_parameter", "P", "PT1H", "M", "item", "volt", "stored", "neutral"),
        ]
        if reverse:
            metadata_rows.reverse()
        cursor = Cursor([
            (("LOT_CD", "PROCESS", "PARAM_NM", "REGEX_PATTERN", "DBLOADTIME"), symbol_rows),
            (("LOT_CD", "PROC_GBN_CD", "YIELD_TABLE", "PARAM_NM", "PARAM_SEQNO"), catalog_rows),
            (("TABLE_NAME",), [("DF_PARAM_META",)]),
            (("SOURCE_ID", "LOT_CD", "PROCESS", "PARAM_NM", "PARAM_KIND", "UNIT",
              "VALUE_BASIS", "BETTER_WHEN"), metadata_rows),
        ])
        conn = connection(cursor)
        monkeypatch.setitem(sys.modules, "common", SimpleNamespace(get_oracle_connection=lambda: conn))
        return symbols.load_definition_snapshot(
            ["P"], ["PT1H TEST"], refresh=refresh, source_ids=["wafer_parameter"]), cursor

    symbols._CACHE.clear()
    first, cursor = load()
    monkeypatch.setitem(sys.modules, "common", SimpleNamespace(
        get_oracle_connection=lambda: pytest.fail("database read on cache hit")))
    cached = symbols.load_definition_snapshot(
        ["P"], ["PT1H TEST"], refresh=False, source_ids=["wafer_parameter"])
    reordered, _ = load(reverse=True)
    changed_metadata, _ = load(basis="second")
    changed_regex, _ = load(pattern="^A.$")

    first_symbol = next(row for row in first["metric_definitions"] if row["parameter"] == "S")
    metadata_symbol = next(row for row in changed_metadata["metric_definitions"] if row["parameter"] == "S")
    regex_symbol = next(row for row in changed_regex["metric_definitions"] if row["parameter"] == "S")
    assert first == cached
    assert first["definition_version"] == reordered["definition_version"]
    assert first["definition_version"] != changed_metadata["definition_version"]
    assert _version(first["metric_definitions"]) != _version(changed_metadata["metric_definitions"])
    assert first_symbol["definition_id"] == metadata_symbol["definition_id"]
    assert first_symbol["definition_version"] == metadata_symbol["definition_version"]
    assert first_symbol["definition_id"] != regex_symbol["definition_id"]
    assert all(row["process"] == "PT1H" for row in first["metric_definitions"])
    assert "PT1H" in cursor.calls[0][1].values()
    assert "PT1H" in cursor.calls[3][1].values()


def test_definition_snapshot_rejects_ambiguous_metadata_process(monkeypatch):
    from harness import wafer_set_symbols as symbols

    symbols._CACHE.clear()
    cursor = Cursor([
        (("LOT_CD", "PROCESS", "PARAM_NM", "REGEX_PATTERN", "DBLOADTIME"),
         [("P", "A", "S", "^..$", "1"), ("P", "A B", "S", "^A.$", "1")]),
        (("LOT_CD", "PROC_GBN_CD", "YIELD_TABLE", "PARAM_NM", "PARAM_SEQNO"), []),
        (("TABLE_NAME",), [("DF_PARAM_META",)]),
        (("SOURCE_ID", "LOT_CD", "PROCESS", "PARAM_NM", "PARAM_KIND", "UNIT",
          "VALUE_BASIS", "BETTER_WHEN"),
         [("wafer_parameter", "P", "A B C", "S", "bin", "fraction", "stored", "lower")]),
    ])
    conn = connection(cursor)
    monkeypatch.setitem(sys.modules, "common", SimpleNamespace(get_oracle_connection=lambda: conn))

    with pytest.raises(ValueError, match="ambiguous"):
        symbols.load_definition_snapshot(["P"], ["A", "A B"], refresh=True)


def test_source_query_pair_batches_stay_within_bind_budget():
    from harness.wafer_set_contracts import SourceQuery
    from harness.wafer_set_sql import source_query_chunks, build_where

    query = SourceQuery(wafer_pairs=[{"lot_id": f"L{i}", "wf_id": i} for i in range(1000)])
    chunks = source_query_chunks("map", query)
    assert len(chunks) == 8
    assert sum(len(chunk.wafer_pairs) for chunk in chunks) == 1000
    assert all(len(build_where("map", chunk)[1]) <= 900 for chunk in chunks)


def test_combined_lists_and_path_bind_reservation_stay_within_budget():
    from harness.wafer_set_contracts import PredicateAtom, PredicateGroup, SourceQuery
    from harness.wafer_set_sql import source_query_chunks, build_where

    lists = SourceQuery(lot_ids=[f"L{i}" for i in range(500)], where=PredicateGroup(items=[
        PredicateAtom(column="process", op="in", value=[f"P{i}" for i in range(500)])]))
    _, binds = build_where("map", lists)
    assert len(binds) <= 900 and any(key.startswith("predicate_json_") for key in binds)

    pairs = SourceQuery(lot_cd=[f"C{i}" for i in range(20)], process=[f"P{i}" for i in range(20)],
        wafer_pairs=[{"lot_id": f"L{i}", "wf_id": i} for i in range(500)],
        where=PredicateGroup(items=[
            PredicateAtom(column="fab_id", op="in", value=[f"F{i}" for i in range(500)]),
            PredicateAtom(column="process", op="in", value=[f"Q{i}" for i in range(250)])]))
    chunks = source_query_chunks("map", pairs, reserved_binds=1)
    assert all(len(build_where("map", chunk)[1]) + 1 <= 900 for chunk in chunks)


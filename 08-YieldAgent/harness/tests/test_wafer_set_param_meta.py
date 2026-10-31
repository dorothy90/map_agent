from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SEED_DATA = json.loads((Path(__file__).parents[2] / "seed_data" / "param_meta_seed.json").read_text()) \
    if (Path(__file__).parents[2] / "seed_data" / "param_meta_seed.json").exists() else {
        "source_id": "wafer_parameter",
        "discarded_params": ["OBSOLETE"],
        "kinds": {
            "item": {"unit": "unspecified", "value_basis": "item parameter value as stored",
                     "better_when": "unknown"},
            "bin": {"unit": "fraction", "value_basis": "bin basis", "better_when": "lower"},
            "yield": {"unit": "fraction", "value_basis": "yield basis", "better_when": "higher"},
        },
        "source_guides": [{"source_id": "wafer_parameter", "question_type": "numeric_yield_fail",
                           "guidance": "Use wafer_parameter."}],
        "comments": [],
    }

YIELD_GUIDE = (
    "For yield (수율), use parameter PT1H unless a process is specified; then use the "
    "yield-kind parameter for that process."
)


def test_seed_plan_derives_item_bin_yield_and_pt1c():
    from seed_param_meta import build_seed_plan

    catalog = [
        ("P", "PT1H", "o", "ITEM_A", 1),
        ("P", "PT1H", "o", "PT1H", 2),
        ("P", "PT1C", "o", "ITEM_C", 1),
        ("P", "PT1C", "o", "PT1C", 2),
    ]
    symbols = [("P", "PT1H", "DDA(d)", "^..$", None),
               ("P", "PT1C", "BIN_C", "^A.$", None)]

    plan = build_seed_plan(catalog, symbols, SEED_DATA)
    by_key = {(r["lot_cd"], r["process"], r["param_nm"]): r for r in plan["param_rows"]}

    assert by_key[("P", "PT1H", "ITEM_A")]["param_kind"] == "item"
    assert by_key[("P", "PT1H", "PT1H")]["param_kind"] == "yield"
    assert by_key[("P", "PT1C", "PT1C")]["param_kind"] == "yield"
    assert by_key[("P", "PT1H", "DDA(d)")]["param_kind"] == "bin"
    assert by_key[("P", "PT1C", "BIN_C")]["unit"] == "fraction"
    assert by_key[("P", "PT1C", "ITEM_C")]["unit"] == "unspecified"
    guides = {(row["source_id"], row["question_type"]): row["guidance"]
              for row in plan["source_guides"]}
    assert guides[("wafer_parameter", "yield_parameter_selection")] == YIELD_GUIDE


def test_description_only_plan_reports_keyed_deltas_and_preserves_other_metadata():
    from seed_param_meta import apply_description_plan, build_description_plan, build_seed_plan

    plan = build_seed_plan([("P", "PT1H", "o", "PT1H", 1)], [], SEED_DATA)
    tables = Cursor([(("TABLE_NAME",), [("DF_PARAM_META",), ("DF_SOURCE_GUIDE",)])])
    params = Cursor([(("SOURCE_ID", "LOT_CD", "PROCESS", "PARAM_NM", "VALUE_BASIS"), [
        ("wafer_parameter", "P", "PT1H", "PT1H", "old basis")])])
    guides = Cursor([(("SOURCE_ID", "QUESTION_TYPE", "GUIDANCE"), [])])
    description_plan = build_description_plan(Connection([tables, params, guides]), plan)

    assert description_plan["param_rows"][0]["before"] == "old basis"
    assert description_plan["param_rows"][0]["after"] == SEED_DATA["kinds"]["yield"]["value_basis"]
    assert any(row["question_type"] == "yield_parameter_selection" and row["before"] is None
               and row["after"] == YIELD_GUIDE for row in description_plan["source_guides"])

    write = Cursor()
    connection = Connection([write])
    apply_description_plan(connection, description_plan)
    sql = "\n".join(call[0].upper() for call in write.calls)
    assert "CREATE " not in sql and "COMMENT " not in sql and "DELETE " not in sql
    assert "PARAM_KIND" not in sql and "UNIT" not in sql and "BETTER_WHEN" not in sql
    assert "VALUE_BASIS" in sql and "GUIDANCE" in sql
    assert connection.commits == 1

    tables = Cursor([(("TABLE_NAME",), [("DF_PARAM_META",), ("DF_SOURCE_GUIDE",)])])
    params = Cursor([(("SOURCE_ID", "LOT_CD", "PROCESS", "PARAM_NM", "VALUE_BASIS"), [
        ("wafer_parameter", "P", "PT1H", "PT1H", SEED_DATA["kinds"]["yield"]["value_basis"])])])
    existing_guides = [(row["source_id"], row["question_type"], row["guidance"])
                       for row in plan["source_guides"]]
    guides = Cursor([(("SOURCE_ID", "QUESTION_TYPE", "GUIDANCE"), existing_guides)])
    assert build_description_plan(Connection([tables, params, guides]), plan) == {
        "param_rows": [], "source_guides": []}


def test_description_only_requires_existing_metadata_tables():
    from seed_param_meta import build_description_plan

    with pytest.raises(ValueError, match="existing metadata tables"):
        build_description_plan(Connection([Cursor([(("TABLE_NAME",), [])])]), {
            "param_rows": [], "source_guides": []})


def test_seed_plan_rejects_cross_kind_conflict():
    from seed_param_meta import build_seed_plan

    catalog = [("P", "PT1H", "o", "DDA(d)", 1)]
    symbols = [("P", "PT1H", "DDA(d)", "^..$", None)]
    with pytest.raises(ValueError, match="multiple parameter kinds"):
        build_seed_plan(catalog, symbols, SEED_DATA)


class Cursor:
    def __init__(self, batches=()):
        self.batches = iter(batches)
        self.calls = []
        self.description = []
        self.rows = []
        self.closed = False

    def execute(self, sql, binds=None):
        self.calls.append((sql, dict(binds or {})))
        try:
            columns, self.rows = next(self.batches)
        except StopIteration:
            self.rows = []
            return
        self.description = [(name,) for name in columns]

    def executemany(self, sql, rows):
        self.calls.append((sql, list(rows)))

    def fetchall(self):
        return list(self.rows)

    def fetchmany(self, count):
        result, self.rows = self.rows[:count], self.rows[count:]
        return result

    def __iter__(self):
        return iter(self.rows)

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, cursors):
        self.cursors = iter(cursors)
        self.commits = 0

    def cursor(self):
        return next(self.cursors)

    def commit(self):
        self.commits += 1


def test_apply_seed_plan_uses_bound_merge_and_is_idempotent_shape():
    from seed_param_meta import apply_seed_plan, build_seed_plan

    plan = build_seed_plan([("P", "PT1H", "o", "ITEM", 1)], [], SEED_DATA)
    table_cursor = Cursor([(("TABLE_NAME",), [("DF_PARAM_META",), ("DF_SOURCE_GUIDE",)])])
    write_cursor = Cursor()
    connection = Connection([table_cursor, write_cursor])

    apply_seed_plan(connection, plan)

    merge_calls = [(sql, rows) for sql, rows in write_cursor.calls if sql.lstrip().upper().startswith("MERGE")]
    assert len(merge_calls) == 2
    assert all(":" in sql and rows for sql, rows in merge_calls)
    deletes = [(sql, binds) for sql, binds in write_cursor.calls if sql.lstrip().upper().startswith("DELETE")]
    assert len(deletes) == 1
    assert "SOURCE_ID=:source_id" in deletes[0][0]
    assert deletes[0][1] == plan["discarded_rows"]
    assert connection.commits == 1


def test_match_definition_process_exact_prefix_and_ambiguity():
    from harness.wafer_set_param_meta import match_definition_process

    assert match_definition_process("PT1H", ["PT1", "PT1H"]) == "PT1H"
    assert match_definition_process("PT1H TEST", ["PT1H"]) == "PT1H"
    assert match_definition_process("PT1H_TEST", ["PT1H"]) is None
    with pytest.raises(ValueError, match="ambiguous"):
        match_definition_process("A B C", ["A", "A B"])


def test_read_metadata_missing_tables_returns_explicit_defaults_and_bound_sql():
    from harness.wafer_set_param_meta import read_param_metadata, resolve_param_meta

    cursor = Cursor([(("TABLE_NAME",), [])])
    snapshot = read_param_metadata(Connection([cursor]), lot_cd=["P' OR 1=1"],
                                      source_ids=["wafer_parameter"])
    resolved = resolve_param_meta(snapshot, source_id="wafer_parameter", lot_cd="P",
                                  process="PT1H TEST", parameter="PT1H")

    assert resolved["unit"] == "unspecified"
    assert resolved["param_kind"] == "unspecified"
    assert resolved["better_when"] == "unknown"
    assert {item["code"] for item in snapshot["limitations"]} == {
        "PARAM_METADATA_TABLE_MISSING", "SOURCE_GUIDE_TABLE_MISSING"}
    assert cursor.calls[0][1] == {"table_0": "DF_PARAM_META", "table_1": "DF_SOURCE_GUIDE"}


def test_read_metadata_is_clob_safe_and_resolves_process_prefix():
    from harness.wafer_set_param_meta import read_param_metadata, resolve_param_meta

    class Lob:
        def read(self):
            return "stored basis"

    table_cursor = Cursor([(("TABLE_NAME",), [("DF_PARAM_META",), ("DF_SOURCE_GUIDE",)])])
    meta_cursor = Cursor([(("SOURCE_ID", "LOT_CD", "PROCESS", "PARAM_NM", "PARAM_KIND", "UNIT",
                            "VALUE_BASIS", "BETTER_WHEN"),
                           [("wafer_parameter", "P", "PT1C", "PT1C", "yield", "fraction",
                             Lob(), "higher")])])
    guide_cursor = Cursor([(("SOURCE_ID", "QUESTION_TYPE", "GUIDANCE"),
                            [("wafer_parameter", "numeric_yield_fail", Lob())])])
    snapshot = read_param_metadata(Connection([table_cursor, meta_cursor, guide_cursor]),
                                      lot_cd=["P"], source_ids=["wafer_parameter"])
    resolved = resolve_param_meta(snapshot, source_id="wafer_parameter", lot_cd="P",
                                  process="PT1C TEST", parameter="PT1C")

    assert resolved["value_basis"] == "stored basis"
    assert snapshot["question_guides"][0]["guidance"] == "stored basis"
    assert all("P" not in sql or ":" in sql for sql, _ in meta_cursor.calls)


@pytest.mark.parametrize("process, expected", [(["PT1H"], ["PT1H"]), (None, ["PT1H", "PT1C"]),
                                                 ([], ["PT1H", "PT1C"])])
def test_read_metadata_process_filter_is_bound_and_optional(process, expected):
    from harness.wafer_set_param_meta import read_param_metadata

    all_rows = [
        ("wafer_parameter", "P", "PT1H", "A", "item", "volt", "a", "neutral"),
        ("wafer_parameter", "P", "PT1C", "B", "item", "amp", "b", "neutral"),
    ]
    returned = all_rows if not process else [row for row in all_rows if row[2] in process]
    tables = Cursor([(("TABLE_NAME",), [("DF_PARAM_META",)])])
    metadata = Cursor([(("SOURCE_ID", "LOT_CD", "PROCESS", "PARAM_NM", "PARAM_KIND", "UNIT",
                         "VALUE_BASIS", "BETTER_WHEN"), returned)])
    snapshot = read_param_metadata(Connection([tables, metadata]), lot_cd=["P"],
                                   source_ids=["wafer_parameter"], process=process)

    assert [row["process"] for row in snapshot["param_metadata"]] == expected
    sql, binds = metadata.calls[0]
    if process:
        assert "PROCESS IN" in sql and set(process) <= set(binds.values())
    else:
        assert "PROCESS IN" not in sql


def test_read_metadata_rejects_duplicate_source_qualified_rows():
    from harness.wafer_set_param_meta import read_param_metadata

    row = ("wafer_parameter", "P", "PT1H", "A", "item", "volt", "a", "neutral")
    tables = Cursor([(("TABLE_NAME",), [("DF_PARAM_META",)])])
    metadata = Cursor([(("SOURCE_ID", "LOT_CD", "PROCESS", "PARAM_NM", "PARAM_KIND", "UNIT",
                         "VALUE_BASIS", "BETTER_WHEN"), [row, row])])
    with pytest.raises(ValueError, match="Duplicate parameter metadata"):
        read_param_metadata(Connection([tables, metadata]), lot_cd=["P"],
                            source_ids=["wafer_parameter"], process=["PT1H"])


def test_source_metric_uses_per_row_unit_direction_and_metadata():
    from harness.tools.wafer_set_metrics import MetricSpec, _source_metric

    row = {"source_id": "wafer_parameter", "parameter": "DDA(d)", "value": 0.2,
           "unit": "fraction", "better_when": "lower", "param_kind": "bin",
           "value_basis": "regex fraction"}
    spec = MetricSpec(metric_id="m", mode="source_value", quantity="value", value_column="value")
    result = _source_metric(row, spec, {"value": "per_row_unit"})

    assert result["unit"] == "fraction" and result["better_when"] == "lower"
    assert result["parameter"] == "DDA(d)" and result["param_kind"] == "bin"
    assert result["numerator"] is None and result["denominator"] is None


def test_metadata_snapshot_hash_changes_with_database_meaning():
    from harness.wafer_set_param_meta import read_param_metadata

    def snapshot(basis):
        tables = Cursor([(("TABLE_NAME",), [("DF_PARAM_META",), ("DF_SOURCE_GUIDE",)])])
        metadata = Cursor([(("SOURCE_ID", "LOT_CD", "PROCESS", "PARAM_NM", "PARAM_KIND", "UNIT",
                             "VALUE_BASIS", "BETTER_WHEN"),
                            [("wafer_parameter", "P", "PT1H", "PT1H", "yield", "fraction",
                              basis, "higher")])])
        guides = Cursor([(("SOURCE_ID", "QUESTION_TYPE", "GUIDANCE"), [])])
        return read_param_metadata(Connection([tables, metadata, guides]), lot_cd=["P"],
                                   source_ids=["wafer_parameter"])

    assert snapshot("first")["definition_version"] != snapshot("second")["definition_version"]


def test_latest_wafer_parameter_rows_stay_separate_by_process(monkeypatch):
    from harness import wafer_set_param_meta, wafer_set_sources

    names = ("LOT_CD", "WEEK", "YEAR", "MONTH", "LOT_ID", "SOURCE_WF_ID", "WF_ID",
             "PARAMETER", "VALUE", "PROCESS", "MEASUREMENT_START")
    rows = [("P", "2026-01", 2026, 1, "L", "1", "1", "PT1H", 0.9, "PT1H TEST", "2026-01-01"),
            ("P", "2026-01", 2026, 1, "L", "1", "1", "PT1C", 0.8, "PT1C TEST", "2026-01-01")]

    class DataCursor:
        arraysize = 1000
        description = [(name,) for name in names]
        def execute(self, sql, binds):
            self.offset = 0
        def fetchmany(self, count):
            result = rows[self.offset:self.offset + count]
            self.offset += len(result)
            return result
        def close(self):
            pass

    connection = SimpleNamespace(cursor=lambda: DataCursor(), close=lambda: None, call_timeout=15_000)
    monkeypatch.setattr(wafer_set_sources, "_connection", lambda: connection)
    monkeypatch.setattr(wafer_set_param_meta, "read_param_metadata", lambda *a, **k: {
        "param_metadata": [], "question_guides": [], "metadata_availability": {},
        "limitations": [], "definition_version": "v"})

    result = wafer_set_sources.fetch_source("wafer_parameter", None, [], "latest_per_entity", 10, 10_000)
    assert {row["process"] for row in result.rows} == {"PT1H TEST", "PT1C TEST"}


def test_discovery_facts_metrics_aggregate_pipeline_uses_table_metadata_without_map(monkeypatch):
    from harness.tools import wafer_set_aggregate as aggregate_tool
    from harness.tools import wafer_set_discovery as discovery_tool
    from harness.tools import wafer_set_facts as facts_tool
    from harness.tools import wafer_set_metrics as metrics_tool
    from harness.types import ResultTable
    from harness.wafer_set_sources import SourceBatch

    async def direct(function, *args, **kwargs):
        return function(*args, **kwargs)

    definitions = [
        {"definition_id": "source_value:wafer_parameter:y", "source_id": "wafer_parameter",
         "lot_cd": "P", "process": "PT1H", "parameter": "PT1H", "mode": "source_value",
         "regex_pattern": None, "param_kind": "yield", "unit": "fraction",
         "value_basis": "pass fraction", "better_when": "higher", "included": True,
         "display_order": 1, "definition_version": "y"},
        {"definition_id": "source_value:wafer_parameter:b", "source_id": "wafer_parameter",
         "lot_cd": "P", "process": "PT1H", "parameter": "DDA(d)", "mode": "source_value",
         "regex_pattern": None, "param_kind": "bin", "unit": "fraction",
         "value_basis": "regex fraction", "better_when": "lower", "included": True,
         "display_order": 2, "definition_version": "b"},
    ]
    monkeypatch.setattr(discovery_tool, "run_blocking", direct)
    monkeypatch.setattr(discovery_tool, "inspect_catalog", lambda source_ids: {
        "wafer_parameter": {"available": True, "columns": {
            name.upper(): {"data_type": "VARCHAR2", "nullable": True}
            for name in ("lot_cd", "process", "parameter", "value")}}})
    monkeypatch.setattr(discovery_tool, "load_definition_snapshot",
        lambda lot_cd, process, refresh=False, source_ids=None: {
            "metric_definitions": definitions, "process_links": [],
            "question_guides": [{"source_id": "wafer_parameter",
                "question_type": "numeric_yield_fail", "guidance": "Use stored values."}],
            "metadata_availability": {"param_metadata": True, "source_guides": True},
            "metadata_limitations": [], "definition_version": "snapshot"})
    discovered = asyncio.run(discovery_tool.describe_sources(discovery_tool.DescribeSourcesInput(
        source_ids=["wafer_parameter"], lot_cd=["P"], process=["PT1H TEST"],
        include_definitions=False), SimpleNamespace()))
    assert discovered.tables[3].rows == []
    assert discovered.tables[5].rows[0]["question_type"] == "numeric_yield_fail"
    value_column = next(row for row in discovered.tables[1].rows if row["column"] == "value")
    assert value_column["unit"] == "fraction"

    fact_rows = [
        {"source_id": "wafer_parameter", "fact_id": "f1", "source_row_hash": "h1",
         "lot_cd": "P", "process": "PT1H TEST", "lot_id": "L", "wf_id": 1,
         "parameter": "PT1H", "value": 0.9, "unit": "fraction", "better_when": "higher",
         "param_kind": "yield", "value_basis": "pass fraction", "definition_version": "y"},
        {"source_id": "wafer_parameter", "fact_id": "f2", "source_row_hash": "h2",
         "lot_cd": "P", "process": "PT1H TEST", "lot_id": "L", "wf_id": 1,
         "parameter": "DDA(d)", "value": 0.1, "unit": "fraction", "better_when": "lower",
         "param_kind": "bin", "value_basis": "regex fraction", "definition_version": "b"},
    ]
    monkeypatch.setattr(facts_tool, "run_blocking", direct)
    monkeypatch.setattr(facts_tool, "fetch_source", lambda *args: SourceBatch(
        fact_rows, 2, 0, 0, 0, 100, True, None, [], "snapshot"))
    monkeypatch.setattr(facts_tool, "fetch_map_range", lambda *a, **k: pytest.fail("MAP reader called"))
    monkeypatch.setattr(facts_tool, "fetch_maps_by_refs", lambda *a, **k: pytest.fail("MAP reader called"))
    read = asyncio.run(facts_tool.read_facts(facts_tool.ReadFactsInput(
        source_id="wafer_parameter", query={"lot_cd": ["P"]}), SimpleNamespace()))
    assert read.tables[0].units["value"] == "per_row_unit"

    class Store:
        def __init__(self):
            self.values = {}
        async def observation(self, principal, session, result_id):
            return self.values[result_id]
        async def rows(self, principal, session, result_id, table_id=None):
            observation = self.values[result_id]
            return next(table.rows for table in observation.tables if table.table_id == table_id)
        def add(self, result_id, result):
            self.values[result_id] = SimpleNamespace(result_id=result_id, tables=result.tables,
                status=result.status, scope=result.scope, provenance={"data_origin": "fixture"})

    store = Store()
    store.add("facts", read)
    ctx = SimpleNamespace(store=store,
        run={"principal_id": "p", "session_id": "s", "run_id": "r", "epoch": 1})
    computed = asyncio.run(metrics_tool.compute_metrics(metrics_tool.ComputeMetricsInput(
        source={"result_id": "facts", "table_id": "facts"}, metrics=[{
            "metric_id": "stored_value", "mode": "source_value", "quantity": "value",
            "value_column": "value"}]), ctx))
    assert {(row["parameter"], row["better_when"]) for row in computed.tables[0].rows} == {
        ("PT1H", "higher"), ("DDA(d)", "lower")}

    store.add("metrics", computed)
    monkeypatch.setattr(aggregate_tool, "run_blocking", direct)
    aggregated = asyncio.run(aggregate_tool.aggregate_metrics(aggregate_tool.AggregateMetricsInput(
        sources=[{"result_id": "metrics", "table_id": "metrics"}], group_by=["parameter"],
        operation="mean", cohort_overlap="error"), ctx))
    assert {row["parameter"] for row in aggregated.tables[0].rows} == {"PT1H", "DDA(d)"}


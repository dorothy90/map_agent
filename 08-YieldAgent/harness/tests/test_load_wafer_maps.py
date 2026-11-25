from contextlib import nullcontext
import json

import pytest


def map_row(lot_id="LOT1", wf_id=1, payload=None, **updates):
    row = {
        "lot_id": lot_id,
        "wf_id": wf_id,
        "lot_cd": "4SS",
        "oper": "PT1H TEST",
        "start_tm": "2026-10-01T01:00:00",
        "end_tm": "2026-10-01T02:00:00",
        "map_val_json": payload or json.dumps({"MAP": ["-2,7,A,B", "3,-5,?,?"]}),
    }
    row.update(updates)
    return row


def test_normalize_map_rows_preserves_asymmetric_negative_coordinates_and_symbols():
    from harness.tools.load_wafer_maps import normalize_map_rows

    result = normalize_map_rows(
        [map_row()],
        symbol_patterns={("4SS", "PT1H TEST"): {"edge_fail": "AB"}},
        max_measurements=10,
        max_dies=10,
    )

    assert result.measurement_count == 1
    assert result.die_rows == [
        {
            "measurement_id": result.die_rows[0]["measurement_id"],
            "lot_id": "LOT1",
            "wf_id": 1,
            "lot_cd": "4SS",
            "oper": "PT1H TEST",
            "start_tm": "2026-10-01T01:00:00",
            "end_tm": "2026-10-01T02:00:00",
            "row": -2,
            "col": 7,
            "left_bin": "A",
            "right_bin": "B",
            "bin": "AB",
            "fail_bin_name": "edge_fail",
        },
        {
            "measurement_id": result.die_rows[0]["measurement_id"],
            "lot_id": "LOT1",
            "wf_id": 1,
            "lot_cd": "4SS",
            "oper": "PT1H TEST",
            "start_tm": "2026-10-01T01:00:00",
            "end_tm": "2026-10-01T02:00:00",
            "row": 3,
            "col": -5,
            "left_bin": "?",
            "right_bin": "?",
            "bin": "??",
            "fail_bin_name": None,
        },
    ]
    assert result.facts["row_range"] == [-2, 3]
    assert result.facts["col_range"] == [-5, 7]
    assert result.facts["bin_value_counts"] == {"??": 1, "AB": 1}


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not json", "Malformed MAP_VAL_JSON"),
        (json.dumps({"OTHER": []}), "MAP array"),
        (json.dumps({"MAP": ["x,1,A,B"]}), "coordinates"),
    ],
)
def test_normalize_map_rows_rejects_malformed_json(payload, message):
    from harness.tools.load_wafer_maps import normalize_map_rows

    with pytest.raises(ValueError, match=message):
        normalize_map_rows([map_row(payload=payload)], max_measurements=10, max_dies=10)


def test_normalize_map_rows_rejects_duplicate_coordinates_within_measurement():
    from harness.tools.load_wafer_maps import normalize_map_rows

    payload = json.dumps({"MAP": ["1,2,A,B", "1,2,C,D"]})
    with pytest.raises(ValueError, match="duplicate coordinate"):
        normalize_map_rows([map_row(payload=payload)], max_measurements=10, max_dies=10)


def test_normalize_map_rows_deduplicates_identical_measurements():
    from harness.tools.load_wafer_maps import normalize_map_rows

    row = map_row()
    result = normalize_map_rows([row, dict(row)], max_measurements=10, max_dies=10)

    assert result.measurement_count == 1
    assert len(result.die_rows) == 2
    assert result.facts["duplicate_measurement_count"] == 1


def test_normalize_map_rows_limits_complete_measurements_and_reports_next_offset():
    from harness.tools.load_wafer_maps import normalize_map_rows

    rows = [
        map_row(wf_id=1, payload=json.dumps({"MAP": ["1,2,A,B", "2,3,C,D"]})),
        map_row(wf_id=2, payload=json.dumps({"MAP": ["4,5,E,F", "6,7,G,H"]})),
        map_row(wf_id=3, payload=json.dumps({"MAP": ["8,9,I,J"]})),
    ]

    by_measurement = normalize_map_rows(rows, max_measurements=1, max_dies=10, measurement_offset=4)
    assert by_measurement.measurement_count == 1
    assert by_measurement.facts["truncated"] is True
    assert by_measurement.facts["truncation_reason"] == "max_measurements"
    assert by_measurement.facts["next_measurement_offset"] == 5

    by_dies = normalize_map_rows(rows, max_measurements=3, max_dies=3, measurement_offset=4)
    assert by_dies.measurement_count == 1
    assert len(by_dies.die_rows) == 2
    assert by_dies.facts["truncation_reason"] == "max_dies"
    assert by_dies.facts["next_measurement_offset"] == 5


def test_measurement_limit_does_not_parse_rows_beyond_the_page():
    from harness.tools.load_wafer_maps import normalize_map_rows

    result = normalize_map_rows(
        [map_row(), map_row(wf_id=2, payload="not json")],
        max_measurements=1,
        max_dies=10,
    )

    assert result.measurement_count == 1
    assert result.facts["truncation_reason"] == "max_measurements"


def test_load_wafer_maps_returns_only_fact_summary_and_stored_die_table(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import harness.tools.load_wafer_maps as tool

    rows = [map_row()]
    monkeypatch.setattr(
        tool,
        "_open_map_source",
        lambda args: nullcontext((iter(rows), {}, {"lot_ids": ["LOT1"], "wf_ids": [1]})),
    )
    async def immediate(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(tool, "run_blocking", immediate)
    args = tool.LoadWaferMapsInput(lot_ids=["LOT1", "MISSING"], wf_ids=[1, 9], oper="PT1H TEST")

    result = asyncio.run(tool.load_wafer_maps(args, SimpleNamespace()))

    assert result.status == "partial"
    assert result.tables[0].table_id == "wafer_map_dies"
    assert result.tables[0].rows[0]["row"] == -2
    assert "MAP_VAL_JSON" not in result.summary
    facts = result.scope["wafer_maps"]
    assert facts["wafer_count"] == 1
    assert facts["measurement_count"] == 1
    assert facts["die_count"] == 2
    assert facts["lots_found"] == ["LOT1"]
    assert facts["wafers_found"] == [1]
    assert facts["missing_requests"] == {"lot_ids": ["MISSING"], "wf_ids": [9]}
    evidence = result.scope["map_measurements"]
    assert evidence["measurement_count"] == 1
    row, = evidence["preview_rows"]
    assert (row["lot_id"], row["wf_id"], row["oper"], row["measurement_id"]) == (
        "LOT1", 1, "PT1H TEST", result.tables[0].rows[0]["measurement_id"])
    assert (row["pass_die_count"], row["die_count"], row["map_pass_fraction"]) == (1, 2, .5)
    assert evidence["wafer_yield_lookup"]["source"] == "DF_WF_ALL_PRB_V2"
    assert "value" not in evidence["wafer_yield_lookup"]


def test_measurement_preview_bounds_observed_measurements_and_preserves_all_dies():
    from harness.tools.load_wafer_maps import map_measurements, normalize_map_rows

    measurements = [map_row(wf_id=wf) for wf in range(12)]
    measurements.append(map_row(wf_id=20, payload='{"MAP": []}'))
    result = normalize_map_rows(measurements, max_dies=50, max_measurements=20)
    evidence = map_measurements({None: result.die_rows})
    assert result.measurement_count == 13 and len(result.die_rows) == 24
    assert evidence["grain"] == "MAP measurement with observed dies"
    assert evidence["measurement_count"] == 12 and len(evidence["preview_rows"]) == 10
    assert evidence["truncated"] and all(row["map_pass_fraction"] == .5 for row in evidence["preview_rows"])


def test_database_loader_materializes_map_clob_before_reusing_cursor(monkeypatch):
    import harness.tools.load_wafer_maps as tool

    class Lob:
        def __init__(self, cursor):
            self.cursor = cursor
            self.generation = cursor.generation

        def read(self):
            if self.cursor.generation != self.generation:
                raise RuntimeError("LOB locator is no longer readable")
            return json.dumps({"MAP": ["1,2,A,B"]})

    class Cursor:
        def __init__(self):
            self.generation = 0
            self.description = []
            self.rows = []

        def execute(self, sql, binds=None):
            self.generation += 1
            if "MAP_VAL_JSON" in sql:
                self.description = [(name,) for name in (
                    "LOT_ID", "WF_ID", "LOT_CD", "OPER", "START_TM", "END_TM", "MAP_VAL_JSON",
                )]
                self.rows = [("LOT1", 1, "4SS", "PT1H TEST", "s", "e", Lob(self))]
            elif "SELECT DISTINCT" in sql:
                self.rows = [("LOT1", 1, "4SS", "PT1H TEST")]
            else:
                self.rows = []

        def __iter__(self):
            return iter(self.rows)

        def fetchall(self):
            return self.rows

        def close(self):
            self.generation += 1

    class Connection:
        def __init__(self):
            self.cursor_value = Cursor()

        def cursor(self):
            return self.cursor_value

        def close(self):
            pass

    connection = Connection()
    monkeypatch.setattr(tool, "_connection", lambda: connection)
    args = tool.LoadWaferMapsInput(lot_ids=["LOT1"])

    with tool._open_map_source(args) as (source, patterns, found):
        rows = list(source)
    normalized = tool.normalize_map_rows(rows, symbol_patterns=patterns, max_measurements=10, max_dies=10)

    assert found == {"lot_ids": ["LOT1"], "wf_ids": [1]}
    assert normalized.die_rows[0]["bin"] == "AB"


def test_tool_description_separates_selection_filters_from_aggregation_groups():
    from harness.tools.load_wafer_maps import register
    from harness.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register(registry)
    description = registry.tools["load_wafer_maps"].description

    assert "선택 필터" in description
    assert "집계 그룹" in description



def test_memory_selection_filters_the_lot_and_wafer_as_a_pair():
    import sqlite3
    import harness.tools.load_wafer_maps as tool

    groups = [dict(label="one", lot_id="A", wf_ids=[1, 3]), dict(label="two", lot_id="B", wf_ids=[2])]
    args = tool.LoadWaferMapsInput(wafer_groups=groups, oper="op")
    where, binds = tool._filters(args)
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE map(lot_id, wf_id, oper_det_desc)")
        db.executemany("INSERT INTO map VALUES (?, ?, ?)",
                       [(lot, wf, oper) for lot in "AB" for wf in (1, 2, 3) for oper in ("op", "other")])
        assert db.execute("SELECT lot_id,wf_id FROM map WHERE " + where, binds).fetchall() == [
            ("A", 1), ("A", 3), ("B", 2)]


def test_remembered_raw_maps_render_original_coordinates_and_report_missing_pairs(monkeypatch):
    import harness.tools.load_wafer_maps as tool
    from harness.native_verification import validate_wafer_map_contract
    from plotly.basedatatypes import BaseFigure

    groups = [dict(label="one", lot_id="A", wf_ids=[1, 2]), dict(label="two", lot_id="B", wf_ids=[2])]
    rows = [map_row("A", 1), map_row("B", 2)]
    found = dict(lot_ids=["A", "B"], wf_ids=[1, 2])
    monkeypatch.setattr(tool, "_open_map_source", lambda args: nullcontext((iter(rows), {}, found)))
    figures = []
    def capture(figure, **kwargs):
        figures.append(figure)
        return "<html></html>"
    monkeypatch.setattr(BaseFigure, "to_html", capture)
    args = tool.LoadWaferMapsInput(wafer_groups=groups)
    result = tool._load(args, "result")
    assert result.status == "partial"
    assert result.scope["wafer_maps"]["missing_requests"]["lot_wafers"] == [["A", 2]]
    assert result.scope["filters"]["wafer_groups"] == groups
    assert len(result.artifacts) == 1
    assert [(t.meta["label"], t.meta["lot_id"], t.meta["wf_id"]) for t in figures[0].data] == [
        ("one", "A", 1), ("two", "B", 2)]
    for trace in figures[0].data:
        actual = {(row, col): cell for row, line in zip(trace.y, trace.customdata)
                  for col, cell in zip(trace.x, line) if cell is not None}
        assert actual == {(-2, 7): ["A", "B"], (3, -5): ["?", "?"]}
    tables = {table.table_id: table.rows for table in result.tables}
    assert validate_wafer_map_contract(tables, {"result": tables["wafer_map_dies"]})["verdict"] == "pass"


def test_default_raw_lookup_keeps_reading_the_same_selection_past_first_page(monkeypatch, tmp_path):
    import asyncio
    import sqlite3
    from types import SimpleNamespace

    import harness.tools.load_wafer_maps as maps

    class MapCursor(sqlite3.Cursor):
        def execute(self, sql, binds=()):
            if sql.upper().startswith("SET TRANSACTION READ ONLY"):
                return super().execute("BEGIN")
            return super().execute(sql, binds)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    class MapConnection(sqlite3.Connection):
        def cursor(self):
            return super().cursor(MapCursor)

    db_path = tmp_path / "maps.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE MAP (lot_id, wf_id, lot_cd, oper_det_desc, start_tm, end_tm, map_val_json)")
        db.execute("CREATE TABLE DF_SYMBOL_BIN (lot_cd, process, param_nm, regex_pattern)")
        values = [("A", i, "prod", "op", "s", "e", '{"MAP":["1,2,?,?"]}') for i in range(1, 204)]
        db.executemany("INSERT INTO MAP VALUES (?, ?, ?, ?, ?, ?, ?)", [
            *values, values[199], (*values[199][:4], "z-retest", *values[199][5:]),
            ("other", 1, "prod", "op", "s", "e", '{"MAP":["1,2,A,B"]}'),
            ("A", 9, "prod", "other", "s", "e", '{"MAP":["1,2,A,B"]}'),
        ])

    async def local(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(maps, "_connection", lambda: sqlite3.connect(db_path, factory=MapConnection))
    monkeypatch.setattr(maps, "run_blocking", local)
    args = maps.LoadWaferMapsInput(lot_ids=["A"], oper="op")
    result = asyncio.run(maps.load_wafer_maps(args, SimpleNamespace()))
    table = next(t for t in result.tables if t.table_id == "wafer_map_dies")
    try:
        assert result.status == "success" and table.complete
        rows = [json.loads(line) for line in table.source] if hasattr(table, "source") else table.rows
        assert {(r["lot_id"], r["wf_id"], r["oper"]) for r in rows} == {("A", n, "op") for n in range(1, 204)}
        assert len({r["measurement_id"] for r in rows}) == len(rows) == 204
        facts = result.scope["wafer_maps"]
        assert (facts["wafer_count"], facts["measurement_count"], facts["duplicate_measurement_count"]) == (203, 204, 1)
        assert not facts["truncated"] and facts["next_measurement_offset"] is None
    finally:
        if hasattr(table, "source"):
            table.source.close()

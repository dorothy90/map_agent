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
        "_load_rows_and_patterns",
        lambda args: (rows, {}, False, {"lot_ids": ["LOT1"], "wf_ids": [1]}),
    )
    async def immediate(function, *args):
        return function(*args)
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

        def execute(self, sql, binds):
            self.generation += 1
            if "ROWNUM AS RN" in sql:
                self.description = [(name,) for name in (
                    "LOT_ID", "WF_ID", "LOT_CD", "OPER", "START_TM", "END_TM", "MAP_VAL_JSON",
                )]
                self.rows = [("LOT1", 1, "4SS", "PT1H TEST", "s", "e", Lob(self))]
            elif "SELECT DISTINCT" in sql:
                self.rows = [("LOT1", 1)]
            else:
                self.rows = []

        def fetchmany(self, _limit):
            return self.rows

        def fetchall(self):
            return self.rows

        def close(self):
            pass

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

    rows, patterns, has_more, found = tool._load_rows_and_patterns(args)
    normalized = tool.normalize_map_rows(rows, symbol_patterns=patterns, max_measurements=10, max_dies=10)

    assert has_more is False
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


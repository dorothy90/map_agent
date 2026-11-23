import asyncio
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from harness.tools import load_wafer_maps as maps


def test_map_selection_binds_product_test_and_half_open_measurement_window():
    args = maps.LoadWaferMapsInput(
        lot_ids=["shared-lot"], lot_cd="product-A", oper="test-B",
        start_time="2026-02-02", end_time="2026-03-01",
    )
    where, values = maps._filters(args)
    assert "LOT_CD = :lot_cd" in where and values["lot_cd"] == "product-A"
    assert "OPER_DET_DESC = :oper" in where and values["oper"] == "test-B"
    assert "END_TM >= :start_time" in where and values["start_time"] == datetime(2026, 2, 2)
    assert "END_TM < :end_time" in where and values["end_time"] == datetime(2026, 3, 1)


def test_recent_map_window_uses_product_and_stage_latest_measurement(monkeypatch):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [(datetime(2026, 1, 7),), (datetime(2026, 1, 12),), (None,)]
    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
    monkeypatch.setattr(maps, "_connection", lambda: connection)
    assert maps.recent_map_window("product-A", "test-A", 4) == (
        datetime(2025, 12, 15), datetime(2026, 1, 8),
    )
    assert maps.recent_map_window("product-B", "test-A", 4) == (
        datetime(2025, 12, 22), datetime(2026, 1, 13),
    )
    assert cursor.execute.call_count == 2
    for call, product in zip(cursor.execute.call_args_list, ["product-A", "product-B"]):
        sql, binds = call.args
        assert "TRUNC(MAX(END_TM))" in sql
        assert "WHERE LOT_CD = :lot_cd AND OPER_DET_DESC = :oper" in sql
        assert binds == {"lot_cd": product, "oper": "test-A"}


def test_unknown_measurement_reports_only_db_candidates_without_changing_requested_scope(monkeypatch):
    cursor = MagicMock()
    cursor.fetchone.return_value = (None,)
    cursor.fetchall.return_value = [("Measurement Z",), ("Stage Δ",)]
    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
    monkeypatch.setattr(maps, "_connection", lambda: connection)
    with pytest.raises(ValueError, match="No MAP measurement date") as error:
        maps.recent_map_window("product-B", "wrong name", 4)
    assert '["Measurement Z", "Stage Δ"]' in str(error.value)
    query, options_query = cursor.execute.call_args_list
    assert query.args[1] == {"lot_cd": "product-B", "oper": "wrong name"}
    sql, binds = options_query.args
    assert "DISTINCT OPER_DET_DESC FROM MAP WHERE LOT_CD = :lot_cd" in sql
    assert "FETCH FIRST 12 ROWS ONLY" in sql
    assert binds == {"lot_cd": "product-B"}


def test_explicit_and_unbounded_periods_need_no_oracle_anchor(monkeypatch):
    monkeypatch.setattr(maps, "_connection", lambda: pytest.fail("unexpected Oracle query"))
    monkeypatch.setattr(maps, "_load", lambda args, source_id: maps._filters(args))
    args = maps.LoadWaferMapsInput(lot_ids=["A"], all_time=True)
    where, _ = maps._query_maps(args, "source", "Asia/Seoul")
    assert "END_TM" not in where
    args = maps.LoadWaferMapsInput(lot_ids=["A"], start_time="2026-01-01T15:00:00Z")
    _, values = maps._query_maps(args, "source", "Asia/Seoul")
    assert values["start_time"] == datetime(2026, 1, 2)


def test_aggregate_population_uses_filters_without_expanding_lot_ids():
    from pydantic import ValidationError

    spec = {"group_by": "recipe", "group_oper": "INSPECTION"}
    args = maps.LoadWaferMapsInput(aggregate=spec, lot_cd="P", oper="MAP TEST", all_time=True)
    where, values = maps._filters(args)
    assert "LOT_ID IN" not in where
    assert "LOT_CD = :lot_cd" in where and "OPER_DET_DESC = :oper" in where
    assert values == {"lot_cd": "P", "oper": "MAP TEST"}
    for field, default in (("measurement_offset", 0), ("max_measurements", 200), ("max_dies", 500_000)):
        with pytest.raises(ValidationError, match=field):
            maps.LoadWaferMapsInput(aggregate=spec, **{field: default})
    with pytest.raises(ValidationError, match="lot_ids"):
        maps.LoadWaferMapsInput()


@pytest.mark.parametrize("filters", [
    {"lot_cd": "A", "oper": "STAGE"},
    {"oper": "STAGE", "recent_weeks": 4},
    {"lot_cd": "A", "recent_weeks": 4},
    {"lot_cd": "A", "oper": "STAGE", "recent_weeks": 4, "all_time": True},
    {"lot_cd": "A", "oper": "STAGE", "recent_weeks": 4, "end_time": "2026-01-01"},
    {"lot_cd": "A", "oper": "STAGE", "recent_weeks": 4, "time_column": "start_tm"},
])
def test_population_map_rejects_ambiguous_or_incomplete_filters(filters):
    with pytest.raises(ValueError):
        maps.LoadWaferMapsInput(aggregate={"group_by": "all"}, **filters)


def test_aggregate_dispatches_one_native_call_and_binds_only_its_requested_window(monkeypatch):
    from types import SimpleNamespace
    import uuid
    from harness.tools import map_aggregate

    args = maps.LoadWaferMapsInput(lot_cd="A", oper="STAGE", recent_weeks=4,
                                   aggregate={"group_by": "recipe", "group_oper": "INSPECTION"})
    calls = []

    def window(product, oper, weeks):
        assert (product, oper, weeks) == ("A", "STAGE", 4)
        return datetime(2025, 12, 15), datetime(2026, 1, 8)

    def aggregate(args, source_id):
        assert args.start_time == datetime(2025, 12, 15) and args.end_time == datetime(2026, 1, 8)
        assert args.lot_cd == "A" and args.oper == "STAGE" and args.recent_weeks == 4
        assert args.aggregate.group_oper == "INSPECTION"
        return maps.ToolResult(summary=source_id)

    async def execute(fn, *params):
        calls.append(fn)
        return fn(*params)

    monkeypatch.setattr(maps, "recent_map_window", window)
    monkeypatch.setattr(map_aggregate, "aggregate_wafer_maps", aggregate)
    monkeypatch.setattr(maps, "run_blocking", execute)
    ctx = SimpleNamespace(run={"run_id": "r"}, invocation_id="i")
    result = asyncio.run(maps.load_wafer_maps(args, ctx))
    assert calls == [maps._query_maps]
    assert result.summary == str(uuid.uuid5(uuid.NAMESPACE_URL, "r:i"))

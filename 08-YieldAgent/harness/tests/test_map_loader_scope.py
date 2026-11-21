import asyncio
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from harness.tools import load_wafer_maps as maps


def test_map_load_uses_bound_filter_values_and_returns_scope(monkeypatch):
    from harness.tools import map_scope

    bare = maps.LoadWaferMapsInput(lot_ids=["L1"], max_dies=50)
    confirmed = {"lot_cd": "A", "oper": "stage", "start_time": datetime(2026, 1, 1),
                 "end_time": datetime(2026, 2, 1)}
    scope = {"lot_cd": "A", "period": {"kind": "recent_weeks", "count": 4}}

    async def bind(args, ctx):
        assert args == bare
        return args.model_copy(update=confirmed), scope

    async def load(function, args):
        assert function is maps._load
        assert all(getattr(args, key) == value for key, value in confirmed.items())
        assert args.lot_ids == bare.lot_ids and args.max_dies == bare.max_dies
        return maps.ToolResult(scope={"filters": args.model_dump(mode="json")})

    monkeypatch.setattr(map_scope, "effective_map_args", bind)
    monkeypatch.setattr(maps, "run_blocking", load)
    result = asyncio.run(maps.load_wafer_maps(bare, object()))
    assert result.scope["map_scope"] == scope
    assert result.scope["filters"]["start_time"] == "2026-01-01T00:00:00"


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
    period = {"kind": "recent_weeks", "count": 4}
    assert maps.resolve_map_window("product-A", "test-A", period) == (
        datetime(2025, 12, 15), datetime(2026, 1, 8),
    )
    assert maps.resolve_map_window("product-B", "test-A", period) == (
        datetime(2025, 12, 22), datetime(2026, 1, 13),
    )
    with pytest.raises(ValueError, match="No MAP measurement date"):
        maps.resolve_map_window("empty-product", "test-A", period)
    for call, product in zip(cursor.execute.call_args_list, ["product-A", "product-B", "empty-product"]):
        sql, binds = call.args
        assert "TRUNC(MAX(END_TM))" in sql
        assert "WHERE LOT_CD = :lot_cd AND OPER_DET_DESC = :oper" in sql
        assert binds == {"lot_cd": product, "oper": "test-A"}


def test_explicit_map_periods_need_no_oracle_anchor(monkeypatch):
    monkeypatch.setattr(maps, "_connection", lambda: pytest.fail("unexpected Oracle query"))
    assert maps.resolve_map_window("A", "B", {"kind": "all"}) == (None, None)
    period = {"kind": "range", "start_time": "2026-11-16T09:00", "end_time": "2026-11-20T15:00"}
    assert maps.resolve_map_window("A", "B", period) == (datetime(2026, 11, 16, 9), datetime(2026, 11, 20, 15))
    with pytest.raises(ValueError, match="after"):
        maps.resolve_map_window("A", "B", {**period, "end_time": period["start_time"]})


def test_aggregate_population_uses_filters_without_expanding_lot_ids():
    from pydantic import ValidationError

    spec = {"group_by": "recipe", "group_oper": "INSPECTION"}
    args = maps.LoadWaferMapsInput(aggregate=spec, lot_cd="P", oper="MAP TEST")
    where, values = maps._filters(args)
    assert "LOT_ID IN" not in where
    assert "LOT_CD = :lot_cd" in where and "OPER_DET_DESC = :oper" in where
    assert values == {"lot_cd": "P", "oper": "MAP TEST"}
    for field, default in (("measurement_offset", 0), ("max_measurements", 200), ("max_dies", 500_000)):
        with pytest.raises(ValidationError, match=field):
            maps.LoadWaferMapsInput(aggregate=spec, **{field: default})
    with pytest.raises(ValidationError, match="lot_ids"):
        maps.LoadWaferMapsInput()


def test_aggregate_dispatches_once_with_confirmed_target_and_without_raw_paging(monkeypatch):
    from types import SimpleNamespace
    import uuid

    from harness.tools import map_aggregate, map_scope

    scope = {"kind": "targeted", "target_lot_ids": ["L1"], "target_wf_ids": [4]}
    args = maps.LoadWaferMapsInput(aggregate={"group_by": "all"})

    async def bind(args, ctx):
        return map_scope._apply(args, scope, "Asia/Seoul")

    calls = []

    async def execute(fn, args, source_id):
        calls.append((fn, args.lot_ids, args.wf_ids, source_id))
        return maps.ToolResult(summary="complete")

    monkeypatch.setattr(map_scope, "effective_map_args", bind)
    monkeypatch.setattr(maps, "run_blocking", execute)
    ctx = SimpleNamespace(run={"run_id": "r"}, invocation_id="i")
    result = asyncio.run(maps.load_wafer_maps(args, ctx))
    assert calls == [(map_aggregate.aggregate_wafer_maps, ["L1"], [4], str(uuid.uuid5(uuid.NAMESPACE_URL, "r:i")))]
    assert result.scope["map_scope"] == scope

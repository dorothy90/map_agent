import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from harness.tools import load_wafer_maps as maps


def test_thousand_explicit_lots_and_larger_stored_selection_use_bound_in_lists():
    lots = [f'L{i}' for i in range(1001)]
    args = maps.LoadWaferMapsInput(lot_ids=lots[:1000], oper="MAP TEST")
    predicate, binds = maps._filters(args)
    assert predicate.count("LOT_ID IN") == 1
    assert binds["lot_999"] == lots[999]
    with pytest.raises(ValidationError):
        maps.LoadWaferMapsInput(lot_ids=lots)
    predicate, binds = maps._filters(args.model_copy(update={"lot_ids": lots}))
    assert predicate.count("LOT_ID IN") == 2
    assert " OR LOT_ID IN (:lot_1000)" in predicate
    assert binds["lot_1000"] == lots[-1]


def test_history_selection_reads_full_owned_table_and_shows_maps_without_inheriting_dates(monkeypatch):
    owner = ('employee', 'session', 'history-id')
    table = SimpleNamespace(table_id='lot_summary', complete=True, columns=['lot_id'], total_rows=2)
    history = SimpleNamespace(tool_name='query_lot_history', status='success', tables=[table],
                              scope={'end_time': '2026-04-05'}, preview_rows=[{'lot_id': 'A'}])
    async def all_lots(*key, table_id):
        assert key == owner and table_id == 'lot_summary'
        for lot in ('A', 'B'):
            yield {'lot_id': lot}

    store = SimpleNamespace(observation=AsyncMock(return_value=history), iter_table_rows=all_lots)
    ctx = SimpleNamespace(run={'principal_id': owner[0], 'session_id': owner[1]}, store=store)
    async def immediate(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def query(args):
        where, binds = maps._filters(args)
        assert 'END_TM' not in where and 'START_TM' not in where
        assert binds == {'lot_0': 'A', 'lot_1': 'B', 'oper': 'MAP TEST'}
        rows = [dict(lot_id='B', wf_id=8, oper='MAP TEST', lot_cd='P', start_tm='2026-05-01',
                     end_tm='2026-05-02', map_val_json='{"MAP":["-2,7,A,B"]}')]
        return nullcontext((iter(rows), {}, {'lot_ids': ['B'], 'wf_ids': [8]}))

    monkeypatch.setattr(maps, '_open_map_source', query)
    monkeypatch.setattr(maps, 'run_blocking', immediate)
    result = asyncio.run(maps.load_wafer_maps(maps.LoadWaferMapsInput(lot_result_id=owner[-1], oper="MAP TEST"), ctx))
    store.observation.assert_awaited_once_with(*owner)
    assert result.source_result_ids == [owner[-1]]
    assert result.status == 'partial' and len(result.artifacts) == 1
    tables = {table.table_id: table.rows for table in result.tables}
    assert tables['lot_map_coverage'] == [{'lot_id': 'A', 'has_map': False}, {'lot_id': 'B', 'has_map': True}]
    assert [(row['lot_id'], row['wf_id']) for row in tables['wafer_map_members']] == [('B', 8)]
    assert result.scope['wafer_maps']['missing_requests']['lot_count'] == 1
    assert '검사 MAP TEST, 전체 맵 측정 기간. 전체 대상 2 LOT 중 일치 1, 맵 없음 1.' in result.summary


def test_empty_and_unavailable_lot_selections_never_query_map(monkeypatch):
    table = SimpleNamespace(table_id='lot_summary', complete=True, columns=['lot_id'], total_rows=0)
    history = SimpleNamespace(tool_name='query_lot_history', status='empty', tables=[table])
    async def empty_rows(*key, table_id):
        for row in []:
            yield row
    store = SimpleNamespace(observation=AsyncMock(return_value=history), iter_table_rows=empty_rows)
    ctx = SimpleNamespace(run={'principal_id': 'p', 'session_id': 's'}, store=store)
    monkeypatch.setattr(maps, 'run_blocking', lambda *args: pytest.fail('unexpected MAP lookup'))
    selection = maps.LoadWaferMapsInput(lot_result_id='history-id', oper='PT1C TEST')
    result = asyncio.run(maps.load_wafer_maps(selection, ctx))
    assert result.status == 'empty' and result.scope['selected_lot_count'] == 0
    table.complete = False
    with pytest.raises(ValueError, match='complete'):
        asyncio.run(maps.load_wafer_maps(selection, ctx))
    store.observation.side_effect = PermissionError('Result unavailable')
    with pytest.raises(PermissionError):
        asyncio.run(maps.load_wafer_maps(selection, ctx))


def test_missing_cohort_maps_distinguish_unknown_operation_from_no_matches(monkeypatch):
    from unittest.mock import MagicMock
    cursor = MagicMock()
    connection = MagicMock()
    connection.cursor.return_value = cursor
    cursor.description = [('LOT_ID',)]
    cursor.fetchmany.return_value = []
    cursor.fetchall.side_effect = [[], [('Actual test',)], []]
    cursor.fetchone.side_effect = [None, (1,)]
    monkeypatch.setattr(maps, '_connection', lambda: connection)
    args = maps.LoadWaferMapsInput(lot_result_id='selected', oper='Unrecognized').model_copy(update={'lot_ids': ['A']})
    with pytest.raises(ValueError, match='Actual test'):
        with maps._open_map_source(args):
            pytest.fail('unknown operation accepted')
    args = args.model_copy(update={'oper': 'Actual test'})
    with maps._open_map_source(args) as (rows, _, found):
        assert not list(rows) and found['lot_ids'] == []

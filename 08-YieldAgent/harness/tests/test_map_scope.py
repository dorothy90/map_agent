import asyncio
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from harness.store import HarnessStore
from harness.tools import map_scope
from harness.tools.load_wafer_maps import LoadWaferMapsInput


def test_remembered_scope_cannot_widen_into_a_flat_followup():
    groups = [dict(label='left', lot_id='A', wf_ids=[1, 3]), dict(label='right', lot_id='B', wf_ids=[2])]
    prior = dict(kind='targeted', lot_cd='P', wafer_groups=groups, memory_refs=['a', 'b'])
    with pytest.raises(ValueError, match='memory_refs'):
        map_scope._selection(prior, dict(action='continue', kind='targeted', target_lot_ids=['A']), 'r')
    scope = map_scope._selection(prior, dict(action='start', kind='targeted', target_lot_ids=['C']), 'r')
    assert scope == dict(kind='targeted', target_lot_ids=['C'], analysis_run_id='r')
    args = LoadWaferMapsInput(memory_refs=['a', 'b'])
    assert map_scope._apply(args, prior, 'UTC')[0].lot_cd == 'P'
    scoped = dict(prior, oper='Canonical Test')
    assert map_scope._apply(args.model_copy(update={'oper':'current shorthand'}), scoped, 'UTC')[0].oper == 'Canonical Test'
    with pytest.raises(ValueError, match='confirm the measurement stage'):
        map_scope._apply(args.model_copy(update={'oper':'other'}), prior, 'UTC')
    with pytest.raises(ValueError, match='memory_refs'):
        map_scope._apply(args.model_copy(update={'memory_refs':['a']}), prior, 'UTC')


def test_scoped_map_reuses_a_frozen_window_and_isolates_updates(monkeypatch):
    async def scenario():
        store = HarnessStore(database='test_map_scope_' + uuid4().hex)
        first_window = datetime(2026, 10, 5), datetime(2026, 11, 1)
        next_window = datetime(2026, 11, 2), datetime(2026, 11, 25)
        resolutions, windows = [], []
        intent = {'action': 'start', 'kind': 'scoped', 'lot_cd': '4SA', 'oper': 'PT1H TEST'}

        async def interpret(ctx, previous, history):
            resolutions.append(previous)
            return dict(intent)

        async def window(function, lot_cd, oper, period):
            windows.append((lot_cd, oper, period))
            return first_window if lot_cd == '4SA' else next_window

        monkeypatch.setattr(map_scope, 'interpret_map_intent', interpret)
        monkeypatch.setattr(map_scope, 'run_blocking', window)

        async def new_run(principal='user', session='chat'):
            run = await store.start_run(principal, session, str(uuid4()), 'test')
            acquired = await store.acquire(run['run_id'], 'worker')
            return SimpleNamespace(store=store, run=acquired, map_scope_history=[],
                                   map_scope_lock=asyncio.Lock(), settings=SimpleNamespace(timezone='Asia/Seoul'))

        async def finish(ctx):
            await store.finish(ctx.run['run_id'], ctx.run['epoch'], 'completed', 'ok', 'completed')

        bare = LoadWaferMapsInput(lot_ids=['L1', 'L2'])
        try:
            await store.setup()
            ctx = await new_run()
            with pytest.raises(ValueError, match='period'):
                await map_scope.effective_map_args(bare, ctx)
            assert not windows
            intent.clear()
            intent.update(action='continue', kind='scoped', period={'kind': 'recent_weeks', 'count': 4})
            ctx.map_scope_history.append({'role': 'user', 'content': '최근 4주', 'current_turn': True})
            pages = await asyncio.gather(*(map_scope.effective_map_args(bare, ctx) for _ in range(3)))
            for args, scope in pages:
                assert (args.lot_cd, args.oper, args.start_time, args.end_time) == ('4SA', 'PT1H TEST', *first_window)
            assert len(resolutions) == 2 and len(windows) == 1
            for modified in ({'lot_cd': '4SS'}, {'oper': 'PT1C TEST'}, {'start_time': datetime(2026, 8, 1)},
                             {'time_column': 'start_tm'}):
                with pytest.raises(ValueError, match='conflict|CONFLICT'):
                    await map_scope.effective_map_args(bare.model_copy(update=modified), ctx)
            await finish(ctx)

            ctx = await new_run()
            intent.clear()
            intent.update(action='continue', kind='scoped')
            inherited, _ = await map_scope.effective_map_args(bare, ctx)
            assert inherited.start_time == first_window[0] and len(windows) == 1
            await finish(ctx)

            ctx = await new_run()
            intent.update(action='change', lot_cd='4SS')
            updated, _ = await map_scope.effective_map_args(bare, ctx)
            assert (updated.lot_cd, updated.oper, updated.start_time, updated.end_time) == ('4SS', 'PT1H TEST', *next_window)
            assert len(windows) == 2
            await finish(ctx)

            # Beginning another analysis anchors it anew even with identical filters.
            ctx = await new_run()
            intent.update(action='start', oper='PT1H TEST', period={'kind': 'recent_weeks', 'count': 4})
            await map_scope.effective_map_args(bare, ctx)
            assert len(windows) == 3
            await finish(ctx)

            for principal, session in [('other', 'chat'), ('user', 'elsewhere')]:
                ctx = await new_run(principal, session)
                intent.clear()
                intent.update(action='continue', kind='scoped')
                with pytest.raises(ValueError, match='lot_cd'):
                    await map_scope.effective_map_args(bare, ctx)
                assert resolutions[-1] is None
                await finish(ctx)

            ctx = await new_run()
            intent.clear()
            intent.update(action='start', kind='targeted', target_lot_ids=['other-lot'], target_wf_ids=[8, 10])
            single = LoadWaferMapsInput(lot_ids=['other-lot'], wf_ids=[8])
            single_args, _ = await map_scope.effective_map_args(single, ctx)
            assert single_args == single
            no_wafers = LoadWaferMapsInput(lot_ids=['other-lot'])
            both, _ = await map_scope.effective_map_args(no_wafers, ctx)
            assert both.wf_ids == [8, 10]
            with pytest.raises(ValueError, match='wf_ids'):
                await map_scope.effective_map_args(no_wafers.model_copy(update={'wf_ids': [1]}), ctx)
            with pytest.raises(ValueError):
                await map_scope.effective_map_args(bare, ctx)
            await finish(ctx)

            ctx = await new_run()
            intent.update(target_wf_ids=[], target_lot_ids=['other-lot','another-lot'])
            whole, _ = await map_scope.effective_map_args(no_wafers, ctx)
            assert whole == no_wafers
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['intent', 'anchor'])
def test_confirmation_error_is_stable_until_another_user_answer(monkeypatch, failure):
    async def scenario():
        store = HarnessStore(database='test_map_scope_error_' + uuid4().hex)
        attempts = []

        async def interpret(ctx, previous, history):
            attempts.append(len(history))
            if failure == 'intent' and not history:
                raise ValueError('Unable to resolve requested stage')
            period = {'kind': 'all'} if history else {'kind': 'recent_weeks', 'count': 2}
            return dict(action='change', kind='scoped', lot_cd='OTHER', oper='TEST', period=period)

        async def no_measurements(*unused):
            raise map_scope.DomainError('No measurement date', error_type='ValueError')

        monkeypatch.setattr(map_scope, 'interpret_map_intent', interpret)
        monkeypatch.setattr(map_scope, 'run_blocking', no_measurements)
        try:
            run = await store.start_run('user', 'chat', 'request', 'map')
            run = await store.acquire(run['run_id'], 'owner')
            ctx = SimpleNamespace(store=store, run=run, map_scope_history=[],
                                  map_scope_lock=asyncio.Lock(), settings=SimpleNamespace(timezone='Asia/Seoul'))
            bare = LoadWaferMapsInput(lot_ids=['L'])
            for _ in range(2):
                with pytest.raises(ValueError, match='MAP_SCOPE_UNRESOLVED'):
                    await map_scope.effective_map_args(bare, ctx)
            assert attempts == [0]
            ctx.map_scope_history.append({'role':'user', 'content':'해당 검사 전체 기간', 'current_turn':True})
            args, scope = await map_scope.effective_map_args(bare, ctx)
            assert args.oper == 'TEST' and scope['period'] == {'kind':'all'}
            assert args.start_time is None and args.end_time is None and attempts == [0, 1]
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()

    asyncio.run(scenario())

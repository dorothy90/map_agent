import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


def test_completed_run_emits_one_terminal_event_and_rejects_stale_input(native_result):
    from harness.control import RunController
    from harness.config import Settings
    from harness.store import HarnessStore, Conflict

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        control = RunController(store, Settings())
        try:
            await store.setup()
            run = await control.start("test", "session", "req", "안녕")
            await control.tasks[run["run_id"]]
            completed = await store.get_run("test", run["run_id"])
            assert completed["status"] == "completed"
            assert [e["sequence"] for e in completed["events"]] == list(range(1, len(completed["events"]) + 1))
            assert sum(e["type"] == "run_finished" for e in completed["events"]) == 1
            with pytest.raises(Conflict):
                await control.submit_input("test", run["run_id"], {"kind": "input", "goal_revision": 1, "interrupt_id": "stale", "value": "yes", "request_id": "input1"})
        finally:
            await control.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["launch", "steer", "recover"])
@pytest.mark.parametrize("status", ["created", "running", "waiting_user"])
@pytest.mark.parametrize("engine", ["langgraph", None])
def test_stored_non_native_run_is_failed_without_execution(monkeypatch, operation, status, engine):
    from harness.config import Settings
    from harness.control import RunController
    from harness.store import Conflict

    async def scenario():
        run = {"run_id": "old", "principal_id": "p", "session_id": "s", "epoch": 3,
               "active": True, "runtime_version": "harness/v2", "status": status,
               "lease_until": 0, "engine": engine}
        owned = {**run, "epoch": 4, "status": "running"}
        runs = SimpleNamespace(find_one=AsyncMock(return_value=run), update_one=AsyncMock())
        store = SimpleNamespace(runs=runs, acquire=AsyncMock(return_value=owned),
                                finish=AsyncMock(), get_run=AsyncMock(return_value=run))
        async def cursor(items):
            for item in items:
                yield item
        runs.find = Mock(side_effect=[cursor([]), cursor([run]), cursor([]), cursor([]), cursor([])])
        native = AsyncMock(side_effect=AssertionError("unsupported run executed"))
        monkeypatch.setattr("harness.hermes_chat.run_chat", native)
        controller = RunController(store, Settings())
        if operation == "launch":
            await controller.launch("old")
        elif operation == "steer":
            with pytest.raises(Conflict, match="only hermes"):
                await controller.submit_input("p", "old", {"kind": "steer"})
        else:
            await controller.recover()
        store.acquire.assert_awaited_once_with("old", controller.owner)
        args = store.finish.await_args.args
        assert args[:3] == ("old", 4, "failed")
        assert "only hermes" in args[3]
        assert args[4] == "unsupported_engine"
        assert run["engine"] == engine
        assert not controller.tasks
        native.assert_not_awaited()
        if status == "waiting_user":
            query, update = runs.update_one.await_args_list[0].args
            assert query["epoch"] == 3 and query["active"] is True
            assert "lease_until" in query and update == {"$set": {"status": "created"}}
    asyncio.run(scenario())


def test_non_native_history_remains_readable_and_is_not_relabelled():
    from harness.store import HarnessStore

    document = {"run_id": "old", "engine": "langgraph", "active": False, "status": "completed"}
    store = SimpleNamespace(runs=SimpleNamespace(find_one=AsyncMock(return_value=document)))
    assert asyncio.run(HarnessStore.get_run(store, "p", "old")) is document
    assert document["engine"] == "langgraph"


def test_non_native_rejection_does_not_steal_an_existing_lease():
    from harness.config import Settings
    from harness.control import RunController
    from harness.store import Conflict

    run = {"run_id": "old", "engine": "langgraph", "epoch": 3,
           "active": True, "status": "running", "runtime_version": "harness/v2"}
    runs = SimpleNamespace(find_one=AsyncMock(return_value=run), update_one=AsyncMock())
    store = SimpleNamespace(runs=runs, acquire=AsyncMock(return_value=None), finish=AsyncMock())
    controller = RunController(store, Settings())
    with pytest.raises(Conflict, match="only hermes"):
        asyncio.run(controller.launch("old"))
    store.finish.assert_not_awaited()
    query, update = runs.update_one.await_args.args
    assert query["epoch"] == 3 and query["status"] == "waiting_user"
    assert "lease_until" in query
    assert update == {"$set": {"status": "created"}}
    assert not controller.tasks


def test_terminal_non_native_steer_recovery_preserves_history_and_releases_pending():
    from harness.config import Settings
    from harness.control import RunController
    from harness.store import Conflict

    run = {"run_id": "old", "engine": "langgraph", "epoch": 3, "active": False,
           "status": "cancelled", "answer": "preserved", "steer_pending": {"value": "new goal"}}
    before = dict(run)
    runs = SimpleNamespace(update_one=AsyncMock())
    store = SimpleNamespace(runs=runs, acquire=AsyncMock(), finish=AsyncMock())
    controller = RunController(store, Settings())
    with pytest.raises(Conflict, match="only hermes"):
        asyncio.run(controller._apply_steer(run, run["steer_pending"]))
    store.acquire.assert_not_awaited()
    store.finish.assert_not_awaited()
    runs.update_one.assert_awaited_once_with(
        {"_id": "old", "epoch": 3, "active": False}, {"$unset": {"steer_pending": ""}})
    assert run == before
    assert not controller.tasks


def test_terminal_non_native_launch_is_explicitly_rejected_without_rewriting_history(monkeypatch):
    from harness.config import Settings
    from harness.control import RunController
    from harness.store import Conflict

    run = {"run_id": "old", "engine": "langgraph", "epoch": 3, "active": False,
           "status": "completed", "answer": "preserved"}
    before = dict(run)
    runs = SimpleNamespace(find_one=AsyncMock(return_value=run), update_one=AsyncMock())
    store = SimpleNamespace(runs=runs, acquire=AsyncMock(), finish=AsyncMock())
    native = AsyncMock(side_effect=AssertionError("historical run executed"))
    monkeypatch.setattr("harness.hermes_chat.run_chat", native)
    controller = RunController(store, Settings())
    with pytest.raises(Conflict, match="only hermes"):
        asyncio.run(controller.launch("old"))
    store.acquire.assert_not_awaited()
    store.finish.assert_not_awaited()
    runs.update_one.assert_not_awaited()
    native.assert_not_awaited()
    assert run == before
    assert not controller.tasks


def test_steer_leftovers_start_one_visible_followup_after_worker_exit(monkeypatch):
    from harness.config import Settings
    from harness.control import RunController
    from harness.gateway.events import SessionEvents
    from harness.gateway.session_store import MongoSessionStore
    from harness.store import HarnessStore

    async def scenario():
        store = HarnessStore(database='test_steer_carryover_' + uuid.uuid4().hex)
        controller = RunController(store, Settings())
        running = maximum = 0
        queries = []
        first_id = None
        original_finish = store.finish

        async def finish(run_id, *args, **kwargs):
            # Arrives after run_chat has returned, immediately before active is released.
            if run_id == first_id:
                await store.runs.update_one({'_id': run_id, 'active': True}, {'$push': {
                    'steer_inbox': {'id': 'late', 'text': 'late correction', 'at': 2}}})
            return await original_finish(run_id, *args, **kwargs)

        async def chat(context):
            nonlocal running, maximum, first_id
            running += 1
            maximum = max(maximum, running)
            try:
                assert await store.runs.count_documents({'session_id': 's', 'active': True}) == 1
                queries.append(context.run['query'])
                if first_id is None:
                    first_id = context.run['run_id']
                    await store.runs.update_one({'_id': first_id}, {'$set': {'steer_inbox': [
                        {'id': 'delivered', 'text': 'already consumed', 'at': 0},
                        {'id': 'pending', 'text': 'pending correction', 'at': 1}]}})
                    return {'status': 'completed', 'answer': 'first', 'stop_reason': 'completed',
                            'pending_steer': 'pending correction',
                            'steer_forwarded_ids': ['delivered', 'pending']}
                assert (await store.get_run('p', first_id))['active'] is False
                await SessionEvents(store, 'p').publish_live('s', 'message.delta', {'text': 'second delta'})
                return {'status': 'completed', 'answer': 'second', 'stop_reason': 'completed'}
            finally:
                running -= 1

        monkeypatch.setattr('harness.hermes_chat.run_chat', chat)
        monkeypatch.setattr(store, 'finish', finish)
        try:
            await store.setup()
            await MongoSessionStore(store, 'p').create_session('s', 'api_server')
            first = await controller.start('p', 's', 'first', 'original')
            await controller.tasks[first['run_id']]
            followups = await store.runs.find({'request_id': 'steer-carryover:' + first['run_id']}).to_list(None)
            assert len(followups) == 1
            second = followups[0]
            await controller.tasks[second['run_id']]
            # Retrying the same admission cannot launch a second conversation.
            await controller.start('p', 's', second['request_id'], second['query'])
            assert queries == ['original', 'pending correction\nlate correction']
            assert maximum == 1
            assert await store.runs.count_documents({'session_id': 's'}) == 2
            events = SessionEvents(store, 'p')
            mark = await events.watermark('s')
            frames = (await events.since('s', mark['replay_epoch'], 1))['events']
            start = next(f for f in frames if f['type'] == 'message.start')
            assert start['payload']['carryover_text'] == second['query']
            assert start['payload']['carryover_id'] == second['run_id']
            terminal = next(i for i, f in enumerate(frames)
                            if f['type'] == 'run_finished' and f['payload']['run_id'] == first['run_id'])
            assert frames.index(start) > terminal
            delta = next(f for f in frames if f['type'] == 'message.delta')
            assert frames.index(delta) > frames.index(start)
        finally:
            await controller.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_existing_interrupt_path_waits_for_worker_before_next_submit(monkeypatch):
    from harness.config import Settings
    from harness.control import RunController
    from harness.gateway.execution import Execution
    from harness.gateway.session_store import MongoSessionStore
    from harness.store import HarnessStore

    async def scenario():
        store = HarnessStore(database='test_interrupt_next_' + uuid.uuid4().hex)
        controller = RunController(store, Settings())
        entered, exited = asyncio.Event(), asyncio.Event()
        queries = []

        async def chat(context):
            queries.append(context.run['query'])
            if len(queries) == 1:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    await asyncio.sleep(.02)
                    exited.set()
            assert exited.is_set()
            assert await store.runs.count_documents({'session_id': 's', 'active': True}) == 1
            return {'status': 'completed', 'answer': 'replacement', 'stop_reason': 'completed'}

        monkeypatch.setattr('harness.hermes_chat.run_chat', chat)
        try:
            await store.setup()
            await MongoSessionStore(store, 'p').create_session('s', 'api_server')
            execution = Execution(controller, 'p', profile_id='default')
            first = await execution.submit('s', {'session_id': 's', 'submission_id': 'first', 'text': 'original'})
            await entered.wait()
            await execution.interrupt('s', {'session_id': 's', 'execution_generation': first['execution_generation']},
                                      expected_run_id=first['run_id'])
            assert exited.is_set()
            assert (await store.get_run('p', first['run_id']))['status'] == 'cancelled'
            second = await execution.submit('s', {'session_id': 's', 'submission_id': 'second', 'text': 'replacement'})
            await controller.tasks[second['run_id']]
            assert queries == ['original', 'replacement']
        finally:
            await controller.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_interrupt_from_another_controller_keeps_slot_until_worker_stops(monkeypatch):
    from harness.config import Settings
    from harness.control import RunController
    from harness.gateway.execution import Execution, RpcError
    from harness.gateway.session_store import MongoSessionStore
    from harness.store import HarnessStore

    async def scenario():
        store = HarnessStore(database='test_remote_interrupt_' + uuid.uuid4().hex)
        owner = RunController(store, Settings())
        remote = RunController(store, Settings())
        entered, exited = asyncio.Event(), asyncio.Event()

        async def chat(context):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.02)
                exited.set()

        monkeypatch.setattr('harness.hermes_chat.run_chat', chat)
        try:
            await store.setup()
            await MongoSessionStore(store, 'p').create_session('s', 'api_server')
            first = await owner.start('p', 's', 'first', 'original')
            await entered.wait()
            execution = Execution(remote, 'p', profile_id='default')
            run = await store.get_run('p', first['run_id'])
            await execution.interrupt('s', {'session_id': 's', 'execution_generation': run['epoch']},
                                      expected_run_id=run['run_id'])
            assert not exited.is_set()
            assert (await store.get_run('p', run['run_id']))['active'] is True
            with pytest.raises(RpcError, match='session_busy'):
                await execution.submit('s', {'session_id': 's', 'submission_id': 'second', 'text': 'replacement'})
            async with asyncio.timeout(8):
                await owner.tasks[run['run_id']]
            assert exited.is_set()
            assert (await store.get_run('p', run['run_id']))['status'] == 'cancelled'
        finally:
            await remote.close()
            await owner.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('failure_point', ['before_admission', 'after_admission'])
def test_carryover_recovers_after_settlement_storage_failure(monkeypatch, failure_point):
    from harness.config import Settings
    from harness.control import RunController
    from harness.store import HarnessStore

    async def scenario():
        store = HarnessStore(database='test_carryover_recovery_' + uuid.uuid4().hex)
        controller = RunController(store, Settings())
        queries = []
        failed = False
        start_run, update_one = store.start_run, store.runs.update_one

        async def chat(context):
            queries.append(context.run['query'])
            assert await store.runs.count_documents({'session_id': 's', 'active': True}) == 1
            return {'status': 'completed', 'answer': 'answer', 'stop_reason': 'completed',
                    'pending_steer': 'follow-up' if len(queries) == 1 else None}

        async def admit(principal, session, request_id, *args, **kwargs):
            nonlocal failed
            if failure_point == 'before_admission' and request_id.startswith('steer-carryover:') and not failed:
                failed = True
                raise RuntimeError('temporary storage failure')
            return await start_run(principal, session, request_id, *args, **kwargs)

        async def update(query, change, *args, **kwargs):
            nonlocal failed
            if failure_point == 'after_admission' and 'steer_carryover_from' in change.get('$set', {}) and not failed:
                failed = True
                raise RuntimeError('temporary storage failure')
            return await update_one(query, change, *args, **kwargs)

        monkeypatch.setattr('harness.hermes_chat.run_chat', chat)
        monkeypatch.setattr(store, 'start_run', admit)
        monkeypatch.setattr(store.runs, 'update_one', update)
        try:
            await store.setup()
            first = await controller.start('p', 's', 'first', 'original')
            await controller.tasks[first['run_id']]
            assert failed
            await controller.recover()
            second = await store.runs.find_one({'request_id': 'steer-carryover:' + first['run_id']})
            assert second is not None
            await controller.tasks[second['run_id']]
            await controller.recover()
            assert queries == ['original', 'follow-up']
            assert await store.runs.count_documents({'session_id': 's'}) == 2
        finally:
            await controller.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())

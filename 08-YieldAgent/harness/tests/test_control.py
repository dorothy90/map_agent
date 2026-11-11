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
        runs.find = Mock(side_effect=[cursor([run]), cursor([]), cursor([]), cursor([])])
        native = AsyncMock(side_effect=AssertionError("unsupported run executed"))
        monkeypatch.setattr("harness.native_hermes.run_native", native)
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
    monkeypatch.setattr("harness.native_hermes.run_native", native)
    controller = RunController(store, Settings())
    with pytest.raises(Conflict, match="only hermes"):
        asyncio.run(controller.launch("old"))
    store.acquire.assert_not_awaited()
    store.finish.assert_not_awaited()
    runs.update_one.assert_not_awaited()
    native.assert_not_awaited()
    assert run == before
    assert not controller.tasks

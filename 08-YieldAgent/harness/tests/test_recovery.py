import asyncio
import uuid

import pytest


@pytest.mark.parametrize("new_owner", [False, True])
def test_expired_worker_finishes_only_its_own_epoch(native_result, monkeypatch, new_owner):
    from harness.config import Settings
    from harness.control import RunController
    from harness.executor import ExecutionContext
    from harness.store import HarnessStore

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        service = RunController(store, Settings())
        original_check = ExecutionContext.check

        async def expire(context):
            await store.runs.update_one({"_id": context.run["run_id"]}, {"$set": {"lease_until": 0}})
            if new_owner:
                await store.acquire(context.run["run_id"], "successor")
            await original_check(context)

        monkeypatch.setattr(ExecutionContext, "check", expire)
        try:
            await store.setup()
            run = await service.start("p", "s", "r", "fixture")
            await service.tasks[run["run_id"]]
            final = await store.get_run("p", run["run_id"])
            if new_owner:
                assert final["epoch"] == 2
                assert final["owner"] == "successor"
                assert final["status"] == "running"
                assert not any(e["type"] == "run_finished" for e in final["events"])
            else:
                assert final["status"] == "partial"
                assert final["stop_reason"] == "lease_expired"
                assert not final["active"]
                assert sum(e["type"] == "run_finished" for e in final["events"]) == 1
        finally:
            await service.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_heartbeat_failure_stops_work_and_records_the_failure(monkeypatch):
    from harness.config import Settings
    from harness.control import RunController
    from harness.store import HarnessStore
    from harness.tools.registry import domain_registry

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        entered, cancelled = asyncio.Event(), asyncio.Event()
        registry = domain_registry()

        async def slow(ctx, registry, *, history):
            entered.set()
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.set()

        async def fail_renew(*args):
            raise ConnectionError("fixture renewal failure")

        monkeypatch.setattr(store, "renew", fail_renew)
        monkeypatch.setattr("harness.native_hermes.run_native", slow)
        service = RunController(store, Settings(), registry_factory=lambda: registry)
        try:
            await store.setup()
            run = await service.start("p", "s", "r", "fixture")
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.wait_for(asyncio.shield(service.tasks[run["run_id"]]), 8)
            final = await store.get_run("p", run["run_id"])
            assert cancelled.is_set()
            assert final["status"] == "partial"
            assert final["stop_reason"] == "lease_renewal_failed"
            assert not final["active"]
            assert sum(e["type"] == "run_finished" for e in final["events"]) == 1
            assert await store.results.count_documents({}) == 0
        finally:
            await service.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure, expected_status, heartbeat_mode", [
    ("lease", "partial", "exception"), ("cancel", "cancelled", "exception"), ("error", "failed", "exception"),
    ("cancel", "cancelled", "real"), ("lease", "partial", "real"),
])
def test_heartbeat_failure_during_finish_cannot_interrupt_terminal_save(native_result, monkeypatch, failure, expected_status, heartbeat_mode):
    from harness.config import Settings
    from harness.control import RunController
    from harness.executor import ExecutionContext
    from harness.store import HarnessStore, LeaseLost

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        settings = Settings(active_seconds=5 if heartbeat_mode == "real" and failure == "lease" else 600)
        service = RunController(store, settings)
        finishing = asyncio.Event()
        original_finish = store.finish

        async def fail_check(context):
            if failure == "lease":
                raise LeaseLost("fixture expiry")
            if failure == "cancel":
                await store.runs.update_one({"_id": context.run["run_id"]}, {"$set": {"status": "cancelling"}})
                raise asyncio.CancelledError
            raise RuntimeError("fixture failure")

        async def slow_finish(*args, **kwargs):
            finishing.set()
            await asyncio.sleep(6 if heartbeat_mode == "real" else .05)
            return await original_finish(*args, **kwargs)

        async def failing_heartbeat(run):
            await finishing.wait()
            raise ConnectionError("fixture late renewal failure")

        monkeypatch.setattr(ExecutionContext, "check", fail_check)
        monkeypatch.setattr(store, "finish", slow_finish)
        if heartbeat_mode == "exception":
            monkeypatch.setattr(service, "_heartbeat", failing_heartbeat)
        try:
            await store.setup()
            run = await service.start("p", "s", "r", "fixture")
            await service.tasks[run["run_id"]]
            final = await store.get_run("p", run["run_id"])
            assert final["status"] == expected_status
            assert not final["active"]
            assert sum(e["type"] == "run_finished" for e in final["events"]) == 1
        finally:
            await service.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_cancel_rejects_late_result_and_emits_one_terminal_event(monkeypatch):
    from harness.config import Settings
    from harness.control import RunController
    from harness.store import HarnessStore
    from harness.tools.registry import domain_registry

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        entered, cancelled = asyncio.Event(), asyncio.Event()
        registry = domain_registry()
        async def slow(ctx, registry, *, history):
            entered.set()
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.set()
            return {"status": "completed", "answer": "late", "stop_reason": "completed"}
        monkeypatch.setattr("harness.native_hermes.run_native", slow)
        service = RunController(store, Settings(), registry_factory=lambda: registry)
        try:
            await store.setup()
            run = await service.start("p", "s", "r", "조회")
            await asyncio.wait_for(entered.wait(), 5)
            await service.cancel("p", run["run_id"])
            assert cancelled.is_set()
            final = await store.get_run("p", run["run_id"])
            assert final["status"] == "cancelled"
            await service.cancel("p", run["run_id"])
            assert sum(e["type"] == "run_finished" for e in final["events"]) == 1
            assert await store.results.count_documents({}) == 0
        finally:
            await service.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_lease_recovery_replays_success_and_retries_only_unknown_reads():
    from harness.store import HarnessStore, Conflict
    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        try:
            await store.setup()
            run = await store.start_run("p", "s", "r", "조회")
            first = await store.acquire(run["run_id"], "a")
            for call in ("read", "write"):
                await store.claim_invocation(run["run_id"], call, call, {}, first["epoch"])
            await store.runs.update_one({"_id": run["run_id"]}, {"$set": {"lease_until": 0}})
            second = await store.acquire(run["run_id"], "b")
            with pytest.raises(Conflict):
                await store.fence(run["run_id"], first["epoch"])
            recovered = await store.claim_invocation(run["run_id"], "read", "read", {}, second["epoch"], read_only=True)
            assert recovered["epoch"] == second["epoch"]
            with pytest.raises(Conflict):
                await store.claim_invocation(run["run_id"], "write", "write", {}, second["epoch"])
            obs = await store.put_result("p", "s", {"run_id": run["run_id"], "invocation_id": "read", "tool_name": "read"}, [{"n": 1}], [])
            await store.finish_invocation(run["run_id"], "read", obs, second["epoch"])
            assert (await store.claim_invocation(run["run_id"], "read", "read", {}, second["epoch"]))["result_id"] == obs.result_id
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())




@pytest.mark.parametrize('child', [False, True])
def test_recovered_tool_reuses_atomic_budget_charge(child):
    from harness.config import Settings
    from harness.executor import ExecutionContext, ToolExecutor
    from harness.store import HarnessStore
    from harness.tools.registry import ToolRegistry, ToolResult, ToolSpec
    from harness.types import Contract
    class Input(Contract):
        pass
    async def scenario():
        store = HarnessStore(database='harness_recovery_charge_' + uuid.uuid4().hex)
        attempts = []
        async def handler(args, ctx):
            attempts.append(True)
            if len(attempts) == 1:
                raise asyncio.CancelledError
            return ToolResult(rows=[{'value': 7}])
        registry = ToolRegistry()
        registry.add(ToolSpec('query', 'fixture', Input, handler))
        try:
            await store.setup()
            run = await store.start_run('p', 's', 'r', 'recover')
            run = await store.acquire(run['run_id'], 'first')
            if child:
                await store.runs.update_one({'_id': run['run_id']}, {'$set': {'usage.child_tools': 5}})
            settings = Settings(tool_limit=1)
            context_args = {'depth': 1, 'namespace': 'child_'} if child else {}
            executor = ToolExecutor(registry, ExecutionContext(store, settings, run, **context_args))
            call = {'id': 'same', 'name': 'query', 'args': {}}
            with pytest.raises(asyncio.CancelledError):
                await executor.execute(call)
            await store.runs.update_one({'_id': run['run_id']}, {'$set': {'lease_until': 0}})
            recovered = await store.acquire(run['run_id'], 'second')
            executor = ToolExecutor(registry, ExecutionContext(store, settings, recovered, **context_args))
            result = await executor.execute(call)
            assert result.status == 'success'
            assert (await executor.execute(call)).result_id == result.result_id
            current = await store.get_run('p', run['run_id'])
            assert current['usage']['tools'] == 1
            if child:
                assert current['usage']['child_tools'] == 6
            assert len(attempts) == 2
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_tool_reservation_updates_shared_and_child_budgets_together():
    from harness.store import HarnessStore, BudgetExceeded
    async def scenario():
        store = HarnessStore(database='harness_atomic_charge_' + uuid.uuid4().hex)
        try:
            await store.setup()
            run = await store.start_run('p', 's', 'r', 'reserve')
            run = await store.acquire(run['run_id'], 'owner')
            await store.runs.update_one({'_id': run['run_id']}, {'$set': {'usage.child_tools': 6}})
            with pytest.raises(BudgetExceeded, match='child_tools_limit'):
                await store.reserve_tool_call(run['run_id'], run['epoch'], invocation_id='call', tool_limit=24, child_kind='child_tools')
            current = await store.get_run('p', run['run_id'])
            assert current['usage']['tools'] == 0
            assert not current.get('tool_charges')
            await store.runs.update_one({'_id': run['run_id']}, {'$set': {'usage.child_tools': 0}})
            await asyncio.gather(*(store.reserve_tool_call(run['run_id'], run['epoch'], invocation_id='call', tool_limit=1, child_kind='child_tools') for _ in range(2)))
            current = await store.get_run('p', run['run_id'])
            assert current['usage']['tools'] == current['usage']['child_tools'] == 1
            assert len(current['tool_charges']) == 1
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())

import asyncio
import uuid
from dataclasses import replace


def test_cancelling_cannot_be_overwritten_by_completed():
    from harness.store import HarnessStore
    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        try:
            await store.setup()
            run = await store.start_run("p", "s", "r", "q")
            run = await store.acquire(run["run_id"], "a")
            await store.runs.update_one({"_id": run["run_id"]}, {"$set": {"status": "cancelling"}})
            await store.finish(run["run_id"], run["epoch"], "completed", "late", "verified")
            final = await store.get_run("p", run["run_id"])
            assert final["status"] == "cancelled"
            assert final["events"][-1]["payload"]["status"] == "cancelled"
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_recovery_ignores_mcp_and_recovers_durable_steer_once(native_result):
    from harness.store import HarnessStore
    from harness.config import Settings
    from harness.control import RunController
    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        service = RunController(store, Settings())
        try:
            await store.setup()
            mcp = await store.start_run("p", "mcp", "r", "MCP", execution_kind="mcp")
            old = await store.start_run("p", "s", "r", "원래 목표")
            old = await store.acquire(old["run_id"], "dead-worker")
            payload = {"kind": "steer", "request_id": "change", "goal_revision": 1, "value": "기간 수정"}
            await store.runs.update_one({"_id": old["run_id"]}, {"$set": {"steer_pending": payload, "status": "cancelling"}})
            await store.finish(old["run_id"], old["epoch"], "cancelled", "", "user_cancelled")
            await service.recover()
            assert mcp["run_id"] not in service.tasks
            new = await store.runs.find_one({"parent_run_id": old["run_id"]})
            assert new["goal_revision"] == 2 and new["ready"]
            await service.tasks[new["run_id"]]
            repeated = await service.submit_input("p", old["run_id"], payload)
            assert repeated["run_id"] == new["run_id"]
            assert await store.runs.count_documents({"session_id": "s"}) == 2
        finally:
            await service.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_success_replay_repairs_artifact_event_without_duplication():
    from harness.store import HarnessStore
    from harness.config import Settings
    from harness.executor import ToolExecutor, ExecutionContext
    from harness.tools.registry import domain_registry
    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        try:
            await store.setup()
            run = await store.start_run("p", "s", "r", "q")
            run = await store.acquire(run["run_id"], "a")
            await store.claim_invocation(run["run_id"], "call", "render_yield_table", {}, run["epoch"])
            obs = await store.put_result("p", "s", {"run_id": run["run_id"], "invocation_id": "call", "tool_name": "render_yield_table"}, [], [{"title": "test", "mime": "text/plain", "data": "saved"}])
            await store.finish_invocation(run["run_id"], "call", obs, run["epoch"])
            executor = ToolExecutor(domain_registry(), ExecutionContext(store, Settings(), run))
            for _ in range(2):
                await executor.execute({"name": "render_yield_table", "id": "call", "args": {}})
            events = (await store.get_run("p", run["run_id"]))["events"]
            assert sum(e["type"] == "artifact" for e in events) == 1
            assert sum(e["type"] == "tool_finished" for e in events) == 1
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_root_and_children_share_two_domain_slots():
    from harness.store import HarnessStore
    from harness.config import Settings
    from harness.executor import ToolExecutor, ExecutionContext
    from harness.tools.registry import domain_registry, ToolResult
    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        active, peak = 0, 0
        async def work(args, ctx):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(.05)
                return ToolResult()
            finally:
                active -= 1
        try:
            await store.setup()
            run = await store.start_run("p", "s", "r", "q")
            run = await store.acquire(run["run_id"], "a")
            registry = domain_registry()
            registry.tools["list_tables"].handler = work
            context = ExecutionContext(store, Settings(), run)
            await asyncio.gather(*(ToolExecutor(registry, replace(context, depth=1, namespace=f"child_{i}_")).execute(
                {"name": "list_tables", "id": "read", "args": {"table": str(i)}}) for i in range(4)))
            assert peak == 2
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())

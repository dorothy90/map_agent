import asyncio
import uuid


def test_child_is_bounded_readonly_and_uses_parent_budget():
    from langgraph.checkpoint.memory import InMemorySaver
    from harness.config import Settings
    from harness.delegation import register_delegation
    from harness.executor import ExecutionContext, ToolExecutor
    from harness.store import HarnessStore
    from harness.testing import ScriptedModel
    from harness.tools.registry import domain_registry

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        try:
            await store.setup()
            run = await store.start_run("p", "s", "r", "독립 조사")
            run = await store.acquire(run["run_id"], "worker")
            registry = domain_registry()
            register_delegation(registry, lambda: ScriptedModel([{"finish": {"answer": "자료가 더 필요합니다."}}]), InMemorySaver())
            executor = ToolExecutor(registry, ExecutionContext(store, Settings(), run))
            rejected = await executor.execute({"name": "delegate_readonly", "id": "bad", "args": {"question": "출력", "allowed_tools": ["export_report"], "result_ids": []}})
            assert rejected.status == "error"
            result = await executor.execute({"name": "delegate_readonly", "id": "good", "args": {"question": "독립 검토", "allowed_tools": ["read_result"], "result_ids": []}})
            assert result.status == "success"
            current = await store.get_run("p", run["run_id"])
            assert current["usage"]["models"] == 2
            assert current["usage"]["tools"] == 2
            assert current["usage"]["children"] == 1
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())

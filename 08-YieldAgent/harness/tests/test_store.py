import asyncio
import uuid

import pytest


def test_full_results_and_session_ownership():
    from harness.store import HarnessStore

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        try:
            await store.setup()
            result = await store.put_result("a", "s", {"tool_name": "query", "run_id": "r", "invocation_id": "i"}, [{"n": i} for i in range(120)], [])
            assert result.total_rows == 120
            assert len(result.preview_rows) == 50
            page = await store.read_result("a", "s", result.result_id, offset=50)
            assert page["rows"][0]["n"] == 50
            assert page["total_rows"] == 120
            with pytest.raises(PermissionError):
                await store.read_result("b", "s", result.result_id)
            with pytest.raises(PermissionError):
                await store.read_result("a", "other", result.result_id)
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_duplicate_request_and_invocation_are_fenced():
    from harness.store import HarnessStore, Conflict

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        try:
            await store.setup()
            a = await store.start_run("a", "s", "req", "query")
            b = await store.start_run("a", "s", "req", "query")
            assert a["run_id"] == b["run_id"]
            with pytest.raises(Conflict):
                await store.start_run("a", "s", "req2", "different")
            run = await store.acquire(a["run_id"], "worker1")
            assert await store.acquire(a["run_id"], "worker2") is None
            first = await store.claim_invocation(run["run_id"], "call1", "query", {}, run["epoch"])
            assert first["status"] == "running"
            result = await store.put_result("a", "s", {"tool_name": "query", "run_id": run["run_id"], "invocation_id": "call1"}, [], [])
            await store.finish_invocation(run["run_id"], "call1", result, run["epoch"])
            again = await store.claim_invocation(run["run_id"], "call1", "query", {}, run["epoch"])
            assert again["status"] == "succeeded"
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())

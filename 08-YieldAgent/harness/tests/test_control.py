import asyncio
import uuid


def test_completed_run_emits_one_terminal_event_and_rejects_stale_input():
    from harness.control import RunController
    from harness.config import Settings
    from harness.store import HarnessStore, Conflict
    from harness.testing import ScriptedModel
    from langgraph.checkpoint.memory import InMemorySaver
    import pytest

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        control = RunController(store, Settings(), InMemorySaver(), model_factory=lambda: ScriptedModel([{"finish": {"answer": "안녕하세요"}}]))
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

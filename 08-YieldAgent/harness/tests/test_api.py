import asyncio
import uuid


def test_run_api_returns_terminal_events_and_enforces_ownership():
    import httpx
    from fastapi import FastAPI
    from langgraph.checkpoint.memory import InMemorySaver
    from harness.config import Settings
    from harness.control import RunController
    from harness.router import router
    from harness.store import HarnessStore
    from harness.testing import ScriptedModel

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        app = FastAPI()
        app.state.harness = RunController(store, Settings(), InMemorySaver(), model_factory=lambda: ScriptedModel([{"finish": {"answer": "안녕하세요"}}]))
        app.include_router(router)
        try:
            await store.setup()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/runs", json={"session_id": "s", "request_id": "r", "query": "안녕"})
                assert response.status_code == 200
                run_id = response.json()["run_id"]
                await app.state.harness.tasks[run_id]
                result = await client.get("/runs/" + run_id)
                assert result.json()["status"] == "completed"
                stream = await client.get(f"/runs/{run_id}/events")
                assert '"run_finished"' in stream.text
                other = await store.start_run("other", "s", "r2", "secret")
                assert (await client.get("/runs/" + other["run_id"])).status_code == 404
        finally:
            await app.state.harness.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())

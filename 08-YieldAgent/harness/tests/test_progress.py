import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from langchain_core.messages import AIMessage, HumanMessage

from harness.config import Settings
from harness.executor import ExecutionContext
from harness.router import compatibility_chat, router
from harness.store import HarnessStore
from models import ChatRequest


class Reply:
    def __init__(self, message):
        self.message, self.calls = message, 0
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.release.set()

    def bind_tools(self, *args, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self.message


@asynccontextmanager
async def execution(message):
    store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
    await store.setup()
    run = await store.start_run("local", "session", "request", "자료 확인")
    run = await store.acquire(run["run_id"], "test")
    model = Reply(message)
    ctx = ExecutionContext(store, Settings(enabled=True), run)
    try:
        yield store, run, model, ctx
    finally:
        await store.client.drop_database(store.db.name)
        store.client.close()


@pytest.mark.parametrize("purpose", ["reasoning", "answer", "review", "compaction"])
def test_phase_status_arrives_while_model_is_still_running(purpose):
    async def scenario():
        async with execution(AIMessage(content="done")) as (store, run, model, ctx):
            model.release.clear()
            task = asyncio.create_task(ctx.model_call(model, [HumanMessage(content="test")], purpose=purpose))
            try:
                await asyncio.wait_for(model.started.wait(), 3)
                saved = await store.get_run("local", run["run_id"])
                progress = [e for e in saved["events"] if e["type"] == "progress"]
                assert progress and progress[-1]["payload"]["name"] == purpose
                assert progress[-1]["payload"]["message"]
                assert not task.done()
            finally:
                model.release.set()
                await task
    asyncio.run(scenario())


def test_commentary_sse_replay_and_completed_run():

    async def scenario():
        async with execution(AIMessage(content="")) as (store, run, model, ctx):
            app = FastAPI()
            app.state.harness = SimpleNamespace(store=store, settings=ctx.settings)
            app.include_router(router)

            @app.post("/chat/stream")
            async def chat(body: ChatRequest, request: Request):
                return await compatibility_chat(body, request)

            await store.event(run["run_id"], "progress", {"worklog": {"hypotheses": ["PRIVATE"]}})
            await store.event(run["run_id"], "commentary", {"content": "자료를 확인하겠습니다."})
            await store.event(run["run_id"], "tool_started", {"name": "query_db"})
            await store.event(run["run_id"], "progress", {"name": "review", "message": "답변을 검증하고 있습니다."})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                active = (await client.get(f"/runs/{run['run_id']}")).json()
                assert active["status"] == "running"
                await store.finish(run["run_id"], run["epoch"], "completed", "최종 답변", "verified")
                body = {"session_id": "session", "run_id": run["run_id"], "query": "", "after_sequence": 0}
                first = await client.post("/chat/stream", json=body)
                replay = await client.post("/chat/stream", json=body)
                events = [json.loads(line[6:]) for line in first.text.splitlines() if line.startswith("data: ")]
                messages = [e for e in events if e["type"] == "commentary"]
                assert len(messages) == 1 and messages[0]["content"] == "자료를 확인하겠습니다."
                assert messages[0]["event_id"] in replay.text
                assert [e["type"] for e in events][-2:] == ["message", "stream_end"]
                assert "PRIVATE" not in first.text
                completed = (await client.get(f"/runs/{run['run_id']}")).json()
                assert completed['status'] == 'completed'
                assert completed['answer'] == '최종 답변'

    asyncio.run(scenario())

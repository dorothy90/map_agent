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
from harness.nodes import Nodes
from harness.router import compatibility_chat
from harness.store import HarnessStore
from harness.testing import initial_state
from harness.tools.registry import ToolRegistry
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


def test_public_commentary_precedes_tool_execution_and_excludes_reasoning():
    async def scenario():
        response = AIMessage(content=[{"type": "reasoning", "reasoning": "PRIVATE"},
            {"type": "text", "text": "조회 범위를 확인하겠습니다."}],
            additional_kwargs={"reasoning_content": "PRIVATE"},
            tool_calls=[{"id": "load-1", "name": "load_tools", "args": {"names": []}}])
        async with execution(response) as (store, run, model, ctx):
            node = Nodes(model, ToolRegistry(), ctx, "")
            state = initial_state(run, "자료 확인")
            update = await node.think(state)
            saved = await store.get_run("local", run["run_id"])
            commentary = [e for e in saved["events"] if e["type"] == "commentary"]
            assert len(commentary) == 1
            assert commentary[0]["payload"] == {"content": "조회 범위를 확인하겠습니다."}
            assert "PRIVATE" not in json.dumps(commentary)
            assert update["pending"] == response.tool_calls
            assert await store.calls.count_documents({"run_id": run["run_id"]}) == 0
            assert model.calls == 1
            # Replaying an already-returned tool-call message keeps one commentary.
            await node.think(state)
            saved = await store.get_run("local", run["run_id"])
            assert len([e for e in saved["events"] if e["type"] == "commentary"]) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("tool,content", [
    (None, "unverified final"), ("finish", "unverified final"),
    ("ask_user", "question"), ("load_tools", [{"type": "reasoning", "reasoning": "PRIVATE"}]),
])
def test_final_question_and_reasoning_are_not_commentary(tool, content):
    async def scenario():
        calls = [{"id": "c", "name": tool, "args": {}}] if tool else []
        async with execution(AIMessage(content=content, tool_calls=calls)) as (store, run, model, ctx):
            await Nodes(model, ToolRegistry(), ctx, "").think(initial_state(run, "자료 확인"))
            saved = await store.get_run("local", run["run_id"])
            assert not [e for e in saved["events"] if e["type"] == "commentary"]
    asyncio.run(scenario())


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


def test_commentary_sse_replay_and_completed_history():
    from agent_server import get_session_history

    async def scenario():
        async with execution(AIMessage(content="")) as (store, run, model, ctx):
            app = FastAPI()
            app.state.harness = SimpleNamespace(store=store, settings=ctx.settings)
            app.add_api_route("/session/{session_id}/history", get_session_history, methods=["GET"])

            @app.post("/chat/stream")
            async def chat(body: ChatRequest, request: Request):
                return await compatibility_chat(body, request)

            await store.event(run["run_id"], "progress", {"worklog": {"hypotheses": ["PRIVATE"]}})
            await store.event(run["run_id"], "commentary", {"content": "자료를 확인하겠습니다."})
            await store.event(run["run_id"], "tool_started", {"name": "query_yield"})
            await store.event(run["run_id"], "progress", {"name": "review", "message": "답변을 검증하고 있습니다."})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                # Active history is restored by SSE only, avoiding duplicate commentary.
                active = (await client.get("/session/session/history")).json()
                assert len(active["turns"]) == 1
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
                history = (await client.get("/session/session/history")).json()["turns"]
                assert [(t["role"], t.get("phase"), t["content"]) for t in history] == [
                    ("user", None, "자료 확인"), ("assistant", "commentary", "자료를 확인하겠습니다."),
                    ("assistant", None, "최종 답변")]
    asyncio.run(scenario())

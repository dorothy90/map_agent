import asyncio
import uuid
from dataclasses import replace


def test_scope_and_missing_calculation_lineage_are_rejected():
    from harness.completion import validate_evidence
    issues = validate_evidence({"answer": "전체 평균 90", "result_ids": ["r"], "scope": {"lotcd": "B"}},
        {"constraints": {"lotcd": "A"}}, {"r": {"result_id": "r", "status": "partial", "scope": {"lotcd": "B"}, "rows": [{"value": 90}], "source_result_ids": ["missing"]}})
    assert {"scope_mismatch", "missing_lineage"}.issubset(i["code"] for i in issues)


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


def test_recovery_ignores_mcp_and_recovers_durable_steer_once():
    from langgraph.checkpoint.memory import InMemorySaver
    from harness.store import HarnessStore
    from harness.config import Settings
    from harness.control import RunController
    from harness.testing import ScriptedModel
    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        service = RunController(store, Settings(), InMemorySaver(), model_factory=lambda: ScriptedModel([{"finish": {"answer": "수정 반영"}}]))
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
            await store.claim_invocation(run["run_id"], "call", "export_report", {}, run["epoch"])
            obs = await store.put_result("p", "s", {"run_id": run["run_id"], "invocation_id": "call", "tool_name": "export_report"}, [], [{"title": "test", "mime": "text/plain", "data": "saved"}])
            await store.finish_invocation(run["run_id"], "call", obs, run["epoch"])
            executor = ToolExecutor(domain_registry(), ExecutionContext(store, Settings(), run))
            for _ in range(2):
                await executor.execute({"name": "export_report", "id": "call", "args": {}})
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
            registry.tools["query_lot_history"].handler = work
            context = ExecutionContext(store, Settings(), run)
            await asyncio.gather(*(ToolExecutor(registry, replace(context, depth=1, namespace=f"child_{i}_")).execute(
                {"name": "query_lot_history", "id": "read", "args": {"lot_ids": [str(i)]}}) for i in range(4)))
            assert peak == 2
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_reconnected_stream_does_not_replay_answered_question():
    import httpx
    from fastapi import FastAPI, Request
    from langgraph.checkpoint.memory import InMemorySaver
    from harness.config import Settings
    from harness.control import RunController
    from harness.router import compatibility_chat
    from harness.store import HarnessStore
    from harness.testing import ScriptedModel
    from models import ChatRequest
    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        model = ScriptedModel([{"tool": "ask_user", "arguments": {"message": "제품 정보", "fields": [{"slot": "lotcd"}]}}, {"finish": {"answer": "답변 완료"}}])
        service = RunController(store, Settings(), InMemorySaver(), model_factory=lambda: model)
        app = FastAPI()
        app.state.harness = service
        @app.post("/chat/stream")
        async def chat(body: ChatRequest, request: Request):
            return await compatibility_chat(body, request)
        try:
            await store.setup()
            run = await service.start("local", "s", "r", "분석")
            await service.tasks[run["run_id"]]
            paused = await store.get_run("local", run["run_id"])
            await service.submit_input("local", run["run_id"], {"kind": "input", "request_id": "answer", "goal_revision": 1, "interrupt_id": paused["question"]["interrupt_id"], "value": {"lotcd": "B"}})
            await service.tasks[run["run_id"]]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/chat/stream", json={"session_id": "s", "run_id": run["run_id"], "query": "", "after_sequence": 0})
                assert response.status_code == 200
                assert '"type": "interrupt"' not in response.text
                assert '"type": "stream_end"' in response.text
            assert (await store.get_run("local", run["run_id"]))["user_inputs"][0]["value"] == {"lotcd": "B"}
        finally:
            await service.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())

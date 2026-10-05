import asyncio
import json
import uuid

from langchain_core.messages import AIMessage, ToolMessage, HumanMessage


def test_compaction_keeps_native_tool_pairs_and_result_references():
    from harness.testing import run_scripted
    from harness.context import message_groups
    messages = []
    for i in range(30):
        messages.extend([HumanMessage(content="질문 " + str(i)),
            AIMessage(content="", tool_calls=[{"id": str(i), "name": "read_result", "args": {"result_id": str(i)}}]),
            ToolMessage(content=json.dumps({"result_id": str(i), "rows": "자료" * 200}), tool_call_id=str(i))])
    assert all(group[0].type == "ai" for group in message_groups(messages) if any(m.type == "tool" for m in group))
    report = asyncio.run(run_scripted(query="안내", messages=messages, settings={"context_tokens": 4096},
        script=[{"finish": {"answer": "안내"}}], tool_results={}))
    assert report["summary"]
    assert len(report["messages"]) < len(messages)
    for group in message_groups(report["messages"]):
        tools = [m for m in group if m.type == "tool"]
        if tools:
            assert {m.tool_call_id for m in tools} == {c["id"] for c in group[0].tool_calls}


def test_thirty_turn_old_result_remains_retrievable_only_in_own_session():
    from harness.store import HarnessStore
    from harness.tools.python_tools import recall_session, RecallInput
    from harness.executor import ExecutionContext
    from harness.config import Settings

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        try:
            await store.setup()
            for i in range(30):
                run = await store.start_run("p", "s", str(i), f"질문 {i}")
                run = await store.acquire(run["run_id"], "worker")
                if i == 0:
                    obs = await store.put_result("p", "s", {"run_id": run["run_id"], "invocation_id": "first", "tool_name": "query_yield", "scope": {"lotcd": "DEMO"}}, [{"n": n} for n in range(120)], [])
                    await store.runs.update_one({"_id": run["run_id"]}, {"$set": {"observations": [obs.model_dump(mode="json")]}})
                await store.finish(run["run_id"], run["epoch"], "completed", f"답변 {i}", "test")
            run = await store.start_run("p", "s", "31", "첫 자료 재조회")
            ctx = ExecutionContext(store, Settings(), run)
            old = await recall_session(RecallInput(offset=29, limit=1), ctx)
            assert old.rows[0]["query"] == "질문 0"
            result_id = old.rows[0]["results"][0]["result_id"]
            assert len(await store.rows("p", "s", result_id)) == 120
            import pytest
            with pytest.raises(PermissionError):
                await store.rows("p", "another", result_id)
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_context_preserves_previous_result_summary_and_artifact_references():
    from harness.context import build_context
    from harness.types import ToolObservation
    obs = ToolObservation(principal_id="p", session_id="s", run_id="old", invocation_id="i", result_id="r", tool_name="query",
        summary="이전 조회의 요약", total_rows=14, artifact_refs=[{"artifact_id": "a", "title": "표"}], provenance={"data_origin": "fixture"})
    metadata = json.loads(build_context({"goal": {}, "observations": [obs.model_dump(mode="json")]}, "")[1].content)
    result = metadata["result_index"][0]
    assert result["summary"] == obs.summary
    assert result["total_rows"] == 14
    assert result["artifact_refs"] == obs.artifact_refs
    assert result["data_origin"] == "fixture"


def test_near_budget_finishes_instead_of_spending_remaining_tokens_on_compaction():
    from types import SimpleNamespace
    from harness.config import Settings
    from harness.nodes import Nodes
    from harness.testing import ScriptedModel
    from harness.tools.registry import ToolRegistry

    async def scenario():
        calls = []
        async def check():
            return {"usage": {"tokens": 87000, "models": 8, "tools": 3}}
        async def model_call(model, messages, *, final=False, **kwargs):
            calls.append(final)
            if not final:
                raise AssertionError("No exploration budget for compaction")
            return AIMessage(content="", tool_calls=[{"name": "finish", "args": {"answer": "안내"}, "id": "finish"}])
        context = SimpleNamespace(settings=Settings(), check=check, model_call=model_call)
        nodes = Nodes(ScriptedModel([]), ToolRegistry(), context, "")
        history = [*(HumanMessage(content="이전 대화 " * 1000) for _ in range(10)), HumanMessage(content="현재 질문")]
        result = await nodes.think({"goal": {}, "messages": history, "observations": []})
        assert calls == [True]
        assert result["pending"][0]["name"] == "finish"
    asyncio.run(scenario())


def test_finish_argument_feedback_survives_finalizer_context_rebuild():
    from types import SimpleNamespace
    from harness.config import Settings
    from harness.nodes import Nodes
    from harness.testing import ScriptedModel
    from harness.tools.registry import ToolRegistry

    async def scenario():
        captured = []
        async def check():
            return {"usage": {"tokens": 95000, "models": 8, "tools": 24}}
        async def model_call(model, messages, *, final=False, **kwargs):
            captured.append((final, messages))
            return AIMessage(content="", tool_calls=[{"name": "finish", "args": {"answer": "안내"}, "id": "fixed"}])
        nodes = Nodes(ScriptedModel([]), ToolRegistry(), SimpleNamespace(settings=Settings(), check=check, model_call=model_call), "")
        state = {"goal": {}, "messages": [HumanMessage(content="자료를 골라줘"), AIMessage(content="두 번째 자료는 DEMO 8월입니다."), HumanMessage(content="두 번째 것으로")], "observations": [], "pending": [{"id": "bad", "name": "finish", "args": {
            "answer": ""}}]}
        state.update(await nodes.execute(state))
        await nodes.think(state)
        assert captured[0][0] is True
        metadata = json.loads(next(m.content for m in captured[0][1]
            if m.additional_kwargs.get('input_section') == 'context'))
        assert metadata["validation_issues"][0]["code"] == "missing_answer"
        assert any(m.type == "ai" and "DEMO 8월" in m.content for m in captured[0][1])
    asyncio.run(scenario())

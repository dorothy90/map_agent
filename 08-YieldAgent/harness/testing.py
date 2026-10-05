"""Scripted providers for testing the real graph. Never used by the server."""
import json
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from .config import Settings
from .executor import ExecutionContext
from .graph import build_harness
from .instructions import load_instructions
from .store import HarnessStore
from .tools.registry import ToolResult, domain_registry


class ScriptedModel:
    def __init__(self, script):
        self.script = iter(script)
        self.messages = []
        self.calls = 0

    def bind_tools(self, tools, **kwargs):
        parent = self
        class Bound:
            async def ainvoke(self, messages):
                if kwargs.get("tool_choice") == "submit_verdict":
                    return AIMessage(content="", tool_calls=[{"name": "submit_verdict", "args": {"accepted": True, "complete": True, "issues": []}, "id": str(uuid.uuid4()), "type": "tool_call"}])
                parent.calls += 1
                parent.messages.extend(m.model_dump() for m in messages if m.type == "tool")
                item = next(parent.script)
                name = item.get("tool", "finish")
                args = item.get("arguments", item.get("finish", {}))
                return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": str(uuid.uuid4()), "type": "tool_call"}])
        return Bound()

    async def ainvoke(self, messages):
        return AIMessage(content=json.dumps({"accepted": True, "complete": True, "issues": []}))


async def setup_case(script, tool_results, settings=None):
    store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
    await store.setup()
    run = await store.start_run("test", "session", "request", "test")
    run = await store.acquire(run["run_id"], "test-worker")
    registry = domain_registry()
    for name, result in tool_results.items():
        async def handler(args, context, result=result):
            return ToolResult(**result, data_origin="fixture")
        registry.tools[name].handler = handler
    model = ScriptedModel(script)
    context = ExecutionContext(store, Settings(**(settings or {})), run)
    graph = build_harness(model=model, registry=registry, store=store, checkpointer=InMemorySaver(), control=context, instructions=load_instructions())
    return store, run, model, graph


def initial_state(run, query):
    return {"run_id": run["run_id"], "principal_id": run["principal_id"], "session_id": run["session_id"],
        "goal": {"original_request": query, "revision": 1, "acceptance_items": [], "constraints": {}},
        "messages": [HumanMessage(content=query)], "observations": [], "status": "running", "pending": [], "question": {}, "candidate": {}}


async def run_scripted(*, query, script, tool_results, settings=None, messages=None):
    store, run, model, graph = await setup_case(script, tool_results, settings)
    try:
        state = initial_state(run, query)
        if messages:
            state["messages"] = [*messages, *state["messages"]]
        result = await graph.ainvoke(state, {"configurable": {"thread_id": run["run_id"]}, "recursion_limit": 160})
        calls = await store.calls.find({"run_id": run["run_id"]}).sort("_id", 1).to_list(length=None)
        return {**result, "tool_calls": [{"name": c["tool_name"]} for c in calls], "model_observations": model.messages}
    finally:
        await store.client.drop_database(store.db.name)
        store.client.close()


async def interrupt_roundtrip():
    store, run, model, graph = await setup_case([
        {"tool": "ask_user", "arguments": {"message": "제품을 알려주세요", "fields": [{"slot": "lotcd", "type": "string", "label": "제품"}]}},
        {"finish": {"answer": "안내 완료"}},
    ], {})
    try:
        config = {"configurable": {"thread_id": run["run_id"]}, "recursion_limit": 160}
        paused = await graph.ainvoke(initial_state(run, "제품 확인"), config)
        calls = model.calls
        resumed = await graph.ainvoke(Command(resume={"lotcd": "4SS"}), config)
        return {"questions": len(paused["__interrupt__"]), "model_calls_before_resume": calls, "answer": resumed["answer"]}
    finally:
        await store.client.drop_database(store.db.name)
        store.client.close()

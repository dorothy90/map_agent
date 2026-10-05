import asyncio
import uuid

import pytest


def test_lost_owner_cannot_publish_terminal_node_state():
    from harness.nodes import Nodes
    from harness.store import LeaseLost
    with pytest.raises(LeaseLost):
        Nodes.stopped(None, {}, LeaseLost("lost"))


def test_epoch_snapshot_preserves_interrupt_and_isolates_late_writes():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import interrupt, Command
    from harness.checkpoints import epoch_config

    async def scenario():
        saver = InMemorySaver()
        graph = StateGraph(dict)
        graph.add_node("ask", lambda state: {"answer": interrupt("value?")})
        graph.add_edge(START, "ask")
        graph.add_edge("ask", END)
        compiled = graph.compile(checkpointer=saver)
        first = await epoch_config(saver, "r", 1)
        await compiled.ainvoke({}, first)
        second = await epoch_config(saver, "r", 2)
        # The old owner can finish after takeover; its checkpoint is isolated.
        await compiled.ainvoke(Command(resume="stale"), first)
        result = await compiled.ainvoke(Command(resume="current"), second)
        assert result["answer"] == "current"
        assert (await compiled.aget_state(first)).values["answer"] == "stale"
        third = await epoch_config(saver, "r", 3)
        assert (await compiled.aget_state(third)).values["answer"] == "current"
    asyncio.run(scenario())


def test_metadata_ack_loss_preserves_published_data(monkeypatch):
    from harness.store import HarnessStore
    from pymongo.errors import AutoReconnect

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        try:
            await store.setup()
            insert = store.results.insert_one
            async def acknowledged_on_server_only(document):
                await insert(document)
                raise AutoReconnect("lost insert acknowledgement")
            monkeypatch.setattr(store.results, "insert_one", acknowledged_on_server_only)
            obs = {"run_id": "r", "invocation_id": "i", "tool_name": "query"}
            try:
                await store.put_result("p", "s", obs, [{"n": 7}], [])
            except AutoReconnect:
                pass
            retry = await store.put_result("p", "s", obs, [{"n": 7}], [])
            assert await store.rows("p", "s", retry.result_id) == [{"n": 7}]
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_interrupted_clone_is_not_visible_as_recovery_source(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import interrupt, Command
    from harness.checkpoints import epoch_config

    async def scenario():
        saver = InMemorySaver()
        graph = StateGraph(dict)
        graph.add_node("ask", lambda state: {"answer": interrupt("value?")})
        graph.add_edge(START, "ask")
        graph.add_edge("ask", END)
        compiled = graph.compile(checkpointer=saver)
        await compiled.ainvoke({}, await epoch_config(saver, "r", 1))
        write = saver.aput_writes
        async def crash(*args, **kwargs):
            raise ConnectionError("clone interrupted")
        monkeypatch.setattr(saver, "aput_writes", crash)
        with pytest.raises(ConnectionError):
            await epoch_config(saver, "r", 2)
        assert await saver.aget_tuple({"configurable": {"thread_id": "harness:r:epoch:2"}}) is None
        monkeypatch.setattr(saver, "aput_writes", write)
        third = await epoch_config(saver, "r", 3)
        assert (await compiled.ainvoke(Command(resume="recovered"), third))["answer"] == "recovered"
    asyncio.run(scenario())

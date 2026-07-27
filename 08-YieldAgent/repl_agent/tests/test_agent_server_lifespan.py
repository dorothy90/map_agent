import asyncio
from types import SimpleNamespace

import agent_server
import wiki_queue as wiki_queue_module


def test_lifespan_closes_repl_sessions_before_mongo(monkeypatch):
    calls: list[str] = []

    class FakeMotorClient:
        def __getitem__(self, name):
            return object()

        def close(self):
            calls.append("mongo.close")

    class FakeWikiQueue:
        def set_summarizer(self, _summarizer):
            pass

        async def start(self):
            pass

        async def stop(self, timeout):
            assert timeout == 10
            calls.append("wiki.stop")

    class FakeCheckpointerContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    monkeypatch.setenv("WIKI_LINT_CRON_HOURS", "0")
    monkeypatch.setattr(agent_server, "AsyncIOMotorClient", lambda _uri: FakeMotorClient())
    monkeypatch.setattr(
        agent_server.MongoDBSaver,
        "from_conn_string",
        lambda *_args, **_kwargs: FakeCheckpointerContext(),
    )
    monkeypatch.setattr(
        agent_server.workflow,
        "compile",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(wiki_queue_module, "wiki_queue", FakeWikiQueue())
    monkeypatch.setattr(
        agent_server,
        "close_all_sessions",
        lambda: calls.append("repl.close_all"),
        raising=False,
    )

    async def exercise_lifespan():
        async with agent_server.lifespan(SimpleNamespace(state=SimpleNamespace())):
            assert calls == []

    asyncio.run(exercise_lifespan())

    assert calls == ["wiki.stop", "repl.close_all", "mongo.close"]

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.no_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_wiki_summarizer_uses_context_aware_observations():
    import wiki_summarizer

    tree = ast.parse(Path(wiki_summarizer.__file__).read_text(encoding="utf-8"))
    raw_observed = []
    privacy_observed = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(
                decorator.func, ast.Name
            ):
                continue
            if decorator.func.id == "observe":
                raw_observed.append(node.name)
            elif decorator.func.id == "observe_with_privacy":
                privacy_observed.append(node.name)

    assert raw_observed == []
    assert set(privacy_observed) == {
        "summarize",
        "synthesize_concept",
        "synthesize_super_concept",
        "synthesize_concept_from_docs",
    }


@pytest.mark.anyio
async def test_queue_propagates_private_context_and_resets_between_items(monkeypatch):
    import lf_utils
    import wiki_queue

    observed_payloads = []
    callback_lists = []
    persisted_contexts = []
    materialized_contexts = []

    def fake_observe(**options):
        def decorate(function):
            def observed(payload):
                observed_payloads.append(payload["query"])
                return function(payload)

            return observed

        return decorate

    monkeypatch.setattr(lf_utils, "observe", fake_observe, raising=False)
    monkeypatch.setattr(lf_utils, "llm_trace_handler", lambda: "local-callback")
    monkeypatch.setattr(lf_utils, "_LFHandler", lambda **kwargs: "remote-callback")
    monkeypatch.setattr(
        lf_utils,
        "get_client",
        lambda: SimpleNamespace(
            get_current_trace_id=lambda: "trace-id",
            get_current_observation_id=lambda: "observation-id",
        ),
    )

    @lf_utils.observe_with_privacy(name="queue_test_summarizer")
    def summarize(payload):
        callback_lists.append((payload["query"], lf_utils.lf_callbacks()))
        return {
            "episode": {"query": payload["query"]},
            "concept_filters": {
                "product": payload["query"],
                "fail_type": "IOFF",
                "cause_oper": "PT1H",
            },
        }

    def persist_episode(payload):
        persisted_contexts.append(
            (payload["query"], lf_utils.lf_capture_disabled())
        )
        return payload["query"], "created"

    def materialize():
        materialized_contexts.append(lf_utils.lf_capture_disabled())

    def persist_concept(filters, source_episode_id, links):
        wiki_queue.wiki_store.materialize_obsidian_wiki()
        return filters["product"], "created"

    monkeypatch.setattr(wiki_queue.wiki_store, "upsert_episode", persist_episode)
    monkeypatch.setattr(wiki_queue.wiki_store, "upsert_concept", persist_concept)
    monkeypatch.setattr(
        wiki_queue.wiki_store, "materialize_obsidian_wiki", materialize
    )

    queue = wiki_queue.WikiQueue(summarize_fn=summarize, max_retry=1)
    await queue.start()
    try:
        assert queue.summarize_enqueue(
            {"query": "PRIVATE_QUEUE_SENTINEL", "raw_results": [{}]},
            private=True,
        ) == "queued"
        assert queue.summarize_enqueue(
            {"query": "NON_PRIVATE_QUEUE_VALUE", "raw_results": [{}]},
            private=False,
        ) == "queued"
        assert queue._summarize_q is not None
        assert queue._persist_q is not None
        await queue._summarize_q.join()
        await queue._persist_q.join()
    finally:
        await queue.stop(timeout=2)

    assert callback_lists == [
        ("PRIVATE_QUEUE_SENTINEL", []),
        ("NON_PRIVATE_QUEUE_VALUE", ["local-callback", "remote-callback"]),
    ]
    assert observed_payloads == ["NON_PRIVATE_QUEUE_VALUE"]
    assert persisted_contexts == [
        ("PRIVATE_QUEUE_SENTINEL", True),
        ("NON_PRIVATE_QUEUE_VALUE", False),
    ]
    assert materialized_contexts == [True, False]
    assert lf_utils.lf_callbacks() == ["local-callback", "remote-callback"]

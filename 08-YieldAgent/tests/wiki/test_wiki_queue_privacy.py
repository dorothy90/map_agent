from __future__ import annotations

import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace

import frontmatter
import pytest


pytestmark = pytest.mark.no_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _configure_test_vault(monkeypatch, tmp_path, wiki_store):
    from wiki_config import initialize_wiki_vault, resolve_wiki_paths

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    monkeypatch.setattr(wiki_store, "_PATHS", paths)
    monkeypatch.setattr(wiki_store, "_VAULT", paths.root)
    monkeypatch.setattr(wiki_store, "_EPISODES", paths.episodes)
    monkeypatch.setattr(wiki_store, "_CONCEPTS", paths.concepts)
    monkeypatch.setattr(wiki_store, "_ALIASES", paths.aliases)
    monkeypatch.setattr(wiki_store, "_SUPER_CONCEPTS", paths.super_concepts)
    monkeypatch.setattr(wiki_store, "_LOG", paths.log)
    monkeypatch.setattr(wiki_store, "_INDEX", paths.index)
    monkeypatch.setattr(wiki_store, "materialize_obsidian_wiki", lambda: None)
    return paths


async def _drain_queue(queue):
    assert queue._summarize_q is not None
    assert queue._persist_q is not None
    await queue._summarize_q.join()
    await queue._persist_q.join()
    await queue._summarize_q.join()
    await queue._persist_q.join()


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


def test_episode_privacy_is_explicit_and_sticky_on_dedup(monkeypatch, tmp_path):
    import wiki_store

    paths = _configure_test_vault(monkeypatch, tmp_path, wiki_store)
    payload = {
        "query": "same episode",
        "filters": {
            "product": "4SS",
            "fail_type": "IOFF",
            "cause_oper": "PT1H",
        },
        "doc_ids": ["FH-1"],
        "source_files": ["FH-1.pptx"],
        "source_files_aligned": True,
        "body": "body",
    }

    episode_id, status = wiki_store.upsert_episode({**payload, "private": False})
    assert status == "created"
    path = paths.episodes / f"{episode_id}.md"
    created = frontmatter.load(path)
    assert created.metadata["private"] is False
    assert created.metadata["source_files"] == ["FH-1.pptx"]
    assert created.metadata["source_files_aligned"] is True

    duplicate_id, status = wiki_store.upsert_episode({**payload, "private": True})
    assert (duplicate_id, status) == (episode_id, "skipped")
    assert frontmatter.load(path).metadata["private"] is True

    wiki_store.upsert_episode({**payload, "private": False})
    assert frontmatter.load(path).metadata["private"] is True


@pytest.mark.anyio
async def test_synthesis_checks_privacy_beyond_episode_content_limit(
    monkeypatch, tmp_path
):
    import wiki_queue

    _configure_test_vault(monkeypatch, tmp_path, wiki_queue.wiki_store)
    filters = {
        "product": "4SS",
        "fail_type": "IOFF",
        "cause_oper": "PT1H",
    }
    concept_id = None
    for index in range(11):
        episode_id, _ = wiki_queue.wiki_store.upsert_episode(
            {
                "query": f"episode {index}",
                "filters": filters,
                "doc_ids": [f"FH-{index}"],
                "private": index == 10,
            }
        )
        concept_id, _ = wiki_queue.wiki_store.upsert_concept(
            filters, f"episode:{episode_id}"
        )

    queue = wiki_queue.WikiQueue(max_retry=1)
    queue._summarize_q = asyncio.Queue()
    await queue._maybe_trigger_synthesis(
        f"concept:{concept_id}", filters, private=False
    )

    item = queue._summarize_q.get_nowait()
    assert len(item["episodes"]) == 10
    assert item["private"] is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("first_private", "make_first_legacy", "expected_synthesis_private"),
    [
        pytest.param(True, False, True, id="mixed-private-then-public"),
        pytest.param(False, False, False, id="all-public"),
        pytest.param(False, True, False, id="legacy-missing-field-is-public"),
    ],
)
async def test_synthesis_privacy_uses_all_persisted_source_episodes(
    monkeypatch,
    tmp_path,
    first_private,
    make_first_legacy,
    expected_synthesis_private,
):
    import lf_utils
    import wiki_queue
    import wiki_summarizer

    paths = _configure_test_vault(monkeypatch, tmp_path, wiki_queue.wiki_store)
    observed_concepts = []
    synthesis_contexts = []
    synthesis_persist_contexts = []

    original_upsert_concept = wiki_queue.wiki_store.upsert_concept

    def upsert_concept(*args, **kwargs):
        if kwargs.get("synthesized_body") is not None:
            synthesis_persist_contexts.append(lf_utils.lf_capture_disabled())
            if len(synthesis_persist_contexts) == 1:
                raise RuntimeError("retry synthesis persistence")
        return original_upsert_concept(*args, **kwargs)

    async def no_retry_delay(_delay):
        return None

    monkeypatch.setattr(wiki_queue.wiki_store, "upsert_concept", upsert_concept)
    monkeypatch.setattr(wiki_queue.asyncio, "sleep", no_retry_delay)

    def fake_observe(**options):
        def decorate(function):
            def observed(*args, **kwargs):
                observed_concepts.append(args[0])
                return function(*args, **kwargs)

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

    @lf_utils.observe_with_privacy(name="mixed_source_synthesis")
    def synthesize_concept(concept_id, episodes):
        synthesis_contexts.append(
            (lf_utils.lf_capture_disabled(), lf_utils.lf_callbacks())
        )
        return SimpleNamespace(
            body_markdown="combined",
            confidence=0.8,
            citations=[],
        )

    monkeypatch.setattr(wiki_summarizer, "synthesize_concept", synthesize_concept)

    filters = {
        "product": "4SS",
        "fail_type": "IOFF",
        "cause_oper": "PT1H",
    }

    def summarize(payload):
        return {
            "episode": {
                "query": payload["query"],
                "filters": filters,
                "doc_ids": [payload["doc_id"]],
                "body": "episode body",
            },
            "concept_filters": filters,
        }

    queue = wiki_queue.WikiQueue(summarize_fn=summarize, max_retry=2)
    await queue.start()
    try:
        assert queue.summarize_enqueue(
            {"query": "first", "doc_id": "FH-1"}, private=first_private
        ) == "queued"
        await _drain_queue(queue)

        first_path = next(paths.episodes.glob("*.md"))
        if make_first_legacy:
            first_post = frontmatter.load(first_path)
            del first_post.metadata["private"]
            wiki_queue.wiki_store._write(first_path, first_post)

        assert queue.summarize_enqueue(
            {"query": "second", "doc_id": "FH-2"}, private=False
        ) == "queued"
        await _drain_queue(queue)
    finally:
        await queue.stop(timeout=2)

    assert synthesis_contexts == [
        (
            expected_synthesis_private,
            []
            if expected_synthesis_private
            else ["local-callback", "remote-callback"],
        )
    ]
    expected_observed = [] if expected_synthesis_private else ["concept:4SS|PT1H|IOFF"]
    assert observed_concepts == expected_observed
    assert synthesis_persist_contexts == [
        expected_synthesis_private,
        expected_synthesis_private,
    ]

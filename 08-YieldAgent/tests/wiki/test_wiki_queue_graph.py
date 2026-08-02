from __future__ import annotations

import asyncio
from types import SimpleNamespace

import frontmatter
import pytest

from wiki_graph_models import EntityCandidate, RelationCandidate
from wiki_summarizer import EpisodeRef
from wiki_sync import build_triple_snapshot, make_triple_key


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
    return paths


@pytest.mark.anyio
async def test_background_synthesis_preserves_graph_through_retry_and_materialization(
    monkeypatch, tmp_path
):
    import lf_utils
    import wiki_queue
    import wiki_summarizer

    paths = _configure_test_vault(monkeypatch, tmp_path, wiki_queue.wiki_store)
    filters = {
        "product": "4SS",
        "fail_type": "EASY",
        "cause_oper": "PRE METAL CLN",
    }
    citations = [
        {
            "episode_id": "episode:one",
            "doc_id": "FH-1",
            "source_file": "FH-1.pptx",
        }
    ]
    entities = [
        {"canonical_name": "Queue time exceeded", "entity_type": "condition"},
        {"canonical_name": "Natural oxidation", "entity_type": "mechanism"},
    ]
    relations = [
        {
            "subject": "Queue time exceeded",
            "predicate": "causes",
            "object": "Natural oxidation",
            "confidence": 0.82,
            "source_doc_ids": ["FH-1"],
        }
    ]
    wiki_queue.wiki_store.upsert_concept(
        filters,
        synthesized_body="previous synthesis",
        confidence=0.7,
        citations=citations,
        entities=entities,
        relations=relations,
    )
    relation_path = next(paths.relations.glob("*.md"))
    assert frontmatter.load(relation_path).metadata["status"] == "active"

    synthesis_contexts = []

    def synthesize_concept(concept_id, episodes):
        synthesis_contexts.append(lf_utils.lf_capture_disabled())
        return SimpleNamespace(
            body_markdown="current synthesis",
            confidence=0.9,
            citations=[EpisodeRef(**citations[0])],
            entities=[EntityCandidate(**entity) for entity in entities],
            relations=[RelationCandidate(**relation) for relation in relations],
        )

    monkeypatch.setattr(wiki_summarizer, "synthesize_concept", synthesize_concept)

    persist_contexts = []
    original_upsert_concept = wiki_queue.wiki_store.upsert_concept

    def fail_first_graph_persist(*args, **kwargs):
        if kwargs.get("synthesized_body") is not None:
            persist_contexts.append(lf_utils.lf_capture_disabled())
            if len(persist_contexts) == 1:
                raise RuntimeError("retry graph persistence")
        return original_upsert_concept(*args, **kwargs)

    async def no_retry_delay(_delay):
        return None

    monkeypatch.setattr(
        wiki_queue.wiki_store, "upsert_concept", fail_first_graph_persist
    )
    monkeypatch.setattr(wiki_queue.asyncio, "sleep", no_retry_delay)

    queue = wiki_queue.WikiQueue(max_retry=2)
    await queue.start()
    try:
        assert queue._summarize_q is not None
        synthesis_episodes = [
            {
                "id": "episode:one",
                "frontmatter": {"doc_ids": ["FH-1", "FH-1"]},
            },
            {
                "id": "episode:two",
                "frontmatter": {"doc_ids": ["FH-2"]},
            },
        ]
        queue._summarize_q.put_nowait(
            {
                "task_type": "concept_synthesis",
                "concept_id": "concept:4SS|PRE METAL CLN|EASY",
                "filters": filters,
                "episodes": synthesis_episodes,
                "evidence": {"score": 0.8, "unique_doc_ids": 2},
                "private": True,
            }
        )
        await queue._summarize_q.join()
        assert queue._persist_q is not None
        await queue._persist_q.join()
    finally:
        await queue.stop(timeout=2)

    concept_post = frontmatter.load(next(paths.concepts.glob("*.md")))
    assert concept_post.metadata["entities"] == entities
    assert concept_post.metadata["relations"] == relations
    assert concept_post.metadata["body_versions"][-1]["entities"] == entities
    assert concept_post.metadata["body_versions"][-1]["relations"] == relations
    relation_post = frontmatter.load(relation_path)
    assert relation_post.metadata["status"] == "active"
    expected_fingerprint = build_triple_snapshot(
        make_triple_key("4SS", "EASY", "PRE METAL CLN"),
        [{"doc_id": "FH-1"}, {"doc_id": "FH-2"}],
    ).source_fingerprint
    assert concept_post.metadata["source_fingerprint"] == expected_fingerprint
    assert relation_post.metadata["source_fingerprint"] == expected_fingerprint

    from wiki_graph_projection import build_graph_projection

    graph_context = build_graph_projection(paths).expand_concepts(
        ["concept:4SS|PRE METAL CLN|EASY"]
    )
    assert graph_context.primary_concept_id == "concept:4SS|PRE METAL CLN|EASY"
    assert [relation.relation_id for relation in graph_context.relations] == [
        relation_post.metadata["id"]
    ]
    assert synthesis_contexts == [True]
    assert persist_contexts == [True, True]

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
            body_markdown="current synthesis [FH-1]",
            confidence=0.9,
            citations=[
                EpisodeRef(
                    episode_id="episode:forged",
                    doc_id="FH-1",
                    source_file="FORGED.pptx",
                    date="2099-12-31",
                    natural_label="FORGED LABEL",
                    download_url="https://evil.example/FORGED.pptx",
                ),
                EpisodeRef(episode_id="episode:ambiguous", doc_id="FH-A"),
                EpisodeRef(episode_id="episode:ambiguous", doc_id="FH-B"),
                EpisodeRef(episode_id="episode:forged", doc_id="FH-FORGED"),
            ],
            entities=[EntityCandidate(**entity) for entity in entities],
            relations=[
                RelationCandidate(**relations[0]),
                RelationCandidate(
                    subject="Queue time exceeded",
                    predicate="contributes_to",
                    object="Natural oxidation",
                    confidence=0.72,
                    source_doc_ids=["FH-1", "FH-FORGED"],
                ),
                RelationCandidate(
                    subject="Queue time exceeded",
                    predicate="associated_with",
                    object="Natural oxidation",
                    confidence=0.62,
                    source_doc_ids=["FH-FORGED"],
                ),
            ],
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
                    "frontmatter": {
                        "doc_ids": ["FH-1", "FH-1"],
                        "source_files": ["FH-1-AUTH.pptx", "FH-1-AUTH.pptx"],
                        "source_files_aligned": True,
                        "created": "2026-08-01T00:00:00+00:00",
                    },
            },
            {
                "id": "episode:two",
                "frontmatter": {"doc_ids": ["FH-2"]},
            },
            {
                "id": "episode:ambiguous",
                "frontmatter": {
                    "doc_ids": ["FH-A", "FH-B"],
                    "source_files": ["", "B.pptx"],
                    "source_files_aligned": True,
                },
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
    assert [citation["doc_id"] for citation in concept_post.metadata["citations"]] == [
        "FH-1",
        "FH-A",
        "FH-B",
    ]
    citation = concept_post.metadata["citations"][0]
    assert citation["episode_id"] == "one"
    assert citation["source_file"] == "FH-1-AUTH.pptx"
    assert citation["date"] == "2026-08-01"
    assert citation["natural_label"] == ""
    assert citation["download_url"] == ""
    assert concept_post.content.startswith("current synthesis [FH-1]\n")
    assert concept_post.metadata["relations"] == relations
    assert concept_post.metadata["body_versions"][-1]["entities"] == entities
    assert concept_post.metadata["body_versions"][-1]["relations"] == relations
    relation_post = frontmatter.load(relation_path)
    assert relation_post.metadata["status"] == "active"
    expected_fingerprint = build_triple_snapshot(
        make_triple_key("4SS", "EASY", "PRE METAL CLN"),
        [
            {"doc_id": "FH-1"},
            {"doc_id": "FH-2"},
            {"doc_id": "FH-A"},
            {"doc_id": "FH-B"},
        ],
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
    assert not (paths.sources / "FH-FORGED.md").exists()
    source_post = frontmatter.load(paths.sources / "FH-1.md")
    assert source_post.metadata["source_file"] == "FH-1-AUTH.pptx"
    assert source_post.metadata["date"] == "2026-08-01"
    assert "download_url" not in source_post.metadata
    assert "FORGED" not in source_post.content
    assert "evil.example" not in source_post.content
    sparse_a_source = frontmatter.load(paths.sources / "FH-A.md")
    sparse_b_source = frontmatter.load(paths.sources / "FH-B.md")
    assert "source_file" not in sparse_a_source.metadata
    assert "B.pptx" not in sparse_a_source.content
    assert sparse_b_source.metadata["source_file"] == "B.pptx"
    assert synthesis_contexts == [True]
    assert persist_contexts == [True, True]


@pytest.mark.anyio
async def test_background_synthesis_rejects_unsupported_body_and_preserves_concept(
    monkeypatch, tmp_path
):
    import wiki_queue
    import wiki_summarizer

    paths = _configure_test_vault(monkeypatch, tmp_path, wiki_queue.wiki_store)
    filters = {
        "product": "4SS",
        "fail_type": "EASY",
        "cause_oper": "PRE METAL CLN",
    }
    wiki_queue.wiki_store.upsert_concept(
        filters,
        synthesized_body="approved body [FH-REAL]",
        citations=[{"doc_id": "FH-REAL"}],
    )

    synthesis_calls = []

    def invalid_synthesis(*args):
        synthesis_calls.append(args)
        return SimpleNamespace(
            body_markdown="unsupported queue claim [FH-FORGED]",
            confidence=0.9,
            citations=[EpisodeRef(episode_id="episode:one", doc_id="FH-REAL")],
            entities=[],
            relations=[],
        )

    async def no_retry_delay(_delay):
        return None

    monkeypatch.setattr(wiki_summarizer, "synthesize_concept", invalid_synthesis)
    monkeypatch.setattr(wiki_queue.asyncio, "sleep", no_retry_delay)

    queue = wiki_queue.WikiQueue(max_retry=2)
    await queue.start()
    try:
        assert queue._summarize_q is not None
        queue._summarize_q.put_nowait(
            {
                "task_type": "concept_synthesis",
                "concept_id": "concept:4SS|PRE METAL CLN|EASY",
                "filters": filters,
                "episodes": [
                    {
                        "id": "episode:one",
                        "frontmatter": {
                            "doc_ids": ["FH-REAL"],
                            "source_files": ["real.pptx"],
                            "source_files_aligned": True,
                        },
                    }
                ],
                "evidence": {"score": 0.8, "unique_doc_ids": 1},
            }
        )
        await queue._summarize_q.join()
        assert queue._persist_q is not None
        await queue._persist_q.join()
    finally:
        await queue.stop(timeout=2)

    concept_post = frontmatter.load(next(paths.concepts.glob("*.md")))
    assert concept_post.content.startswith("approved body [FH-REAL]\n")
    assert "FH-FORGED" not in concept_post.content
    assert concept_post.metadata["version"] == 1
    assert queue.drops["synthesis"] == 1
    assert len(synthesis_calls) == 2

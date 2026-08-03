import hashlib
import json

import frontmatter
import pytest

from wiki_config import initialize_wiki_vault, resolve_wiki_paths


pytestmark = pytest.mark.no_server


def _paths(tmp_path):
    paths = resolve_wiki_paths(
        {"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")}
    )
    initialize_wiki_vault(paths)
    return paths


def _write_concept(paths, *, body="Generated body"):
    path = paths.concepts / "4SS_PRE_METAL_CLN_EASY.md"
    post = frontmatter.Post(
        content=body,
        id="concept:4SS|PRE METAL CLN|EASY",
        type="concept",
        product="4SS",
        fail_type="EASY",
        cause_oper="PRE METAL CLN",
        citations=[{"doc_id": "FH-1"}],
    )
    path.write_text(frontmatter.dumps(post), encoding="utf-8")
    return path


def test_stable_evidence_id_hides_raw_path():
    from wiki_evidence_enrichment import stable_evidence_id

    raw_id = "/private/uploads/company-deck.pptx_p1_0"
    first = stable_evidence_id("syld_gpt_2067627", raw_id)

    assert first.startswith("EVD-")
    assert len(first) == 24
    assert first == stable_evidence_id("syld_gpt_2067627", raw_id)
    assert "company" not in first


def test_read_concept_snapshots_filters_exact_triple(tmp_path):
    from wiki_evidence_enrichment import EvidenceSelector, read_concept_snapshots

    paths = _paths(tmp_path)
    _write_concept(paths)

    selected = read_concept_snapshots(
        paths,
        EvidenceSelector("4SS", "EASY", "PRE METAL CLN"),
    )

    assert [(item.product, item.fail_type, item.cause_oper) for item in selected] == [
        ("4SS", "EASY", "PRE METAL CLN")
    ]
    assert selected[0].file_sha256
    assert selected[0].semantic_sha256


def test_semantic_hash_ignores_materializer_block_but_file_hash_does_not(tmp_path):
    from wiki_evidence_enrichment import read_concept_snapshots

    paths = _paths(tmp_path)
    path = _write_concept(paths)
    before = read_concept_snapshots(paths)[0]
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n<!-- yield-wiki:knowledge-links:start -->\n"
        + "## Knowledge Links\n\n- Product: [[products/4SS|4SS]]\n"
        + "<!-- yield-wiki:knowledge-links:end -->\n",
        encoding="utf-8",
    )
    after = read_concept_snapshots(paths)[0]

    assert before.file_sha256 != after.file_sha256
    assert before.semantic_sha256 == after.semantic_sha256


def test_manifest_round_trip_excludes_sensitive_payloads(tmp_path):
    from wiki_evidence_enrichment import EvidenceManifestStore

    paths = _paths(tmp_path)
    store = EvidenceManifestStore(paths, paths.state_dir / "evidence-manifest.json")
    manifest = {
        "version": 1,
        "pairs": {
            "concept:4SS|PRE METAL CLN|EASY\0EVD-abc": {
                "concept_sha256": "a" * 64,
                "content_sha256": "b" * 64,
                "accepted": False,
                "confidence": 0.1,
                "relation": "supporting_context",
                "retrieval_model": "qwen/qwen3-embedding-8b",
                "judgment_model": "test-model",
            }
        },
    }

    store.save(manifest)

    raw = store.path.read_text(encoding="utf-8")
    assert "page_content" not in raw
    assert "/private/" not in raw
    assert store.load() == manifest


def test_manifest_rejects_unapproved_fields(tmp_path):
    from wiki_evidence_enrichment import EvidenceManifestStore

    paths = _paths(tmp_path)
    store = EvidenceManifestStore(paths, paths.state_dir / "evidence-manifest.json")
    with pytest.raises(ValueError, match="unapproved manifest fields"):
        store.save(
            {
                "version": 1,
                "pairs": {
                    "concept:x\0EVD-x": {
                        "concept_sha256": hashlib.sha256(b"c").hexdigest(),
                        "content_sha256": hashlib.sha256(b"d").hexdigest(),
                        "accepted": False,
                        "confidence": 0.0,
                        "relation": "supporting_context",
                        "retrieval_model": "embedding-model",
                        "judgment_model": "judge-model",
                        "page_content": "must not persist",
                    }
                },
            }
        )


def test_empty_manifest_is_versioned(tmp_path):
    from wiki_evidence_enrichment import EvidenceManifestStore

    paths = _paths(tmp_path)
    store = EvidenceManifestStore(paths, paths.state_dir / "evidence-manifest.json")
    assert store.load() == {"version": 1, "pairs": {}}
    assert not store.path.exists()


class _FakeIndices:
    def __init__(self, dimension=4096):
        self.dimension = dimension

    def get_mapping(self, *, index):
        return {
            index: {
                "mappings": {
                    "properties": {
                        "embedding": {
                            "type": "knn_vector",
                            "dimension": self.dimension,
                        }
                    }
                }
            }
        }


class _FakeOpenSearch:
    def __init__(self, *, dimension=4096, hits=None):
        self.indices = _FakeIndices(dimension)
        self.hits = list(hits or [])
        self.last_search = None

    def search(self, *, index, body):
        self.last_search = (index, body)
        return {"hits": {"hits": self.hits}}


def _concept_snapshot():
    from wiki_evidence_enrichment import ConceptEvidenceSnapshot

    return ConceptEvidenceSnapshot(
        path=None,
        concept_id="concept:4SS|PRE METAL CLN|EASY",
        product="4SS",
        fail_type="EASY",
        cause_oper="PRE METAL CLN",
        body="oxide leakage investigation",
        file_sha256="a" * 64,
        semantic_sha256="b" * 64,
    )


def test_retriever_validates_exact_index_and_vector_dimension():
    from wiki_evidence_enrichment import OpenSearchEvidenceRetriever

    client = _FakeOpenSearch()
    retriever = OpenSearchEvidenceRetriever(
        client,
        "syld_gpt_2067627",
        lambda _: [0.0] * 4096,
    )

    assert retriever.validate() == 4096
    with pytest.raises(ValueError, match="exact source index"):
        OpenSearchEvidenceRetriever(client, "syld_*", lambda _: [0.0] * 4096)
    with pytest.raises(ValueError, match="4096"):
        OpenSearchEvidenceRetriever(
            _FakeOpenSearch(dimension=1024),
            "syld_gpt_2067627",
            lambda _: [0.0] * 4096,
        ).validate()


def test_retriever_uses_knn_and_redacts_raw_id():
    from wiki_evidence_enrichment import OpenSearchEvidenceRetriever

    client = _FakeOpenSearch(
        hits=[
            {
                "_id": "/private/company.pptx_p1_0",
                "_score": 0.99,
                "_source": {
                    "page_content": "unrelated text",
                    "source_file": "/private/company.pptx",
                    "page_num": 1,
                    "download_url": "https://example.invalid/a",
                },
            }
        ]
    )
    retriever = OpenSearchEvidenceRetriever(
        client,
        "syld_gpt_2067627",
        lambda _: [0.0] * 4096,
    )

    candidate = retriever.search(_concept_snapshot())[0]

    index, body = client.last_search
    assert index == "syld_gpt_2067627"
    assert body["query"]["knn"]["embedding"]["vector"] == [0.0] * 4096
    assert body["_source"] == [
        "page_content",
        "source_file",
        "page_num",
        "download_url",
    ]
    assert candidate.source_file == "company.pptx"
    assert "/private/" not in repr(candidate)


def test_retriever_rejects_wrong_query_vector_dimension():
    from wiki_evidence_enrichment import OpenSearchEvidenceRetriever

    retriever = OpenSearchEvidenceRetriever(
        _FakeOpenSearch(),
        "syld_gpt_2067627",
        lambda _: [0.0] * 3,
    )
    with pytest.raises(ValueError, match="4096"):
        retriever.search(_concept_snapshot())


class _FakeStructuredChain:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return self.output


class _FakeStructuredLLM:
    def __init__(self, output):
        self.chain = _FakeStructuredChain(output)
        self.schemas = []

    def with_structured_output(self, schema, method):
        self.schemas.append((schema, method))
        return self.chain


def _candidate(doc_id="EVD-abc", content="unrelated programming project"):
    from wiki_evidence_enrichment import EvidenceCandidate

    return EvidenceCandidate(
        raw_id="/private/producer-path",
        doc_id=doc_id,
        page_content=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        source_file="deck.pptx",
        page_num=1,
        download_url="",
        score=0.99,
    )


def test_judge_batches_candidates_in_one_structured_call():
    from wiki_evidence_enrichment import (
        EvidenceDecision,
        EvidenceDecisionBatch,
        StructuredEvidenceJudge,
    )

    output = EvidenceDecisionBatch(
        decisions=[
            EvidenceDecision(
                doc_id="EVD-abc",
                relevant=False,
                confidence=0.99,
                relation="supporting_context",
                reason="The candidate is unrelated.",
            ),
            EvidenceDecision(
                doc_id="EVD-def",
                relevant=True,
                confidence=0.91,
                relation="possible_action",
                reason="The candidate contains related action evidence.",
            ),
        ]
    )
    llm = _FakeStructuredLLM(output)
    judge = StructuredEvidenceJudge(llm, "test-model")

    decisions = judge.decide_batch(
        _concept_snapshot(),
        (_candidate("EVD-abc"), _candidate("EVD-def", "related action")),
    )

    assert [decision.doc_id for decision in decisions] == ["EVD-abc", "EVD-def"]
    assert len(llm.chain.calls) == 1
    assert llm.schemas[0][1] == "function_calling"


@pytest.mark.parametrize(
    "decision_ids",
    [
        ["EVD-abc"],
        ["EVD-abc", "EVD-abc"],
        ["EVD-abc", "EVD-unknown"],
    ],
)
def test_judge_rejects_missing_duplicate_or_unknown_decision_ids(decision_ids):
    from wiki_evidence_enrichment import (
        EvidenceDecision,
        EvidenceDecisionBatch,
        StructuredEvidenceJudge,
    )

    output = EvidenceDecisionBatch(
        decisions=[
            EvidenceDecision(
                doc_id=doc_id,
                relevant=False,
                confidence=0.9,
                relation="supporting_context",
                reason="No grounded relationship.",
            )
            for doc_id in decision_ids
        ]
    )
    judge = StructuredEvidenceJudge(_FakeStructuredLLM(output), "test-model")

    with pytest.raises(ValueError, match="decision IDs"):
        judge.decide_batch(
            _concept_snapshot(),
            (_candidate("EVD-abc"), _candidate("EVD-def")),
        )

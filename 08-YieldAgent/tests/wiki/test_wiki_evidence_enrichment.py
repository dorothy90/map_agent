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

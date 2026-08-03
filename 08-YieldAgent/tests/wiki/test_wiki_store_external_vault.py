import json
import os
import subprocess
import sys
import hashlib
from pathlib import Path

import frontmatter
import pytest


pytestmark = pytest.mark.no_server


def test_store_writes_only_to_explicit_vault(tmp_path):
    vault = tmp_path / "YieldWiki"
    script = """
import json
import bootstrap_wiki_warmup
import wiki_store
import wiki_config

assert bootstrap_wiki_warmup._VAULT_PATH == wiki_store._VAULT
assert wiki_config.resolve_wiki_paths().root == wiki_store._VAULT

eid, status = wiki_store.upsert_episode({
    "query": "4SS EASY 이력",
    "filters": {"product": "4SS", "fail_type": "EASY", "cause_oper": "PRE METAL CLN"},
    "doc_ids": ["FH-1"],
    "body": "## 근거\\n\\n검증 본문",
    "summary": "검증",
})
print(json.dumps({"root": str(wiki_store._VAULT), "eid": eid, "status": status}))
"""
    env = {**os.environ, "WIKI_VAULT_PATH": str(vault)}
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout.strip())
    assert result["root"] == str(vault.resolve())
    assert result["status"] == "created"
    assert (vault / "episodes" / f"{result['eid']}.md").exists()
    assert (vault / "sources").is_dir()
    assert (vault / "reviews").is_dir()


def test_concept_graph_state_tracks_current_and_auditable_body_versions(tmp_path):
    vault = tmp_path / "YieldWiki"
    script = """
import json
import wiki_store
from wiki_graph_models import EntityCandidate, RelationCandidate

filters = {"product": "4SS", "fail_type": "EASY", "cause_oper": "PRE METAL CLN"}
first_entities = [
    EntityCandidate(
        canonical_name="Queue time 초과",
        entity_type="process_condition",
    ).model_dump(mode="json")
]
first_relations = [
    RelationCandidate(
        subject="Queue time 초과",
        predicate="causes",
        object="자연 산화",
        confidence=0.82,
        source_doc_ids=["FH-1"],
    ).model_dump(mode="json")
]
second_entities = [
    EntityCandidate(
        canonical_name="자연 산화",
        entity_type="failure_mechanism",
    ).model_dump(mode="json")
]
second_relations = [
    RelationCandidate(
        subject="세정",
        predicate="prevents",
        object="자연 산화",
        confidence=0.91,
        source_doc_ids=["FH-2"],
    ).model_dump(mode="json")
]

wiki_store.upsert_concept(
    filters=filters,
    synthesized_body="first body",
    confidence=0.8,
    citations=[{"doc_id": "FH-1"}],
    entities=first_entities,
    relations=first_relations,
    sync_metadata={"source_fingerprint": "sha256:one"},
    materialize=False,
)
wiki_store.upsert_concept(
    filters=filters,
    entities=second_entities,
    relations=second_relations,
    materialize=False,
)
unchanged = wiki_store.read_node("concept:4SS|PRE METAL CLN|EASY")["frontmatter"]
wiki_store.upsert_concept(
    filters=filters,
    synthesized_body="second body",
    confidence=0.9,
    citations=[{"doc_id": "FH-2"}],
    entities=second_entities,
    relations=second_relations,
    sync_metadata={"source_fingerprint": "sha256:two"},
    materialize=False,
)

node = wiki_store.read_node("concept:4SS|PRE METAL CLN|EASY")
print(json.dumps({"unchanged": unchanged, "current": node["frontmatter"]}, ensure_ascii=False))
"""
    env = {**os.environ, "WIKI_VAULT_PATH": str(vault)}

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout.strip())
    unchanged = result["unchanged"]
    assert unchanged["entities"] == [
        {"canonical_name": "Queue time 초과", "entity_type": "process_condition"}
    ]
    assert unchanged["relations"][0]["predicate"] == "causes"
    assert len(unchanged["body_versions"]) == 1

    metadata = result["current"]
    assert metadata["entities"] == [
        {"canonical_name": "자연 산화", "entity_type": "failure_mechanism"}
    ]
    assert metadata["relations"] == [
        {
            "subject": "세정",
            "predicate": "prevents",
            "object": "자연 산화",
            "confidence": 0.91,
            "source_doc_ids": ["FH-2"],
        }
    ]
    assert [version["entities"] for version in metadata["body_versions"]] == [
        [{"canonical_name": "Queue time 초과", "entity_type": "process_condition"}],
        [{"canonical_name": "자연 산화", "entity_type": "failure_mechanism"}],
    ]
    assert [version["relations"] for version in metadata["body_versions"]] == [
        [
            {
                "subject": "Queue time 초과",
                "predicate": "causes",
                "object": "자연 산화",
                "confidence": 0.82,
                "source_doc_ids": ["FH-1"],
            }
        ],
        [
            {
                "subject": "세정",
                "predicate": "prevents",
                "object": "자연 산화",
                "confidence": 0.91,
                "source_doc_ids": ["FH-2"],
            }
        ],
    ]


def test_bootstrap_default_resolves_relative_external_vault(tmp_path):
    vault_name = "YieldWiki"
    script = """
import json
import bootstrap_wiki_warmup
import wiki_store

print(json.dumps({
    "bootstrap": str(bootstrap_wiki_warmup._VAULT_PATH),
    "store": str(wiki_store._VAULT),
}))
"""
    app_root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "PYTHONPATH": str(app_root),
        "WIKI_VAULT_PATH": vault_name,
    }
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout.strip())
    assert result["bootstrap"] == str((tmp_path / vault_name).resolve())
    assert result["store"] == result["bootstrap"]


def _vault_snapshot(vault: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(vault)): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file()
    }


def test_wiki_lint_cli_defaults_to_environment_vault_without_vault_option(tmp_path):
    app_root = Path(__file__).resolve().parents[2]
    repository_vault = app_root / "wiki"
    repository_before = _vault_snapshot(repository_vault)
    vault = tmp_path / "YieldWiki"
    concepts = vault / "concepts"
    concepts.mkdir(parents=True)
    (concepts / "external-only.md").write_text(
        "---\n"
        "id: concept:EXTERNAL|ONLY|LOW\n"
        "type: concept\n"
        "product: EXTERNAL\n"
        "cause_oper: ONLY\n"
        "fail_type: LOW\n"
        "confidence: 0.1\n"
        "---\n"
        "external\n",
        encoding="utf-8",
    )
    env = {**os.environ, "WIKI_VAULT_PATH": str(vault)}

    completed = subprocess.run(
        [sys.executable, "wiki_lint.py", "--json"],
        cwd=app_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    result = json.loads(completed.stdout)
    assert result["issues"]["low_confidence"] == [
        {"id": "concept:EXTERNAL|ONLY|LOW", "confidence": 0.1}
    ]
    assert _vault_snapshot(repository_vault) == repository_before


def test_v2_to_v3_cli_defaults_to_environment_vault_without_vault_option(tmp_path):
    app_root = Path(__file__).resolve().parents[2]
    repository_vault = app_root / "wiki"
    repository_before = _vault_snapshot(repository_vault)
    vault = tmp_path / "YieldWiki"
    episodes = vault / "episodes"
    episodes.mkdir(parents=True)
    external_note = episodes / "external-default-path.md"
    external_note.write_text(
        "---\nid: episode:external-default-path\ntype: episode\n---\nexternal\n",
        encoding="utf-8",
    )
    env = {**os.environ, "WIKI_VAULT_PATH": str(vault)}

    completed = subprocess.run(
        [sys.executable, "migrate_v2_to_v3.py"],
        cwd=app_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "[migrated] external-default-path.md" in completed.stdout
    migrated = external_note.read_text(encoding="utf-8")
    assert "status: active" in migrated
    assert "stale_after_days:" in migrated
    assert _vault_snapshot(repository_vault) == repository_before


def _load_store(monkeypatch, tmp_path):
    vault = tmp_path / "YieldWiki"
    monkeypatch.setenv("WIKI_VAULT_PATH", str(vault))
    for name in ("wiki_store", "wiki_config"):
        sys.modules.pop(name, None)
    import wiki_store

    return wiki_store


def _filters(product="4SS"):
    return {
        "product": product,
        "fail_type": "EASY",
        "cause_oper": "PRE METAL CLN",
    }


def _evidence_item(doc_id="EVD-0123456789abcdef0123"):
    content = "related source content"
    return {
        "doc_id": doc_id,
        "source_index": "syld_gpt_2067627",
        "source_file": "/private/uploads/deck.pptx",
        "page_num": 3,
        "download_url": "",
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "relevance": 0.91,
        "relation": "supporting_context",
    }


def test_replace_related_evidence_preserves_concept_body_and_citations(
    monkeypatch, tmp_path
):
    wiki_store = _load_store(monkeypatch, tmp_path)
    wiki_store.upsert_concept(
        _filters(),
        synthesized_body="manual-safe body",
        citations=[{"doc_id": "FH-1"}],
        materialize=False,
    )
    path = next(wiki_store._PATHS.concepts.glob("*.md"))
    expected = hashlib.sha256(path.read_bytes()).hexdigest()

    changed = wiki_store.replace_related_evidence(
        _filters(),
        "syld_gpt_2067627",
        [_evidence_item()],
        expected,
    )

    post = frontmatter.load(path)
    assert changed is True
    assert post.content == "manual-safe body"
    assert post["citations"] == [{"doc_id": "FH-1"}]
    assert post["related_evidence"][0]["doc_id"].startswith("EVD-")
    assert post["related_evidence"][0]["source_file"] == "deck.pptx"


def test_replace_related_evidence_rejects_changed_snapshot(monkeypatch, tmp_path):
    wiki_store = _load_store(monkeypatch, tmp_path)
    wiki_store.upsert_concept(
        _filters(), synthesized_body="before", materialize=False
    )
    path = next(wiki_store._PATHS.concepts.glob("*.md"))
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text(path.read_text(encoding="utf-8") + "operator edit", encoding="utf-8")

    with pytest.raises(wiki_store.ConceptEditConflict):
        wiki_store.replace_related_evidence(
            _filters(), "syld_gpt_2067627", [], expected
        )


def test_source_writer_uses_distinct_owner_and_refreshes_backlink(monkeypatch, tmp_path):
    wiki_store = _load_store(monkeypatch, tmp_path)
    wiki_store.upsert_concept(
        _filters(), synthesized_body="body", materialize=False
    )
    concept_path = next(wiki_store._PATHS.concepts.glob("*.md"))
    expected = hashlib.sha256(concept_path.read_bytes()).hexdigest()
    item = _evidence_item()
    wiki_store.replace_related_evidence(
        _filters(), "syld_gpt_2067627", [item], expected
    )

    assert wiki_store.upsert_related_evidence_source(
        item, "related source content"
    ) is True
    assert wiki_store.refresh_related_evidence_backlinks(item["doc_id"]) is True

    source_path = wiki_store._PATHS.sources / f"{item['doc_id']}.md"
    source = frontmatter.load(source_path)
    assert source["generated_by"] == "yield-wiki-evidence-enricher"
    assert source["source_file"] == "deck.pptx"
    assert "/private/" not in source_path.read_text(encoding="utf-8")
    assert "related source content" in source.content
    assert "[[concepts/" in source.content


def test_source_writer_rejects_manual_owner_collision(monkeypatch, tmp_path):
    wiki_store = _load_store(monkeypatch, tmp_path)
    wiki_store._ensure_dirs()
    item = _evidence_item()
    path = wiki_store._PATHS.sources / f"{item['doc_id']}.md"
    path.write_text("# manual", encoding="utf-8")

    with pytest.raises(Exception, match="ownership collision"):
        wiki_store.upsert_related_evidence_source(item, "related source content")

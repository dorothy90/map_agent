from __future__ import annotations

import json
import re
from dataclasses import replace

import frontmatter
import pytest

from wiki_config import initialize_wiki_vault, resolve_wiki_paths


pytestmark = pytest.mark.no_server


def _write_concept(paths, *, citations=None, entities=None, relations=None):
    if entities is None:
        entities = [
            {
                "canonical_name": "Queue time 초과",
                "entity_type": "process_condition",
            },
            {
                "canonical_name": "자연 산화",
                "entity_type": "failure_mechanism",
            },
        ]
    if relations is None:
        relations = [
            {
                "subject": "Queue time 초과",
                "predicate": "causes",
                "object": "자연 산화",
                "confidence": 0.82,
                "source_doc_ids": ["FH-000238"],
            }
        ]
    post = frontmatter.Post(
        content="## Analysis\n\nLLM BODY SENTINEL\n",
        id="concept:4SS|PRE METAL CLN|EASY",
        type="concept",
        product="4SS",
        fail_type="EASY",
        cause_oper="PRE METAL CLN",
        citations=citations
        or [
            {
                "doc_id": "FH-000238",
                "source_file": "failure-238.pptx",
                "date": "2024-02-15",
                "download_url": "https://files.example/FH-000238",
            },
            {
                "doc_id": "FH-000243",
                "source_file": "failure-243.pptx",
                "date": "2025-03-15",
                "download_url": "https://files.example/FH-000243",
            },
        ],
        entities=entities,
        relations=relations,
        source_fingerprint="sha256:concept-source-set",
    )
    path = paths.concepts / "4SS_PRE_METAL_CLN_EASY.md"
    path.write_text(frontmatter.dumps(post), encoding="utf-8")
    return path


def _snapshot(vault):
    return {
        str(path.relative_to(vault)): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file()
    }


def _write_generated_graph_note(path, *, node_id, node_type, status="active"):
    path.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                content=f"# {node_id}\n",
                id=node_id,
                type=node_type,
                generated_by="yield-wiki-materializer",
                status=status,
            )
        ),
        encoding="utf-8",
    )


def test_materializes_product_to_source_topology_and_preserves_concept_body(
    tmp_path,
):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    concept_path = _write_concept(paths)

    report = materialize_wiki(paths, apply=True)

    assert report.errors == ()
    assert "[[product_fails/4SS_EASY|EASY]]" in (
        paths.products / "4SS.md"
    ).read_text(encoding="utf-8")
    product_fail = (paths.product_fails / "4SS_EASY.md").read_text(
        encoding="utf-8"
    )
    assert "[[products/4SS|4SS]]" in product_fail
    assert "[[operations/PRE_METAL_CLN|PRE METAL CLN]]" in product_fail
    assert "[[concepts/4SS_PRE_METAL_CLN_EASY|4SS EASY]]" in (
        paths.operations / "PRE_METAL_CLN.md"
    ).read_text(encoding="utf-8")
    concept_text = concept_path.read_text(encoding="utf-8")
    assert "LLM BODY SENTINEL" in concept_text
    assert "[[operations/PRE_METAL_CLN|PRE METAL CLN]]" in concept_text
    assert "[[sources/FH-000238|FH-000238]]" in concept_text

    source = (paths.sources / "FH-000238.md").read_text(encoding="utf-8")
    assert "failure-238.pptx" in source
    assert "https://files.example/FH-000238" in source
    assert "[[concepts/4SS_PRE_METAL_CLN_EASY|4SS EASY]]" in source

    index = paths.index.read_text(encoding="utf-8")
    assert "Products: 1" in index
    assert "Product Fails: 1" in index
    assert "Operations: 1" in index
    assert "Concepts: 1" in index
    assert "Sources: 2" in index
    assert "Entities: 2" in index
    assert "Relations: 1" in index

    assert paths.entities.is_dir()
    assert paths.relations.is_dir()
    entity_posts = [frontmatter.load(path) for path in paths.entities.glob("*.md")]
    assert {post.metadata["canonical_name"] for post in entity_posts} == {
        "Queue time 초과",
        "자연 산화",
    }
    assert all(post.metadata["status"] == "active" for post in entity_posts)
    entity_paths = {
        frontmatter.load(path).metadata["canonical_name"]: path
        for path in paths.entities.glob("*.md")
    }
    assert re.fullmatch(
        r"Queue time 초과--[0-9a-f]{8}\.md",
        entity_paths["Queue time 초과"].name,
    )
    assert re.fullmatch(
        r"자연 산화--[0-9a-f]{8}\.md",
        entity_paths["자연 산화"].name,
    )
    relation_path = next(paths.relations.glob("*.md"))
    assert re.fullmatch(
        r"Queue time 초과 causes 자연 산화--[0-9a-f]{8}\.md",
        relation_path.name,
    )
    assert (
        f"[[relations/{relation_path.stem}|Queue time 초과 causes 자연 산화]]"
        in concept_text
    )
    assert (
        f"[[relations/{relation_path.stem}|Queue time 초과 causes 자연 산화]]"
        in index
    )
    relation_post = frontmatter.load(relation_path)
    assert relation_post.metadata["predicate"] == "causes"
    assert relation_post.metadata["status"] == "active"
    assert relation_post.metadata["source_doc_ids"] == ["FH-000238"]
    relation_body = relation_post.content
    assert "[[sources/FH-000238|FH-000238]]" in relation_body
    assert "[[concepts/4SS_PRE_METAL_CLN_EASY|4SS EASY]]" in relation_body
    assert "[[entities/" in relation_body


def test_graph_filenames_preserve_unicode_and_bound_unsafe_long_labels(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(
        paths,
        entities=[
            {"canonical_name": 'Plasma/Damage:*?"<>|', "entity_type": "condition"},
            {"canonical_name": "가" * 200, "entity_type": "condition"},
        ],
        relations=[],
    )

    report = materialize_wiki(paths, apply=True)

    assert report.errors == ()
    names = sorted(path.name for path in paths.entities.glob("*.md"))
    assert any(name.startswith("Plasma_Damage_") for name in names)
    assert all(not set('/\\:*?"<>|').intersection(name) for name in names)
    assert all(len(name.encode("utf-8")) < 180 for name in names)
    assert all(re.search(r"--[0-9a-f]{8}\.md$", name) for name in names)


def test_same_sanitized_graph_prefix_keeps_distinct_hash_paths(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(
        paths,
        entities=[
            {"canonical_name": "A/B", "entity_type": "condition"},
            {"canonical_name": "A:B", "entity_type": "condition"},
        ],
        relations=[],
    )

    report = materialize_wiki(paths, apply=True)

    assert report.errors == ()
    names = sorted(path.name for path in paths.entities.glob("*.md"))
    assert len(names) == 2
    assert all(name.startswith("A_B--") for name in names)
    assert names[0] != names[1]


def test_materializes_super_concept_links_and_marks_missing_references_stale(
    tmp_path,
):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    concept_path = _write_concept(paths)
    super_path = paths.super_concepts / "fail_type_EASY.md"
    super_path.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                content="## Existing Analysis\n\nSUPER BODY SENTINEL\n",
                id="super:fail_type=EASY",
                type="super_concept",
                axis="fail_type",
                axis_value="EASY",
                status="reference_only",
                source_concept_ids=[
                    "concept:4SS|PRE METAL CLN|EASY",
                    "concept:4SS|STI CMP|EASY(W)",
                ],
            )
        ),
        encoding="utf-8",
    )

    report = materialize_wiki(paths, apply=True)

    assert report.errors == ()
    super_post = frontmatter.load(super_path)
    assert super_post.metadata["status"] == "stale"
    super_text = super_path.read_text(encoding="utf-8")
    assert "SUPER BODY SENTINEL" in super_text
    assert "[[concepts/4SS_PRE_METAL_CLN_EASY|4SS EASY]]" in super_text
    assert "concept:4SS|STI CMP|EASY(W)" in super_text
    assert "[[super_concepts/fail_type_EASY|fail_type=EASY]]" in (
        concept_path.read_text(encoding="utf-8")
    )
    index = paths.index.read_text(encoding="utf-8")
    assert "Super Concepts: 1" in index
    assert "[[super_concepts/fail_type_EASY|fail_type=EASY]]" in index


def test_apply_is_idempotent_and_prunes_only_materializer_owned_files(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths)
    generated_stale = paths.products / "OBSOLETE.md"
    generated_stale.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                content="old",
                id="product:OBSOLETE",
                type="product",
                generated_by="yield-wiki-materializer",
                product="OBSOLETE",
            )
        ),
        encoding="utf-8",
    )
    user_note = paths.products / "operator-note.md"
    user_note.write_text("operator note\n", encoding="utf-8")
    paths.purpose.write_text("operator purpose\n", encoding="utf-8")
    paths.schema.write_text("operator schema\n", encoding="utf-8")
    graph_config = '{"search":"operator filter"}\n'
    paths.graph_config.write_text(graph_config, encoding="utf-8")

    first = materialize_wiki(paths, apply=True)
    first_snapshot = _snapshot(paths.root)
    second = materialize_wiki(paths, apply=True)

    assert first.errors == ()
    assert not generated_stale.exists()
    assert user_note.read_text(encoding="utf-8") == "operator note\n"
    assert paths.purpose.read_text(encoding="utf-8") == "operator purpose\n"
    assert paths.schema.read_text(encoding="utf-8") == "operator schema\n"
    assert paths.graph_config.read_text(encoding="utf-8") == graph_config
    assert second.changed_count == 0
    assert _snapshot(paths.root) == first_snapshot


def test_creates_default_graph_filter_only_when_config_is_missing(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths)

    report = materialize_wiki(paths, apply=True)

    assert report.errors == ()
    config = json.loads(paths.graph_config.read_text(encoding="utf-8"))
    assert config["search"] == "-file:index -file:log -path:lint_logs"


def test_validation_error_changes_no_file_for_conflicting_source_metadata(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(
        paths,
        citations=[
            {"doc_id": "FH-1", "source_file": "first.pptx"},
            {"doc_id": "FH-1", "source_file": "different.pptx"},
        ],
    )
    before = _snapshot(paths.root)

    report = materialize_wiki(paths, apply=True)

    assert report.errors
    assert "conflicting source_file" in "\n".join(report.errors)
    assert _snapshot(paths.root) == before


def test_validation_error_changes_no_file_when_citation_has_no_doc_id(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths, citations=[{"source_file": "missing-id.pptx"}])
    before = _snapshot(paths.root)

    report = materialize_wiki(paths, apply=True)

    assert "citation missing doc_id" in "\n".join(report.errors)
    assert _snapshot(paths.root) == before


def test_validation_error_changes_no_file_for_generated_filename_collision(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    for filename, product in (("first.md", "A B"), ("second.md", "A@B")):
        post = frontmatter.Post(
            content="body",
            id=f"concept:{product}|OP|FAIL",
            type="concept",
            product=product,
            fail_type="FAIL",
            cause_oper="OP",
            citations=[],
        )
        paths.concepts.joinpath(filename).write_text(
            frontmatter.dumps(post), encoding="utf-8"
        )
    before = _snapshot(paths.root)

    report = materialize_wiki(paths, apply=True)

    assert "generated path collision" in "\n".join(report.errors)
    assert _snapshot(paths.root) == before


def test_validation_error_changes_no_file_for_generated_path_collision(
    tmp_path,
):
    from wiki_materializer import _readable_graph_path, _stable_graph_id, materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths)
    node_id = _stable_graph_id("entity", {"canonical_name": "Queue time 초과"})
    target = _readable_graph_path(paths.entities, node_id, "Queue time 초과")
    target.write_text("operator note\n", encoding="utf-8")
    before = _snapshot(paths.root)

    report = materialize_wiki(paths, apply=True)

    assert "generated path collision" in "\n".join(report.errors)
    assert _snapshot(paths.root) == before


@pytest.mark.parametrize(
    ("directory_name", "filename"),
    [
        ("products", "4SS.md"),
        ("product_fails", "4SS_EASY.md"),
        ("operations", "PRE_METAL_CLN.md"),
        ("sources", "FH-000238.md"),
    ],
)
def test_generated_namespace_preflight_preserves_operator_note(
    tmp_path, directory_name, filename
):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths)
    target = getattr(paths, directory_name) / filename
    target.write_text("operator-authored markdown\n", encoding="utf-8")
    before = _snapshot(paths.root)

    report = materialize_wiki(paths, apply=True)

    assert "generated path collision" in "\n".join(report.errors)
    assert _snapshot(paths.root) == before


def test_generated_namespace_preflight_requires_exact_full_id(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths)
    target = paths.sources / "FH-000238.md"
    target.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                content="operator content",
                id="source:FH-DIFFERENT",
                type="source",
                generated_by="yield-wiki-materializer",
                doc_id="FH-DIFFERENT",
            )
        ),
        encoding="utf-8",
    )
    before = _snapshot(paths.root)

    report = materialize_wiki(paths, apply=True)

    assert "generated path collision" in "\n".join(report.errors)
    assert _snapshot(paths.root) == before


def test_active_legacy_hash_path_is_replaced_not_marked_stale(tmp_path):
    from wiki_materializer import _stable_graph_id, materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths)
    node_id = _stable_graph_id("entity", {"canonical_name": "Queue time 초과"})
    legacy = paths.entities / f"{node_id.rsplit(':', 1)[-1]}.md"
    _write_generated_graph_note(legacy, node_id=node_id, node_type="entity")

    report = materialize_wiki(paths, apply=True)

    assert report.errors == ()
    assert not legacy.exists()
    assert legacy.relative_to(paths.root).as_posix() in report.deleted
    readable = [
        path for path in paths.entities.glob("Queue time 초과--*.md")
        if frontmatter.load(path).metadata["id"] == node_id
    ]
    assert len(readable) == 1
    assert frontmatter.load(readable[0]).metadata["status"] == "active"


def test_interrupted_path_migration_deletes_only_legacy_duplicate(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths)
    first = materialize_wiki(paths, apply=True)
    assert first.errors == ()
    readable = next(paths.entities.glob("Queue time 초과--*.md"))
    node_id = frontmatter.load(readable).metadata["id"]
    legacy = paths.entities / f"{node_id.rsplit(':', 1)[-1]}.md"
    legacy.write_bytes(readable.read_bytes())

    resumed = materialize_wiki(paths, apply=True)

    assert resumed.errors == ()
    assert readable.exists()
    assert not legacy.exists()
    assert resumed.deleted == (legacy.relative_to(paths.root).as_posix(),)


def test_duplicate_noncanonical_graph_paths_are_fatal_and_write_nothing(tmp_path):
    from wiki_materializer import _stable_graph_id, materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths)
    node_id = _stable_graph_id("entity", {"canonical_name": "Queue time 초과"})
    for name in ("duplicate-a.md", "duplicate-b.md"):
        _write_generated_graph_note(
            paths.entities / name,
            node_id=node_id,
            node_type="entity",
        )
    before = _snapshot(paths.root)

    report = materialize_wiki(paths, apply=True)

    assert "duplicate generated graph id" in "\n".join(report.errors)
    assert _snapshot(paths.root) == before


def test_invalid_relation_endpoint_warns_without_blocking_valid_targets(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    valid = {
        "subject": "Queue time 초과",
        "predicate": "causes",
        "object": "자연 산화",
        "confidence": 0.82,
        "source_doc_ids": ["FH-000238"],
    }
    _write_concept(paths, relations=[valid, {**valid, "subject": "없는 Entity"}])

    report = materialize_wiki(paths, apply=True)

    assert report.errors == ()
    assert "missing endpoint" in "\n".join(report.warnings)
    assert len(list(paths.relations.glob("*.md"))) == 1
    assert len(list(paths.entities.glob("*.md"))) == 2
    assert paths.products.joinpath("4SS.md").exists()


def test_relation_with_uncited_source_warns_while_valid_relation_is_written(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    valid = {
        "subject": "Queue time 초과",
        "predicate": "causes",
        "object": "자연 산화",
        "confidence": 0.82,
        "source_doc_ids": ["FH-000238"],
    }
    _write_concept(
        paths,
        relations=[valid, {**valid, "predicate": "prevents", "source_doc_ids": ["FH-NOT-CITED"]}],
    )

    report = materialize_wiki(paths, apply=True)

    assert report.errors == ()
    assert "source_doc_id" in "\n".join(report.warnings)
    assert len(list(paths.relations.glob("*.md"))) == 1
    assert paths.concepts.joinpath("4SS_PRE_METAL_CLN_EASY.md").exists()


def test_removed_graph_notes_become_stale_and_leave_active_entity_links(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    concept_path = _write_concept(paths)
    first = materialize_wiki(paths, apply=True)
    assert first.errors == ()
    relation_path = next(paths.relations.glob("*.md"))
    relation_before = frontmatter.load(relation_path)

    concept = frontmatter.load(concept_path)
    concept.metadata["relations"] = []
    concept_path.write_text(frontmatter.dumps(concept), encoding="utf-8")
    second = materialize_wiki(paths, apply=True)

    assert second.errors == ()
    stale_relation = frontmatter.load(relation_path)
    assert stale_relation.metadata["status"] == "stale"
    assert stale_relation.content == relation_before.content
    assert all(
        relation_path.stem not in frontmatter.load(path).content
        for path in paths.entities.glob("*.md")
    )

    entity_paths = list(paths.entities.glob("*.md"))
    entity_bodies = {path: frontmatter.load(path).content for path in entity_paths}
    concept = frontmatter.load(concept_path)
    concept.metadata["entities"] = []
    concept_path.write_text(frontmatter.dumps(concept), encoding="utf-8")
    third = materialize_wiki(paths, apply=True)

    assert third.errors == ()
    for path in entity_paths:
        stale_entity = frontmatter.load(path)
        assert stale_entity.metadata["status"] == "stale"
        assert stale_entity.content == entity_bodies[path]


def test_stale_concept_cannot_keep_generated_graph_notes_active(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    concept_path = _write_concept(paths)
    first = materialize_wiki(paths, apply=True)
    assert first.errors == ()
    entity_paths = list(paths.entities.glob("*.md"))
    relation_path = next(paths.relations.glob("*.md"))

    concept = frontmatter.load(concept_path)
    concept.metadata["status"] = "stale"
    concept_path.write_text(frontmatter.dumps(concept), encoding="utf-8")
    second = materialize_wiki(paths, apply=True)

    assert second.errors == ()
    assert frontmatter.load(relation_path).metadata["status"] == "stale"
    assert all(
        frontmatter.load(path).metadata["status"] == "stale"
        for path in entity_paths
    )
    index = paths.index.read_text(encoding="utf-8")
    assert "Entities: 0" in index
    assert "Relations: 0" in index


def test_malformed_managed_block_is_fatal_and_changes_no_file(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    concept_path = _write_concept(paths)
    concept_path.write_text(
        concept_path.read_text(encoding="utf-8")
        + "\n<!-- yield-wiki:knowledge-links:start -->\n",
        encoding="utf-8",
    )
    before = _snapshot(paths.root)

    report = materialize_wiki(paths, apply=True)

    assert "unbalanced managed Knowledge Links markers" in "\n".join(report.errors)
    assert _snapshot(paths.root) == before


def test_escaped_generated_entity_path_is_fatal_and_changes_no_file(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths)
    outside = tmp_path / "outside-entities"
    outside.mkdir()
    escaped = replace(paths, entities=outside)
    before = _snapshot(paths.root)

    report = materialize_wiki(escaped, apply=True)

    assert report.errors
    assert "managed path" in "\n".join(report.errors)
    assert _snapshot(paths.root) == before
    assert list(outside.iterdir()) == []


def test_symlinked_relation_directory_is_fatal_and_writes_nothing_outside(tmp_path):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths)
    outside = tmp_path / "outside-relations"
    outside.mkdir()
    paths.relations.rmdir()
    paths.relations.symlink_to(outside, target_is_directory=True)
    before = _snapshot(paths.root)

    report = materialize_wiki(paths, apply=True)

    assert report.errors
    assert str(paths.relations) in "\n".join(report.errors)
    assert _snapshot(paths.root) == before
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("directory_name", ["entities", "relations"])
def test_check_rejects_symlinked_graph_directory_without_persistent_writes(
    tmp_path, directory_name
):
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths)
    managed_directory = getattr(paths, directory_name)
    outside = tmp_path / f"outside-{directory_name}"
    outside.mkdir()
    sentinel = outside / "sentinel.md"
    sentinel.write_text("retained\n", encoding="utf-8")
    managed_directory.rmdir()
    managed_directory.symlink_to(outside, target_is_directory=True)
    before = _snapshot(paths.root)
    outside_before = _snapshot(outside)

    report = materialize_wiki(paths, apply=False)

    assert report.errors
    assert str(managed_directory) in "\n".join(report.errors)
    assert _snapshot(paths.root) == before
    assert _snapshot(outside) == outside_before


def test_materializer_replace_rejects_directory_swap_without_outside_write(
    tmp_path, monkeypatch
):
    import wiki_safe_mutation
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths)
    outside = tmp_path / "outside-products"
    outside.mkdir()
    sentinel = outside / "sentinel.md"
    sentinel.write_text("retained\n", encoding="utf-8")
    parked = tmp_path / "parked-products"
    swapped = False

    def swap(operation, path):
        nonlocal swapped
        if swapped or operation != "replace" or path.parent != paths.products:
            return
        swapped = True
        paths.products.rename(parked)
        paths.products.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(wiki_safe_mutation, "_before_commit", swap)

    report = materialize_wiki(paths, apply=True)

    assert report.errors
    assert sentinel.read_text(encoding="utf-8") == "retained\n"
    assert not (outside / "4SS.md").exists()


def test_materializer_delete_rejects_directory_swap_without_outside_delete(
    tmp_path, monkeypatch
):
    import wiki_safe_mutation
    from wiki_materializer import materialize_wiki

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    _write_concept(paths)
    obsolete = paths.products / "OBSOLETE.md"
    obsolete.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                content="generated obsolete",
                id="product:OBSOLETE",
                type="product",
                generated_by="yield-wiki-materializer",
                product="OBSOLETE",
            )
        ),
        encoding="utf-8",
    )
    outside = tmp_path / "outside-products"
    outside.mkdir()
    outside_obsolete = outside / "OBSOLETE.md"
    outside_obsolete.write_text("outside retained\n", encoding="utf-8")
    parked = tmp_path / "parked-products"
    swapped = False

    def swap(operation, path):
        nonlocal swapped
        if swapped or operation != "delete" or path != obsolete:
            return
        swapped = True
        paths.products.rename(parked)
        paths.products.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(wiki_safe_mutation, "_before_commit", swap)

    report = materialize_wiki(paths, apply=True)

    assert report.errors
    assert outside_obsolete.read_text(encoding="utf-8") == "outside retained\n"


def test_store_write_rejects_directory_swap_without_outside_write(
    tmp_path, monkeypatch
):
    import importlib
    import wiki_safe_mutation
    import wiki_store

    monkeypatch.setenv("WIKI_VAULT_PATH", str(tmp_path / "YieldWiki"))
    store = importlib.reload(wiki_store)
    outside = tmp_path / "outside-concepts"
    outside.mkdir()
    sentinel = outside / "sentinel.md"
    sentinel.write_text("retained\n", encoding="utf-8")
    parked = tmp_path / "parked-concepts"
    swapped = False

    def swap(operation, path):
        nonlocal swapped
        if swapped or operation != "replace" or path.parent != store._PATHS.concepts:
            return
        swapped = True
        store._PATHS.concepts.rename(parked)
        store._PATHS.concepts.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(wiki_safe_mutation, "_before_commit", swap)

    with pytest.raises(RuntimeError, match="managed directory changed"):
        store.upsert_concept(
            {
                "product": "4SS",
                "fail_type": "EASY",
                "cause_oper": "PRE METAL CLN",
            },
            synthesized_body="generated body",
            materialize=False,
        )

    assert sentinel.read_text(encoding="utf-8") == "retained\n"
    assert not any(outside.glob("4SS*.md"))


def test_replace_rejects_same_directory_swap_after_snapshot(tmp_path, monkeypatch):
    import wiki_safe_mutation
    from wiki_safe_mutation import GeneratedOwner, PinnedWikiMutation

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    target = paths.products / "TARGET.md"
    owner = GeneratedOwner(
        "yield-wiki-materializer", "product", "product:TARGET"
    )
    target.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                content="generated original",
                id=owner.node_id,
                type=owner.node_type,
                generated_by=owner.generated_by,
                product="TARGET",
            )
        ),
        encoding="utf-8",
    )
    parked = paths.products / "parked-original.md"
    operator_bytes = b"operator-owned replacement\n"

    def swap(operation, path):
        if operation != "replace" or path != target or parked.exists():
            return
        target.rename(parked)
        target.write_bytes(operator_bytes)

    monkeypatch.setattr(wiki_safe_mutation, "_after_snapshot", swap, raising=False)

    with PinnedWikiMutation(paths) as mutation:
        expected = mutation.snapshot(target)
        with pytest.raises(RuntimeError, match="changed before replacement"):
            mutation.replace_text(
                target,
                "new generated content\n",
                expected=expected,
                owner=owner,
            )

    assert target.read_bytes() == operator_bytes
    assert parked.exists()


def test_delete_rejects_same_directory_swap_after_snapshot(tmp_path, monkeypatch):
    import wiki_safe_mutation
    from wiki_safe_mutation import GeneratedOwner, PinnedWikiMutation

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    target = paths.products / "TARGET.md"
    owner = GeneratedOwner(
        "yield-wiki-materializer", "product", "product:TARGET"
    )
    target.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                content="generated original",
                id=owner.node_id,
                type=owner.node_type,
                generated_by=owner.generated_by,
                product="TARGET",
            )
        ),
        encoding="utf-8",
    )
    parked = paths.products / "parked-original.md"
    operator_bytes = b"operator-owned replacement\n"

    def swap(operation, path):
        if operation != "delete" or path != target or parked.exists():
            return
        target.rename(parked)
        target.write_bytes(operator_bytes)

    monkeypatch.setattr(wiki_safe_mutation, "_after_snapshot", swap, raising=False)

    with PinnedWikiMutation(paths) as mutation:
        expected = mutation.snapshot(target)
        with pytest.raises(RuntimeError, match="changed before deletion"):
            mutation.delete(target, expected=expected, owner=owner)

    assert target.read_bytes() == operator_bytes
    assert parked.exists()


def test_replace_recovers_interruption_immediately_after_quarantine(
    tmp_path, monkeypatch
):
    import wiki_safe_mutation
    from wiki_safe_mutation import GeneratedOwner, PinnedWikiMutation

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    target = paths.products / "TARGET.md"
    owner = GeneratedOwner(
        "yield-wiki-materializer", "product", "product:TARGET"
    )
    target.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                content="generated original",
                id=owner.node_id,
                type=owner.node_type,
                generated_by=owner.generated_by,
                product="TARGET",
            )
        ),
        encoding="utf-8",
    )
    before = target.read_bytes()
    original_rename = wiki_safe_mutation.os.rename
    original_fsync = wiki_safe_mutation.os.fsync
    crashed = False
    directory_fsyncs = []

    class SimulatedCrash(BaseException):
        pass

    def crash_after_quarantine(source, destination, **kwargs):
        nonlocal crashed
        original_rename(source, destination, **kwargs)
        if source == target.name and str(destination).endswith(".quarantine"):
            crashed = True
            raise SimulatedCrash("interrupted after quarantine")

    def observe_fsync(descriptor):
        if wiki_safe_mutation.stat.S_ISDIR(
            wiki_safe_mutation.os.fstat(descriptor).st_mode
        ):
            directory_fsyncs.append(descriptor)
        return original_fsync(descriptor)

    monkeypatch.setattr(wiki_safe_mutation.os, "rename", crash_after_quarantine)
    monkeypatch.setattr(wiki_safe_mutation.os, "fsync", observe_fsync)

    with PinnedWikiMutation(paths) as mutation:
        expected = mutation.snapshot(target)
        with pytest.raises(SimulatedCrash, match="interrupted after quarantine"):
            mutation.replace_text(
                target,
                "new generated content\n",
                expected=expected,
                owner=owner,
            )

    assert crashed
    assert not target.exists()
    assert len(list(paths.products.glob(".*.yield-wiki-transaction"))) == 1
    with PinnedWikiMutation(paths) as recovery:
        recovered = recovery.snapshot(target)

    assert recovered == expected
    assert target.read_bytes() == before
    assert list(paths.products.glob(".*.quarantine")) == []
    assert list(paths.products.glob(".*.yield-wiki-transaction")) == []
    assert directory_fsyncs


def test_quarantine_cleanup_rejects_concurrent_replacement_and_preserves_data(
    tmp_path, monkeypatch
):
    import wiki_safe_mutation
    from wiki_safe_mutation import GeneratedOwner, PinnedWikiMutation

    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    target = paths.products / "TARGET.md"
    owner = GeneratedOwner(
        "yield-wiki-materializer", "product", "product:TARGET"
    )
    target.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                content="generated original",
                id=owner.node_id,
                type=owner.node_type,
                generated_by=owner.generated_by,
                product="TARGET",
            )
        ),
        encoding="utf-8",
    )
    original_bytes = target.read_bytes()
    operator_bytes = b"operator-owned cleanup replacement\n"
    parked = paths.products / "parked-cleanup-original.md"
    original_snapshot = PinnedWikiMutation.snapshot
    quarantine_snapshots = 0
    replacement_path = None

    def swap_after_cleanup_snapshot(self, path):
        nonlocal quarantine_snapshots, replacement_path
        snapshot = original_snapshot(self, path)
        if path.name.endswith(".quarantine"):
            quarantine_snapshots += 1
            if quarantine_snapshots == 2:
                path.rename(parked)
                path.write_bytes(operator_bytes)
                replacement_path = path
        return snapshot

    monkeypatch.setattr(
        PinnedWikiMutation,
        "snapshot",
        swap_after_cleanup_snapshot,
    )

    with PinnedWikiMutation(paths) as mutation:
        expected = mutation.snapshot(target)
        with pytest.raises(RuntimeError, match="changed before cleanup"):
            mutation.replace_text(
                target,
                "new generated content\n",
                expected=expected,
                owner=owner,
            )

    assert quarantine_snapshots == 2
    assert parked.read_bytes() == original_bytes
    assert replacement_path is not None
    assert replacement_path.read_bytes() == operator_bytes
    assert len(list(paths.products.glob(".*.yield-wiki-transaction"))) == 1

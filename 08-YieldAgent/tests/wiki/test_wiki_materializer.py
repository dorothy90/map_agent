from __future__ import annotations

import json

import frontmatter
import pytest

from wiki_config import initialize_wiki_vault, resolve_wiki_paths


pytestmark = pytest.mark.no_server


def _write_concept(paths, *, citations=None):
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
        "---\ntype: product\ngenerated_by: yield-wiki-materializer\n---\nold\n",
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


def test_validation_error_changes_no_file_for_generated_path_collision(tmp_path):
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

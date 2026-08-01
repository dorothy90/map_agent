from __future__ import annotations

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

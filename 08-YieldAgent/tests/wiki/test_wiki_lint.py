from __future__ import annotations

import frontmatter
import pytest

from wiki_lint import scan


pytestmark = pytest.mark.no_server


def _write(path, **metadata):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        frontmatter.dumps(frontmatter.Post(content="body", **metadata)),
        encoding="utf-8",
    )


def _valid_graph(vault):
    _write(
        vault / "concepts" / "concept.md",
        id="concept:one",
        type="concept",
        confidence=0.8,
    )
    _write(
        vault / "entities" / "subject.md",
        id="entity:subject",
        type="entity",
        generated_by="yield-wiki-materializer",
        status="active",
    )
    _write(
        vault / "entities" / "object.md",
        id="entity:object",
        type="entity",
        generated_by="yield-wiki-materializer",
        status="active",
    )
    _write(
        vault / "sources" / "FH-1.md",
        id="source:FH-1",
        type="source",
        doc_id="FH-1",
    )


@pytest.mark.parametrize(
    ("metadata_update", "expected_reason"),
    [
        ({"subject_entity_id": "entity:missing"}, "subject_entity_id"),
        ({"object_entity_id": "entity:missing"}, "object_entity_id"),
        ({"source_doc_ids": ["FH-missing"]}, "source_doc_ids"),
        ({"predicate": "invented_predicate"}, "predicate"),
    ],
)
def test_scan_reports_invalid_active_relation(tmp_path, metadata_update, expected_reason):
    _valid_graph(tmp_path)
    metadata = {
        "id": "relation:one",
        "type": "relation",
        "generated_by": "yield-wiki-materializer",
        "status": "active",
        "origin_concept_id": "concept:one",
        "subject_entity_id": "entity:subject",
        "predicate": "causes",
        "object_entity_id": "entity:object",
        "source_doc_ids": ["FH-1"],
    }
    metadata.update(metadata_update)
    _write(tmp_path / "relations" / "relation.md", **metadata)

    issues = scan(tmp_path)

    assert len(issues["invalid_relation"]) == 1
    assert expected_reason in str(issues["invalid_relation"][0])


def test_scan_reports_stale_nodes_without_invalidating_stale_relation(tmp_path):
    _valid_graph(tmp_path)
    _write(
        tmp_path / "relations" / "relation.md",
        id="relation:stale",
        type="relation",
        generated_by="yield-wiki-materializer",
        status="stale",
        subject_entity_id="entity:missing",
        predicate="invented_predicate",
        object_entity_id="entity:missing",
        source_doc_ids=["FH-missing"],
    )
    _write(
        tmp_path / "entities" / "stale.md",
        id="entity:stale",
        type="entity",
        generated_by="yield-wiki-materializer",
        status="stale",
    )

    issues = scan(tmp_path)

    assert issues["invalid_relation"] == []
    assert {item["id"] for item in issues["stale_node"]} == {
        "relation:stale",
        "entity:stale",
    }

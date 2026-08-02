from __future__ import annotations

from dataclasses import FrozenInstanceError
import os

import frontmatter
import pytest

from wiki_config import initialize_wiki_vault, resolve_wiki_paths


pytestmark = pytest.mark.no_server


SEED = "concept:4SS|PRE METAL CLN|EASY"
RELATED = "concept:4SS|STI CMP|EASY"
SHARED = "entity:shared"
OTHER = "entity:other"


@pytest.fixture
def paths(tmp_path):
    resolved = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(resolved)
    return resolved


def _write_note(path, body="BODY MUST NOT BE TRAVERSED", **metadata):
    path.write_text(
        frontmatter.dumps(frontmatter.Post(content=body, **metadata)),
        encoding="utf-8",
    )
    return path


def _write_concept(
    paths, filename, concept_id, *, source_doc_ids=(), status="active"
):
    return _write_note(
        paths.concepts / filename,
        id=concept_id,
        type="concept",
        status=status,
        citations=[{"doc_id": doc_id} for doc_id in source_doc_ids],
    )


def _write_entity(
    paths,
    filename,
    entity_id,
    canonical_name,
    concept_ids,
    *,
    status="active",
):
    return _write_note(
        paths.entities / filename,
        id=entity_id,
        type="entity",
        status=status,
        canonical_name=canonical_name,
        entity_type="condition",
        source_concept_ids=concept_ids,
    )


def _write_source(paths, filename, doc_id):
    return _write_note(
        paths.sources / filename,
        id=f"source:{doc_id}",
        type="source",
        doc_id=doc_id,
    )


def _write_relation(
    paths,
    filename,
    relation_id,
    origin_concept_id,
    subject_entity_id,
    object_entity_id,
    source_doc_ids,
    *,
    status="active",
    body="BODY MUST NOT BE TRAVERSED",
):
    return _write_note(
        paths.relations / filename,
        body,
        id=relation_id,
        type="relation",
        status=status,
        origin_concept_id=origin_concept_id,
        subject_entity_id=subject_entity_id,
        predicate="causes",
        object_entity_id=object_entity_id,
        confidence=0.82,
        source_doc_ids=source_doc_ids,
    )


def _write_shared_graph(paths):
    _write_concept(paths, "seed.md", SEED, source_doc_ids=["FH-1"])
    _write_concept(paths, "related.md", RELATED, source_doc_ids=["FH-1"])
    _write_entity(paths, "shared.md", SHARED, "Queue time", [SEED, RELATED])
    _write_entity(paths, "other.md", OTHER, "Natural oxidation", [SEED])
    _write_source(paths, "FH-1.md", "FH-1")
    _write_relation(
        paths,
        "active.md",
        "relation:active",
        SEED,
        SHARED,
        OTHER,
        ["FH-1", "FH-1"],
    )


def test_expands_frontmatter_only_active_source_backed_one_hop(paths):
    from wiki_graph_projection import build_graph_projection

    _write_shared_graph(paths)
    _write_relation(
        paths,
        "stale.md",
        "relation:stale",
        SEED,
        SHARED,
        OTHER,
        ["FH-1"],
        status="stale",
        body="status: active\nsource_doc_ids: [FH-BODY-ONLY]",
    )
    _write_relation(
        paths,
        "missing-source.md",
        "relation:missing-source",
        SEED,
        SHARED,
        OTHER,
        ["FH-MISSING"],
    )

    result = build_graph_projection(paths).expand_concepts([SEED])

    assert result.primary_concept_id == SEED
    assert result.concept_ids == [SEED, RELATED]
    assert [relation.relation_id for relation in result.relations] == [
        "relation:active"
    ]
    assert result.relations[0].subject == "Queue time"
    assert result.relations[0].object == "Natural oxidation"
    assert result.relations[0].source_doc_ids == ["FH-1"]
    assert result.source_doc_ids == ["FH-1"]


def test_expansion_uses_exact_ids_and_returns_empty_for_missing_seed(paths):
    from wiki_graph_models import GraphContext
    from wiki_graph_projection import build_graph_projection

    _write_shared_graph(paths)
    projection = build_graph_projection(paths)

    assert projection.expand_concepts([SEED.lower()]) == GraphContext()
    assert projection.expand_concepts([]) == GraphContext()


def test_relation_requires_an_exact_source_doc_id(paths):
    from wiki_graph_projection import build_graph_projection

    _write_concept(paths, "seed.md", SEED, source_doc_ids=["FH-1"])
    _write_entity(paths, "shared.md", SHARED, "Queue time", [SEED])
    _write_entity(paths, "other.md", OTHER, "Natural oxidation", [SEED])
    _write_source(paths, "FH-1.md", "fh-1")
    _write_relation(
        paths,
        "case-mismatch.md",
        "relation:case-mismatch",
        SEED,
        SHARED,
        OTHER,
        ["FH-1"],
    )

    result = build_graph_projection(paths).expand_concepts([SEED])

    assert result.relations == []
    assert result.source_doc_ids == []


def test_related_concept_can_share_only_a_canonical_source(paths):
    from wiki_graph_projection import build_graph_projection

    _write_concept(paths, "seed.md", SEED, source_doc_ids=["FH-1"])
    _write_concept(paths, "related.md", RELATED, source_doc_ids=["FH-1"])
    _write_entity(paths, "shared.md", SHARED, "Queue time", [SEED])
    _write_entity(paths, "other.md", OTHER, "Natural oxidation", [SEED])
    _write_source(paths, "FH-1.md", "FH-1")
    _write_relation(
        paths,
        "active.md",
        "relation:active",
        SEED,
        SHARED,
        OTHER,
        ["FH-1"],
    )

    result = build_graph_projection(paths).expand_concepts([SEED])

    assert result.concept_ids == [SEED, RELATED]


def test_malformed_note_does_not_hide_valid_projection_records(paths):
    from wiki_graph_projection import build_graph_projection

    _write_shared_graph(paths)
    (paths.concepts / "malformed.md").write_text(
        "---\nid: [unterminated\n---\nbody\n", encoding="utf-8"
    )

    result = build_graph_projection(paths).expand_concepts([SEED])

    assert result.primary_concept_id == SEED
    assert [relation.relation_id for relation in result.relations] == [
        "relation:active"
    ]


def test_expansion_never_exceeds_requested_bounds(paths):
    from wiki_graph_projection import build_graph_projection

    _write_concept(paths, "seed.md", SEED)
    for index in range(4):
        related_id = f"concept:related:{index}"
        entity_id = f"entity:{index}"
        other_id = f"entity:other:{index}"
        doc_id = f"FH-{index}"
        _write_concept(paths, f"related-{index}.md", related_id)
        _write_entity(
            paths,
            f"entity-{index}.md",
            entity_id,
            f"Entity {index}",
            [SEED, related_id],
        )
        _write_entity(
            paths,
            f"other-{index}.md",
            other_id,
            f"Other {index}",
            [SEED],
        )
        _write_source(paths, f"{doc_id}.md", doc_id)
        _write_relation(
            paths,
            f"relation-{index}.md",
            f"relation:{index}",
            SEED,
            entity_id,
            other_id,
            [doc_id],
        )

    result = build_graph_projection(paths).expand_concepts(
        [SEED], max_relations=2, max_related=1, max_sources=1
    )

    assert result.concept_ids[0] == SEED
    assert len(result.concept_ids[1:]) <= 1
    assert len(result.relations) <= 2
    assert len(result.source_doc_ids) <= 1


def test_symlinked_entity_and_source_notes_are_not_traversed(paths, tmp_path):
    from wiki_graph_projection import build_graph_projection

    _write_concept(
        paths,
        "seed.md",
        SEED,
        source_doc_ids=["FH-DIRECT", "FH-LINKED"],
    )
    _write_entity(paths, "subject.md", "entity:subject", "Subject", [SEED])
    _write_entity(paths, "direct.md", OTHER, "Direct", [SEED])
    _write_source(paths, "FH-DIRECT.md", "FH-DIRECT")

    outside_entity = _write_note(
        tmp_path / "outside-entity.md",
        id=SHARED,
        type="entity",
        status="active",
        canonical_name="Outside entity",
        entity_type="condition",
        source_concept_ids=[SEED],
    )
    (paths.entities / "linked.md").symlink_to(outside_entity)
    outside_source = _write_note(
        tmp_path / "outside-source.md",
        id="source:FH-LINKED",
        type="source",
        doc_id="FH-LINKED",
    )
    (paths.sources / "FH-LINKED.md").symlink_to(outside_source)
    _write_relation(
        paths,
        "linked-entity.md",
        "relation:linked-entity",
        SEED,
        SHARED,
        OTHER,
        ["FH-DIRECT"],
    )
    _write_relation(
        paths,
        "linked-source.md",
        "relation:linked-source",
        SEED,
        "entity:subject",
        OTHER,
        ["FH-LINKED"],
    )

    result = build_graph_projection(paths).expand_concepts([SEED])

    assert result.relations == []
    assert result.source_doc_ids == []


def test_projection_records_are_frozen_and_cache_invalidates_by_stat_fingerprint(paths):
    from wiki_graph_projection import build_graph_projection

    _write_shared_graph(paths)
    first = build_graph_projection(paths)
    unchanged = build_graph_projection(paths)

    assert unchanged is first
    with pytest.raises(FrozenInstanceError):
        first.concepts[SEED].concept_id = "changed"
    with pytest.raises(TypeError):
        first.concepts[SEED] = first.concepts[SEED]

    source_path = paths.sources / "FH-1.md"
    initial_stat = source_path.stat()
    os.utime(
        source_path,
        ns=(initial_stat.st_atime_ns, initial_stat.st_mtime_ns + 1),
    )
    mtime_changed = build_graph_projection(paths)

    assert mtime_changed is not first
    assert mtime_changed.fingerprint != first.fingerprint

    mtime_stat = source_path.stat()
    source_path.write_text(
        source_path.read_text(encoding="utf-8") + "\nchanged size\n",
        encoding="utf-8",
    )
    os.utime(source_path, ns=(mtime_stat.st_atime_ns, mtime_stat.st_mtime_ns))
    size_changed = build_graph_projection(paths)

    assert size_changed is not mtime_changed
    assert size_changed.fingerprint != mtime_changed.fingerprint

    renamed = paths.sources / "renamed-source.md"
    source_path.rename(renamed)
    path_changed = build_graph_projection(paths)

    assert path_changed is not size_changed
    assert path_changed.fingerprint != size_changed.fingerprint

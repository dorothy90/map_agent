from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
import os
import re

import frontmatter
import pytest

from wiki_config import initialize_wiki_vault, resolve_wiki_paths


pytestmark = pytest.mark.no_server


SEED = "concept:4SS|PRE METAL CLN|EASY"
RELATED = "concept:4SS|STI CMP|EASY"
SOURCE_FINGERPRINT = "sha256:concept-source-set"
_GENERATED_BY = "yield-wiki-materializer"


def _stable_graph_id(kind, payload):
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"{kind}:sha256:{hashlib.sha256(encoded).hexdigest()}"


def _stable_filename(value):
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", value)


def _entity_id(canonical_name):
    return _stable_graph_id("entity", {"canonical_name": canonical_name})


def _relation_id(origin_concept_id, subject, predicate, object_name):
    return _stable_graph_id(
        "relation",
        {
            "origin_concept_id": origin_concept_id,
            "subject": subject,
            "predicate": predicate,
            "object": object_name,
        },
    )


SHARED = _entity_id("Queue time")
OTHER = _entity_id("Natural oxidation")
ACTIVE_RELATION = _relation_id(
    SEED, "Queue time", "causes", "Natural oxidation"
)
_ENTITY_ALIASES = {
    SHARED: (SHARED, "Queue time"),
    OTHER: (OTHER, "Natural oxidation"),
}


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
    paths,
    filename,
    concept_id,
    *,
    source_doc_ids=(),
    status="active",
    source_fingerprint=SOURCE_FINGERPRINT,
):
    return _write_note(
        paths.concepts / filename,
        id=concept_id,
        type="concept",
        status=status,
        source_fingerprint=source_fingerprint,
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
    canonical_path=True,
    generated_by=_GENERATED_BY,
    stable_id=True,
):
    expected_id = _entity_id(canonical_name)
    actual_id = expected_id if stable_id else entity_id
    _ENTITY_ALIASES[entity_id] = (actual_id, canonical_name)
    _ENTITY_ALIASES[actual_id] = (actual_id, canonical_name)
    path = paths.entities / (
        f"{expected_id.rsplit(':', 1)[-1]}.md" if canonical_path else filename
    )
    return _write_note(
        path,
        id=actual_id,
        type="entity",
        generated_by=generated_by,
        status=status,
        canonical_name=canonical_name,
        entity_type="condition",
        source_concept_ids=concept_ids,
    )


def _write_source(
    paths,
    filename,
    doc_id,
    *,
    canonical_path=True,
    generated_by=_GENERATED_BY,
    stable_id=True,
):
    path = paths.sources / (
        f"{_stable_filename(doc_id)}.md" if canonical_path else filename
    )
    return _write_note(
        path,
        id=f"source:{doc_id}" if stable_id else "source:forged",
        type="source",
        generated_by=generated_by,
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
    predicate="causes",
    source_fingerprint=SOURCE_FINGERPRINT,
    canonical_path=True,
    generated_by=_GENERATED_BY,
    stable_id=True,
):
    subject_id, subject_name = _ENTITY_ALIASES[subject_entity_id]
    object_id, object_name = _ENTITY_ALIASES[object_entity_id]
    expected_id = _relation_id(
        origin_concept_id, subject_name, predicate, object_name
    )
    actual_id = expected_id if stable_id else relation_id
    path = paths.relations / (
        f"{expected_id.rsplit(':', 1)[-1]}.md" if canonical_path else filename
    )
    return _write_note(
        path,
        body,
        id=actual_id,
        type="relation",
        generated_by=generated_by,
        status=status,
        source_fingerprint=source_fingerprint,
        origin_concept_id=origin_concept_id,
        subject_entity_id=subject_id,
        predicate=predicate,
        object_entity_id=object_id,
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
        predicate="prevents",
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
        predicate="resolved_by",
    )

    result = build_graph_projection(paths).expand_concepts([SEED])

    assert result.primary_concept_id == SEED
    assert result.concept_ids == [SEED, RELATED]
    assert [relation.relation_id for relation in result.relations] == [
        ACTIVE_RELATION
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


@pytest.mark.parametrize("mismatched_endpoint", ["subject", "object"])
def test_relation_requires_both_entities_to_belong_to_origin_concept(
    paths, mismatched_endpoint
):
    from wiki_graph_projection import build_graph_projection

    _write_concept(paths, "seed.md", SEED, source_doc_ids=["FH-1"])
    _write_concept(paths, "related.md", RELATED)
    _write_entity(
        paths,
        "subject.md",
        SHARED,
        "Queue time",
        [RELATED] if mismatched_endpoint == "subject" else [SEED],
    )
    _write_entity(
        paths,
        "object.md",
        OTHER,
        "Natural oxidation",
        [RELATED] if mismatched_endpoint == "object" else [SEED],
    )
    _write_source(paths, "FH-1.md", "FH-1")
    _write_relation(
        paths,
        "wrong-origin-entity.md",
        "relation:wrong-origin-entity",
        SEED,
        SHARED,
        OTHER,
        ["FH-1"],
    )

    result = build_graph_projection(paths).expand_concepts([SEED])

    assert result.relations == []
    assert result.source_doc_ids == []


def test_relation_requires_sources_cited_by_its_origin_concept(paths):
    from wiki_graph_projection import build_graph_projection

    _write_concept(paths, "seed.md", SEED, source_doc_ids=["FH-OWNED"])
    _write_concept(paths, "related.md", RELATED, source_doc_ids=["FH-GLOBAL"])
    _write_entity(paths, "subject.md", SHARED, "Queue time", [SEED])
    _write_entity(paths, "object.md", OTHER, "Natural oxidation", [SEED])
    _write_source(paths, "FH-OWNED.md", "FH-OWNED")
    _write_source(paths, "FH-GLOBAL.md", "FH-GLOBAL")
    _write_relation(
        paths,
        "wrong-origin-source.md",
        "relation:wrong-origin-source",
        SEED,
        SHARED,
        OTHER,
        ["FH-GLOBAL"],
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
        ACTIVE_RELATION
    ]


def test_expansion_never_exceeds_requested_bounds(paths):
    from wiki_graph_projection import build_graph_projection

    _write_concept(
        paths,
        "seed.md",
        SEED,
        source_doc_ids=[f"FH-{index}" for index in range(4)],
    )
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


def test_relation_source_ids_are_trimmed_to_the_context_source_bound(paths):
    from wiki_graph_projection import build_graph_projection

    _write_concept(paths, "seed.md", SEED, source_doc_ids=["FH-1", "FH-2"])
    _write_entity(paths, "subject.md", SHARED, "Queue time", [SEED])
    _write_entity(paths, "object.md", OTHER, "Natural oxidation", [SEED])
    _write_source(paths, "FH-1.md", "FH-1")
    _write_source(paths, "FH-2.md", "FH-2")
    _write_relation(
        paths,
        "two-sources.md",
        "relation:two-sources",
        SEED,
        SHARED,
        OTHER,
        ["FH-1", "FH-2"],
    )

    result = build_graph_projection(paths).expand_concepts(
        [SEED], max_sources=1
    )

    assert result.source_doc_ids == ["FH-1"]
    assert len(result.relations) == 1
    assert result.relations[0].source_doc_ids == ["FH-1"]


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


def test_relation_requires_matching_origin_source_fingerprint(paths):
    from wiki_graph_projection import build_graph_projection

    _write_shared_graph(paths)
    relation_path = next(paths.relations.glob("*.md"))
    relation = frontmatter.load(relation_path)
    relation.metadata["source_fingerprint"] = "sha256:obsolete-source-set"
    relation_path.write_text(frontmatter.dumps(relation), encoding="utf-8")

    result = build_graph_projection(paths).expand_concepts([SEED])

    assert result.relations == []
    assert result.source_doc_ids == []


@pytest.mark.parametrize("node_type", ["entity", "relation", "source"])
def test_projection_rejects_materializer_note_at_forged_location(paths, node_type):
    from wiki_graph_projection import build_graph_projection

    _write_shared_graph(paths)
    directory = {
        "entity": paths.entities,
        "relation": paths.relations,
        "source": paths.sources,
    }[node_type]
    if node_type == "entity":
        original = next(
            path
            for path in directory.glob("*.md")
            if frontmatter.load(path).metadata["id"] == SHARED
        )
    else:
        original = next(directory.glob("*.md"))
    original.rename(directory / f"forged-{node_type}.md")

    result = build_graph_projection(paths).expand_concepts([SEED])

    assert result.relations == []
    assert result.source_doc_ids == []


@pytest.mark.parametrize("node_type", ["entity", "relation", "source"])
def test_projection_requires_materializer_owned_active_graph_notes(paths, node_type):
    from wiki_graph_projection import build_graph_projection

    _write_shared_graph(paths)
    directory = {
        "entity": paths.entities,
        "relation": paths.relations,
        "source": paths.sources,
    }[node_type]
    if node_type == "entity":
        path = next(
            path
            for path in directory.glob("*.md")
            if frontmatter.load(path).metadata["id"] == SHARED
        )
    else:
        path = next(directory.glob("*.md"))
    post = frontmatter.load(path)
    post.metadata["generated_by"] = "forged-writer"
    path.write_text(frontmatter.dumps(post), encoding="utf-8")

    result = build_graph_projection(paths).expand_concepts([SEED])

    assert result.relations == []


def test_projection_rejects_forged_relation_and_source_ids(paths):
    from wiki_graph_projection import build_graph_projection

    _write_shared_graph(paths)
    relation_path = next(paths.relations.glob("*.md"))
    relation = frontmatter.load(relation_path)
    relation.metadata["id"] = "relation:sha256:" + "0" * 64
    relation_path.write_text(frontmatter.dumps(relation), encoding="utf-8")
    source_path = paths.sources / "FH-1.md"
    source = frontmatter.load(source_path)
    source.metadata["id"] = "source:forged"
    source_path.write_text(frontmatter.dumps(source), encoding="utf-8")

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

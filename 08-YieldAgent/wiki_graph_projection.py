"""Read-only, one-hop projection of materialized Wiki graph frontmatter."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from types import MappingProxyType
from typing import Any, Iterable, Mapping

import frontmatter

from wiki_config import WikiPaths
from wiki_graph_models import GraphContext, GraphRelation, RelationPredicate
from wiki_plugin_notes import NoteNotFound, resolve_markdown_path


@dataclass(frozen=True)
class _ConceptRecord:
    concept_id: str
    source_doc_ids: tuple[str, ...]
    path: str


@dataclass(frozen=True)
class _EntityRecord:
    entity_id: str
    canonical_name: str
    entity_type: str
    source_concept_ids: tuple[str, ...]
    path: str


@dataclass(frozen=True)
class _RelationRecord:
    relation_id: str
    origin_concept_id: str
    subject_entity_id: str
    predicate: RelationPredicate
    object_entity_id: str
    confidence: float
    source_doc_ids: tuple[str, ...]
    path: str


@dataclass(frozen=True)
class _SourceRecord:
    doc_id: str
    path: str


@dataclass(frozen=True)
class _FileState:
    path: Path
    relative_path: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class WikiGraphProjection:
    fingerprint: str
    concepts: Mapping[str, _ConceptRecord]
    entities: Mapping[str, _EntityRecord]
    relations: Mapping[str, _RelationRecord]
    sources: Mapping[str, _SourceRecord]
    relations_by_concept: Mapping[str, tuple[str, ...]]
    concepts_by_entity: Mapping[str, tuple[str, ...]]
    concepts_by_source: Mapping[str, tuple[str, ...]]

    def expand_concepts(
        self,
        concept_ids: Iterable[str],
        *,
        max_relations: int = 10,
        max_related: int = 3,
        max_sources: int = 8,
    ) -> GraphContext:
        seeds = _deduplicate(
            concept_id
            for concept_id in concept_ids
            if concept_id in self.concepts
        )
        if not seeds:
            return GraphContext()

        relation_ids = _deduplicate(
            relation_id
            for concept_id in seeds
            for relation_id in self.relations_by_concept.get(concept_id, ())
        )[: max(0, max_relations)]
        selected_relations = [
            self.relations[relation_id] for relation_id in relation_ids
        ]
        source_doc_ids = _deduplicate(
            doc_id
            for relation in selected_relations
            for doc_id in relation.source_doc_ids
        )[: max(0, max_sources)]
        source_set = set(source_doc_ids)
        bounded_relations = [
            (
                relation,
                tuple(
                    doc_id
                    for doc_id in relation.source_doc_ids
                    if doc_id in source_set
                ),
            )
            for relation in selected_relations
        ]
        bounded_relations = [
            item for item in bounded_relations if item[1]
        ]

        related_candidates: list[str] = []
        for relation, relation_source_doc_ids in bounded_relations:
            for entity_id in (
                relation.subject_entity_id,
                relation.object_entity_id,
            ):
                related_candidates.extend(
                    self.concepts_by_entity.get(entity_id, ())
                )
            for doc_id in relation_source_doc_ids:
                related_candidates.extend(
                    self.concepts_by_source.get(doc_id, ())
                )

        seed_set = set(seeds)
        related = _deduplicate(
            concept_id
            for concept_id in related_candidates
            if concept_id in self.concepts and concept_id not in seed_set
        )[: max(0, max_related)]

        return GraphContext(
            primary_concept_id=seeds[0],
            concept_ids=[*seeds, *related],
            relations=[
                GraphRelation(
                    relation_id=relation.relation_id,
                    origin_concept_id=relation.origin_concept_id,
                    subject=self.entities[relation.subject_entity_id].canonical_name,
                    predicate=relation.predicate,
                    object=self.entities[relation.object_entity_id].canonical_name,
                    confidence=relation.confidence,
                    source_doc_ids=list(relation_source_doc_ids),
                )
                for relation, relation_source_doc_ids in bounded_relations
            ],
            source_doc_ids=source_doc_ids,
        )


_CACHE_LOCK = Lock()
_PROJECTION_CACHE: dict[Path, tuple[str, WikiGraphProjection]] = {}


def _deduplicate(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _exact_string(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    return value


def _exact_string_list(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        return None
    result: list[str] = []
    for item in value:
        exact = _exact_string(item)
        if exact is None:
            return None
        if exact not in result:
            result.append(exact)
    return tuple(result)


def _canonical_file(paths: WikiPaths, candidate: Path) -> Path | None:
    try:
        relative = candidate.relative_to(paths.root).as_posix()
        resolved = resolve_markdown_path(paths, relative)
    except (NoteNotFound, ValueError):
        return None
    if resolved != candidate:
        return None
    return resolved


def _file_states(paths: WikiPaths) -> tuple[_FileState, ...]:
    states: list[_FileState] = []
    for directory in (
        paths.concepts,
        paths.entities,
        paths.relations,
        paths.sources,
    ):
        for candidate in sorted(directory.glob("*.md")):
            resolved = _canonical_file(paths, candidate)
            if resolved is None:
                continue
            try:
                info = resolved.stat()
            except OSError:
                continue
            states.append(
                _FileState(
                    path=resolved,
                    relative_path=resolved.relative_to(paths.root).as_posix(),
                    size=info.st_size,
                    mtime_ns=info.st_mtime_ns,
                )
            )
    return tuple(states)


def _fingerprint(states: tuple[_FileState, ...]) -> str:
    payload = [
        [state.relative_path, state.size, state.mtime_ns]
        for state in states
    ]
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _metadata(state: _FileState) -> dict[str, Any] | None:
    try:
        return dict(frontmatter.load(state.path).metadata)
    except Exception:
        return None


def _unique(records: Iterable[Any], key: str) -> dict[str, Any]:
    unique: dict[str, Any] = {}
    duplicates: set[str] = set()
    for record in records:
        identity = getattr(record, key)
        if identity in unique:
            duplicates.add(identity)
        else:
            unique[identity] = record
    for identity in duplicates:
        unique.pop(identity, None)
    return unique


def _load_concept(state: _FileState, metadata: dict[str, Any]) -> _ConceptRecord | None:
    concept_id = _exact_string(metadata.get("id"))
    status = metadata.get("status", "active")
    if metadata.get("type") != "concept" or status != "active" or concept_id is None:
        return None
    citations = metadata.get("citations")
    if not isinstance(citations, list):
        citations = []
    source_doc_ids = _deduplicate(
        doc_id
        for citation in citations
        if isinstance(citation, dict)
        if (doc_id := _exact_string(citation.get("doc_id"))) is not None
    )
    return _ConceptRecord(
        concept_id=concept_id,
        source_doc_ids=tuple(source_doc_ids),
        path=state.relative_path,
    )


def _load_entity(state: _FileState, metadata: dict[str, Any]) -> _EntityRecord | None:
    entity_id = _exact_string(metadata.get("id"))
    canonical_name = _exact_string(metadata.get("canonical_name"))
    entity_type = _exact_string(metadata.get("entity_type"))
    source_concept_ids = _exact_string_list(metadata.get("source_concept_ids"))
    if (
        metadata.get("type") != "entity"
        or metadata.get("status") != "active"
        or entity_id is None
        or canonical_name is None
        or entity_type is None
        or source_concept_ids is None
    ):
        return None
    return _EntityRecord(
        entity_id=entity_id,
        canonical_name=canonical_name,
        entity_type=entity_type,
        source_concept_ids=source_concept_ids,
        path=state.relative_path,
    )


def _load_source(state: _FileState, metadata: dict[str, Any]) -> _SourceRecord | None:
    doc_id = _exact_string(metadata.get("doc_id"))
    if metadata.get("type") != "source" or doc_id is None:
        return None
    return _SourceRecord(doc_id=doc_id, path=state.relative_path)


def _load_relation(
    state: _FileState, metadata: dict[str, Any]
) -> _RelationRecord | None:
    relation_id = _exact_string(metadata.get("id"))
    origin_concept_id = _exact_string(metadata.get("origin_concept_id"))
    subject_entity_id = _exact_string(metadata.get("subject_entity_id"))
    object_entity_id = _exact_string(metadata.get("object_entity_id"))
    source_doc_ids = _exact_string_list(metadata.get("source_doc_ids"))
    confidence = metadata.get("confidence")
    try:
        predicate = RelationPredicate(metadata.get("predicate"))
    except (TypeError, ValueError):
        return None
    if (
        metadata.get("type") != "relation"
        or metadata.get("status") != "active"
        or relation_id is None
        or origin_concept_id is None
        or subject_entity_id is None
        or object_entity_id is None
        or not source_doc_ids
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0.0 <= confidence <= 1.0
    ):
        return None
    return _RelationRecord(
        relation_id=relation_id,
        origin_concept_id=origin_concept_id,
        subject_entity_id=subject_entity_id,
        predicate=predicate,
        object_entity_id=object_entity_id,
        confidence=float(confidence),
        source_doc_ids=source_doc_ids,
        path=state.relative_path,
    )


def _immutable_adjacency(
    pairs: Iterable[tuple[str, str]],
) -> Mapping[str, tuple[str, ...]]:
    values: dict[str, list[str]] = {}
    for key, relation_id in pairs:
        values.setdefault(key, []).append(relation_id)
    return MappingProxyType(
        {key: tuple(sorted(set(relation_ids))) for key, relation_ids in values.items()}
    )


def _build_projection(
    states: tuple[_FileState, ...], fingerprint: str
) -> WikiGraphProjection:
    loaded = [
        (state, metadata)
        for state in states
        if (metadata := _metadata(state)) is not None
    ]
    concepts = _unique(
        (
            record
            for state, metadata in loaded
            if state.path.parent.name == "concepts"
            if (record := _load_concept(state, metadata)) is not None
        ),
        "concept_id",
    )
    entities = _unique(
        (
            record
            for state, metadata in loaded
            if state.path.parent.name == "entities"
            if (record := _load_entity(state, metadata)) is not None
        ),
        "entity_id",
    )
    sources = _unique(
        (
            record
            for state, metadata in loaded
            if state.path.parent.name == "sources"
            if (record := _load_source(state, metadata)) is not None
        ),
        "doc_id",
    )
    candidate_relations = _unique(
        (
            record
            for state, metadata in loaded
            if state.path.parent.name == "relations"
            if (record := _load_relation(state, metadata)) is not None
        ),
        "relation_id",
    )
    relations: dict[str, _RelationRecord] = {}
    for relation_id, relation in candidate_relations.items():
        origin = concepts.get(relation.origin_concept_id)
        subject = entities.get(relation.subject_entity_id)
        object_entity = entities.get(relation.object_entity_id)
        if (
            origin is not None
            and subject is not None
            and object_entity is not None
            and relation.origin_concept_id in subject.source_concept_ids
            and relation.origin_concept_id in object_entity.source_concept_ids
            and all(
                doc_id in origin.source_doc_ids
                for doc_id in relation.source_doc_ids
            )
            and all(doc_id in sources for doc_id in relation.source_doc_ids)
        ):
            relations[relation_id] = relation

    return WikiGraphProjection(
        fingerprint=fingerprint,
        concepts=MappingProxyType(concepts),
        entities=MappingProxyType(entities),
        relations=MappingProxyType(relations),
        sources=MappingProxyType(sources),
        relations_by_concept=_immutable_adjacency(
            (relation.origin_concept_id, relation_id)
            for relation_id, relation in relations.items()
        ),
        concepts_by_entity=_immutable_adjacency(
            (entity_id, concept_id)
            for entity_id, entity in entities.items()
            for concept_id in entity.source_concept_ids
            if concept_id in concepts
        ),
        concepts_by_source=_immutable_adjacency(
            (doc_id, concept_id)
            for concept_id, concept in concepts.items()
            for doc_id in concept.source_doc_ids
            if doc_id in sources
        ),
    )


def build_graph_projection(paths: WikiPaths) -> WikiGraphProjection:
    """Return the cached immutable projection for the current Vault fingerprint."""
    states = _file_states(paths)
    fingerprint = _fingerprint(states)
    with _CACHE_LOCK:
        cached = _PROJECTION_CACHE.get(paths.root)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]

    projection = _build_projection(states, fingerprint)
    with _CACHE_LOCK:
        current = _PROJECTION_CACHE.get(paths.root)
        if current is not None and current[0] == fingerprint:
            return current[1]
        _PROJECTION_CACHE[paths.root] = (fingerprint, projection)
    return projection

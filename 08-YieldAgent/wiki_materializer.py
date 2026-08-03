"""Deterministically materialize Wiki metadata as Obsidian Markdown links."""
from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import frontmatter
from pydantic import ValidationError

from wiki_config import (
    WIKI_OVERVIEW_TEMPLATE,
    WIKI_PURPOSE_TEMPLATE,
    WIKI_SCHEMA_TEMPLATE,
    WikiConfigurationError,
    WikiPaths,
    validate_wiki_vault,
    validate_wiki_vault_paths,
)
from wiki_graph_models import EntityCandidate, RelationCandidate
from wiki_safe_mutation import GeneratedOwner, PinnedWikiMutation


_GENERATED_BY = "yield-wiki-materializer"
_EVIDENCE_GENERATED_BY = "yield-wiki-evidence-enricher"
_BLOCK_START = "<!-- yield-wiki:knowledge-links:start -->"
_BLOCK_END = "<!-- yield-wiki:knowledge-links:end -->"
_GRAPH_FILENAME_MAX_BYTES = 179
_UNSAFE_GRAPH_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_EVIDENCE_ID = re.compile(r"^EVD-[0-9a-f]{20}$")


@dataclass(frozen=True)
class MaterializationReport:
    created: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def changed_count(self) -> int:
        return len(self.created) + len(self.modified) + len(self.deleted)


@dataclass(frozen=True)
class _MaterializationPlan:
    targets: dict[Path, str]
    deletions: tuple[Path, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    target_owners: dict[Path, GeneratedOwner] = field(default_factory=dict)
    deletion_owners: dict[Path, GeneratedOwner] = field(default_factory=dict)


@dataclass(frozen=True)
class _Concept:
    path: Path
    concept_id: str
    status: str
    product: str
    fail_type: str
    cause_oper: str
    citations: tuple[dict[str, Any], ...]
    related_evidence: tuple[dict[str, Any], ...]
    entities: tuple[Any, ...]
    relations: tuple[Any, ...]
    source_fingerprint: str

    @property
    def title(self) -> str:
        return f"{self.product} {self.fail_type}"


@dataclass(frozen=True)
class _SuperConcept:
    path: Path
    super_id: str
    title: str
    source_concept_ids: tuple[str, ...]


def _stable_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", value)


def _stable_graph_id(kind: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"{kind}:sha256:{hashlib.sha256(encoded).hexdigest()}"


def _relation_label(subject: str, predicate: str, object_name: str) -> str:
    return f"{subject} {predicate} {object_name}"


def _truncate_utf8(value: str, byte_limit: int) -> str:
    while len(value.encode("utf-8")) > byte_limit:
        value = value[:-1]
    return value


def _readable_graph_path(
    directory: Path,
    node_id: str,
    label: str,
) -> Path:
    digest = node_id.rsplit(":", 1)[-1]
    suffix = f"--{digest[:8]}.md"
    readable = _UNSAFE_GRAPH_FILENAME.sub("_", label)
    readable = re.sub(r"_+", "_", readable).strip(" .")
    fallback = node_id.split(":", 1)[0]
    byte_limit = _GRAPH_FILENAME_MAX_BYTES - len(suffix.encode("utf-8"))
    readable = _truncate_utf8(readable or fallback, byte_limit).rstrip(" .")
    return directory / f"{readable or fallback}{suffix}"


def _relative(paths: WikiPaths, path: Path) -> str:
    return path.relative_to(paths.root).as_posix()


def _wikilink(paths: WikiPaths, path: Path, label: str) -> str:
    target = path.relative_to(paths.root).with_suffix("").as_posix()
    return f"[[{target}|{label}]]"


def _render_post(metadata: dict[str, Any], body: str) -> str:
    return frontmatter.dumps(frontmatter.Post(content=body.rstrip() + "\n", **metadata))


def _generated_metadata(node_id: str, node_type: str, **values: Any) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "generated_by": _GENERATED_BY,
        **values,
    }


def _owner_from_rendered_target(content: str) -> GeneratedOwner | None:
    try:
        metadata = frontmatter.loads(content).metadata
    except Exception:
        return None
    if metadata.get("generated_by") != _GENERATED_BY:
        return None
    node_type = str(metadata.get("type") or "")
    node_id = str(metadata.get("id") or "")
    if not node_type or not node_id:
        return None
    return GeneratedOwner(_GENERATED_BY, node_type, node_id)


def _bootstrap_scaffold(paths: WikiPaths, path: Path, content: str) -> bool:
    return (path == paths.index and content == "# Wiki Index\n\n") or (
        path == paths.overview and content == WIKI_OVERVIEW_TEMPLATE
    )


def _preflight_generated_targets(
    paths: WikiPaths,
    targets: dict[Path, str],
) -> tuple[dict[Path, GeneratedOwner], list[str]]:
    owners: dict[Path, GeneratedOwner] = {}
    errors: list[str] = []
    for path, content in targets.items():
        owner = _owner_from_rendered_target(content)
        if owner is None:
            continue
        try:
            info = path.lstat()
        except FileNotFoundError:
            owners[path] = owner
            continue
        if not stat.S_ISREG(info.st_mode):
            errors.append(
                f"generated path collision: {_relative(paths, path)} is not a regular file"
            )
            continue
        try:
            existing = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(
                f"generated path collision: {_relative(paths, path)} is unreadable: {exc}"
            )
            continue
        if _bootstrap_scaffold(paths, path, existing):
            continue
        try:
            metadata = frontmatter.loads(existing).metadata
        except Exception as exc:
            errors.append(
                f"generated path collision: {_relative(paths, path)} is unreadable: {exc}"
            )
            continue
        actual = (
            metadata.get("generated_by"),
            metadata.get("type"),
            metadata.get("id"),
        )
        expected = (owner.generated_by, owner.node_type, owner.node_id)
        if actual != expected:
            errors.append(
                f"generated path collision: {_relative(paths, path)} owner "
                f"{actual!r} != {expected!r}"
            )
            continue
        owners[path] = owner
    return owners, errors


def _namespace_deletion_owner(
    paths: WikiPaths,
    path: Path,
    node_type: str,
    metadata: dict[str, Any],
) -> GeneratedOwner | None:
    if metadata.get("generated_by") != _GENERATED_BY or metadata.get("type") != node_type:
        return None
    if node_type == "product":
        product = str(metadata.get("product") or "")
        node_id = f"product:{product}"
        expected_path = paths.products / f"{_stable_filename(product)}.md"
    elif node_type == "product_fail":
        product = str(metadata.get("product") or "")
        fail_type = str(metadata.get("fail_type") or "")
        node_id = f"product_fail:{product}|{fail_type}"
        expected_path = paths.product_fails / (
            f"{_stable_filename(product)}_{_stable_filename(fail_type)}.md"
        )
    elif node_type == "operation":
        cause_oper = str(metadata.get("cause_oper") or "")
        node_id = f"operation:{cause_oper}"
        expected_path = paths.operations / f"{_stable_filename(cause_oper)}.md"
    elif node_type == "source":
        doc_id = str(metadata.get("doc_id") or "")
        node_id = f"source:{doc_id}"
        expected_path = paths.sources / f"{_stable_filename(doc_id)}.md"
    else:
        return None
    if not node_id.split(":", 1)[1] or path != expected_path or metadata.get("id") != node_id:
        return None
    return GeneratedOwner(_GENERATED_BY, node_type, node_id)


def _scan_generated_graph_paths(
    paths: WikiPaths,
) -> tuple[dict[str, list[Path]], list[str]]:
    by_id: dict[str, list[Path]] = {}
    errors: list[str] = []
    for directory, node_type in (
        (paths.entities, "entity"),
        (paths.relations, "relation"),
    ):
        for path in sorted(directory.glob("*.md")):
            try:
                metadata = frontmatter.load(path).metadata
            except Exception:
                continue
            if metadata.get("generated_by") != _GENERATED_BY:
                continue
            if metadata.get("type") != node_type:
                continue
            node_id = str(metadata.get("id") or "")
            if not node_id:
                errors.append(f"{_relative(paths, path)}: generated graph note missing id")
                continue
            by_id.setdefault(node_id, []).append(path)
    return by_id, errors


def _replace_managed_block(raw: str, block_body: str) -> str:
    block = f"{_BLOCK_START}\n## Knowledge Links\n\n{block_body.rstrip()}\n{_BLOCK_END}"
    start = raw.find(_BLOCK_START)
    end = raw.find(_BLOCK_END)
    if start == -1 and end == -1:
        return raw.rstrip() + "\n\n" + block + "\n"
    if start == -1 or end == -1 or end < start:
        raise ValueError("unbalanced managed Knowledge Links markers")
    end += len(_BLOCK_END)
    return raw[:start] + block + raw[end:]


def _read_concepts(paths: WikiPaths) -> tuple[list[_Concept], list[str]]:
    concepts: list[_Concept] = []
    errors: list[str] = []
    for path in sorted(paths.concepts.glob("*.md")):
        try:
            post = frontmatter.load(path)
        except Exception as exc:
            errors.append(f"{_relative(paths, path)}: invalid frontmatter: {exc}")
            continue
        metadata = post.metadata
        required = {
            key: str(metadata.get(key) or "").strip()
            for key in ("id", "product", "fail_type", "cause_oper")
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            errors.append(
                f"{_relative(paths, path)}: missing metadata: {', '.join(missing)}"
            )
            continue
        citations = tuple(
            citation
            for citation in (metadata.get("citations") or [])
            if isinstance(citation, dict)
        )
        related_evidence = tuple(
            item
            for item in (metadata.get("related_evidence") or [])
            if isinstance(item, dict)
        )
        raw_entities = metadata.get("entities") or []
        entities = tuple(raw_entities if isinstance(raw_entities, list) else [raw_entities])
        raw_relations = metadata.get("relations") or []
        relations = tuple(
            raw_relations if isinstance(raw_relations, list) else [raw_relations]
        )
        concepts.append(
            _Concept(
                path=path,
                concept_id=required["id"],
                status=str(metadata.get("status") or "active").strip(),
                product=required["product"],
                fail_type=required["fail_type"],
                cause_oper=required["cause_oper"],
                citations=citations,
                related_evidence=related_evidence,
                entities=entities,
                relations=relations,
                source_fingerprint=str(metadata.get("source_fingerprint") or "").strip(),
            )
        )
    return concepts, errors


def _read_super_concepts(
    paths: WikiPaths,
) -> tuple[list[_SuperConcept], list[str]]:
    super_concepts: list[_SuperConcept] = []
    errors: list[str] = []
    for path in sorted(paths.super_concepts.glob("*.md")):
        try:
            post = frontmatter.load(path)
        except Exception as exc:
            errors.append(f"{_relative(paths, path)}: invalid frontmatter: {exc}")
            continue
        metadata = post.metadata
        super_id = str(metadata.get("id") or "").strip()
        axis = str(metadata.get("axis") or "").strip()
        axis_value = str(metadata.get("axis_value") or "").strip()
        if not super_id or not axis or not axis_value:
            errors.append(
                f"{_relative(paths, path)}: missing metadata: id, axis, or axis_value"
            )
            continue
        source_ids = tuple(
            str(value).strip()
            for value in (metadata.get("source_concept_ids") or [])
            if str(value).strip()
        )
        super_concepts.append(
            _SuperConcept(
                path=path,
                super_id=super_id,
                title=f"{axis}={axis_value}",
                source_concept_ids=source_ids,
            )
        )
    return super_concepts, errors


def _build_plan(paths: WikiPaths) -> _MaterializationPlan:
    concepts, errors = _read_concepts(paths)
    super_concepts, super_errors = _read_super_concepts(paths)
    errors.extend(super_errors)
    if errors:
        return _MaterializationPlan({}, (), (), tuple(errors))

    targets: dict[Path, str] = {}
    warnings: list[str] = []
    product_fails: dict[tuple[str, str], set[str]] = {}
    products: dict[str, set[tuple[str, str]]] = {}
    operations: dict[str, set[tuple[str, str]]] = {}
    operation_concepts: dict[str, list[_Concept]] = {}
    sources: dict[str, dict[str, Any]] = {}
    related_sources: dict[str, dict[str, Any]] = {}
    concepts_by_id = {concept.concept_id: concept for concept in concepts}
    concept_super_links: dict[str, list[_SuperConcept]] = {}

    for super_concept in super_concepts:
        for concept_id in super_concept.source_concept_ids:
            if concept_id in concepts_by_id:
                concept_super_links.setdefault(concept_id, []).append(super_concept)

    for concept in concepts:
        product_fail = (concept.product, concept.fail_type)
        products.setdefault(concept.product, set()).add(product_fail)
        product_fails.setdefault(product_fail, set()).add(concept.cause_oper)
        operations.setdefault(concept.cause_oper, set()).add(product_fail)
        operation_concepts.setdefault(concept.cause_oper, []).append(concept)
        for citation in concept.citations:
            doc_id = str(citation.get("doc_id") or "").strip()
            if not doc_id:
                errors.append(
                    f"{_relative(paths, concept.path)}: citation missing doc_id"
                )
                continue
            source = sources.get(doc_id)
            if source is None:
                source = dict(citation)
                source["concepts"] = []
                sources[doc_id] = source
            else:
                for key in ("source_file", "date", "page_num", "download_url"):
                    current = source.get(key)
                    incoming = citation.get(key)
                    if current not in (None, "") and incoming not in (None, ""):
                        if current != incoming:
                            errors.append(
                                f"source:{doc_id}: conflicting {key}: "
                                f"{current!r} != {incoming!r}"
                            )
                    elif incoming not in (None, ""):
                        source[key] = incoming
            source["concepts"].append(concept)
        for item in concept.related_evidence:
            doc_id = str(item.get("doc_id") or "").strip()
            source_index = str(item.get("source_index") or "").strip()
            content_sha256 = str(item.get("content_sha256") or "").strip()
            source_file = str(item.get("source_file") or "").strip()
            relation = str(item.get("relation") or "").strip()
            if (
                not _EVIDENCE_ID.fullmatch(doc_id)
                or not source_index
                or any(value in source_index for value in ("*", "?", ","))
                or not re.fullmatch(r"[0-9a-f]{64}", content_sha256)
                or Path(source_file).name != source_file
                or relation
                not in {
                    "supporting_context",
                    "possible_cause",
                    "possible_action",
                    "contradiction",
                }
            ):
                errors.append(
                    f"{_relative(paths, concept.path)}: invalid related evidence metadata"
                )
                continue
            source_path = paths.sources / f"{_stable_filename(doc_id)}.md"
            try:
                info = source_path.lstat()
            except FileNotFoundError:
                errors.append(
                    f"{_relative(paths, concept.path)}: missing related evidence Source: {doc_id}"
                )
                continue
            if not stat.S_ISREG(info.st_mode):
                errors.append(
                    f"{_relative(paths, concept.path)}: invalid related evidence Source owner: {doc_id}"
                )
                continue
            try:
                source_post = frontmatter.load(source_path)
            except Exception:
                errors.append(
                    f"{_relative(paths, concept.path)}: invalid related evidence Source owner: {doc_id}"
                )
                continue
            owner = (
                source_post.metadata.get("generated_by"),
                source_post.metadata.get("type"),
                source_post.metadata.get("id"),
            )
            if owner != (
                _EVIDENCE_GENERATED_BY,
                "source",
                f"source:{doc_id}",
            ):
                errors.append(
                    f"{_relative(paths, concept.path)}: invalid related evidence Source owner: {doc_id}"
                )
                continue
            compared = {
                "doc_id": doc_id,
                "source_index": source_index,
                "source_file": source_file,
                "page_num": item.get("page_num"),
                "content_sha256": content_sha256,
            }
            observed = {key: source_post.metadata.get(key) for key in compared}
            if observed != compared:
                errors.append(
                    f"{_relative(paths, concept.path)}: related evidence metadata mismatch: {doc_id}"
                )
                continue
            existing = related_sources.get(doc_id)
            if existing is not None and existing["metadata"] != compared:
                errors.append(f"source:{doc_id}: conflicting related evidence metadata")
                continue
            related_sources.setdefault(
                doc_id,
                {"path": source_path, "metadata": compared},
            )

    if errors:
        return _MaterializationPlan({}, (), (), tuple(sorted(set(errors))))

    product_paths = {
        product: paths.products / f"{_stable_filename(product)}.md"
        for product in products
    }
    product_fail_paths = {
        key: paths.product_fails
        / f"{_stable_filename(key[0])}_{_stable_filename(key[1])}.md"
        for key in product_fails
    }
    operation_paths = {
        operation: paths.operations / f"{_stable_filename(operation)}.md"
        for operation in operations
    }
    source_paths = {
        doc_id: paths.sources / f"{_stable_filename(doc_id)}.md"
        for doc_id in sources
    }

    entity_records: dict[str, dict[str, Any]] = {}
    concept_entity_names: dict[str, set[str]] = {}
    concept_relation_ids: dict[str, list[str]] = {}
    relation_records: dict[str, dict[str, Any]] = {}

    for concept in concepts:
        names = concept_entity_names.setdefault(concept.concept_id, set())
        if concept.status != "active":
            continue
        for index, candidate in enumerate(concept.entities):
            try:
                entity = EntityCandidate.model_validate(candidate)
            except ValidationError as exc:
                warnings.append(
                    f"{_relative(paths, concept.path)}: entity[{index}] invalid: "
                    f"{exc.errors()[0]['msg']}"
                )
                continue
            canonical_name = entity.canonical_name
            entity_type = entity.entity_type
            names.add(canonical_name)
            entity_id = _stable_graph_id(
                "entity", {"canonical_name": canonical_name}
            )
            record = entity_records.setdefault(
                canonical_name,
                {
                    "entity_id": entity_id,
                    "canonical_name": canonical_name,
                    "entity_type": entity_type,
                    "concepts": [],
                    "relation_ids": set(),
                },
            )
            if concept not in record["concepts"]:
                record["concepts"].append(concept)

    for concept in concepts:
        if concept.status != "active":
            continue
        citation_doc_ids = {
            str(citation.get("doc_id") or "").strip()
            for citation in concept.citations
            if str(citation.get("doc_id") or "").strip()
        }
        for index, candidate in enumerate(concept.relations):
            prefix = f"{_relative(paths, concept.path)}: relation[{index}]"
            try:
                relation = RelationCandidate.model_validate(candidate)
            except ValidationError as exc:
                warnings.append(f"{prefix} invalid: {exc.errors()[0]['msg']}")
                continue
            subject = relation.subject
            object_name = relation.object
            missing_endpoints = [
                name
                for name in (subject, object_name)
                if not name or name not in concept_entity_names[concept.concept_id]
            ]
            if missing_endpoints:
                warnings.append(
                    f"{prefix} missing endpoint: {', '.join(missing_endpoints) or '<empty>'}"
                )
                continue
            predicate = relation.predicate.value
            confidence = relation.confidence
            source_doc_ids = relation.source_doc_ids
            missing_sources = [
                doc_id for doc_id in source_doc_ids if doc_id not in citation_doc_ids
            ]
            if not source_doc_ids or missing_sources:
                detail = ", ".join(missing_sources) if missing_sources else "<empty>"
                warnings.append(f"{prefix} invalid source_doc_ids: {detail}")
                continue
            relation_id = _stable_graph_id(
                "relation",
                {
                    "origin_concept_id": concept.concept_id,
                    "subject": subject,
                    "predicate": predicate,
                    "object": object_name,
                },
            )
            relation_records[relation_id] = {
                "relation_id": relation_id,
                "concept": concept,
                "subject": subject,
                "subject_entity_id": entity_records[subject]["entity_id"],
                "predicate": predicate,
                "object": object_name,
                "object_entity_id": entity_records[object_name]["entity_id"],
                "display_label": _relation_label(subject, predicate, object_name),
                "confidence": confidence,
                "source_doc_ids": source_doc_ids,
            }
            concept_relation_ids.setdefault(concept.concept_id, []).append(relation_id)
            entity_records[subject]["relation_ids"].add(relation_id)
            entity_records[object_name]["relation_ids"].add(relation_id)

    entity_paths = {
        record["entity_id"]: _readable_graph_path(
            paths.entities, record["entity_id"], record["canonical_name"]
        )
        for record in entity_records.values()
    }
    relation_paths = {
        relation_id: _readable_graph_path(
            paths.relations,
            relation_id,
            relation_records[relation_id]["display_label"],
        )
        for relation_id in relation_records
    }

    generated_graph_paths, scan_errors = _scan_generated_graph_paths(paths)
    errors.extend(scan_errors)
    migration_deletions: set[Path] = set()
    migration_deletion_owners: dict[Path, GeneratedOwner] = {}
    for node_id, path in (*entity_paths.items(), *relation_paths.items()):
        digest = node_id.rsplit(":", 1)[-1]
        legacy_path = path.with_name(f"{digest}.md")
        existing_paths = generated_graph_paths.get(node_id, [])
        legacy_paths = [candidate for candidate in existing_paths if candidate == legacy_path]
        noncanonical_paths = [
            candidate
            for candidate in existing_paths
            if candidate not in (path, legacy_path)
        ]
        if len(legacy_paths) > 1 or noncanonical_paths:
            errors.append(
                f"duplicate generated graph id: {node_id} claims "
                + ", ".join(
                    _relative(paths, candidate)
                    for candidate in sorted(existing_paths)
                )
            )
        else:
            migration_deletions.update(legacy_paths)
            node_type = node_id.split(":", 1)[0]
            for legacy_path in legacy_paths:
                migration_deletion_owners[legacy_path] = GeneratedOwner(
                    _GENERATED_BY, node_type, node_id
                )
        if not path.exists():
            continue
        try:
            metadata = frontmatter.load(path).metadata
        except Exception as exc:
            errors.append(
                f"generated path collision: {_relative(paths, path)} is unreadable: {exc}"
            )
            continue
        if metadata.get("generated_by") != _GENERATED_BY or metadata.get("id") != node_id:
            errors.append(
                f"generated path collision: {node_id} resolves to {_relative(paths, path)}"
            )

    for path_map in (
        product_paths,
        product_fail_paths,
        operation_paths,
        source_paths,
    ):
        owners: dict[Path, object] = {}
        for owner, path in path_map.items():
            previous = owners.setdefault(path, owner)
            if previous != owner:
                errors.append(
                    f"generated path collision: {previous!r} and {owner!r} "
                    f"both resolve to {_relative(paths, path)}"
                )

    if errors:
        return _MaterializationPlan(
            {}, (), tuple(sorted(set(warnings))), tuple(sorted(set(errors)))
        )

    for product in sorted(products):
        links = [
            f"- {_wikilink(paths, product_fail_paths[key], key[1])}"
            for key in sorted(products[product])
        ]
        targets[product_paths[product]] = _render_post(
            _generated_metadata(
                f"product:{product}", "product", product=product
            ),
            f"# {product}\n\n## Product Fails\n\n" + "\n".join(links),
        )

    for key in sorted(product_fails):
        product, fail_type = key
        operation_links = [
            f"- {_wikilink(paths, operation_paths[operation], operation)}"
            for operation in sorted(product_fails[key])
        ]
        body = (
            f"# {product} / {fail_type}\n\n"
            f"- Product: {_wikilink(paths, product_paths[product], product)}\n\n"
            "## Cause Operations\n\n"
            + "\n".join(operation_links)
        )
        targets[product_fail_paths[key]] = _render_post(
            _generated_metadata(
                f"product_fail:{product}|{fail_type}",
                "product_fail",
                product=product,
                fail_type=fail_type,
            ),
            body,
        )

    for operation in sorted(operations):
        parent_links = [
            f"- {_wikilink(paths, product_fail_paths[key], f'{key[0]} {key[1]}')}"
            for key in sorted(operations[operation])
        ]
        concept_links = [
            f"- {_wikilink(paths, concept.path, concept.title)}"
            for concept in sorted(
                operation_concepts[operation], key=lambda item: item.concept_id
            )
        ]
        body = (
            f"# {operation}\n\n## Product Fails\n\n"
            + "\n".join(parent_links)
            + "\n\n## Concepts\n\n"
            + "\n".join(concept_links)
        )
        targets[operation_paths[operation]] = _render_post(
            _generated_metadata(
                f"operation:{operation}", "operation", cause_oper=operation
            ),
            body,
        )

    for canonical_name in sorted(entity_records):
        record = entity_records[canonical_name]
        entity_id = record["entity_id"]
        concept_links = [
            f"- {_wikilink(paths, concept.path, concept.title)}"
            for concept in sorted(record["concepts"], key=lambda item: item.concept_id)
        ]
        relation_links = [
            f"- {_wikilink(paths, relation_paths[relation_id], relation_records[relation_id]['display_label'])}"
            for relation_id in sorted(record["relation_ids"])
        ]
        body = (
            f"# {canonical_name}\n\n"
            f"- Entity Type: {record['entity_type']}\n\n"
            "## Concepts\n\n"
            + "\n".join(concept_links)
        )
        if relation_links:
            body += "\n\n## Active Relations\n\n" + "\n".join(relation_links)
        targets[entity_paths[entity_id]] = _render_post(
            _generated_metadata(
                entity_id,
                "entity",
                canonical_name=canonical_name,
                entity_type=record["entity_type"],
                status="active",
                source_concept_ids=sorted(
                    concept.concept_id for concept in record["concepts"]
                ),
            ),
            body,
        )

    for relation_id in sorted(relation_records):
        relation = relation_records[relation_id]
        concept = relation["concept"]
        subject_path = entity_paths[relation["subject_entity_id"]]
        object_path = entity_paths[relation["object_entity_id"]]
        source_links = [
            f"  - {_wikilink(paths, source_paths[doc_id], doc_id)}"
            for doc_id in relation["source_doc_ids"]
        ]
        body = (
            f"# {relation['subject']} {relation['predicate']} {relation['object']}\n\n"
            f"- Subject: {_wikilink(paths, subject_path, relation['subject'])}\n"
            f"- Predicate: `{relation['predicate']}`\n"
            f"- Object: {_wikilink(paths, object_path, relation['object'])}\n"
            f"- Concept: {_wikilink(paths, concept.path, concept.title)}\n"
            "- Sources:\n"
            + "\n".join(source_links)
            + f"\n- Confidence: {relation['confidence']}"
        )
        targets[relation_paths[relation_id]] = _render_post(
            _generated_metadata(
                relation_id,
                "relation",
                origin_concept_id=concept.concept_id,
                subject_entity_id=relation["subject_entity_id"],
                predicate=relation["predicate"],
                object_entity_id=relation["object_entity_id"],
                confidence=relation["confidence"],
                source_doc_ids=relation["source_doc_ids"],
                status="active",
                source_fingerprint=concept.source_fingerprint,
            ),
            body,
        )

    for concept in concepts:
        source_links = [
            _wikilink(paths, source_paths[doc_id], doc_id)
            for doc_id in sorted(
                {
                    str(citation.get("doc_id") or "").strip()
                    for citation in concept.citations
                    if str(citation.get("doc_id") or "").strip()
                }
            )
        ]
        lines = [
            f"- Operation: {_wikilink(paths, operation_paths[concept.cause_oper], concept.cause_oper)}"
        ]
        if source_links:
            lines.append("- Sources:")
            lines.extend(f"  - {link}" for link in source_links)
        related_links = []
        for item in sorted(
            concept.related_evidence,
            key=lambda value: str(value.get("doc_id") or ""),
        ):
            doc_id = str(item.get("doc_id") or "").strip()
            record = related_sources.get(doc_id)
            if record is None:
                continue
            source_file = str(item.get("source_file") or "").strip()
            page_num = item.get("page_num")
            label = source_file or doc_id
            if page_num not in (None, ""):
                label = f"{label} · p.{page_num}"
            related_links.append(_wikilink(paths, record["path"], label))
        if related_links:
            lines.append("- Related Evidence:")
            lines.extend(f"  - {link}" for link in related_links)
        entity_links = [
            _wikilink(
                paths,
                entity_paths[entity_records[name]["entity_id"]],
                name,
            )
            for name in sorted(concept_entity_names[concept.concept_id])
        ]
        if entity_links:
            lines.append("- Entities:")
            lines.extend(f"  - {link}" for link in entity_links)
        relation_links = [
            _wikilink(
                paths,
                relation_paths[relation_id],
                relation_records[relation_id]["display_label"],
            )
            for relation_id in sorted(concept_relation_ids.get(concept.concept_id, []))
        ]
        if relation_links:
            lines.append("- Relations:")
            lines.extend(f"  - {link}" for link in relation_links)
        super_links = [
            _wikilink(paths, item.path, item.title)
            for item in sorted(
                concept_super_links.get(concept.concept_id, []),
                key=lambda item: item.super_id,
            )
        ]
        if super_links:
            lines.append("- Super Concepts:")
            lines.extend(f"  - {link}" for link in super_links)
        raw = concept.path.read_text(encoding="utf-8")
        try:
            targets[concept.path] = _replace_managed_block(raw, "\n".join(lines))
        except ValueError as exc:
            errors.append(f"{_relative(paths, concept.path)}: {exc}")

    for super_concept in super_concepts:
        post = frontmatter.load(super_concept.path)
        metadata = dict(post.metadata)
        valid = [
            concepts_by_id[concept_id]
            for concept_id in super_concept.source_concept_ids
            if concept_id in concepts_by_id
        ]
        missing = [
            concept_id
            for concept_id in super_concept.source_concept_ids
            if concept_id not in concepts_by_id
        ]
        lines: list[str] = []
        if valid:
            lines.append("- Concepts:")
            lines.extend(
                f"  - {_wikilink(paths, concept.path, concept.title)}"
                for concept in sorted(valid, key=lambda item: item.concept_id)
            )
        if missing:
            lines.append("- Missing References:")
            lines.extend(f"  - {concept_id}" for concept_id in sorted(missing))
        metadata["status"] = "stale" if missing else "reference_only"
        try:
            body = _replace_managed_block(post.content or "", "\n".join(lines))
        except ValueError as exc:
            errors.append(f"{_relative(paths, super_concept.path)}: {exc}")
            continue
        targets[super_concept.path] = frontmatter.dumps(
            frontmatter.Post(content=body, **metadata)
        )

    for doc_id in sorted(sources):
        source = sources[doc_id]
        backlinks = [
            f"- {_wikilink(paths, concept.path, concept.title)}"
            for concept in sorted(source["concepts"], key=lambda item: item.concept_id)
        ]
        details = []
        for key, label in (
            ("source_file", "Source File"),
            ("date", "Date"),
            ("page_num", "Page"),
            ("download_url", "Download"),
        ):
            value = source.get(key)
            if value not in (None, ""):
                details.append(f"- {label}: {value}")
        body = (
            f"# {doc_id}\n\n"
            + "\n".join(details)
            + "\n\n## Cited By\n\n"
            + "\n".join(backlinks)
        )
        metadata_values = {
            key: source[key]
            for key in ("source_file", "date", "page_num", "download_url")
            if source.get(key) not in (None, "")
        }
        targets[source_paths[doc_id]] = _render_post(
            _generated_metadata(
                f"source:{doc_id}", "source", doc_id=doc_id, **metadata_values
            ),
            body,
        )

    active_graph_targets = set(entity_paths.values()) | set(relation_paths.values())
    for directory, node_type in (
        (paths.entities, "entity"),
        (paths.relations, "relation"),
    ):
        for path in sorted(directory.glob("*.md")):
            if path in migration_deletions:
                continue
            if path in active_graph_targets:
                continue
            try:
                post = frontmatter.load(path)
            except Exception:
                continue
            if (
                post.metadata.get("generated_by") != _GENERATED_BY
                or post.metadata.get("type") != node_type
            ):
                continue
            if post.metadata.get("status") == "stale":
                targets[path] = path.read_text(encoding="utf-8")
                continue
            metadata = dict(post.metadata)
            metadata["status"] = "stale"
            targets[path] = frontmatter.dumps(
                frontmatter.Post(content=post.content, **metadata)
            )

    counts = (
        f"- Products: {len(products)}\n"
        f"- Product Fails: {len(product_fails)}\n"
        f"- Operations: {len(operations)}\n"
        f"- Concepts: {len(concepts)}\n"
        f"- Entities: {len(entity_records)}\n"
        f"- Relations: {len(relation_records)}\n"
        f"- Super Concepts: {len(super_concepts)}\n"
        f"- Sources: {len(sources)}"
    )
    index_sections = [
        "## Products\n\n"
        + "\n".join(
            f"- {_wikilink(paths, product_paths[key], key)}"
            for key in sorted(product_paths)
        ),
        "## Concepts\n\n"
        + "\n".join(
            f"- {_wikilink(paths, concept.path, concept.title)}"
            for concept in sorted(concepts, key=lambda item: item.concept_id)
        ),
        "## Entities\n\n"
        + "\n".join(
            f"- {_wikilink(paths, entity_paths[record['entity_id']], canonical_name)}"
            for canonical_name, record in sorted(entity_records.items())
        ),
        "## Relations\n\n"
        + "\n".join(
            f"- {_wikilink(paths, relation_paths[relation_id], relation_records[relation_id]['display_label'])}"
            for relation_id in sorted(relation_records)
        ),
        "## Sources\n\n"
        + "\n".join(
            f"- {_wikilink(paths, source_paths[key], key)}"
            for key in sorted(source_paths)
        ),
        "## Super Concepts\n\n"
        + "\n".join(
            f"- {_wikilink(paths, item.path, item.title)}"
            for item in sorted(super_concepts, key=lambda item: item.super_id)
        ),
    ]
    targets[paths.index] = _render_post(
        _generated_metadata("wiki:index", "index"),
        "# Wiki Index\n\n## Counts\n\n"
        + counts
        + "\n\n"
        + "\n\n".join(index_sections),
    )
    targets[paths.overview] = _render_post(
        _generated_metadata("wiki:overview", "overview"),
        "# Wiki Overview\n\n"
        + counts
        + "\n\n## Start Here\n\n"
        + "\n".join(
            f"- {_wikilink(paths, product_paths[key], key)}"
            for key in sorted(product_paths)
        ),
    )
    if not paths.purpose.exists():
        targets[paths.purpose] = WIKI_PURPOSE_TEMPLATE
    if not paths.schema.exists():
        targets[paths.schema] = WIKI_SCHEMA_TEMPLATE
    if not paths.graph_config.exists():
        targets[paths.graph_config] = json.dumps(
            {
                "collapse-filter": True,
                "search": "-file:index -file:log -path:lint_logs",
                "showTags": False,
                "showAttachments": False,
                "hideUnresolved": False,
                "showOrphans": True,
                "collapse-color-groups": True,
                "colorGroups": [],
                "collapse-display": True,
                "showArrow": False,
                "textFadeMultiplier": 0,
                "nodeSizeMultiplier": 1,
                "lineSizeMultiplier": 1,
                "collapse-forces": True,
                "centerStrength": 0.518713248970312,
                "repelStrength": 10,
                "linkStrength": 1,
                "linkDistance": 250,
                "scale": 1,
                "close": True,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n"

    deletions: list[Path] = []
    deletion_owners = dict(migration_deletion_owners)
    target_paths = set(targets)
    for directory, node_type in (
        (paths.products, "product"),
        (paths.product_fails, "product_fail"),
        (paths.operations, "operation"),
        (paths.sources, "source"),
    ):
        for path in sorted(directory.glob("*.md")):
            if path in target_paths:
                continue
            try:
                metadata = dict(frontmatter.load(path).metadata)
            except Exception:
                continue
            if metadata.get("generated_by") != _GENERATED_BY:
                continue
            owner = _namespace_deletion_owner(
                paths, path, node_type, metadata
            )
            if owner is None:
                errors.append(
                    f"generated path collision: {_relative(paths, path)} has invalid ownership"
                )
                continue
            deletions.append(path)
            deletion_owners[path] = owner

    target_owners, owner_errors = _preflight_generated_targets(paths, targets)
    errors.extend(owner_errors)
    if errors:
        return _MaterializationPlan(
            {}, (), tuple(sorted(set(warnings))), tuple(sorted(set(errors)))
        )

    return _MaterializationPlan(
        targets,
        tuple(sorted(migration_deletions)) + tuple(sorted(deletions)),
        tuple(sorted(set(warnings))),
        tuple(sorted(set(errors))),
        target_owners,
        deletion_owners,
    )


def _execute_plan(
    paths: WikiPaths,
    plan: _MaterializationPlan,
    *,
    apply: bool,
) -> MaterializationReport:
    created: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    unchanged: list[str] = []
    if apply:
        with PinnedWikiMutation(paths) as mutation:
            for path, content in sorted(
                plan.targets.items(), key=lambda item: str(item[0])
            ):
                relative = _relative(paths, path)
                expected = mutation.snapshot(path)
                if not expected.exists:
                    created.append(relative)
                elif expected.content == content.encode("utf-8"):
                    unchanged.append(relative)
                    continue
                else:
                    modified.append(relative)
                mutation.replace_text(
                    path,
                    content,
                    expected=expected,
                    owner=plan.target_owners.get(path),
                )
            for path in plan.deletions:
                relative = _relative(paths, path)
                expected = mutation.snapshot(path)
                if not expected.exists:
                    raise WikiConfigurationError(
                        f"Wiki deletion target disappeared: {path}"
                    )
                owner = plan.deletion_owners.get(path)
                if owner is None:
                    raise WikiConfigurationError(
                        f"Wiki deletion target has no exact owner: {path}"
                    )
                mutation.delete(path, expected=expected, owner=owner)
                deleted.append(relative)
    else:
        for path, content in sorted(
            plan.targets.items(), key=lambda item: str(item[0])
        ):
            relative = _relative(paths, path)
            if not path.exists():
                created.append(relative)
            elif path.read_text(encoding="utf-8") == content:
                unchanged.append(relative)
            else:
                modified.append(relative)
        deleted.extend(_relative(paths, path) for path in plan.deletions)
    return MaterializationReport(
        created=tuple(created),
        modified=tuple(modified),
        deleted=tuple(deleted),
        unchanged=tuple(unchanged),
        warnings=plan.warnings,
    )


def materialize_wiki(
    paths: WikiPaths,
    *,
    apply: bool = False,
) -> MaterializationReport:
    try:
        if apply:
            validate_wiki_vault(paths)
        else:
            validate_wiki_vault_paths(paths, allow_missing_directories=True)
    except WikiConfigurationError as exc:
        return MaterializationReport(errors=(str(exc),))
    plan = _build_plan(paths)
    if plan.errors:
        return MaterializationReport(warnings=plan.warnings, errors=plan.errors)
    try:
        return _execute_plan(paths, plan, apply=apply)
    except (OSError, WikiConfigurationError) as exc:
        return MaterializationReport(
            warnings=plan.warnings,
            errors=(str(exc),),
        )

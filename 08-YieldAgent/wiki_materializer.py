"""Deterministically materialize Wiki metadata as Obsidian Markdown links."""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import frontmatter

from wiki_config import WIKI_PURPOSE_TEMPLATE, WIKI_SCHEMA_TEMPLATE, WikiPaths


_GENERATED_BY = "yield-wiki-materializer"
_BLOCK_START = "<!-- yield-wiki:knowledge-links:start -->"
_BLOCK_END = "<!-- yield-wiki:knowledge-links:end -->"


@dataclass(frozen=True)
class MaterializationReport:
    created: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def changed_count(self) -> int:
        return len(self.created) + len(self.modified) + len(self.deleted)


@dataclass(frozen=True)
class _MaterializationPlan:
    targets: dict[Path, str]
    deletions: tuple[Path, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class _Concept:
    path: Path
    concept_id: str
    product: str
    fail_type: str
    cause_oper: str
    citations: tuple[dict[str, Any], ...]

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
        concepts.append(
            _Concept(
                path=path,
                concept_id=required["id"],
                product=required["product"],
                fail_type=required["fail_type"],
                cause_oper=required["cause_oper"],
                citations=citations,
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
        return _MaterializationPlan({}, (), tuple(errors))

    targets: dict[Path, str] = {}
    product_fails: dict[tuple[str, str], set[str]] = {}
    products: dict[str, set[tuple[str, str]]] = {}
    operations: dict[str, set[tuple[str, str]]] = {}
    operation_concepts: dict[str, list[_Concept]] = {}
    sources: dict[str, dict[str, Any]] = {}
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

    if errors:
        return _MaterializationPlan({}, (), tuple(sorted(set(errors))))

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
        return _MaterializationPlan({}, (), tuple(sorted(set(errors))))

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

    counts = (
        f"- Products: {len(products)}\n"
        f"- Product Fails: {len(product_fails)}\n"
        f"- Operations: {len(operations)}\n"
        f"- Concepts: {len(concepts)}\n"
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
    target_paths = set(targets)
    for directory in (
        paths.products,
        paths.product_fails,
        paths.operations,
        paths.sources,
    ):
        for path in sorted(directory.glob("*.md")):
            if path in target_paths:
                continue
            try:
                generated_by = frontmatter.load(path).metadata.get("generated_by")
            except Exception:
                continue
            if generated_by == _GENERATED_BY:
                deletions.append(path)

    return _MaterializationPlan(
        targets,
        tuple(sorted(deletions)),
        tuple(sorted(set(errors))),
    )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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
    for path, content in sorted(plan.targets.items(), key=lambda item: str(item[0])):
        relative = _relative(paths, path)
        if not path.exists():
            created.append(relative)
            if apply:
                _atomic_write(path, content)
        elif path.read_text(encoding="utf-8") == content:
            unchanged.append(relative)
        else:
            modified.append(relative)
            if apply:
                _atomic_write(path, content)
    for path in plan.deletions:
        relative = _relative(paths, path)
        deleted.append(relative)
        if apply:
            path.unlink()
    return MaterializationReport(
        created=tuple(created),
        modified=tuple(modified),
        deleted=tuple(deleted),
        unchanged=tuple(unchanged),
    )


def materialize_wiki(
    paths: WikiPaths,
    *,
    apply: bool = False,
) -> MaterializationReport:
    plan = _build_plan(paths)
    if plan.errors:
        return MaterializationReport(errors=plan.errors)
    return _execute_plan(paths, plan, apply=apply)

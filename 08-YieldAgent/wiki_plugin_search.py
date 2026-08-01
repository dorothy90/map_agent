from __future__ import annotations

from dataclasses import dataclass

import frontmatter

from fail_history_tools import search_opensearch_with_mode
from models import PluginEvidence, PluginSearchResponse, PluginSearchResult
from wiki_config import WikiPaths
from wiki_materializer import _stable_filename
from wiki_sync import make_triple_key


@dataclass(frozen=True)
class _Concept:
    concept_id: str
    path: str


def _concepts_by_triple(paths: WikiPaths) -> dict[str, _Concept]:
    concepts: dict[str, _Concept] = {}
    for path in sorted(paths.concepts.glob("*.md")):
        try:
            metadata = frontmatter.load(path).metadata
            concept_id = str(metadata.get("id") or "").strip()
            product = str(metadata.get("product") or "").strip()
            fail_type = str(metadata.get("fail_type") or "").strip()
            cause_oper = str(metadata.get("cause_oper") or "").strip()
        except Exception:
            continue
        if not all((concept_id, product, fail_type, cause_oper)):
            continue
        triple = make_triple_key(product, fail_type, cause_oper)
        concepts.setdefault(
            triple.canonical,
            _Concept(
                concept_id=concept_id,
                path=path.relative_to(paths.root).as_posix(),
            ),
        )
    return concepts


def _source_path(paths: WikiPaths, doc_id: str) -> str | None:
    if not doc_id:
        return None
    path = paths.sources / f"{_stable_filename(doc_id)}.md"
    if path.parent != paths.sources or not path.is_file():
        return None
    return path.relative_to(paths.root).as_posix()


def _evidence(paths: WikiPaths, hit: dict) -> PluginEvidence:
    doc_id = str(hit.get("doc_id") or "")
    return PluginEvidence(
        doc_id=doc_id,
        content=str(hit.get("content") or ""),
        cause=str(hit.get("cause") or ""),
        action=str(hit.get("action") or ""),
        comment=str(hit.get("comment") or ""),
        source_file=str(hit.get("source_file") or ""),
        date=str(hit.get("date") or ""),
        score=float(hit.get("score") or 0.0),
        source_path=_source_path(paths, doc_id),
        download_url=str(hit.get("download_url") or ""),
    )


def search_wiki(
    query: str,
    product: str,
    fail_type: str,
    cause_oper: str,
    limit: int,
    paths: WikiPaths,
) -> PluginSearchResponse:
    """Read OpenSearch hits and existing Wiki files without materializing anything."""
    hits, retrieval_mode = search_opensearch_with_mode(
        query,
        product=product,
        fail_type=fail_type,
        cause_oper=cause_oper,
        top_k=limit,
        allow_embedding_fallback=True,
    )
    concepts = _concepts_by_triple(paths)
    grouped: dict[str, tuple[object, list[PluginEvidence]]] = {}
    for hit in hits:
        triple = make_triple_key(
            str(hit.get("product") or ""),
            str(hit.get("fail_type") or ""),
            str(hit.get("cause_oper") or ""),
        )
        canonical = triple.canonical
        if canonical not in grouped:
            grouped[canonical] = (triple, [])
        grouped[canonical][1].append(_evidence(paths, hit))

    results = []
    for canonical, (triple, evidence) in grouped.items():
        evidence.sort(key=lambda item: item.score, reverse=True)
        concept = concepts.get(canonical)
        results.append(
            PluginSearchResult(
                concept_id=concept.concept_id if concept else None,
                concept_path=concept.path if concept else None,
                concept_status="materialized" if concept else "source_only",
                product=triple.product,
                fail_type=triple.fail_type,
                cause_oper=triple.cause_oper,
                retrieval_mode=retrieval_mode,
                score=evidence[0].score if evidence else 0.0,
                evidence=evidence,
            )
        )
    results.sort(key=lambda item: item.score, reverse=True)
    return PluginSearchResponse(
        query=query,
        retrieval_mode=retrieval_mode,
        results=results,
    )

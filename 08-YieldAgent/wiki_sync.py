"""Pure contracts for incremental Wiki source snapshots."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal


_FINGERPRINT_FIELDS = (
    "doc_id",
    "content",
    "cause",
    "action",
    "comment",
    "date",
    "source_file",
    "product",
    "fail_type",
    "cause_oper",
)

ChangeType = Literal["new", "changed", "unchanged", "source_removed"]


def normalize_fail_type(value: str) -> str:
    """Apply the existing metadata suffix contract (for example EASY(W) -> EASY)."""
    return value.split("(", 1)[0].strip() if "(" in value else value


@dataclass(frozen=True)
class TripleKey:
    product: str
    fail_type: str
    cause_oper: str

    @property
    def canonical(self) -> str:
        return f"{self.product}|{self.cause_oper}|{self.fail_type}"


@dataclass(frozen=True)
class TripleSnapshot:
    key: TripleKey
    source_fingerprint: str
    source_doc_ids: tuple[str, ...]
    documents: tuple[dict[str, Any], ...]
    raw_fail_types: tuple[str, ...]

    @property
    def evidence_count(self) -> int:
        return len(self.documents)

    @property
    def evidence_scope(self) -> str:
        return "single_source" if self.evidence_count == 1 else "multiple_sources"


def make_triple_key(product: str, fail_type: str, cause_oper: str) -> TripleKey:
    return TripleKey(
        product=product,
        fail_type=normalize_fail_type(fail_type),
        cause_oper=cause_oper,
    )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(value)).hexdigest()}"


def build_triple_snapshot(
    key: TripleKey,
    documents: list[dict[str, Any]],
) -> TripleSnapshot:
    fingerprinted = []
    for document in documents:
        semantic_source = {
            field: document.get(field)
            for field in _FINGERPRINT_FIELDS
        }
        fingerprinted.append(
            (
                str(document.get("doc_id") or ""),
                _sha256(semantic_source),
                dict(document),
            )
        )
    fingerprinted.sort(key=lambda item: (item[0], item[1]))
    triple_fingerprint = _sha256(
        [
            {"doc_id": doc_id, "document_fingerprint": fingerprint}
            for doc_id, fingerprint, _ in fingerprinted
        ]
    )
    return TripleSnapshot(
        key=key,
        source_fingerprint=triple_fingerprint,
        source_doc_ids=tuple(sorted({item[0] for item in fingerprinted if item[0]})),
        documents=tuple(item[2] for item in fingerprinted),
        raw_fail_types=tuple(
            sorted(
                {
                    str(item[2].get("fail_type"))
                    for item in fingerprinted
                    if item[2].get("fail_type")
                }
            )
        ),
    )


def classify_snapshot(
    snapshot: TripleSnapshot,
    previous: dict[str, Any] | None,
) -> ChangeType:
    if previous is None:
        return "new"
    previous_ids = {str(value) for value in previous.get("source_doc_ids", [])}
    current_ids = set(snapshot.source_doc_ids)
    if previous_ids - current_ids:
        return "source_removed"
    if previous.get("source_fingerprint") == snapshot.source_fingerprint:
        return "unchanged"
    return "changed"


def find_removed_triples(
    current: dict[str, TripleSnapshot],
    manifest: dict[str, Any],
) -> list[str]:
    previous_keys = set(manifest.get("triples", {}))
    return sorted(previous_keys - set(current))


@dataclass(frozen=True)
class PlannedChange:
    change_type: ChangeType
    triple_key: str
    snapshot: TripleSnapshot | None
    previous: dict[str, Any] | None
    missing_doc_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SyncPlan:
    new: tuple[PlannedChange, ...]
    changed: tuple[PlannedChange, ...]
    source_removed: tuple[PlannedChange, ...]
    unchanged: tuple[PlannedChange, ...]


def plan_sync(
    snapshots: dict[str, TripleSnapshot],
    manifest: dict[str, Any],
) -> SyncPlan:
    grouped: dict[ChangeType, list[PlannedChange]] = {
        "new": [],
        "changed": [],
        "source_removed": [],
        "unchanged": [],
    }
    previous_entries = manifest.get("triples", {})
    for triple_key in sorted(snapshots):
        snapshot = snapshots[triple_key]
        previous = previous_entries.get(triple_key)
        change_type = classify_snapshot(snapshot, previous)
        previous_ids = set((previous or {}).get("source_doc_ids", []))
        missing = tuple(sorted(previous_ids - set(snapshot.source_doc_ids)))
        grouped[change_type].append(
            PlannedChange(
                change_type=change_type,
                triple_key=triple_key,
                snapshot=snapshot,
                previous=previous,
                missing_doc_ids=missing,
            )
        )
    for triple_key in find_removed_triples(snapshots, manifest):
        previous = previous_entries[triple_key]
        grouped["source_removed"].append(
            PlannedChange(
                change_type="source_removed",
                triple_key=triple_key,
                snapshot=None,
                previous=previous,
                missing_doc_ids=tuple(sorted(previous.get("source_doc_ids", []))),
            )
        )
    return SyncPlan(
        new=tuple(grouped["new"]),
        changed=tuple(grouped["changed"]),
        source_removed=tuple(sorted(grouped["source_removed"], key=lambda item: item.triple_key)),
        unchanged=tuple(grouped["unchanged"]),
    )


class OpenSearchWikiScanner:
    SOURCE_FIELDS = list(_FINGERPRINT_FIELDS)

    def __init__(
        self,
        client: Any,
        index: str,
        *,
        composite_page_size: int = 500,
        max_documents_per_raw_triple: int = 10_000,
    ) -> None:
        self.client = client
        self.index = index
        self.composite_page_size = composite_page_size
        self.max_documents_per_raw_triple = max_documents_per_raw_triple
        self.last_document_count = 0

    def _list_raw_triples(self) -> list[tuple[str, str, str, int]]:
        triples: list[tuple[str, str, str, int]] = []
        after_key: dict[str, Any] | None = None
        while True:
            composite: dict[str, Any] = {
                "size": self.composite_page_size,
                "sources": [
                    {"product": {"terms": {"field": "product.keyword"}}},
                    {"fail_type": {"terms": {"field": "fail_type.keyword"}}},
                    {"cause_oper": {"terms": {"field": "cause_oper"}}},
                ],
            }
            if after_key is not None:
                composite["after"] = after_key
            response = self.client.search(
                index=self.index,
                body={
                    "size": 0,
                    "aggs": {"triples": {"composite": composite}},
                },
            )
            aggregation = response["aggregations"]["triples"]
            buckets = aggregation.get("buckets", [])
            for bucket in buckets:
                key = bucket["key"]
                triples.append(
                    (
                        str(key["product"]),
                        str(key["fail_type"]),
                        str(key["cause_oper"]),
                        int(bucket.get("doc_count", 0)),
                    )
                )
            after_key = aggregation.get("after_key")
            if not after_key or not buckets:
                break
        return triples

    def _fetch_raw_documents(
        self,
        product: str,
        fail_type: str,
        cause_oper: str,
    ) -> list[dict[str, Any]]:
        response = self.client.search(
            index=self.index,
            body={
                "size": self.max_documents_per_raw_triple,
                "_source": self.SOURCE_FIELDS,
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"product.keyword": product}},
                            {"term": {"fail_type.keyword": fail_type}},
                            {"term": {"cause_oper": cause_oper}},
                        ]
                    }
                },
            },
        )
        return [
            dict(hit.get("_source", {}))
            for hit in response.get("hits", {}).get("hits", [])
        ]

    @staticmethod
    def _deduplicate(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: dict[bytes, dict[str, Any]] = {}
        for document in documents:
            identity = _canonical_json(
                {field: document.get(field) for field in _FINGERPRINT_FIELDS}
            )
            unique.setdefault(identity, document)
        return list(unique.values())

    def scan(self) -> dict[str, TripleSnapshot]:
        grouped: dict[str, tuple[TripleKey, list[dict[str, Any]]]] = {}
        raw_triples = self._list_raw_triples()
        self.last_document_count = sum(item[3] for item in raw_triples)
        for product, raw_fail_type, cause_oper, _ in raw_triples:
            key = make_triple_key(product, raw_fail_type, cause_oper)
            grouped.setdefault(key.canonical, (key, []))[1].extend(
                self._fetch_raw_documents(product, raw_fail_type, cause_oper)
            )
        return {
            canonical: build_triple_snapshot(key, self._deduplicate(documents))
            for canonical, (key, documents) in sorted(grouped.items())
        }

    def fetch_snapshot(
        self,
        product: str,
        fail_type: str,
        cause_oper: str,
        *,
        raw_fail_types: tuple[str, ...] | list[str] | None = None,
    ) -> TripleSnapshot:
        raw_values = tuple(raw_fail_types or (fail_type,))
        documents: list[dict[str, Any]] = []
        for raw_fail_type in raw_values:
            documents.extend(
                self._fetch_raw_documents(product, raw_fail_type, cause_oper)
            )
        return build_triple_snapshot(
            make_triple_key(product, fail_type, cause_oper),
            self._deduplicate(documents),
        )

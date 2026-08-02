"""Pure contracts for incremental Wiki source snapshots."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
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


@dataclass(frozen=True)
class SyncRunResult:
    status: str
    new: int = 0
    changed: int = 0
    source_removed: int = 0
    unchanged: int = 0
    enqueued: int = 0
    succeeded: int = 0
    recovered: int = 0
    failed: int = 0
    materialized: bool = False
    errors: tuple[str, ...] = ()
    targets: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _filters_from_key(triple_key: str) -> dict[str, str]:
    product, cause_oper, fail_type = triple_key.split("|", 2)
    return {
        "product": product,
        "fail_type": fail_type,
        "cause_oper": cause_oper,
    }


class WikiSyncService:
    """Coordinate scanner, Mongo jobs, existing synthesis, and Vault writes."""

    def __init__(
        self,
        *,
        scanner: Any,
        job_store: Any,
        manifest_path: Path,
        index: str,
        synthesize: Any,
        wiki_store: Any,
        materialize: Any,
        now: Any = _now_iso,
        owner_factory: Any = lambda: uuid.uuid4().hex,
    ) -> None:
        self.scanner = scanner
        self.job_store = job_store
        self.manifest_path = manifest_path
        self.index = index
        self.synthesize = synthesize
        self.wiki_store = wiki_store
        self.materialize = materialize
        self.now = now
        self.owner_factory = owner_factory

    def check(self) -> SyncRunResult:
        from wiki_manifest import load_manifest

        manifest = load_manifest(self.manifest_path, self.index)
        plan = plan_sync(self.scanner.scan(), manifest)
        return SyncRunResult(
            status="checked",
            new=len(plan.new),
            changed=len(plan.changed),
            source_removed=len(plan.source_removed),
            unchanged=len(plan.unchanged),
            targets={
                "new": tuple(change.triple_key for change in plan.new),
                "changed": tuple(change.triple_key for change in plan.changed),
                "source_removed": tuple(
                    change.triple_key for change in plan.source_removed
                ),
                "unchanged": tuple(change.triple_key for change in plan.unchanged),
            },
        )

    def apply(self, limit: int) -> SyncRunResult:
        return self._run(limit=limit, scan=True)

    def resume(self, limit: int) -> SyncRunResult:
        return self._run(limit=limit, scan=False)

    def _run(self, *, limit: int, scan: bool) -> SyncRunResult:
        from wiki_manifest import load_manifest

        owner = self.owner_factory()
        if not self.job_store.acquire_global_lock(owner):
            return SyncRunResult(status="already_running")
        counts = {
            "new": 0,
            "changed": 0,
            "source_removed": 0,
            "unchanged": 0,
            "enqueued": 0,
            "succeeded": 0,
            "recovered": 0,
            "failed": 0,
        }
        errors: list[str] = []
        vault_changed = False
        materialized = False
        try:
            manifest = load_manifest(self.manifest_path, self.index)
            if scan:
                plan = plan_sync(self.scanner.scan(), manifest)
                counts.update(
                    new=len(plan.new),
                    changed=len(plan.changed),
                    source_removed=len(plan.source_removed),
                    unchanged=len(plan.unchanged),
                )
                detected_at = self.now()
                for change in plan.source_removed:
                    filters = _filters_from_key(change.triple_key)
                    previous_ids = list((change.previous or {}).get("source_doc_ids", []))
                    current_ids = list(change.snapshot.source_doc_ids) if change.snapshot else []
                    _, stale_changed = self.wiki_store.mark_concept_stale(
                        filters, change.missing_doc_ids, detected_at
                    )
                    _, review_created = self.wiki_store.create_source_removal_review(
                        filters, previous_ids, current_ids, detected_at
                    )
                    vault_changed = vault_changed or stale_changed or review_created
                for change in (*plan.changed, *plan.new):
                    _, created = self.job_store.enqueue(
                        change.snapshot, change.change_type
                    )
                    counts["enqueued"] += int(created)

            for _ in range(limit):
                if not self.job_store.renew_global_lock(owner):
                    raise RuntimeError("Wiki sync global lock was lost")
                job = self.job_store.claim_next(owner)
                if job is None:
                    break
                try:
                    outcome, concept_changed = self._process_job(
                        job, owner, manifest
                    )
                    counts[outcome] += 1
                    vault_changed = vault_changed or concept_changed
                except Exception as exc:
                    self.job_store.mark_failed(job["_id"], owner, exc)
                    counts["failed"] += 1
                    errors.append(" ".join(str(exc).split())[:500])

            if vault_changed:
                report = self.materialize()
                report_errors = tuple(getattr(report, "errors", ()) or ())
                if report_errors:
                    errors.extend(str(error) for error in report_errors)
                else:
                    materialized = True
            status = "completed" if not errors else "completed_with_errors"
            return SyncRunResult(
                status=status,
                materialized=materialized,
                errors=tuple(errors),
                **counts,
            )
        finally:
            self.job_store.release_global_lock(owner)

    def _process_job(
        self,
        job: dict[str, Any],
        owner: str,
        manifest: dict[str, Any],
    ) -> tuple[Literal["succeeded", "recovered"], bool]:
        from wiki_manifest import record_success, save_manifest

        snapshot = self.scanner.fetch_snapshot(
            job["product"],
            job["fail_type"],
            job["cause_oper"],
            raw_fail_types=job.get("raw_fail_types"),
        )
        if snapshot.source_fingerprint != job["source_fingerprint"]:
            raise RuntimeError(
                "OpenSearch source fingerprint changed after planning; run apply to replan"
            )
        concept_id = f"concept:{snapshot.key.canonical}"
        existing = self.wiki_store.read_node(concept_id)
        if (
            existing
            and existing.get("frontmatter", {}).get("source_fingerprint")
            == snapshot.source_fingerprint
        ):
            version = int(existing["frontmatter"].get("version", 1))
            success_at = self.now()
            record_success(
                manifest,
                snapshot,
                concept_id=concept_id,
                concept_version=version,
                success_at=success_at,
            )
            save_manifest(self.manifest_path, manifest)
            if not self.job_store.mark_succeeded(
                job["_id"],
                owner,
                concept_id=concept_id,
                concept_version=version,
            ):
                raise RuntimeError("Wiki sync job ownership was lost before recovery")
            return "recovered", True

        synthesis = self.synthesize(concept_id, list(snapshot.documents))
        if synthesis is None:
            raise RuntimeError("Wiki synthesis returned no result")
        citations = [
            citation.model_dump() if hasattr(citation, "model_dump") else dict(citation)
            for citation in synthesis.citations
        ]
        entities = [
            candidate.model_dump(mode="json")
            for candidate in getattr(synthesis, "entities", [])
        ]
        relations = [
            candidate.model_dump(mode="json")
            for candidate in getattr(synthesis, "relations", [])
        ]
        filters = {
            "product": snapshot.key.product,
            "fail_type": snapshot.key.fail_type,
            "cause_oper": snapshot.key.cause_oper,
        }
        stored_id, _ = self.wiki_store.upsert_concept(
            filters=filters,
            source_episode_id=None,
            synthesized_body=synthesis.body_markdown,
            confidence=synthesis.confidence,
            citations=citations,
            entities=entities,
            relations=relations,
            evidence={
                "score": 1.0
                if snapshot.evidence_count >= 5
                else snapshot.evidence_count / 5.0,
                "unique_doc_ids": len(snapshot.source_doc_ids),
                "n_episodes": 0,
                "n_dates": len(
                    {
                        document.get("date")
                        for document in snapshot.documents
                        if document.get("date")
                    }
                ),
            },
            sync_metadata={
                "source_fingerprint": snapshot.source_fingerprint,
                "source_doc_ids": list(snapshot.source_doc_ids),
                "evidence_count": snapshot.evidence_count,
                "evidence_scope": snapshot.evidence_scope,
                "sync_job_id": job["_id"],
            },
            materialize=False,
        )
        concept_id = stored_id if stored_id.startswith("concept:") else f"concept:{stored_id}"
        stored = self.wiki_store.read_node(concept_id)
        if stored is None:
            raise RuntimeError("Stored Wiki Concept could not be read back")
        version = int(stored["frontmatter"].get("version", 1))
        success_at = self.now()
        record_success(
            manifest,
            snapshot,
            concept_id=concept_id,
            concept_version=version,
            success_at=success_at,
        )
        save_manifest(self.manifest_path, manifest)
        if not self.job_store.mark_succeeded(
            job["_id"],
            owner,
            concept_id=concept_id,
            concept_version=version,
        ):
            raise RuntimeError("Wiki sync job ownership was lost before success")
        return "succeeded", True

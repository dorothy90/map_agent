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

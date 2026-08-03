"""Attach semantically related content-only evidence to existing Wiki Concepts."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

import frontmatter
from pydantic import BaseModel, Field

from wiki_config import WikiPaths
from wiki_safe_mutation import PinnedWikiMutation


_BLOCK_RE = re.compile(
    r"\n*<!-- yield-wiki:knowledge-links:start -->.*?"
    r"<!-- yield-wiki:knowledge-links:end -->\s*$",
    re.DOTALL,
)
_PAIR_FIELDS = {
    "concept_sha256",
    "content_sha256",
    "accepted",
    "confidence",
    "relation",
    "retrieval_model",
    "judgment_model",
}


@dataclass(frozen=True)
class EvidenceSelector:
    product: str
    fail_type: str
    cause_oper: str


@dataclass(frozen=True)
class ConceptEvidenceSnapshot:
    path: Path
    concept_id: str
    product: str
    fail_type: str
    cause_oper: str
    body: str = field(repr=False)
    file_sha256: str
    semantic_sha256: str


@dataclass(frozen=True)
class EvidenceCandidate:
    raw_id: str = field(repr=False)
    doc_id: str
    page_content: str = field(repr=False)
    content_sha256: str
    source_file: str
    page_num: int | None
    download_url: str
    score: float


@dataclass(frozen=True)
class EvidencePairState:
    concept_sha256: str
    content_sha256: str
    retrieval_model: str
    judgment_model: str
    accepted: bool
    confidence: float
    relation: str


@dataclass(frozen=True)
class EnrichmentRunResult:
    status: str
    concepts: int = 0
    candidates: int = 0
    evaluated: int = 0
    accepted: int = 0
    rejected: int = 0
    skipped: int = 0
    attached: int = 0
    materialized: bool = False
    errors: tuple[str, ...] = ()


class EvidenceDecision(BaseModel):
    doc_id: str
    relevant: bool
    confidence: float = Field(ge=0.0, le=1.0)
    relation: Literal[
        "supporting_context",
        "possible_cause",
        "possible_action",
        "contradiction",
    ]
    reason: str = Field(max_length=500)


class EvidenceDecisionBatch(BaseModel):
    decisions: list[EvidenceDecision]


def stable_evidence_id(source_index: str, raw_id: str) -> str:
    digest = hashlib.sha256(
        f"{source_index}\0{raw_id}".encode("utf-8")
    ).hexdigest()
    return f"EVD-{digest[:20]}"


def _semantic_hash(
    product: str,
    fail_type: str,
    cause_oper: str,
    body: str,
) -> str:
    generated_body = _BLOCK_RE.sub("", body).rstrip()
    payload = json.dumps(
        {
            "product": product,
            "fail_type": fail_type,
            "cause_oper": cause_oper,
            "body": generated_body,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_concept_snapshots(
    paths: WikiPaths,
    selector: EvidenceSelector | None = None,
) -> tuple[ConceptEvidenceSnapshot, ...]:
    snapshots: list[ConceptEvidenceSnapshot] = []
    for path in sorted(paths.concepts.glob("*.md")):
        raw = path.read_bytes()
        post = frontmatter.loads(raw.decode("utf-8"))
        metadata = post.metadata
        product = str(metadata.get("product") or "").strip()
        fail_type = str(metadata.get("fail_type") or "").strip()
        cause_oper = str(metadata.get("cause_oper") or "").strip()
        concept_id = str(metadata.get("id") or "").strip()
        if not all((product, fail_type, cause_oper, concept_id)):
            continue
        if selector is not None and (
            product,
            fail_type,
            cause_oper,
        ) != (selector.product, selector.fail_type, selector.cause_oper):
            continue
        body = post.content or ""
        snapshots.append(
            ConceptEvidenceSnapshot(
                path=path,
                concept_id=concept_id,
                product=product,
                fail_type=fail_type,
                cause_oper=cause_oper,
                body=body,
                file_sha256=hashlib.sha256(raw).hexdigest(),
                semantic_sha256=_semantic_hash(
                    product, fail_type, cause_oper, body
                ),
            )
        )
    return tuple(snapshots)


class EvidenceManifestStore:
    def __init__(self, paths: WikiPaths, path: Path) -> None:
        self.paths = paths
        self.path = path

    @staticmethod
    def _validate(manifest: Mapping[str, Any]) -> dict[str, Any]:
        if set(manifest) != {"version", "pairs"} or manifest.get("version") != 1:
            raise ValueError("unsupported evidence manifest")
        pairs = manifest.get("pairs")
        if not isinstance(pairs, dict):
            raise ValueError("evidence manifest pairs must be an object")
        for value in pairs.values():
            if not isinstance(value, dict) or set(value) != _PAIR_FIELDS:
                raise ValueError("unapproved manifest fields")
        return {"version": 1, "pairs": dict(pairs)}

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "pairs": {}}
        return self._validate(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, manifest: Mapping[str, Any]) -> None:
        validated = self._validate(manifest)
        content = json.dumps(
            validated,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with PinnedWikiMutation(self.paths) as mutation:
            snapshot = mutation.snapshot(self.path)
            mutation.replace_text(self.path, content, expected=snapshot)

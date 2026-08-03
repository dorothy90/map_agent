"""Attach semantically related content-only evidence to existing Wiki Concepts."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

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
    related_evidence: tuple[dict[str, Any], ...] = ()


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
                related_evidence=tuple(
                    item
                    for item in (metadata.get("related_evidence") or [])
                    if isinstance(item, dict)
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
            if snapshot.exists and snapshot.content == content.encode("utf-8"):
                return
            mutation.replace_text(self.path, content, expected=snapshot)


def _exact_index_name(value: str) -> str:
    name = str(value or "").strip()
    if not name or any(token in name for token in ("*", "?", ",")):
        raise ValueError("an exact source index name is required")
    return name


class OpenSearchEvidenceRetriever:
    EMBEDDING_DIMENSION = 4096
    EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"

    def __init__(
        self,
        client: Any,
        source_index: str,
        embed: Callable[[str], list[float]],
        top_k: int = 5,
    ) -> None:
        self.client = client
        self.source_index = _exact_index_name(source_index)
        self.embed = embed
        self.top_k = min(max(int(top_k), 1), 5)

    def validate(self) -> int:
        response = self.client.indices.get_mapping(index=self.source_index)
        if set(response) != {self.source_index}:
            raise ValueError("source index must resolve to one exact index")
        embedding = (
            response[self.source_index]
            .get("mappings", {})
            .get("properties", {})
            .get("embedding", {})
        )
        if (
            embedding.get("type") != "knn_vector"
            or embedding.get("dimension") != self.EMBEDDING_DIMENSION
        ):
            raise ValueError("source embedding must be a 4096-dimension knn_vector")
        return self.EMBEDDING_DIMENSION

    @staticmethod
    def _query(concept: ConceptEvidenceSnapshot) -> str:
        body = _BLOCK_RE.sub("", concept.body).strip()[:4000]
        return (
            f"product: {concept.product}\n"
            f"fail_type: {concept.fail_type}\n"
            f"cause_oper: {concept.cause_oper}\n"
            f"concept:\n{body}"
        )

    def search(
        self,
        concept: ConceptEvidenceSnapshot,
    ) -> tuple[EvidenceCandidate, ...]:
        self.validate()
        vector = list(self.embed(self._query(concept)))
        if len(vector) != self.EMBEDDING_DIMENSION:
            raise ValueError("query embedding must contain exactly 4096 values")
        body = {
            "size": self.top_k,
            "_source": [
                "page_content",
                "source_file",
                "page_num",
                "download_url",
            ],
            "query": {
                "knn": {
                    "embedding": {
                        "vector": vector,
                        "k": self.top_k,
                    }
                }
            },
        }
        response = self.client.search(index=self.source_index, body=body)
        candidates: list[EvidenceCandidate] = []
        for hit in response.get("hits", {}).get("hits", []):
            raw_id = str(hit.get("_id") or "")
            source = hit.get("_source") or {}
            content = str(source.get("page_content") or "")
            if not raw_id or not content.strip():
                continue
            raw_page = source.get("page_num")
            page_num = int(raw_page) if raw_page not in (None, "") else None
            candidates.append(
                EvidenceCandidate(
                    raw_id=raw_id,
                    doc_id=stable_evidence_id(self.source_index, raw_id),
                    page_content=content,
                    content_sha256=hashlib.sha256(
                        content.encode("utf-8")
                    ).hexdigest(),
                    source_file=Path(str(source.get("source_file") or "")).name,
                    page_num=page_num,
                    download_url=str(source.get("download_url") or ""),
                    score=float(hit.get("_score") or 0.0),
                )
            )
        return tuple(candidates)


_JUDGMENT_SYSTEM = """You assess whether content-only evidence is related to one existing Wiki Concept.
Use only the supplied Concept and candidates. Do not infer missing product, fail type, or operation metadata.
Return one decision for every supplied safe doc_id, exactly once, and no other IDs.
Set relevant=false whenever the relationship is not established by the supplied text.
Reasons must be short and grounded in that candidate only."""


class StructuredEvidenceJudge:
    def __init__(
        self,
        llm: Any,
        model_name: str,
        minimum_confidence: float = 0.8,
    ) -> None:
        self.llm = llm
        self.model_name = model_name
        self.minimum_confidence = minimum_confidence

    @staticmethod
    def _payload(
        concept: ConceptEvidenceSnapshot,
        candidates: Sequence[EvidenceCandidate],
    ) -> str:
        concept_body = _BLOCK_RE.sub("", concept.body).strip()[:4000]
        candidate_payload = [
            {
                "doc_id": item.doc_id,
                "content": item.page_content.strip()[:6000],
            }
            for item in candidates
        ]
        return json.dumps(
            {
                "concept": {
                    "product": concept.product,
                    "fail_type": concept.fail_type,
                    "cause_oper": concept.cause_oper,
                    "body": concept_body,
                },
                "candidates": candidate_payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def decide_batch(
        self,
        concept: ConceptEvidenceSnapshot,
        candidates: Sequence[EvidenceCandidate],
    ) -> tuple[EvidenceDecision, ...]:
        items = tuple(candidates)
        if not items:
            return ()
        if len(items) > 5:
            raise ValueError("at most five evidence candidates may be judged")
        chain = self.llm.with_structured_output(
            EvidenceDecisionBatch,
            method="function_calling",
        )
        output = chain.invoke(
            [
                ("system", _JUDGMENT_SYSTEM),
                ("human", self._payload(concept, items)),
            ]
        )
        batch = (
            output
            if isinstance(output, EvidenceDecisionBatch)
            else EvidenceDecisionBatch.model_validate(output)
        )
        requested = [item.doc_id for item in items]
        returned = [decision.doc_id for decision in batch.decisions]
        if len(returned) != len(set(returned)) or set(returned) != set(requested):
            raise ValueError("structured evidence decision IDs do not match candidates")
        by_id = {decision.doc_id: decision for decision in batch.decisions}
        return tuple(by_id[doc_id] for doc_id in requested)


def _pair_key(concept_id: str, doc_id: str) -> str:
    return f"{concept_id}\0{doc_id}"


def _sanitized_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:500]


class WikiEvidenceEnrichmentService:
    def __init__(
        self,
        *,
        source_index: str,
        retriever: OpenSearchEvidenceRetriever,
        judge: StructuredEvidenceJudge | None,
        manifest_store: EvidenceManifestStore,
        read_concepts: Callable[
            [EvidenceSelector | None], tuple[ConceptEvidenceSnapshot, ...]
        ],
        replace_related_evidence: Callable[..., bool],
        write_source: Callable[[dict[str, Any], str], bool],
        refresh_backlinks: Callable[[str], bool],
        materialize: Callable[[], Any],
    ) -> None:
        self.source_index = _exact_index_name(source_index)
        self.retriever = retriever
        self.judge = judge
        self.manifest_store = manifest_store
        self.read_concepts = read_concepts
        self.replace_related_evidence = replace_related_evidence
        self.write_source = write_source
        self.refresh_backlinks = refresh_backlinks
        self.materialize = materialize

    def check(self, selector: EvidenceSelector | None) -> EnrichmentRunResult:
        self.retriever.validate()
        concepts = self.read_concepts(selector)
        return EnrichmentRunResult(status="checked", concepts=len(concepts))

    @staticmethod
    def _state_matches(
        state: Mapping[str, Any] | None,
        concept: ConceptEvidenceSnapshot,
        candidate: EvidenceCandidate,
        retrieval_model: str,
        judgment_model: str,
    ) -> bool:
        return bool(
            state
            and state.get("concept_sha256") == concept.semantic_sha256
            and state.get("content_sha256") == candidate.content_sha256
            and state.get("retrieval_model") == retrieval_model
            and state.get("judgment_model") == judgment_model
        )

    def apply(
        self,
        limit: int,
        selector: EvidenceSelector | None,
    ) -> EnrichmentRunResult:
        if self.judge is None:
            raise RuntimeError("external evidence judge is not configured")
        concepts = self.read_concepts(selector)[:limit]
        manifest = self.manifest_store.load()
        pairs = manifest["pairs"]
        counters = {
            "candidates": 0,
            "evaluated": 0,
            "accepted": 0,
            "rejected": 0,
            "skipped": 0,
            "attached": 0,
        }
        errors: list[str] = []
        changed = False
        affected_doc_ids: set[str] = set()
        retrieval_model = self.retriever.EMBEDDING_MODEL
        judgment_model = self.judge.model_name

        for concept in concepts:
            try:
                candidates = self.retriever.search(concept)
                counters["candidates"] += len(candidates)
                pending = [
                    candidate
                    for candidate in candidates
                    if not self._state_matches(
                        pairs.get(_pair_key(concept.concept_id, candidate.doc_id)),
                        concept,
                        candidate,
                        retrieval_model,
                        judgment_model,
                    )
                ]
                counters["skipped"] += len(candidates) - len(pending)
                if pending:
                    decisions = self.judge.decide_batch(concept, pending)
                    counters["evaluated"] += len(decisions)
                    for candidate, decision in zip(pending, decisions, strict=True):
                        accepted = bool(
                            decision.relevant
                            and decision.confidence >= self.judge.minimum_confidence
                        )
                        counters["accepted" if accepted else "rejected"] += 1
                        pairs[_pair_key(concept.concept_id, candidate.doc_id)] = {
                            "concept_sha256": concept.semantic_sha256,
                            "content_sha256": candidate.content_sha256,
                            "accepted": accepted,
                            "confidence": float(decision.confidence),
                            "relation": decision.relation,
                            "retrieval_model": retrieval_model,
                            "judgment_model": judgment_model,
                        }

                accepted_items: list[dict[str, Any]] = []
                for candidate in candidates:
                    state = pairs.get(_pair_key(concept.concept_id, candidate.doc_id))
                    if not state or not state.get("accepted"):
                        continue
                    item = {
                        "doc_id": candidate.doc_id,
                        "source_index": self.source_index,
                        "source_file": candidate.source_file,
                        "page_num": candidate.page_num,
                        "download_url": candidate.download_url,
                        "content_sha256": candidate.content_sha256,
                        "relevance": float(state["confidence"]),
                        "relation": str(state["relation"]),
                    }
                    self.write_source(item, candidate.page_content)
                    accepted_items.append(item)

                previous = [
                    item
                    for item in concept.related_evidence
                    if str(item.get("source_index") or "") == self.source_index
                ]
                if accepted_items or previous:
                    concept_changed = self.replace_related_evidence(
                        {
                            "product": concept.product,
                            "fail_type": concept.fail_type,
                            "cause_oper": concept.cause_oper,
                        },
                        self.source_index,
                        accepted_items,
                        concept.file_sha256,
                    )
                    if concept_changed:
                        changed = True
                        counters["attached"] += len(accepted_items)
                        affected_doc_ids.update(
                            str(item.get("doc_id") or "")
                            for item in previous + accepted_items
                        )
            except Exception as exc:
                errors.append(
                    f"{concept.concept_id}: {_sanitized_error(exc)}"
                )

        try:
            self.manifest_store.save(manifest)
        except Exception as exc:
            errors.append(f"manifest: {_sanitized_error(exc)}")
        for doc_id in sorted(value for value in affected_doc_ids if value):
            try:
                self.refresh_backlinks(doc_id)
            except Exception as exc:
                errors.append(f"source:{doc_id}: {_sanitized_error(exc)}")
        materialized = False
        if changed:
            try:
                report = self.materialize()
                report_errors = tuple(getattr(report, "errors", ()) or ())
                errors.extend(
                    f"materialize: {' '.join(str(error).split())[:400]}"
                    for error in report_errors
                )
                materialized = not report_errors
            except Exception as exc:
                errors.append(f"materialize: {_sanitized_error(exc)}")
        return EnrichmentRunResult(
            status="completed_with_errors" if errors else "completed",
            concepts=len(concepts),
            materialized=materialized,
            errors=tuple(errors),
            **counters,
        )

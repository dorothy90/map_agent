from __future__ import annotations

from enum import Enum
import json

from common import extract_json_from_llm
from control_knowledge_models import (
    CurationDecision,
    CurationLedgerEntry,
    KnowledgeCandidate,
    PageType,
    candidate_fingerprint,
)
from control_knowledge_store import ControlKnowledgeStore


class WriteDisposition(str, Enum):
    auto = "auto"
    review = "review"
    deny = "deny"


AUTO = {
    ("system_snapshot", "Agent"),
    ("system_snapshot", "Workflow"),
    ("system_snapshot", "Contract"),
    ("system_snapshot", "Component"),
    ("runtime_observation", "Observation"),
    ("incident", "Observation"),
}


def write_disposition(source_kind: str, page_type: str | PageType) -> str:
    normalized = page_type.value if isinstance(page_type, PageType) else str(page_type)
    if (source_kind, normalized) in AUTO:
        return WriteDisposition.auto.value
    if normalized in {"Runbook", "Decision", "Policy"} or source_kind == "human_correction":
        return WriteDisposition.review.value
    return WriteDisposition.deny.value


CURATOR_SYSTEM = """You curate an internal multi-agent control-plane OKF bundle.
Compare one validated structured candidate with only the explicitly loaded target pages.
Return one JSON object matching CurationDecision.
Use only facts and evidence_refs present in the candidate. Never invent runtime behavior.
Choose no_change when the candidate adds no durable control-plane knowledge.
The draft must be a complete replacement body whose first H1 equals title.
Do not include user data, domain entities, rows, SQL, prompts, message text, or artifacts.
Do not choose a page outside candidate.subjects.
Do not output analysis or markdown fences around the JSON.
"""


class CuratorCallError(RuntimeError):
    pass


class ControlKnowledgeCurator:
    def __init__(self, store: ControlKnowledgeStore, llm):
        self.store = store
        self.llm = llm

    def _decision(self, candidate: KnowledgeCandidate) -> CurationDecision:
        pages = self.store.load_pages(candidate.subjects)
        page_context = [
            {
                "page_id": page.metadata.get("page_id"),
                "metadata": page.metadata,
                "body_markdown": page.body_markdown,
            }
            for page in pages
        ]
        user_payload = {
            "candidate": candidate.model_dump(mode="json"),
            "allowed_target_page_ids": candidate.subjects,
            "existing_pages": page_context,
        }
        try:
            raw = (
                self.llm.invoke(
                    [
                        {"role": "system", "content": CURATOR_SYSTEM},
                        {
                            "role": "user",
                            "content": json.dumps(
                                user_payload, ensure_ascii=False, default=str
                            ),
                        },
                    ]
                ).content
                or ""
            )
        except Exception as exc:
            raise CuratorCallError(type(exc).__name__) from exc
        return extract_json_from_llm(raw, CurationDecision)

    def curate(self, candidate: KnowledgeCandidate) -> CurationLedgerEntry:
        fingerprint = candidate_fingerprint(candidate)
        try:
            decision = self._decision(candidate)
            if decision.action == "no_change":
                entry = CurationLedgerEntry(
                    candidate_id=candidate.candidate_id,
                    fingerprint=fingerprint,
                    action="no_change",
                    rationale=decision.rationale,
                )
                self.store.append_ledger(entry)
                return entry
            assert decision.draft is not None
            if decision.draft.page_id not in candidate.subjects:
                raise ValueError("draft target is outside candidate subjects")
            existing = bool(self.store.load_pages([decision.draft.page_id]))
            if decision.action == "create" and existing:
                raise ValueError("create target already exists")
            if decision.action == "update" and (
                not existing or decision.target_page_id != decision.draft.page_id
            ):
                raise ValueError("update target must exist and match draft page_id")
            candidate_refs = {item.ref for item in candidate.evidence_refs}
            if (
                not set(decision.draft.evidence_refs).issubset(candidate_refs)
                or not decision.draft.evidence_refs
            ):
                raise ValueError("draft evidence must come from candidate")
            disposition = (
                WriteDisposition.review.value
                if decision.action == "review_required"
                else write_disposition(candidate.source_kind, decision.draft.page_type)
            )
            if disposition == "auto":
                self.store.write_page(decision.draft, candidate)
                action = "updated" if existing else "created"
            elif disposition == "review":
                self.store.write_proposal(
                    decision.draft, candidate, rationale=decision.rationale
                )
                action = "proposal"
            else:
                raise ValueError("candidate source cannot write requested page type")
            entry = CurationLedgerEntry(
                candidate_id=candidate.candidate_id,
                fingerprint=fingerprint,
                action=action,
                target_page_id=decision.draft.page_id,
                rationale=decision.rationale,
            )
        except CuratorCallError:
            raise
        except Exception as exc:
            entry = CurationLedgerEntry(
                candidate_id=candidate.candidate_id,
                fingerprint=fingerprint,
                action="invalid_decision",
                rationale=type(exc).__name__,
            )
        self.store.append_ledger(entry)
        return entry

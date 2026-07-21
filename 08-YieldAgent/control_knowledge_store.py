from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

import frontmatter

from control_knowledge_models import (
    CurationLedgerEntry,
    KnowledgeCandidate,
    PageDraft,
    candidate_fingerprint,
)


@dataclass(frozen=True)
class StoredPage:
    path: Path
    metadata: dict[str, Any]
    body_markdown: str


class ControlKnowledgeStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.wiki = self.root / "wiki"
        self.candidates = self.root / "raw" / "candidates"
        self.ledger = self.root / "raw" / "curation-ledger.jsonl"
        self.review_queue = self.wiki / "review_queue"
        self.log = self.wiki / "log.md"

    def ensure_dirs(self) -> None:
        self.candidates.mkdir(parents=True, exist_ok=True)
        self.review_queue.mkdir(parents=True, exist_ok=True)
        if not self.log.exists():
            self._atomic_text(self.log, "# Multi-Agent Knowledge Log\n")

    def _atomic_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(text, encoding="utf-8")
        try:
            os.replace(temp, path)
        except Exception:
            temp.unlink(missing_ok=True)
            raise

    def save_candidate(self, candidate: KnowledgeCandidate) -> Path:
        self.ensure_dirs()
        fingerprint = candidate_fingerprint(candidate)
        path = self.candidates / f"{fingerprint}.json"
        if not path.exists():
            text = json.dumps(
                candidate.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ) + "\n"
            self._atomic_text(path, text)
        return path

    def _processed_fingerprints(self) -> set[str]:
        if not self.ledger.exists():
            return set()
        result = set()
        for line in self.ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                result.add(str(json.loads(line).get("fingerprint") or ""))
            except json.JSONDecodeError:
                continue
        return {item for item in result if item}

    def is_processed(self, fingerprint: str) -> bool:
        return fingerprint in self._processed_fingerprints()

    def pending_candidates(self) -> list[Path]:
        self.ensure_dirs()
        done = self._processed_fingerprints()
        return [
            path
            for path in sorted(self.candidates.glob("*.json"))
            if path.stem not in done
        ]

    def load_candidate(self, path: Path) -> KnowledgeCandidate:
        return KnowledgeCandidate.model_validate_json(path.read_text(encoding="utf-8"))

    def load_pages(self, page_ids: list[str]) -> list[StoredPage]:
        pages = []
        for page_id in page_ids:
            path = (self.wiki / f"{page_id}.md").resolve()
            if self.wiki not in path.parents or not path.exists():
                continue
            post = frontmatter.load(path)
            pages.append(StoredPage(path, dict(post.metadata), post.content or ""))
        return pages

    def _metadata_for(
        self,
        draft: PageDraft,
        candidate: KnowledgeCandidate,
        existing: StoredPage | None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).date().isoformat()
        metadata = dict(existing.metadata) if existing else {}
        metadata.update(
            {
                "type": draft.page_type.value,
                "page_id": draft.page_id,
                "title": draft.title,
                "description": draft.description,
                "routing_summary": draft.routing_summary or draft.description,
                "status": metadata.get("status", "current"),
                "owner": metadata.get("owner", "yield-platform"),
                "source_status": (
                    "code-backed"
                    if candidate.source_kind == "system_snapshot"
                    else "trace-backed"
                ),
                "agent_use": metadata.get("agent_use", "read-and-propose"),
                "sensitivity": "internal",
                "last_reviewed": now,
                "review_cycle": metadata.get("review_cycle", "P90D"),
                "relations": draft.relations,
                "evidence_refs": sorted(
                    set(
                        list(metadata.get("evidence_refs") or [])
                        + draft.evidence_refs
                    )
                ),
            }
        )
        metadata["llmwiki_status"] = metadata["status"]
        metadata["llmwiki_owner"] = metadata["owner"]
        metadata["llmwiki_source_status"] = metadata["source_status"]
        metadata["llmwiki_agent_use"] = metadata["agent_use"]
        return metadata

    def write_page(self, draft: PageDraft, candidate: KnowledgeCandidate) -> Path:
        self.ensure_dirs()
        path = (self.wiki / f"{draft.page_id}.md").resolve()
        if self.wiki not in path.parents:
            raise ValueError("page_id escapes wiki root")
        known_ids = {
            str(frontmatter.load(item).metadata.get("page_id") or "")
            for item in self.wiki.rglob("*.md")
            if item.name != "index.md"
        }
        for values in draft.relations.values():
            for value in values:
                if not (str(value).startswith("[[") and str(value).endswith("]]")):
                    raise ValueError("relation must be an exact wikilink")
                target = str(value)[2:-2]
                if target not in known_ids:
                    raise ValueError(f"relation target does not exist: {target}")
        existing_pages = self.load_pages([draft.page_id])
        existing = existing_pages[0] if existing_pages else None
        metadata = self._metadata_for(draft, candidate, existing)
        comparable = dict(metadata)
        comparable.pop("version", None)
        old_comparable = dict(existing.metadata) if existing else {}
        old_comparable.pop("version", None)
        if (
            existing
            and old_comparable == comparable
            and existing.body_markdown.strip() == draft.body_markdown.strip()
        ):
            return path
        metadata["version"] = int(
            (existing.metadata if existing else {}).get("version", 0)
        ) + 1
        rendered = frontmatter.dumps(
            frontmatter.Post(draft.body_markdown.strip() + "\n", **metadata)
        )
        self._atomic_text(path, rendered)
        self._prepend_log(
            "updated" if existing else "created", draft.page_id, candidate.candidate_id
        )
        return path

    def write_proposal(
        self, draft: PageDraft, candidate: KnowledgeCandidate, *, rationale: str
    ) -> Path:
        self.ensure_dirs()
        page_id = f"review_queue/{candidate.candidate_id}"
        target_exists = bool(self.load_pages([draft.page_id]))
        metadata = {
            "type": "Proposal",
            "page_id": page_id,
            "title": f"Review {draft.title}",
            "description": rationale,
            "routing_summary": f"Review the proposed change to {draft.page_id}",
            "status": "draft",
            "owner": "yield-platform",
            "source_status": "candidate-backed",
            "agent_use": "read-and-propose",
            "sensitivity": "internal",
            "llmwiki_status": "draft",
            "llmwiki_owner": "yield-platform",
            "llmwiki_source_status": "candidate-backed",
            "llmwiki_agent_use": "read-and-propose",
            "last_reviewed": datetime.now(timezone.utc).date().isoformat(),
            "review_cycle": "P30D",
            "version": 1,
            "relations": (
                {"proposes_update_to": [f"[[{draft.page_id}]]"]}
                if target_exists
                else {}
            ),
            "evidence_refs": draft.evidence_refs,
            "candidate_fingerprint": candidate_fingerprint(candidate),
            "target_page_id": draft.page_id,
            "proposed_page_type": draft.page_type.value,
            "proposed_title": draft.title,
            "proposed_description": draft.description,
            "proposed_routing_summary": draft.routing_summary,
            "proposed_relations": draft.relations,
            "proposed_evidence_refs": draft.evidence_refs,
        }
        body = (
            f"# Review {draft.title}\n\n## Rationale\n\n{rationale}"
            f"\n\n## Proposed page\n\n{draft.body_markdown}"
        )
        path = self.review_queue / f"{candidate.candidate_id}.md"
        self._atomic_text(path, frontmatter.dumps(frontmatter.Post(body, **metadata)))
        self._prepend_log("proposal", draft.page_id, candidate.candidate_id)
        return path

    def append_ledger(self, entry: CurationLedgerEntry) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            entry.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
        )
        with self.ledger.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _prepend_log(self, action: str, page_id: str, candidate_id: str) -> None:
        self.ensure_dirs()
        current = self.log.read_text(encoding="utf-8")
        header = "# Multi-Agent Knowledge Log\n"
        rest = current[len(header) :].lstrip("\n") if current.startswith(header) else current
        timestamp = datetime.now(timezone.utc).isoformat()
        entry = (
            f"\n## {timestamp}\n\n- {action} `[[{page_id}]]` from `{candidate_id}`\n"
        )
        self._atomic_text(self.log, header + entry + ("\n" + rest if rest else ""))

    def approve_proposal(
        self, proposal_id: str, candidate: KnowledgeCandidate
    ) -> Path:
        proposal_path = (self.review_queue / f"{proposal_id}.md").resolve()
        if self.review_queue not in proposal_path.parents or not proposal_path.exists():
            raise ValueError("proposal does not exist")
        proposal = frontmatter.load(proposal_path)
        metadata = dict(proposal.metadata)
        if metadata.get("status") != "draft":
            raise ValueError("proposal is not pending")
        if metadata.get("candidate_fingerprint") != candidate_fingerprint(candidate):
            raise ValueError("proposal candidate fingerprint mismatch")
        marker = "## Proposed page"
        if marker not in proposal.content:
            raise ValueError("proposal body is missing proposed page")
        proposed_body = proposal.content.split(marker, 1)[1].strip()
        draft = PageDraft(
            page_id=str(metadata["target_page_id"]),
            page_type=str(metadata["proposed_page_type"]),
            title=str(metadata["proposed_title"]),
            description=str(metadata["proposed_description"]),
            routing_summary=str(metadata.get("proposed_routing_summary") or ""),
            body_markdown=proposed_body,
            relations=dict(metadata.get("proposed_relations") or {}),
            evidence_refs=list(metadata.get("proposed_evidence_refs") or []),
        )
        target = self.write_page(draft, candidate)
        metadata["status"] = "reviewed"
        metadata["llmwiki_status"] = "reviewed"
        metadata["last_reviewed"] = datetime.now(timezone.utc).date().isoformat()
        self._atomic_text(
            proposal_path,
            frontmatter.dumps(frontmatter.Post(proposal.content, **metadata)),
        )
        self._prepend_log("approved", draft.page_id, candidate.candidate_id)
        return target

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Literal
import uuid

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)
FORBIDDEN_FACT_KEYS = frozenset(
    {
        "artifact_payload",
        "base64",
        "bytes",
        "content",
        "data",
        "html",
        "messages",
        "prompt",
        "query",
        "rows",
        "sql",
    }
)


class PageType(str, Enum):
    agent = "Agent"
    workflow = "Workflow"
    contract = "Contract"
    component = "Component"
    runbook = "Runbook"
    observation = "Observation"
    decision = "Decision"
    policy = "Policy"
    proposal = "Proposal"


class EvidenceRef(BaseModel):
    model_config = STRICT

    kind: Literal["snapshot", "trace", "result", "hitl", "incident"]
    ref: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _reject_payload(value: Any, path: str = "value") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_FACT_KEYS:
                raise ValueError(f"forbidden payload key at {path}.{key}")
            _reject_payload(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_payload(item, f"{path}[{index}]")


class CandidateFact(BaseModel):
    model_config = STRICT

    name: str = Field(min_length=1)
    value: JsonValue
    source_path: str = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def value_is_control_plane_only(cls, value: JsonValue) -> JsonValue:
        _reject_payload(value)
        return value


class KnowledgeCandidate(BaseModel):
    model_config = STRICT

    schema_version: Literal["control-knowledge-candidate/v1"] = (
        "control-knowledge-candidate/v1"
    )
    candidate_id: str = Field(default_factory=lambda: f"candidate_{uuid.uuid4().hex}")
    scope: Literal["system"] = "system"
    source_kind: Literal[
        "system_snapshot",
        "runtime_observation",
        "incident",
        "human_correction",
        "registry_drift",
    ]
    subjects: list[str] = Field(min_length=1)
    suggested_page_type: PageType
    summary: str = Field(min_length=1, max_length=500)
    facts: list[CandidateFact] = Field(min_length=1, max_length=100)
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=20)
    sensitivity: Literal["internal"] = "internal"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("subjects")
    @classmethod
    def subjects_are_stable_page_ids(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            page_id = value.strip().strip("/")
            if not page_id or page_id.endswith(".md") or ".." in page_id.split("/"):
                raise ValueError("subjects must be wiki-relative page IDs without .md")
            if page_id not in normalized:
                normalized.append(page_id)
        return normalized


class SystemSnapshot(BaseModel):
    model_config = STRICT

    schema_version: Literal["control-system-snapshot/v2"] = (
        "control-system-snapshot/v2"
    )
    snapshot_id: str
    commit_sha: str
    graph_nodes: list[str]
    graph_edges: list[list[str]]
    agent_slots: dict[str, list[str]]
    agent_profiles: dict[str, dict[str, JsonValue]]
    result_schema_version: str
    result_fields: list[str]
    artifact_fields: list[str]
    hitl_contracts: list[str]
    trace_schema_version: str
    followup_fields: list[str]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SystemCollection(BaseModel):
    model_config = STRICT

    snapshot: SystemSnapshot
    registry_issues: list[dict[str, str]]
    candidates: list[KnowledgeCandidate]


class PageDraft(BaseModel):
    model_config = STRICT

    page_id: str = Field(min_length=1)
    page_type: PageType
    title: str = Field(min_length=1)
    description: str = Field(min_length=1, max_length=500)
    routing_summary: str = Field(default="", max_length=1000)
    body_markdown: str = Field(min_length=1)
    relations: dict[str, list[str]] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def title_matches_first_heading(self):
        first_h1 = next(
            (
                line[2:].strip()
                for line in self.body_markdown.splitlines()
                if line.startswith("# ")
            ),
            "",
        )
        if first_h1 != self.title:
            raise ValueError("body first H1 must equal title")
        return self


class CurationDecision(BaseModel):
    model_config = STRICT

    action: Literal["no_change", "create", "update", "review_required"]
    target_page_id: str = ""
    draft: PageDraft | None = None
    rationale: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def action_shape_is_valid(self):
        if self.action == "no_change" and self.draft is not None:
            raise ValueError("no_change cannot include a draft")
        if self.action != "no_change" and self.draft is None:
            raise ValueError(f"{self.action} requires draft")
        if self.action == "update" and not self.target_page_id:
            raise ValueError("update requires target_page_id")
        return self


class CurationLedgerEntry(BaseModel):
    model_config = STRICT

    candidate_id: str
    fingerprint: str
    action: Literal[
        "no_change",
        "created",
        "updated",
        "proposal",
        "invalid_decision",
        "failed",
    ]
    target_page_id: str = ""
    rationale: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def candidate_fingerprint(candidate: KnowledgeCandidate) -> str:
    stable = candidate.model_dump(mode="json", exclude={"candidate_id", "created_at"})
    encoded = json.dumps(
        stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

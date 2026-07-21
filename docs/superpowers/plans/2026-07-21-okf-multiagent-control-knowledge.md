# OKF Multi-Agent Control Knowledge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a separate OKF-compatible control-plane knowledge bundle that automatically converts structured multi-agent code snapshots and runtime evidence into curated, governed Markdown without touching the existing domain wiki.

**Architecture:** Existing workers emit no Markdown. A collector converts explicit graph/contracts/results/HITL state into redacted `KnowledgeCandidate` records, a dedicated bounded service persists them, and one curator uses semantic comparison plus deterministic policy to choose `no_change`, canonical update, or review proposal. A validator and atomic store own every filesystem mutation under `08-YieldAgent/multiagent_knowledge/`.

**Tech Stack:** Python 3, Pydantic v2, python-frontmatter, PyYAML, LangChain LLM factory, asyncio, pytest, Markdown/YAML/JSON

**Design spec:** `docs/superpowers/specs/2026-07-21-okf-multiagent-control-knowledge-design.md`

## Global Constraints

- Do not modify or reuse `08-YieldAgent/wiki/`, `wiki_store.py`, `wiki_queue.py`, `wiki_lint.py`, or domain wiki state fields.
- Store only multi-agent control-plane knowledge: Agent, Workflow, Contract, Component, Runbook, Observation, Decision, Policy, Proposal.
- Never persist user query text, message content, ResultEnvelope rows, LOT/product/entity values, SQL, prompts, artifact bodies, or secrets.
- Do not use natural-language keyword, regex, phrase lists, Korean expression tables, failure-message matching, or growing few-shot examples to decide what becomes knowledge.
- Candidate routing uses exact structured `subjects`, `source_kind`, `suggested_page_type`, schema versions, agent names, and event types.
- Workers may submit candidates only. Only the dedicated curator/writer may mutate Markdown.
- System snapshots may auto-update Agent, Workflow, Contract, and Component pages. Runtime evidence may auto-update Observation pages only.
- Runbook, Decision, Policy, governance, and any cross-type change always goes to `wiki/review_queue/`.
- First-version scope is exactly `system`; do not add user or tenant scopes.
- Root `wiki/index.md` frontmatter contains only `okf_version`. Nested `index.md` files contain no frontmatter.
- Every non-index wiki page has `type`, `page_id`, `title`, `description`, `routing_summary`, `status`, `owner`, `source_status`, `agent_use`, `llmwiki_status`, `llmwiki_owner`, `llmwiki_source_status`, `llmwiki_agent_use`, `sensitivity`, `last_reviewed`, `review_cycle`, `version`, `relations`, and `evidence_refs`.
- Page body first H1 equals frontmatter `title`; relationships use `[[page_id]]` wikilinks.
- Preserve unknown frontmatter keys on update; represent removal through deprecation/supersession, never file deletion.
- All writes use temporary files plus `os.replace`; only one process runs the curator writer.
- Knowledge failures must never fail, delay before `stream_end`, or change the main analysis result.
- Default rollout flags are `CONTROL_KNOWLEDGE_ENABLED=false` and `CONTROL_KNOWLEDGE_WRITER=false`.
- Reuse dependencies already present in `requirements.txt`; add no package.
- Every implementation task follows red-green TDD and commits only the files listed for that task.
- Completion requires a live run through actual planner, worker/tool, MongoDB, LLM curator, filesystem writer, and server restart. Unit tests alone are insufficient.

## Locked File Structure

### New runtime files

- `08-YieldAgent/control_knowledge_models.py` — candidate, snapshot, page draft, curation decision, ledger models
- `08-YieldAgent/control_knowledge_validator.py` — OKF/profile scan and page validation
- `08-YieldAgent/control_knowledge_store.py` — immutable evidence, dedupe, atomic page/proposal/log/ledger writes
- `08-YieldAgent/control_knowledge_collector.py` — system snapshot and redacted runtime candidate builders
- `08-YieldAgent/control_knowledge_curator.py` — LLM semantic diff and deterministic write policy
- `08-YieldAgent/control_knowledge_service.py` — bounded queue, retry, single-writer lifecycle
- `08-YieldAgent/control_knowledge_cli.py` — `lint`, `snapshot`, `curate-once`, `approve` commands

### New bundle files

- `08-YieldAgent/multiagent_knowledge/AGENTS.md`
- `08-YieldAgent/multiagent_knowledge/CLAUDE.md`
- `08-YieldAgent/multiagent_knowledge/SCHEMA.md`
- `08-YieldAgent/multiagent_knowledge/okf.profile.json`
- `08-YieldAgent/multiagent_knowledge/frontmatter.schema.json`
- `08-YieldAgent/multiagent_knowledge/raw/README.md`
- `08-YieldAgent/multiagent_knowledge/wiki/index.md`
- `08-YieldAgent/multiagent_knowledge/wiki/log.md`
- local indexes under `architecture`, `agents`, `workflows`, `contracts`, `decisions`, `runbooks`, `observations`, `governance`, `review_queue`
- initial pages `architecture/system-overview.md`, `architecture/state-and-data-flow.md`, `governance/ownership.md`, `governance/agent-write-policy.md`, `governance/review-policy.md`, `runbooks/operating-curator.md`

### Modified runtime and guidance files

- `08-YieldAgent/agent_server.py` — service lifecycle, startup snapshot, completed-turn and incident submission
- `08-YieldAgent/AGENTS.md` — control-plane knowledge read routing only

### New tests and verification

- `08-YieldAgent/tests/test_control_knowledge_models.py`
- `08-YieldAgent/tests/test_control_knowledge_validator.py`
- `08-YieldAgent/tests/test_control_knowledge_store.py`
- `08-YieldAgent/tests/test_control_knowledge_collector.py`
- `08-YieldAgent/tests/test_control_knowledge_curator.py`
- `08-YieldAgent/tests/test_control_knowledge_service.py`
- `08-YieldAgent/tests/test_control_knowledge_server.py`
- `08-YieldAgent/tests/test_control_knowledge_e2e.py`
- `08-YieldAgent/tests/verify_control_knowledge_live.py`

---

### Task 1: Define strict control-knowledge contracts

**Files:**
- Create: `08-YieldAgent/control_knowledge_models.py`
- Create: `08-YieldAgent/tests/test_control_knowledge_models.py`

**Interfaces:**
- Consumes: Pydantic v2 `BaseModel`, `ConfigDict`, `Field`, `JsonValue`, validators
- Produces: `PageType`, `EvidenceRef`, `CandidateFact`, `KnowledgeCandidate`, `SystemSnapshot`, `PageDraft`, `CurationDecision`, `CurationLedgerEntry`, `candidate_fingerprint()`

- [ ] **Step 1: Write failing model tests**

```python
# 08-YieldAgent/tests/test_control_knowledge_models.py
from datetime import datetime, timezone
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_knowledge_models import (
    CandidateFact,
    CurationDecision,
    EvidenceRef,
    KnowledgeCandidate,
    PageDraft,
    PageType,
    candidate_fingerprint,
)

pytestmark = pytest.mark.no_server


def _candidate(**updates):
    payload = {
        "source_kind": "system_snapshot",
        "subjects": ["contracts/result-envelope"],
        "suggested_page_type": "Contract",
        "summary": "Result contract snapshot changed",
        "facts": [{"name": "schema_version", "value": "result-envelope/v1",
                   "source_path": "result_contracts.RESULT_ENVELOPE_SCHEMA_VERSION"}],
        "evidence_refs": [{"kind": "snapshot", "ref": "snapshot_1",
                           "sha256": "a" * 64}],
    }
    payload.update(updates)
    return KnowledgeCandidate.model_validate(payload)


def test_candidate_rejects_payload_fields_recursively():
    with pytest.raises(ValidationError, match="forbidden payload key"):
        _candidate(facts=[{
            "name": "bad",
            "value": {"nested": {"rows": [{"lot_id": "4SS0001"}]}},
            "source_path": "result.rows",
        }])


def test_candidate_requires_exact_system_scope():
    with pytest.raises(ValidationError):
        _candidate(scope="tenant-a")


def test_fingerprint_ignores_candidate_identity_and_time():
    first = _candidate(candidate_id="candidate_a")
    second = _candidate(candidate_id="candidate_b")
    assert candidate_fingerprint(first) == candidate_fingerprint(second)


def test_page_draft_requires_h1_equal_to_title():
    with pytest.raises(ValidationError, match="first H1"):
        PageDraft(
            page_id="agents/wads-agent",
            page_type=PageType.agent,
            title="WADS Agent",
            description="WADS worker boundary",
            body_markdown="# Different title\n",
        )


def test_no_change_cannot_carry_draft():
    draft = PageDraft(
        page_id="agents/wads-agent",
        page_type="Agent",
        title="WADS Agent",
        description="WADS worker boundary",
        body_markdown="# WADS Agent\n\nReads task-scoped parameters.\n",
    )
    with pytest.raises(ValidationError):
        CurationDecision(action="no_change", rationale="same", draft=draft)


def test_update_requires_draft_and_evidence():
    with pytest.raises(ValidationError):
        CurationDecision(action="update", target_page_id="agents/wads-agent",
                         rationale="changed")
```

- [ ] **Step 2: Run the test and verify the missing module failure**

Run: `cd 08-YieldAgent && pytest tests/test_control_knowledge_models.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'control_knowledge_models'`.

- [ ] **Step 3: Implement the strict models**

Create `08-YieldAgent/control_knowledge_models.py` with these exact public names and validation rules:

```python
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)
FORBIDDEN_FACT_KEYS = frozenset({
    "artifact_payload", "base64", "bytes", "content", "data", "html",
    "messages", "prompt", "query", "rows", "sql",
})


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
    schema_version: Literal["control-knowledge-candidate/v1"] = "control-knowledge-candidate/v1"
    candidate_id: str = Field(default_factory=lambda: f"candidate_{uuid.uuid4().hex}")
    scope: Literal["system"] = "system"
    source_kind: Literal["system_snapshot", "runtime_observation", "incident", "human_correction"]
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
    schema_version: Literal["control-system-snapshot/v1"] = "control-system-snapshot/v1"
    snapshot_id: str
    commit_sha: str
    graph_nodes: list[str]
    graph_edges: list[list[str]]
    agent_slots: dict[str, list[str]]
    result_schema_version: str
    trace_schema_version: str
    followup_fields: list[str]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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
        first_h1 = next((line[2:].strip() for line in self.body_markdown.splitlines()
                         if line.startswith("# ")), "")
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
    action: Literal["no_change", "created", "updated", "proposal", "invalid_decision", "failed"]
    target_page_id: str = ""
    rationale: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def candidate_fingerprint(candidate: KnowledgeCandidate) -> str:
    stable = candidate.model_dump(mode="json", exclude={"candidate_id", "created_at"})
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
```

The `EvidenceRef.sha256` pattern is structural validation, not semantic routing. Do not add content-dependent exceptions.

- [ ] **Step 4: Run the model tests**

Run: `cd 08-YieldAgent && pytest tests/test_control_knowledge_models.py -v`

Expected: 6 tests PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add 08-YieldAgent/control_knowledge_models.py 08-YieldAgent/tests/test_control_knowledge_models.py
git commit -m "feat(knowledge): define control contracts"
```

### Task 2: Scaffold the OKF bundle and validator

**Files:**
- Create: `08-YieldAgent/control_knowledge_validator.py`
- Create: `08-YieldAgent/tests/test_control_knowledge_validator.py`
- Create: all files listed under “New bundle files” in the locked structure

**Interfaces:**
- Consumes: `frontmatter`, `Path`, `PageType`
- Produces: `ValidationIssue`, `validate_page(path, wiki_root)`, `scan_bundle(bundle_root)`, `main(argv=None)`

- [ ] **Step 1: Write failing validator tests against a temporary bundle**

```python
# 08-YieldAgent/tests/test_control_knowledge_validator.py
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_knowledge_validator import scan_bundle

pytestmark = pytest.mark.no_server


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_root_index_allows_only_okf_version(tmp_path):
    _write(tmp_path / "wiki/index.md", "---\nokf_version: 1.0\nowner: bad\n---\n# Index\n")
    issues = scan_bundle(tmp_path)
    assert any(i.code == "root_index_extra_frontmatter" for i in issues)


def test_nested_index_rejects_frontmatter(tmp_path):
    _write(tmp_path / "wiki/index.md", "---\nokf_version: 1.0\n---\n# Index\n")
    _write(tmp_path / "wiki/agents/index.md", "---\ntype: Agent\n---\n# Agents\n")
    issues = scan_bundle(tmp_path)
    assert any(i.code == "nested_index_frontmatter" for i in issues)


def test_page_requires_governance_and_matching_identity(tmp_path):
    _write(tmp_path / "wiki/index.md", "---\nokf_version: 1.0\n---\n# Index\n")
    _write(tmp_path / "wiki/agents/wads-agent.md", "---\ntype: Agent\npage_id: wrong\n---\n# Other\n")
    codes = {i.code for i in scan_bundle(tmp_path)}
    assert "missing_governance" in codes
    assert "page_id_path_mismatch" in codes


def test_relationship_requires_existing_wikilink_target(tmp_path):
    _write(tmp_path / "wiki/index.md", "---\nokf_version: 1.0\n---\n# Index\n")
    _write(tmp_path / "wiki/agents/a.md", """---
type: Agent
page_id: agents/a
title: A
description: Agent A
routing_summary: Read before changing Agent A
status: current
owner: yield-platform
source_status: code-backed
agent_use: read-and-propose
llmwiki_status: current
llmwiki_owner: yield-platform
llmwiki_source_status: code-backed
llmwiki_agent_use: read-and-propose
sensitivity: internal
last_reviewed: 2026-07-21
review_cycle: P90D
version: 1
relations:
  depends_on: ["[[contracts/missing]]"]
evidence_refs: [snapshot:abc]
---
# A
""")
    issues = scan_bundle(tmp_path)
    assert any(i.code == "broken_relation" for i in issues)
```

- [ ] **Step 2: Run tests and verify the missing module failure**

Run: `cd 08-YieldAgent && pytest tests/test_control_knowledge_validator.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'control_knowledge_validator'`.

- [ ] **Step 3: Implement the validator**

Create `control_knowledge_validator.py` with:

```python
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import frontmatter

from control_knowledge_models import PageType

REQUIRED = frozenset({
    "type", "page_id", "title", "description", "routing_summary", "status", "owner",
    "source_status", "agent_use", "llmwiki_status", "llmwiki_owner",
    "llmwiki_source_status", "llmwiki_agent_use", "sensitivity", "last_reviewed",
    "review_cycle", "version", "relations", "evidence_refs",
})
ALLOWED_STATUS = frozenset({"draft", "reviewed", "current", "stale", "deprecated", "archived", "blocked"})
WIKILINK = re.compile(r'^\[\[([a-z0-9][a-z0-9_./-]*)\]\]$')


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str


def _issue(code: str, path: Path, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=str(path), message=message)


def _frontmatter(path: Path) -> tuple[dict[str, Any], str, list[ValidationIssue]]:
    try:
        post = frontmatter.load(path)
        return dict(post.metadata), post.content or "", []
    except Exception as exc:
        return {}, "", [_issue("invalid_frontmatter", path, str(exc))]


def validate_page(path: Path, wiki_root: Path) -> list[ValidationIssue]:
    metadata, body, issues = _frontmatter(path)
    if issues:
        return issues
    relative = path.relative_to(wiki_root)
    if relative == Path("index.md"):
        extra = set(metadata) - {"okf_version"}
        if extra:
            issues.append(_issue("root_index_extra_frontmatter", path, f"extra={sorted(extra)}"))
        if set(metadata) != {"okf_version"}:
            issues.append(_issue("root_index_missing_version", path, "root index needs okf_version only"))
        return issues
    if relative == Path("log.md"):
        if metadata:
            issues.append(_issue("root_log_frontmatter", path, "root log must not have frontmatter"))
        return issues
    if path.name == "index.md":
        if metadata:
            issues.append(_issue("nested_index_frontmatter", path, "nested index must not have frontmatter"))
        return issues

    missing = REQUIRED - set(metadata)
    if missing:
        issues.append(_issue("missing_governance", path, f"missing={sorted(missing)}"))
    expected_id = relative.with_suffix("").as_posix()
    if metadata.get("page_id") != expected_id:
        issues.append(_issue("page_id_path_mismatch", path, f"expected={expected_id}"))
    if metadata.get("type") not in {item.value for item in PageType}:
        issues.append(_issue("invalid_type", path, str(metadata.get("type"))))
    if metadata.get("status") not in ALLOWED_STATUS:
        issues.append(_issue("invalid_status", path, str(metadata.get("status"))))
    first_h1 = next((line[2:].strip() for line in body.splitlines() if line.startswith("# ")), "")
    if metadata.get("title") and first_h1 != metadata.get("title"):
        issues.append(_issue("title_h1_mismatch", path, f"h1={first_h1!r}"))
    return issues


def scan_bundle(bundle_root: Path) -> list[ValidationIssue]:
    wiki_root = bundle_root / "wiki"
    if not wiki_root.exists():
        return [_issue("missing_wiki_root", wiki_root, "wiki directory does not exist")]
    issues = []
    page_ids = set()
    relations: list[tuple[Path, str]] = []
    for path in sorted(wiki_root.rglob("*.md")):
        issues.extend(validate_page(path, wiki_root))
        if path.name == "index.md":
            continue
        metadata, _, parse_issues = _frontmatter(path)
        if parse_issues:
            continue
        page_id = str(metadata.get("page_id") or "")
        if page_id in page_ids:
            issues.append(_issue("duplicate_page_id", path, page_id))
        elif page_id:
            page_ids.add(page_id)
        for values in (metadata.get("relations") or {}).values():
            for value in values or []:
                match = WIKILINK.fullmatch(str(value))
                if not match:
                    issues.append(_issue("invalid_relation", path, str(value)))
                else:
                    relations.append((path, match.group(1)))
    for path, target in relations:
        if target not in page_ids:
            issues.append(_issue("broken_relation", path, target))
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)
    issues = scan_bundle(args.bundle)
    for issue in issues:
        print(f"[{issue.code}] {issue.path}: {issue.message}")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

The wikilink regex validates a machine format only. It must not be expanded to interpret natural-language relations.

- [ ] **Step 4: Create the exact OKF scaffold**

Use `apply_patch` to create the locked bundle tree. Required initial contents:

```markdown
<!-- multiagent_knowledge/wiki/index.md -->
---
okf_version: "1.0"
---
# Multi-Agent Control Knowledge

- [[architecture/system-overview]]
- [[architecture/state-and-data-flow]]
- [Agents](agents/index.md)
- [Workflows](workflows/index.md)
- [Contracts](contracts/index.md)
- [Runbooks](runbooks/index.md)
- [Governance](governance/index.md)
- [Review queue](review_queue/index.md)
```

Each nested index contains only `# <Section>` and relative links, with no `---` delimiter. `wiki/log.md` starts with `# Multi-Agent Knowledge Log` and no dated entry. `raw/README.md` states that snapshots/candidates are immutable evidence and must not contain domain payloads.

Create each initial non-index page with the required metadata. Use these stable IDs and titles:

| Path | type | page_id | title | source_status |
|---|---|---|---|---|
| `architecture/system-overview.md` | Component | `architecture/system-overview` | Multi-Agent System Overview | code-backed |
| `architecture/state-and-data-flow.md` | Component | `architecture/state-and-data-flow` | State and Data Flow | code-backed |
| `governance/ownership.md` | Policy | `governance/ownership` | Knowledge Ownership | reviewed |
| `governance/agent-write-policy.md` | Policy | `governance/agent-write-policy` | Agent Write Policy | reviewed |
| `governance/review-policy.md` | Policy | `governance/review-policy` | Review Policy | reviewed |
| `runbooks/operating-curator.md` | Runbook | `runbooks/operating-curator` | Operating the Curator | reviewed |

Set `owner: yield-platform`, `agent_use: read-and-propose`, `sensitivity: internal`, `last_reviewed: 2026-07-21`, `review_cycle: P90D`, `version: 1`, `relations: {}`, and `evidence_refs: [design:2026-07-21-okf-multiagent-control-knowledge]`. Set a one-sentence `routing_summary` explaining when an agent should open the page. Mirror the four canonical governance values into `llmwiki_status`, `llmwiki_owner`, `llmwiki_source_status`, and `llmwiki_agent_use`. The body first H1 must equal the table title and describe only the responsibilities fixed in the design spec.

`multiagent_knowledge/AGENTS.md` must state: read `wiki/index.md`, follow local indexes, treat code as runtime authority, never put domain/user data in this bundle, submit proposals for protected pages, and run the validator before edits. `CLAUDE.md` contains only `Read and follow ./AGENTS.md.`

`okf.profile.json` must declare `okf_version: "1.0"`, `bundle_root: "wiki"`, reserved root index/log paths, allowed page types, and the required governance fields. `frontmatter.schema.json` must express the same enum and required keys with JSON Schema draft 2020-12. `SCHEMA.md` documents `page_id`, relations, evidence refs, status, and the protected-type policy without adding fields not present in the design spec.

- [ ] **Step 5: Run validator tests and lint the real scaffold**

Run:

```bash
cd 08-YieldAgent
pytest tests/test_control_knowledge_validator.py -v
python control_knowledge_validator.py multiagent_knowledge
```

Expected: 4 tests PASS and validator exit code 0 with no issue lines.

- [ ] **Step 6: Commit Task 2**

```bash
git add 08-YieldAgent/control_knowledge_validator.py \
  08-YieldAgent/tests/test_control_knowledge_validator.py \
  08-YieldAgent/multiagent_knowledge
git commit -m "feat(knowledge): scaffold OKF control bundle"
```

---
### Task 3: Add immutable evidence and atomic page storage

**Files:**
- Create: `08-YieldAgent/control_knowledge_store.py`
- Create: `08-YieldAgent/tests/test_control_knowledge_store.py`

**Interfaces:**
- Consumes: `KnowledgeCandidate`, `PageDraft`, `CurationLedgerEntry`, `candidate_fingerprint()`, `frontmatter`
- Produces: `StoredPage`, `ControlKnowledgeStore.save_candidate()`, `.pending_candidates()`, `.load_pages()`, `.write_page()`, `.write_proposal()`, `.append_ledger()`, `.approve_proposal()`

- [ ] **Step 1: Write failing storage tests**

```python
# 08-YieldAgent/tests/test_control_knowledge_store.py
import json
import os
from pathlib import Path
import sys

import frontmatter
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_knowledge_models import CurationLedgerEntry, KnowledgeCandidate, PageDraft
from control_knowledge_store import ControlKnowledgeStore

pytestmark = pytest.mark.no_server


def _candidate():
    return KnowledgeCandidate.model_validate({
        "source_kind": "system_snapshot",
        "subjects": ["agents/wads-agent"],
        "suggested_page_type": "Agent",
        "summary": "WADS agent snapshot",
        "facts": [{"name": "slot_keys", "value": ["lotcd"],
                   "source_path": "canonical_request.AGENT_SLOT_RULES.wads_agent"}],
        "evidence_refs": [{"kind": "snapshot", "ref": "snapshot_1", "sha256": "a" * 64}],
    })


def _draft(body=""):
    return PageDraft(
        page_id="agents/wads-agent",
        page_type="Agent",
        title="WADS Agent",
        description="WADS worker contract",
        body_markdown=body or "# WADS Agent\n\nReads task-scoped parameters.\n",
        evidence_refs=["snapshot_1"],
    )


def test_candidate_is_immutable_and_deduplicated(tmp_path):
    store = ControlKnowledgeStore(tmp_path)
    first = store.save_candidate(_candidate())
    second = store.save_candidate(_candidate())
    assert first == second
    assert len(list((tmp_path / "raw/candidates").glob("*.json"))) == 1


def test_page_update_preserves_unknown_metadata_and_increments_version(tmp_path):
    store = ControlKnowledgeStore(tmp_path)
    store.write_page(_draft(), _candidate())
    path = tmp_path / "wiki/agents/wads-agent.md"
    post = frontmatter.load(path)
    post.metadata["custom_field"] = "keep-me"
    path.write_text(frontmatter.dumps(post), encoding="utf-8")

    store.write_page(_draft("# WADS Agent\n\nUses ResultEnvelope.\n"), _candidate())
    updated = frontmatter.load(path)
    assert updated.metadata["custom_field"] == "keep-me"
    assert updated.metadata["version"] == 2
    assert "Uses ResultEnvelope" in updated.content


def test_identical_write_does_not_increment_version(tmp_path):
    store = ControlKnowledgeStore(tmp_path)
    store.write_page(_draft(), _candidate())
    store.write_page(_draft(), _candidate())
    assert frontmatter.load(tmp_path / "wiki/agents/wads-agent.md").metadata["version"] == 1


def test_replace_failure_preserves_existing_page(tmp_path, monkeypatch):
    store = ControlKnowledgeStore(tmp_path)
    store.write_page(_draft(), _candidate())
    path = tmp_path / "wiki/agents/wads-agent.md"
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        store.write_page(_draft("# WADS Agent\n\nChanged.\n"), _candidate())
    assert path.read_text(encoding="utf-8") == before


def test_proposal_does_not_modify_canonical_page(tmp_path):
    store = ControlKnowledgeStore(tmp_path)
    proposal = store.write_proposal(_draft(), _candidate(), rationale="human review")
    assert proposal.parent.name == "review_queue"
    assert not (tmp_path / "wiki/agents/wads-agent.md").exists()
```

- [ ] **Step 2: Run tests and verify the missing module failure**

Run: `cd 08-YieldAgent && pytest tests/test_control_knowledge_store.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'control_knowledge_store'`.

- [ ] **Step 3: Implement atomic storage and dedupe**

Create `control_knowledge_store.py`. Keep these paths and method signatures exact:

```python
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
            text = json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False,
                              sort_keys=True, indent=2) + "\n"
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
        return [path for path in sorted(self.candidates.glob("*.json")) if path.stem not in done]

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

    def _metadata_for(self, draft: PageDraft, candidate: KnowledgeCandidate,
                      existing: StoredPage | None) -> dict[str, Any]:
        now = datetime.now(timezone.utc).date().isoformat()
        metadata = dict(existing.metadata) if existing else {}
        metadata.update({
            "type": draft.page_type.value,
            "page_id": draft.page_id,
            "title": draft.title,
            "description": draft.description,
            "routing_summary": draft.routing_summary or draft.description,
            "status": metadata.get("status", "current"),
            "owner": metadata.get("owner", "yield-platform"),
            "source_status": "code-backed" if candidate.source_kind == "system_snapshot" else "trace-backed",
            "agent_use": metadata.get("agent_use", "read-and-propose"),
            "sensitivity": "internal",
            "last_reviewed": now,
            "review_cycle": metadata.get("review_cycle", "P90D"),
            "relations": draft.relations,
            "evidence_refs": sorted(set(list(metadata.get("evidence_refs") or []) + draft.evidence_refs)),
        })
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
            for item in self.wiki.rglob("*.md") if item.name != "index.md"
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
        if existing and old_comparable == comparable and existing.body_markdown.strip() == draft.body_markdown.strip():
            return path
        metadata["version"] = int((existing.metadata if existing else {}).get("version", 0)) + 1
        rendered = frontmatter.dumps(frontmatter.Post(draft.body_markdown.strip() + "\n", **metadata))
        self._atomic_text(path, rendered)
        self._prepend_log("updated" if existing else "created", draft.page_id, candidate.candidate_id)
        return path

    def write_proposal(self, draft: PageDraft, candidate: KnowledgeCandidate, *, rationale: str) -> Path:
        self.ensure_dirs()
        page_id = f"review_queue/{candidate.candidate_id}"
        target_exists = bool(self.load_pages([draft.page_id]))
        metadata = {
            "type": "Proposal", "page_id": page_id,
            "title": f"Review {draft.title}", "description": rationale,
            "routing_summary": f"Review the proposed change to {draft.page_id}",
            "status": "draft", "owner": "yield-platform", "source_status": "candidate-backed",
            "agent_use": "read-and-propose", "sensitivity": "internal",
            "llmwiki_status": "draft", "llmwiki_owner": "yield-platform",
            "llmwiki_source_status": "candidate-backed",
            "llmwiki_agent_use": "read-and-propose",
            "last_reviewed": datetime.now(timezone.utc).date().isoformat(), "review_cycle": "P30D",
            "version": 1,
            "relations": {"proposes_update_to": [f"[[{draft.page_id}]]"]} if target_exists else {},
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
        body = f"# Review {draft.title}\n\n## Rationale\n\n{rationale}\n\n## Proposed page\n\n{draft.body_markdown}"
        path = self.review_queue / f"{candidate.candidate_id}.md"
        self._atomic_text(path, frontmatter.dumps(frontmatter.Post(body, **metadata)))
        self._prepend_log("proposal", draft.page_id, candidate.candidate_id)
        return path

    def append_ledger(self, entry: CurationLedgerEntry) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        with self.ledger.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _prepend_log(self, action: str, page_id: str, candidate_id: str) -> None:
        self.ensure_dirs()
        current = self.log.read_text(encoding="utf-8")
        header = "# Multi-Agent Knowledge Log\n"
        rest = current[len(header):].lstrip("\n") if current.startswith(header) else current
        timestamp = datetime.now(timezone.utc).isoformat()
        entry = f"\n## {timestamp}\n\n- {action} `[[{page_id}]]` from `{candidate_id}`\n"
        self._atomic_text(self.log, header + entry + ("\n" + rest if rest else ""))
```

Add this method to the same class; it never accepts a target path from CLI input:

```python
    def approve_proposal(self, proposal_id: str, candidate: KnowledgeCandidate) -> Path:
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
```

- [ ] **Step 4: Run storage tests and the validator regression**

Run:

```bash
cd 08-YieldAgent
pytest tests/test_control_knowledge_store.py tests/test_control_knowledge_validator.py -v
```

Expected: 9 tests PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add 08-YieldAgent/control_knowledge_store.py 08-YieldAgent/tests/test_control_knowledge_store.py
git commit -m "feat(knowledge): add atomic control store"
```

### Task 4: Collect code snapshots and redacted runtime evidence

**Files:**
- Create: `08-YieldAgent/control_knowledge_collector.py`
- Create: `08-YieldAgent/tests/test_control_knowledge_collector.py`

**Interfaces:**
- Consumes: existing `workflow`, `AGENT_SLOT_RULES`, `Followup`, trace/result schema versions, completed state values
- Produces: `build_system_snapshot()`, `current_system_snapshot()`, `system_snapshot_candidates()`, `runtime_candidates()`, `incident_candidate()`

- [ ] **Step 1: Write failing collector tests**

```python
# 08-YieldAgent/tests/test_control_knowledge_collector.py
import sys
from pathlib import Path

from langchain_core.messages import AIMessage
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_knowledge_collector import (
    build_system_snapshot,
    incident_candidate,
    runtime_candidates,
    system_snapshot_candidates,
)

pytestmark = pytest.mark.no_server


class FakeWorkflow:
    nodes = {"planner": object(), "wads_agent": object(), "replanner": object()}
    edges = {("__start__", "planner"), ("wads_agent", "replanner")}


def test_snapshot_is_sorted_and_split_into_stable_subjects():
    snapshot = build_system_snapshot(
        workflow=FakeWorkflow(),
        agent_slot_rules={"wads_agent": {"allowed": {"fail_type", "lotcd"}}},
        result_schema_version="result-envelope/v1",
        trace_schema_version="local-trace/v1",
        followup_fields=["goal", "agent"],
        commit_sha="abc123",
    )
    assert snapshot.graph_nodes == ["planner", "replanner", "wads_agent"]
    assert snapshot.agent_slots["wads_agent"] == ["fail_type", "lotcd"]
    assert {c.subjects[0] for c in system_snapshot_candidates(snapshot)} == {
        "agents/wads-agent", "contracts/result-envelope", "contracts/local-trace",
        "workflows/orchestration-graph",
    }


def test_runtime_candidate_keeps_shape_but_drops_rows_and_entities():
    message = AIMessage(content="domain answer", name="wads_agent", additional_kwargs={
        "result": {
            "schema_version": "result-envelope/v1", "result_id": "result_1",
            "source_agent": "wads_agent", "kind": "report", "status": "success",
            "summary": "contains domain values", "rows": [{"lot_id": "4SS0001"}],
            "entities": {"lot_ids": ["4SS0001"]}, "artifact_refs": [],
            "columns": [], "provenance": {}, "metadata": {}, "extensions": {},
            "followups": [], "created_at": "2026-07-21T00:00:00Z",
        }
    })
    candidates = runtime_candidates({
        "trace_id": "trace_1", "turn_id": "turn_1", "messages": [message],
        "task_plan": [{"task_id": "task_1", "agent": "wads_agent",
                       "goal": "secret", "params": {"lotcd": "4SS"}}],
        "task_validation_issues": [], "hitl_responses": [],
    })
    dumped = candidates[0].model_dump_json()
    assert "4SS0001" not in dumped
    assert "domain answer" not in dumped
    assert "secret" not in dumped
    assert '"row_count":1' in dumped


def test_human_correction_omits_raw_answer():
    candidates = runtime_candidates({
        "trace_id": "trace_2", "turn_id": "turn_2", "messages": [], "task_plan": [],
        "task_validation_issues": [],
        "hitl_responses": [{"touchpoint": "plan_review", "decision": "modify",
                            "user_answer": "contains private correction", "agent": "planner"}],
    })
    dumped = "".join(c.model_dump_json() for c in candidates)
    assert "private correction" not in dumped
    assert "human_correction" in dumped


def test_incident_keeps_exception_type_not_message():
    candidate = incident_candidate(RuntimeError("secret DB payload"), source="agent_server",
                                   trace_id="trace_3", turn_id="turn_3", task_id="task_1")
    dumped = candidate.model_dump_json()
    assert "RuntimeError" in dumped
    assert "secret DB payload" not in dumped
```

- [ ] **Step 2: Run tests and verify the missing module failure**

Run: `cd 08-YieldAgent && pytest tests/test_control_knowledge_collector.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'control_knowledge_collector'`.

- [ ] **Step 3: Implement deterministic collection**

Core implementation:

```python
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from control_knowledge_models import CandidateFact, EvidenceRef, KnowledgeCandidate, SystemSnapshot
from result_contracts import validate_result_envelope


def _sha(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _evidence(kind: str, ref: str, value: Any) -> EvidenceRef:
    return EvidenceRef(kind=kind, ref=ref, sha256=_sha(value))


def build_system_snapshot(*, workflow, agent_slot_rules: dict, result_schema_version: str,
                          trace_schema_version: str, followup_fields: list[str],
                          commit_sha: str) -> SystemSnapshot:
    nodes = sorted(str(name) for name in workflow.nodes)
    edges = sorted([[str(source), str(target)] for source, target in workflow.edges])
    slots = {
        agent: sorted(str(key) for key in (rules.get("allowed") or []))
        for agent, rules in sorted(agent_slot_rules.items())
    }
    stable = {
        "commit_sha": commit_sha, "graph_nodes": nodes, "graph_edges": edges,
        "agent_slots": slots, "result_schema_version": result_schema_version,
        "trace_schema_version": trace_schema_version,
        "followup_fields": sorted(followup_fields),
    }
    return SystemSnapshot(snapshot_id=f"snapshot_{_sha(stable)[:16]}", **stable)


def current_system_snapshot() -> SystemSnapshot:
    from canonical_request import AGENT_SLOT_RULES
    from local_trace import TRACE_SCHEMA_VERSION
    from result_contracts import Followup, RESULT_ENVELOPE_SCHEMA_VERSION
    from supervisor import workflow

    repo = Path(__file__).resolve().parent.parent
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
        check=False, timeout=2,
    )
    commit_sha = process.stdout.strip() if process.returncode == 0 else "unknown"
    return build_system_snapshot(
        workflow=workflow, agent_slot_rules=AGENT_SLOT_RULES,
        result_schema_version=RESULT_ENVELOPE_SCHEMA_VERSION,
        trace_schema_version=TRACE_SCHEMA_VERSION,
        followup_fields=list(Followup.__annotations__), commit_sha=commit_sha,
    )
```

Add the remaining functions exactly as follows:

```python
def system_snapshot_candidates(snapshot: SystemSnapshot) -> list[KnowledgeCandidate]:
    snapshot_value = snapshot.model_dump(mode="json", exclude={"created_at"})
    evidence = _evidence("snapshot", snapshot.snapshot_id, snapshot_value)
    candidates: list[KnowledgeCandidate] = []
    for agent, slots in sorted(snapshot.agent_slots.items()):
        candidates.append(KnowledgeCandidate(
            source_kind="system_snapshot",
            subjects=[f"agents/{agent.replace('_', '-')}"],
            suggested_page_type="Agent",
            summary=f"Structured snapshot for canonical agent {agent}",
            facts=[
                CandidateFact(name="canonical_agent", value=agent,
                              source_path="canonical_request.AGENT_SLOT_RULES"),
                CandidateFact(name="slot_keys", value=slots,
                              source_path=f"canonical_request.AGENT_SLOT_RULES.{agent}.allowed"),
            ],
            evidence_refs=[evidence],
        ))
    candidates.extend([
        KnowledgeCandidate(
            source_kind="system_snapshot", subjects=["contracts/result-envelope"],
            suggested_page_type="Contract", summary="ResultEnvelope contract version snapshot",
            facts=[CandidateFact(name="schema_version", value=snapshot.result_schema_version,
                                 source_path="result_contracts.RESULT_ENVELOPE_SCHEMA_VERSION")],
            evidence_refs=[evidence],
        ),
        KnowledgeCandidate(
            source_kind="system_snapshot", subjects=["contracts/local-trace"],
            suggested_page_type="Contract", summary="Local trace contract version snapshot",
            facts=[CandidateFact(name="schema_version", value=snapshot.trace_schema_version,
                                 source_path="local_trace.TRACE_SCHEMA_VERSION")],
            evidence_refs=[evidence],
        ),
        KnowledgeCandidate(
            source_kind="system_snapshot", subjects=["workflows/orchestration-graph"],
            suggested_page_type="Workflow", summary="Explicit LangGraph topology snapshot",
            facts=[
                CandidateFact(name="graph_nodes", value=snapshot.graph_nodes,
                              source_path="supervisor.workflow.nodes"),
                CandidateFact(name="graph_edges", value=snapshot.graph_edges,
                              source_path="supervisor.workflow.edges"),
                CandidateFact(name="followup_fields", value=snapshot.followup_fields,
                              source_path="result_contracts.Followup.__annotations__"),
            ],
            evidence_refs=[evidence],
        ),
    ])
    return candidates


def _result_shapes(messages: list[Any]) -> list[dict[str, Any]]:
    shapes = []
    for message in messages or []:
        raw = (getattr(message, "additional_kwargs", {}) or {}).get("result")
        if not isinstance(raw, dict):
            continue
        try:
            envelope = validate_result_envelope(raw)
        except Exception:
            continue
        entities = envelope.entities.model_dump(mode="json")
        shapes.append({
            "result_id": envelope.result_id,
            "source_agent": envelope.source_agent,
            "kind": envelope.kind.value,
            "status": envelope.status.value,
            "row_count": len(envelope.rows),
            "column_count": len(envelope.columns),
            "artifact_ref_count": len(envelope.artifact_refs),
            "entity_counts": {key: len(value) for key, value in entities.items() if value},
            "followup_count": len(envelope.followups),
            "schema_version": envelope.schema_version,
        })
    return shapes


def runtime_candidates(state_values: dict[str, Any]) -> list[KnowledgeCandidate]:
    trace_id = str(state_values.get("trace_id") or "trace_unknown")
    turn_id = str(state_values.get("turn_id") or "turn_unknown")
    result_shapes = _result_shapes(state_values.get("messages") or [])
    task_shapes = [
        {
            "task_id": str(task.get("task_id") or ""),
            "agent": str(task.get("agent") or ""),
            "param_keys": sorted(str(key) for key in (task.get("params") or {})),
        }
        for task in (state_values.get("task_plan") or []) if isinstance(task, dict)
    ]
    issue_shapes = [
        {key: str(issue.get(key) or "") for key in ("type", "agent", "param")}
        for issue in (state_values.get("task_validation_issues") or []) if isinstance(issue, dict)
    ]
    hitl_shapes = [
        {key: str(item.get(key) or "") for key in ("touchpoint", "decision", "agent")}
        for item in (state_values.get("hitl_responses") or []) if isinstance(item, dict)
    ]
    candidates: list[KnowledgeCandidate] = []
    structural = {
        "results": result_shapes, "tasks": task_shapes,
        "validation_issues": issue_shapes, "hitl": hitl_shapes,
    }
    if result_shapes or task_shapes or issue_shapes:
        facts = []
        if result_shapes:
            facts.append(CandidateFact(name="result_shapes", value=result_shapes,
                                       source_path="result_contracts.ResultEnvelopeV1"))
        if task_shapes:
            facts.append(CandidateFact(name="task_shapes", value=task_shapes,
                                       source_path="query_state.task_plan.structure"))
        if issue_shapes:
            facts.append(CandidateFact(name="validation_issue_shapes", value=issue_shapes,
                                       source_path="query_state.task_validation_issues.structure"))
        candidates.append(KnowledgeCandidate(
            source_kind="runtime_observation", subjects=["observations/runtime-behavior"],
            suggested_page_type="Observation", summary="Completed turn control-flow structure",
            facts=facts, evidence_refs=[_evidence("trace", trace_id, structural)],
        ))
    for item in hitl_shapes:
        if item["decision"] != "modify":
            continue
        agent = item["agent"] or "orchestration"
        candidates.append(KnowledgeCandidate(
            source_kind="human_correction",
            subjects=[f"runbooks/{agent.replace('_', '-')}-operations"],
            suggested_page_type="Runbook", summary="Structured HITL modification signal",
            facts=[CandidateFact(name="hitl_shape", value=item,
                                 source_path="query_state.hitl_responses.structure")],
            evidence_refs=[_evidence("hitl", turn_id, item)],
        ))
    return candidates


def incident_candidate(exc: Exception, *, source: str, trace_id: str,
                       turn_id: str, task_id: str) -> KnowledgeCandidate:
    source_slug = "-".join(filter(None, "".join(
        char.lower() if char.isalnum() else " " for char in source
    ).split())) or "runtime"
    shape = {
        "exception_type": type(exc).__name__, "source": source,
        "trace_id": trace_id, "turn_id": turn_id, "task_id": task_id,
    }
    return KnowledgeCandidate(
        source_kind="incident", subjects=[f"observations/incidents-{source_slug}"],
        suggested_page_type="Observation", summary="Structured runtime incident",
        facts=[CandidateFact(name="incident_shape", value=shape,
                             source_path="agent_server.graph_exception")],
        evidence_refs=[_evidence("incident", trace_id or turn_id or task_id or source, shape)],
    )
```

No collector function reads a raw trace file, LLM response body, or user message.

- [ ] **Step 4: Run collector and model tests**

Run:

```bash
cd 08-YieldAgent
pytest tests/test_control_knowledge_collector.py tests/test_control_knowledge_models.py -v
```

Expected: 10 tests PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add 08-YieldAgent/control_knowledge_collector.py 08-YieldAgent/tests/test_control_knowledge_collector.py
git commit -m "feat(knowledge): collect control evidence"
```

### Task 5: Curate semantic changes behind deterministic policy

**Files:**
- Create: `08-YieldAgent/control_knowledge_curator.py`
- Create: `08-YieldAgent/tests/test_control_knowledge_curator.py`

**Interfaces:**
- Consumes: `ControlKnowledgeStore`, injected LLM, `KnowledgeCandidate`, `CurationDecision`
- Produces: `WriteDisposition`, `write_disposition()`, `ControlKnowledgeCurator.curate()`

- [ ] **Step 1: Write failing policy and curator tests**

```python
# 08-YieldAgent/tests/test_control_knowledge_curator.py
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_knowledge_curator import ControlKnowledgeCurator, write_disposition
from control_knowledge_models import KnowledgeCandidate
from control_knowledge_store import ControlKnowledgeStore

pytestmark = pytest.mark.no_server


class FakeResponse:
    def __init__(self, content): self.content = content


class FakeLLM:
    def __init__(self, payload): self.payload = payload; self.calls = []
    def invoke(self, messages):
        self.calls.append(messages)
        return FakeResponse(json.dumps(self.payload, ensure_ascii=False))


def _candidate(source="system_snapshot", page_type="Agent", subject="agents/wads-agent"):
    return KnowledgeCandidate.model_validate({
        "source_kind": source, "subjects": [subject], "suggested_page_type": page_type,
        "summary": "structured change",
        "facts": [{"name": "agent", "value": "wads_agent", "source_path": "registry"}],
        "evidence_refs": [{"kind": "snapshot" if source == "system_snapshot" else "trace",
                           "ref": "ev_1", "sha256": "a" * 64}],
    })


def _decision(page_type="Agent", subject="agents/wads-agent", action="create"):
    return {
        "action": action, "target_page_id": "" if action == "create" else subject,
        "rationale": "evidence changes the documented boundary",
        "draft": {
            "page_id": subject, "page_type": page_type, "title": "WADS Agent",
            "description": "WADS worker boundary", "body_markdown": "# WADS Agent\n\nStructured facts only.\n",
            "relations": {}, "evidence_refs": ["ev_1"],
        },
    }


def test_write_policy_is_exact_by_source_and_type():
    assert write_disposition("system_snapshot", "Agent") == "auto"
    assert write_disposition("runtime_observation", "Observation") == "auto"
    assert write_disposition("human_correction", "Runbook") == "review"
    assert write_disposition("system_snapshot", "Policy") == "review"


def test_snapshot_agent_page_is_written(tmp_path):
    store = ControlKnowledgeStore(tmp_path)
    curator = ControlKnowledgeCurator(store, FakeLLM(_decision()))
    entry = curator.curate(_candidate())
    assert entry.action == "created"
    assert (tmp_path / "wiki/agents/wads-agent.md").exists()


def test_protected_change_creates_proposal(tmp_path):
    store = ControlKnowledgeStore(tmp_path)
    payload = _decision(page_type="Runbook", subject="runbooks/wads-agent-operations")
    curator = ControlKnowledgeCurator(store, FakeLLM(payload))
    entry = curator.curate(_candidate("human_correction", "Runbook", "runbooks/wads-agent-operations"))
    assert entry.action == "proposal"
    assert list((tmp_path / "wiki/review_queue").glob("*.md"))
    assert not (tmp_path / "wiki/runbooks/wads-agent-operations.md").exists()


def test_no_change_writes_only_ledger(tmp_path):
    store = ControlKnowledgeStore(tmp_path)
    curator = ControlKnowledgeCurator(store, FakeLLM({"action": "no_change", "rationale": "same"}))
    entry = curator.curate(_candidate())
    assert entry.action == "no_change"
    assert store.ledger.exists()
    assert not (tmp_path / "wiki/agents/wads-agent.md").exists()


def test_missing_candidate_evidence_is_rejected(tmp_path):
    payload = _decision()
    payload["draft"]["evidence_refs"] = ["invented"]
    curator = ControlKnowledgeCurator(ControlKnowledgeStore(tmp_path), FakeLLM(payload))
    entry = curator.curate(_candidate())
    assert entry.action == "invalid_decision"
```

- [ ] **Step 2: Run tests and verify the missing module failure**

Run: `cd 08-YieldAgent && pytest tests/test_control_knowledge_curator.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'control_knowledge_curator'`.

- [ ] **Step 3: Implement policy and curator**

Create `control_knowledge_curator.py` with this policy and flow:

```python
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import json

from common import extract_json_from_llm
from control_knowledge_models import (
    CurationDecision, CurationLedgerEntry, KnowledgeCandidate, PageType,
    candidate_fingerprint,
)
from control_knowledge_store import ControlKnowledgeStore


class WriteDisposition(str, Enum):
    auto = "auto"
    review = "review"
    deny = "deny"


AUTO = {
    ("system_snapshot", "Agent"), ("system_snapshot", "Workflow"),
    ("system_snapshot", "Contract"), ("system_snapshot", "Component"),
    ("runtime_observation", "Observation"), ("incident", "Observation"),
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
            {"page_id": page.metadata.get("page_id"), "metadata": page.metadata,
             "body_markdown": page.body_markdown}
            for page in pages
        ]
        user_payload = {
            "candidate": candidate.model_dump(mode="json"),
            "allowed_target_page_ids": candidate.subjects,
            "existing_pages": page_context,
        }
        try:
            raw = self.llm.invoke([
                {"role": "system", "content": CURATOR_SYSTEM},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, default=str)},
            ]).content or ""
        except Exception as exc:
            raise CuratorCallError(type(exc).__name__) from exc
        return extract_json_from_llm(raw, CurationDecision)

    def curate(self, candidate: KnowledgeCandidate) -> CurationLedgerEntry:
        fingerprint = candidate_fingerprint(candidate)
        try:
            decision = self._decision(candidate)
            if decision.action == "no_change":
                entry = CurationLedgerEntry(candidate_id=candidate.candidate_id,
                    fingerprint=fingerprint, action="no_change", rationale=decision.rationale)
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
            if not set(decision.draft.evidence_refs).issubset(candidate_refs) or not decision.draft.evidence_refs:
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
                self.store.write_proposal(decision.draft, candidate, rationale=decision.rationale)
                action = "proposal"
            else:
                raise ValueError("candidate source cannot write requested page type")
            entry = CurationLedgerEntry(candidate_id=candidate.candidate_id,
                fingerprint=fingerprint, action=action, target_page_id=decision.draft.page_id,
                rationale=decision.rationale)
        except CuratorCallError:
            raise
        except Exception as exc:
            entry = CurationLedgerEntry(candidate_id=candidate.candidate_id,
                fingerprint=fingerprint, action="invalid_decision", rationale=type(exc).__name__)
        self.store.append_ledger(entry)
        return entry
```

The ledger rationale for exceptions stores only the exception class, never `str(exc)`. Curator output does not override candidate `source_kind` or write policy.

- [ ] **Step 4: Run curator, store, and model tests**

Run:

```bash
cd 08-YieldAgent
pytest tests/test_control_knowledge_curator.py \
  tests/test_control_knowledge_store.py \
  tests/test_control_knowledge_models.py -v
```

Expected: 16 tests PASS.

- [ ] **Step 5: Commit Task 5**

```bash
git add 08-YieldAgent/control_knowledge_curator.py 08-YieldAgent/tests/test_control_knowledge_curator.py
git commit -m "feat(knowledge): curate governed updates"
```

### Task 6: Run the curator through a bounded service and CLI

**Files:**
- Create: `08-YieldAgent/control_knowledge_service.py`
- Create: `08-YieldAgent/control_knowledge_cli.py`
- Create: `08-YieldAgent/tests/test_control_knowledge_service.py`

**Interfaces:**
- Consumes: `ControlKnowledgeStore`, `ControlKnowledgeCurator`, collector candidate builders, existing `get_llm()`
- Produces: `ControlKnowledgeService`, `service_from_env()`, CLI `lint|snapshot|curate-once|approve`

- [ ] **Step 1: Write failing service tests**

```python
# 08-YieldAgent/tests/test_control_knowledge_service.py
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_knowledge_curator import CuratorCallError
from control_knowledge_models import CurationLedgerEntry, KnowledgeCandidate, candidate_fingerprint
from control_knowledge_service import ControlKnowledgeService
from control_knowledge_store import ControlKnowledgeStore

pytestmark = pytest.mark.no_server


def _candidate():
    return KnowledgeCandidate.model_validate({
        "source_kind": "runtime_observation", "subjects": ["observations/runtime-behavior"],
        "suggested_page_type": "Observation", "summary": "runtime shape",
        "facts": [{"name": "agents", "value": ["wads_agent"], "source_path": "state.messages"}],
        "evidence_refs": [{"kind": "trace", "ref": "trace_1", "sha256": "a" * 64}],
    })


class RecordingCurator:
    def __init__(self, store, fail_once=False):
        self.store = store; self.calls = 0; self.fail_once = fail_once
    def curate(self, candidate):
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise CuratorCallError("offline")
        entry = CurationLedgerEntry(candidate_id=candidate.candidate_id,
            fingerprint=candidate_fingerprint(candidate), action="no_change", rationale="same")
        self.store.append_ledger(entry)
        return entry


def test_disabled_service_does_not_persist(tmp_path):
    async def scenario():
        store = ControlKnowledgeStore(tmp_path)
        service = ControlKnowledgeService(store, RecordingCurator(store), enabled=False, writer=False)
        assert await service.submit(_candidate()) == "disabled"
        assert not (tmp_path / "raw/candidates").exists()
    asyncio.run(scenario())


def test_shadow_service_persists_without_curating(tmp_path):
    async def scenario():
        store = ControlKnowledgeStore(tmp_path)
        curator = RecordingCurator(store)
        service = ControlKnowledgeService(store, curator, enabled=True, writer=False)
        assert await service.submit(_candidate()) == "persisted"
        assert len(list((tmp_path / "raw/candidates").glob("*.json"))) == 1
        assert curator.calls == 0
    asyncio.run(scenario())


def test_writer_drains_and_retries_transient_curator_error(tmp_path):
    async def scenario():
        store = ControlKnowledgeStore(tmp_path)
        curator = RecordingCurator(store, fail_once=True)
        service = ControlKnowledgeService(store, curator, enabled=True, writer=True,
                                          max_retries=2, retry_base_seconds=0)
        await service.start()
        assert await service.submit(_candidate()) == "queued"
        await service.stop(timeout=2)
        assert curator.calls == 2
        assert store.ledger.exists()
    asyncio.run(scenario())


def test_restart_loads_pending_candidate_once(tmp_path):
    async def scenario():
        store = ControlKnowledgeStore(tmp_path)
        store.save_candidate(_candidate())
        curator = RecordingCurator(store)
        service = ControlKnowledgeService(store, curator, enabled=True, writer=True)
        await service.start()
        await service.stop(timeout=2)
        assert curator.calls == 1
    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests and verify the missing module failure**

Run: `cd 08-YieldAgent && pytest tests/test_control_knowledge_service.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'control_knowledge_service'`.

- [ ] **Step 3: Implement the bounded service**

Create `control_knowledge_service.py`:

```python
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from common import get_llm
from control_knowledge_curator import ControlKnowledgeCurator, CuratorCallError
from control_knowledge_models import KnowledgeCandidate
from control_knowledge_store import ControlKnowledgeStore

logger = logging.getLogger("yield_agent.control_knowledge")


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


class ControlKnowledgeService:
    def __init__(self, store: ControlKnowledgeStore, curator: ControlKnowledgeCurator,
                 *, enabled: bool, writer: bool, queue_size: int = 100,
                 max_retries: int = 3, retry_base_seconds: float = 1.0):
        self.store = store
        self.curator = curator
        self.enabled = enabled
        self.writer = writer
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self.queue: asyncio.Queue[tuple[Path, int]] = asyncio.Queue(maxsize=queue_size)
        self.worker_task: asyncio.Task | None = None
        self.queued_fingerprints: set[str] = set()
        self.stats = {"persisted": 0, "queued": 0, "processed": 0,
                      "retried": 0, "dropped": 0, "failed": 0}

    async def start(self) -> None:
        if not self.enabled or not self.writer or self.worker_task is not None:
            return
        self.worker_task = asyncio.create_task(self._run(), name="control_knowledge_curator")
        for path in self.store.pending_candidates():
            try:
                self.queue.put_nowait((path, 0))
                self.queued_fingerprints.add(path.stem)
            except asyncio.QueueFull:
                break

    async def submit(self, candidate: KnowledgeCandidate) -> str:
        if not self.enabled:
            return "disabled"
        path = await asyncio.to_thread(self.store.save_candidate, candidate)
        self.stats["persisted"] += 1
        if await asyncio.to_thread(self.store.is_processed, path.stem):
            return "processed"
        if path.stem in self.queued_fingerprints:
            return "queued"
        if not self.writer:
            return "persisted"
        try:
            self.queue.put_nowait((path, 0))
            self.queued_fingerprints.add(path.stem)
            self.stats["queued"] += 1
            return "queued"
        except asyncio.QueueFull:
            self.stats["dropped"] += 1
            logger.warning("control knowledge queue full; candidate remains pending: %s", path.name)
            return "pending"

    async def _run(self) -> None:
        while True:
            path, attempt = await self.queue.get()
            requeued = False
            try:
                candidate = await asyncio.to_thread(self.store.load_candidate, path)
                await asyncio.to_thread(self.curator.curate, candidate)
                self.stats["processed"] += 1
            except CuratorCallError as exc:
                if attempt + 1 < self.max_retries:
                    self.stats["retried"] += 1
                    await asyncio.sleep(self.retry_base_seconds * (2 ** attempt))
                    await self.queue.put((path, attempt + 1))
                    requeued = True
                else:
                    self.stats["failed"] += 1
                    logger.warning("control curator unavailable after retries: %s", type(exc).__name__)
            except Exception as exc:
                self.stats["failed"] += 1
                logger.warning("control candidate failed: %s", type(exc).__name__)
            finally:
                if not requeued:
                    self.queued_fingerprints.discard(path.stem)
                self.queue.task_done()

    async def stop(self, timeout: float = 10.0) -> None:
        if self.worker_task is None:
            return
        try:
            await asyncio.wait_for(self.queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("control knowledge drain timeout; pending files remain on disk")
        self.worker_task.cancel()
        try:
            await self.worker_task
        except asyncio.CancelledError:
            pass
        self.worker_task = None


def service_from_env() -> ControlKnowledgeService:
    root = Path(os.getenv(
        "CONTROL_KNOWLEDGE_ROOT",
        str(Path(__file__).resolve().parent / "multiagent_knowledge"),
    ))
    store = ControlKnowledgeStore(root)
    model = os.getenv("CONTROL_KNOWLEDGE_MODEL") or None
    curator = ControlKnowledgeCurator(store, get_llm(model))
    return ControlKnowledgeService(
        store, curator,
        enabled=_enabled("CONTROL_KNOWLEDGE_ENABLED"),
        writer=_enabled("CONTROL_KNOWLEDGE_WRITER"),
        queue_size=int(os.getenv("CONTROL_KNOWLEDGE_QUEUE_SIZE", "100")),
        max_retries=int(os.getenv("CONTROL_KNOWLEDGE_MAX_RETRIES", "3")),
    )
```

Candidate persistence precedes queue insertion, so queue overflow and shutdown leave recoverable pending files.

- [ ] **Step 4: Implement the CLI over the same store/service interfaces**

Create `control_knowledge_cli.py` with an `argparse` subcommand parser:

```python
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from common import get_llm
from control_knowledge_collector import current_system_snapshot, system_snapshot_candidates
from control_knowledge_curator import ControlKnowledgeCurator
from control_knowledge_store import ControlKnowledgeStore
from control_knowledge_validator import scan_bundle


def _root(value: str | None) -> Path:
    return Path(value or os.getenv("CONTROL_KNOWLEDGE_ROOT") or
                Path(__file__).resolve().parent / "multiagent_knowledge").resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("lint")
    sub.add_parser("snapshot")
    sub.add_parser("curate-once")
    approve = sub.add_parser("approve")
    approve.add_argument("proposal_id")
    args = parser.parse_args(argv)
    root = _root(args.root)
    store = ControlKnowledgeStore(root)

    if args.command == "lint":
        issues = scan_bundle(root)
        for issue in issues:
            print(f"[{issue.code}] {issue.path}: {issue.message}")
        return 1 if issues else 0
    if args.command == "snapshot":
        paths = [store.save_candidate(item) for item in system_snapshot_candidates(current_system_snapshot())]
        print(f"saved={len(paths)}")
        return 0
    if args.command == "curate-once":
        curator = ControlKnowledgeCurator(store, get_llm(os.getenv("CONTROL_KNOWLEDGE_MODEL") or None))
        paths = store.pending_candidates()
        for path in paths:
            curator.curate(store.load_candidate(path))
        print(f"processed={len(paths)}")
        return 0
    if args.command == "approve":
        proposal_path = store.review_queue / f"{args.proposal_id}.md"
        if not proposal_path.exists():
            parser.error("proposal does not exist")
        fingerprint = str(__import__("frontmatter").load(proposal_path).metadata["candidate_fingerprint"])
        candidate_path = store.candidates / f"{fingerprint}.json"
        store.approve_proposal(args.proposal_id, store.load_candidate(candidate_path))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

The CLI accepts a proposal ID, not an arbitrary target path.

- [ ] **Step 5: Run service tests and CLI smoke checks**

Run:

```bash
cd 08-YieldAgent
pytest tests/test_control_knowledge_service.py -v
python control_knowledge_cli.py --root multiagent_knowledge lint
python control_knowledge_cli.py --root multiagent_knowledge snapshot
```

Expected: 4 tests PASS, lint exits 0, snapshot prints `saved=12` for the current nine-agent registry plus three shared workflow/contract candidates, and creates only `multiagent_knowledge/raw/candidates/*.json`.

Remove the locally generated candidate JSON before committing Task 6; it is runtime evidence, not seed content.

- [ ] **Step 6: Commit Task 6**

```bash
git add 08-YieldAgent/control_knowledge_service.py \
  08-YieldAgent/control_knowledge_cli.py \
  08-YieldAgent/tests/test_control_knowledge_service.py
git commit -m "feat(knowledge): run bounded curator service"
```

### Task 7: Integrate server lifecycle and agent read routing

**Files:**
- Modify: `08-YieldAgent/agent_server.py`
- Modify: `08-YieldAgent/AGENTS.md`
- Create: `08-YieldAgent/tests/test_control_knowledge_server.py`

**Interfaces:**
- Consumes: `service_from_env()`, `current_system_snapshot()`, candidate builders
- Produces: `app.state.control_knowledge`, non-blocking completed-turn/incident submissions, developer-agent routing rule

- [ ] **Step 1: Write failing server integration tests with a fake service**

```python
# 08-YieldAgent/tests/test_control_knowledge_server.py
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_knowledge_collector import incident_candidate, runtime_candidates

pytestmark = pytest.mark.no_server


def test_completed_state_conversion_never_contains_query_or_rows():
    candidates = runtime_candidates({
        "trace_id": "trace_x", "turn_id": "turn_x", "messages": [],
        "task_plan": [{"task_id": "t1", "agent": "yield_agent",
                       "goal": "4SS private", "params": {"lotcd": "4SS"}}],
        "task_validation_issues": [], "hitl_responses": [],
    })
    dumped = "".join(item.model_dump_json() for item in candidates)
    assert "4SS" not in dumped


def test_incident_conversion_never_contains_exception_message():
    item = incident_candidate(ValueError("private lot 4SS0001"), source="agent_server",
                              trace_id="tr", turn_id="tu", task_id="t1")
    assert "4SS0001" not in item.model_dump_json()
```

Add a focused async test around a new helper in `agent_server.py`:

```python
def test_submit_control_candidates_swallows_service_failure():
    async def scenario():
        class BrokenService:
            async def submit(self, candidate):
                raise RuntimeError("down")
        from agent_server import _submit_control_candidates
        candidate = incident_candidate(ValueError("private"), source="agent_server",
                                       trace_id="tr", turn_id="tu", task_id="t1")
        await _submit_control_candidates(BrokenService(), [candidate])
    asyncio.run(scenario())
```

This import may require the same environment monkeypatches already used by other server-independent tests; reuse existing test fixtures instead of adding production fallbacks.

- [ ] **Step 2: Run the test and verify helper/import failures**

Run: `cd 08-YieldAgent && pytest tests/test_control_knowledge_server.py -v`

Expected: FAIL because `_submit_control_candidates` and server integration do not exist.

- [ ] **Step 3: Add a failure-isolated submission helper**

In `agent_server.py`, add:

```python
async def _submit_control_candidates(service, candidates: list) -> None:
    for candidate in candidates:
        try:
            await service.submit(candidate)
        except Exception as exc:
            logger.warning("[ControlKnowledge] candidate submission failed: %s", type(exc).__name__)
```

Do not log candidate content or exception messages.

- [ ] **Step 4: Integrate service startup and shutdown without touching domain wiki lifecycle**

Inside `lifespan`, create and start the control service independently after the existing domain `wiki_queue` starts:

```python
from control_knowledge_collector import current_system_snapshot, system_snapshot_candidates
from control_knowledge_service import service_from_env

control_knowledge = service_from_env()
await control_knowledge.start()
app.state.control_knowledge = control_knowledge
if control_knowledge.enabled:
    await _submit_control_candidates(
        control_knowledge,
        system_snapshot_candidates(current_system_snapshot()),
    )
```

In the lifespan `finally`, drain candidate-submission tasks, then stop the service separately before closing Mongo:

```python
if _control_knowledge_tasks:
    try:
        await asyncio.wait_for(
            asyncio.gather(*list(_control_knowledge_tasks), return_exceptions=True),
            timeout=10,
        )
    except asyncio.TimeoutError:
        logger.warning("[ControlKnowledge] submission drain timeout")
await control_knowledge.stop(timeout=10)
```

Do not alter existing `wiki_queue.stop()` behavior or variables.

- [ ] **Step 5: Submit runtime and incident candidates after user-visible delivery**

Import `runtime_candidates` and `incident_candidate` lazily or at module level. In `generate()`:

1. Leave `stream_end` emission in its current position.
2. For a completed non-interrupted turn, call `graph.aget_state(config)` once and keep `completed_values` for both user-memory flush and control candidate collection.
3. Schedule `_submit_control_candidates(req.app.state.control_knowledge, runtime_candidates(completed_values))` with `asyncio.create_task`; retain the task in a module-level set and discard it on completion, matching the existing `_memory_tasks` pattern.
4. In the graph exception block, emit `ErrorEvent` first, then schedule one `incident_candidate` using exception class, trace/turn/task/source only.
5. Never await these background submissions before returning SSE.

Use this exact task-retention pattern:

```python
_control_knowledge_tasks: set[asyncio.Task] = set()


def _schedule_control_submission(service, candidates: list) -> None:
    if not candidates:
        return
    task = asyncio.create_task(_submit_control_candidates(service, candidates))
    _control_knowledge_tasks.add(task)
    task.add_done_callback(_control_knowledge_tasks.discard)
```

- [ ] **Step 6: Add the routing rule to `08-YieldAgent/AGENTS.md`**

Append one section:

```markdown
## Multi-Agent Control Knowledge

Before changing orchestration, handoff, HITL, agent inputs, or result contracts:

1. Read `multiagent_knowledge/wiki/index.md`.
2. Follow the relevant local index to the Agent, Workflow, or Contract page.
3. Read linked Decision and Runbook pages.
4. Verify every claim against the implementation; code remains runtime authority.
5. Do not write user, LOT, product, SQL, result rows, prompts, messages, or artifact payloads into this bundle.
6. General agents submit structured candidates or review proposals; they do not directly edit protected canonical pages.
7. Run `python control_knowledge_cli.py --root multiagent_knowledge lint` after a knowledge change.
```

This changes guidance only for multi-agent control-plane work.

- [ ] **Step 7: Run focused integration and existing smoke tests**

Run:

```bash
cd 08-YieldAgent
pytest tests/test_control_knowledge_server.py \
  tests/test_control_knowledge_service.py \
  tests/test_mock_routes.py -v
python control_knowledge_cli.py --root multiagent_knowledge lint
```

Expected: all tests PASS and lint exits 0. No test creates a file under `08-YieldAgent/wiki/`.

- [ ] **Step 8: Commit Task 7**

```bash
git add 08-YieldAgent/agent_server.py 08-YieldAgent/AGENTS.md \
  08-YieldAgent/tests/test_control_knowledge_server.py
git commit -m "feat(knowledge): connect runtime evidence"
```

### Task 8: Verify the complete pipeline and staged rollout

**Files:**
- Create: `08-YieldAgent/tests/test_control_knowledge_e2e.py`
- Create: `08-YieldAgent/tests/verify_control_knowledge_live.py`
- Modify: `08-YieldAgent/multiagent_knowledge/runbooks/operating-curator.md`

**Interfaces:**
- Consumes: real bundle, store, collector, curator, service, live `tests.e2e_client.Session`
- Produces: deterministic local integration test, live environment verifier, exact rollout/rollback runbook

- [ ] **Step 1: Write an integration test that crosses every internal boundary**

```python
# 08-YieldAgent/tests/test_control_knowledge_e2e.py
import asyncio
import json
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_knowledge_collector import build_system_snapshot, system_snapshot_candidates
from control_knowledge_curator import ControlKnowledgeCurator
from control_knowledge_service import ControlKnowledgeService
from control_knowledge_store import ControlKnowledgeStore
from control_knowledge_validator import scan_bundle

pytestmark = pytest.mark.no_server


class FakeWorkflow:
    nodes = {"planner": object(), "wads_agent": object(), "replanner": object()}
    edges = {("__start__", "planner"), ("wads_agent", "replanner")}


class RoutingLLM:
    def invoke(self, messages):
        payload = json.loads(messages[-1]["content"])
        candidate = payload["candidate"]
        subject = candidate["subjects"][0]
        page_type = candidate["suggested_page_type"]
        title = subject.rsplit("/", 1)[-1].replace("-", " ").title()
        decision = {
            "action": "create", "target_page_id": "", "rationale": "new snapshot subject",
            "draft": {"page_id": subject, "page_type": page_type, "title": title,
                      "description": candidate["summary"],
                      "body_markdown": f"# {title}\n\nGenerated from structured snapshot evidence.\n",
                      "relations": {},
                      "evidence_refs": [candidate["evidence_refs"][0]["ref"]]},
        }
        return type("Response", (), {"content": json.dumps(decision)})()


def test_snapshot_to_valid_okf_pages_is_idempotent(tmp_path):
    async def scenario():
        source_bundle = Path(__file__).resolve().parent.parent / "multiagent_knowledge"
        root = tmp_path / "bundle"
        shutil.copytree(source_bundle, root)
        store = ControlKnowledgeStore(root)
        curator = ControlKnowledgeCurator(store, RoutingLLM())
        service = ControlKnowledgeService(store, curator, enabled=True, writer=True,
                                          retry_base_seconds=0)
        snapshot = build_system_snapshot(
            workflow=FakeWorkflow(), agent_slot_rules={"wads_agent": {"allowed": {"lotcd"}}},
            result_schema_version="result-envelope/v1", trace_schema_version="local-trace/v1",
            followup_fields=["agent"], commit_sha="abc123",
        )
        candidates = system_snapshot_candidates(snapshot)

        await service.start()
        for candidate in candidates:
            await service.submit(candidate)
        await service.stop(timeout=3)
        versions = {
            path: __import__("frontmatter").load(path).metadata["version"]
            for path in root.joinpath("wiki").rglob("*.md") if path.name != "index.md"
            and __import__("frontmatter").load(path).metadata.get("page_id") in
            {item.subjects[0] for item in candidates}
        }
        first_call_count = len(curator.llm.calls) if hasattr(curator.llm, "calls") else 0
        assert not scan_bundle(root)

        second = ControlKnowledgeService(store, curator, enabled=True, writer=True,
                                         retry_base_seconds=0)
        await second.start()
        for candidate in candidates:
            assert await second.submit(candidate) == "processed"
        await second.stop(timeout=3)
        assert {
            path: __import__("frontmatter").load(path).metadata["version"]
            for path in versions
        } == versions
        if first_call_count:
            assert len(curator.llm.calls) == first_call_count
    asyncio.run(scenario())
```

- [ ] **Step 2: Run the integration test and fix only pipeline defects**

Run: `cd 08-YieldAgent && pytest tests/test_control_knowledge_e2e.py -v`

Expected: PASS. If the fake LLM exposes a schema/interface mismatch, fix the owning module; do not add response-string special cases.

- [ ] **Step 3: Create the live verifier**

Create `tests/verify_control_knowledge_live.py`:

```python
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

import frontmatter

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from e2e_client import Session, server_is_up
from control_knowledge_validator import scan_bundle


def main() -> int:
    root_value = os.getenv("CONTROL_KNOWLEDGE_ROOT", "").strip()
    if not root_value:
        raise SystemExit("CONTROL_KNOWLEDGE_ROOT must point to the live test bundle")
    root = Path(root_value).resolve()
    if not server_is_up():
        raise SystemExit("live agent server is not reachable")

    before = {path: path.read_bytes() for path in
              (Path(__file__).resolve().parent.parent / "wiki").rglob("*") if path.is_file()}
    result = Session().turn("4SS 최근 3주 수율을 실제 데이터로 보여줘", timeout=300)
    if not any(event.get("type") == "stream_end" for event in result.sse_events):
        raise SystemExit("live turn did not reach stream_end")

    deadline = time.time() + 60
    while time.time() < deadline:
        candidates = list((root / "raw/candidates").glob("*.json"))
        ledger = root / "raw/curation-ledger.jsonl"
        if candidates and ledger.exists() and ledger.stat().st_size:
            break
        time.sleep(1)
    else:
        raise SystemExit("control candidate/ledger was not produced")

    forbidden_keys = {"rows", "query", "messages", "artifact_payload", "data", "html",
                      "base64", "bytes", "content", "prompt", "sql"}
    def assert_redacted(value, path_name):
        if isinstance(value, dict):
            bad = forbidden_keys & {str(key).lower() for key in value}
            if bad:
                raise SystemExit(f"forbidden payload keys {sorted(bad)} in {path_name}")
            for item in value.values():
                assert_redacted(item, path_name)
        elif isinstance(value, list):
            for item in value:
                assert_redacted(item, path_name)
        elif isinstance(value, str) and "4SS" in value:
            raise SystemExit(f"controlled domain sentinel leaked into {path_name}")
    for path in candidates:
        assert_redacted(json.loads(path.read_text(encoding="utf-8")), path.name)
    issues = scan_bundle(root)
    if issues:
        raise SystemExit("bundle lint failed: " + json.dumps([i.__dict__ for i in issues], ensure_ascii=False))
    if any("4SS" in path.read_text(encoding="utf-8") for path in (root / "wiki").rglob("*.md")):
        raise SystemExit("controlled domain sentinel leaked into compiled control wiki")
    ledger_entries = [json.loads(line) for line in
                      (root / "raw/curation-ledger.jsonl").read_text(encoding="utf-8").splitlines()
                      if line.strip()]
    fingerprints = [entry["fingerprint"] for entry in ledger_entries]
    if len(fingerprints) != len(set(fingerprints)):
        raise SystemExit("a candidate fingerprint was processed more than once")
    version_state = root / "raw/live-verifier-state.json"
    current_versions = {
        str(post.metadata["page_id"]): int(post.metadata["version"])
        for path in (root / "wiki").rglob("*.md") if path.name != "index.md"
        for post in [frontmatter.load(path)]
        if post.metadata.get("source_status") == "code-backed"
    }
    if version_state.exists():
        previous_versions = json.loads(version_state.read_text(encoding="utf-8"))
        for page_id in set(previous_versions) & set(current_versions):
            if previous_versions[page_id] != current_versions[page_id]:
                raise SystemExit(f"unchanged code-backed page version advanced: {page_id}")
    version_state.write_text(json.dumps(current_versions, sort_keys=True, indent=2) + "\n",
                             encoding="utf-8")
    after = {path: path.read_bytes() for path in
             (Path(__file__).resolve().parent.parent / "wiki").rglob("*") if path.is_file()}
    if before != after:
        raise SystemExit("existing domain wiki changed during control-knowledge run")
    print(f"PASS candidates={len(candidates)} trace={result.trace_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

The verifier uses `4SS` only as a leak sentinel for this controlled E2E input; production collection logic must not contain that token or any product-format rule.

- [ ] **Step 4: Update the curator runbook with exact rollout and rollback commands**

Add these commands to `multiagent_knowledge/wiki/runbooks/operating-curator.md`:

```bash
# Stage 1: lint only
python control_knowledge_cli.py --root multiagent_knowledge lint

# Stage 2: shadow candidate collection
CONTROL_KNOWLEDGE_ENABLED=true
CONTROL_KNOWLEDGE_WRITER=false

# Stage 3: single writer with real curator
CONTROL_KNOWLEDGE_ENABLED=true
CONTROL_KNOWLEDGE_WRITER=true

# Immediate rollback: stop all collection/writes without changing analysis flow
CONTROL_KNOWLEDGE_ENABLED=false
CONTROL_KNOWLEDGE_WRITER=false
```

Also document how to inspect `raw/candidates`, `raw/curation-ledger.jsonl`, `wiki/log.md`, and `wiki/review_queue`, and how to run `approve <proposal_id>`. State that only one server process may set `CONTROL_KNOWLEDGE_WRITER=true`.

- [ ] **Step 5: Run the complete automated suite**

Run:

```bash
cd 08-YieldAgent
pytest tests/test_control_knowledge_models.py \
  tests/test_control_knowledge_validator.py \
  tests/test_control_knowledge_store.py \
  tests/test_control_knowledge_collector.py \
  tests/test_control_knowledge_curator.py \
  tests/test_control_knowledge_service.py \
  tests/test_control_knowledge_server.py \
  tests/test_control_knowledge_e2e.py -v
python control_knowledge_cli.py --root multiagent_knowledge lint
git diff --exit-code -- wiki
```

Expected: all control-knowledge tests PASS, lint exits 0, and the existing domain wiki diff is empty.

- [ ] **Step 6: Run the required live E2E in a copied bundle**

Create a recoverable test copy outside the repository, start the real server with actual configured MongoDB/Oracle/OpenSearch/LLM, wait for health, and run the verifier in one shell so the exact temporary path is shared:

```bash
CONTROL_E2E_ROOT=$(mktemp -d)/multiagent_knowledge
cp -R multiagent_knowledge "$CONTROL_E2E_ROOT"
CONTROL_KNOWLEDGE_ROOT="$CONTROL_E2E_ROOT" \
CONTROL_KNOWLEDGE_ENABLED=true \
CONTROL_KNOWLEDGE_WRITER=true \
uvicorn agent_server:app --port 8001 > /tmp/yield-control-knowledge-e2e.log 2>&1 &
CONTROL_E2E_SERVER_PID=$!
for attempt in {1..30}; do
  python -c 'from tests.e2e_client import server_is_up; raise SystemExit(0 if server_is_up() else 1)' && break
  sleep 1
done
CONTROL_KNOWLEDGE_ROOT="$CONTROL_E2E_ROOT" python tests/verify_control_knowledge_live.py
kill "$CONTROL_E2E_SERVER_PID"
wait "$CONTROL_E2E_SERVER_PID" || true
printf 'Retained test bundle: %s\n' "$CONTROL_E2E_ROOT"
```

Expected: a real `yield_agent` tool path and curator LLM call complete, verifier prints a positive candidate count and a trace ID, bundle lint passes, and `08-YieldAgent/wiki/` remains byte-for-byte unchanged. The command stops the test server normally so the service drains and prints the retained copied-bundle path for review.

- [ ] **Step 7: Restart and verify pending recovery/idempotency live**

With the same `CONTROL_E2E_ROOT` shell variable and copied bundle, run:

```bash
CONTROL_KNOWLEDGE_ROOT="$CONTROL_E2E_ROOT" \
CONTROL_KNOWLEDGE_ENABLED=true \
CONTROL_KNOWLEDGE_WRITER=true \
uvicorn agent_server:app --port 8001 > /tmp/yield-control-knowledge-e2e-restart.log 2>&1 &
CONTROL_E2E_SERVER_PID=$!
for attempt in {1..30}; do
  python -c 'from tests.e2e_client import server_is_up; raise SystemExit(0 if server_is_up() else 1)' && break
  sleep 1
done
CONTROL_KNOWLEDGE_ROOT="$CONTROL_E2E_ROOT" python tests/verify_control_knowledge_live.py
kill "$CONTROL_E2E_SERVER_PID"
wait "$CONTROL_E2E_SERVER_PID" || true
```

Expected: previously processed candidate fingerprints are not applied again, unchanged page versions remain stable, and new turn evidence produces either `no_change` or a grounded Observation update.

- [ ] **Step 8: Commit Task 8**

```bash
git add 08-YieldAgent/tests/test_control_knowledge_e2e.py \
  08-YieldAgent/tests/verify_control_knowledge_live.py \
  08-YieldAgent/multiagent_knowledge/wiki/runbooks/operating-curator.md
git commit -m "test(knowledge): verify live curation pipeline"
```

---

## Final acceptance checklist

- [ ] `08-YieldAgent/wiki/` and all existing domain wiki modules have no diff.
- [ ] Root and nested index rules pass the new validator.
- [ ] Code snapshot produces stable Agent/Workflow/Contract subjects.
- [ ] Runtime candidate contains counts and contract identities but no domain/user payload.
- [ ] Duplicate evidence does not increase page version.
- [ ] Protected page changes create proposals only.
- [ ] Curator outage leaves candidate evidence pending and does not change SSE success.
- [ ] One live run exercises real planner, worker/tool, MongoDB, LLM curator, and filesystem writer.
- [ ] Server restart processes pending evidence exactly once.
- [ ] `CONTROL_KNOWLEDGE_ENABLED=false` restores the pre-feature runtime path without code rollback.

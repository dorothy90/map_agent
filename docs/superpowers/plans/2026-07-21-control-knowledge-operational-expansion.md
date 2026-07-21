# Control Knowledge Operational Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the OKF control Wiki into code-verified operational documentation for all nine agents, shared workflows, contracts, tools, external systems, HITL boundaries, and failure modes.

**Architecture:** A central typed registry supplies authored control-plane descriptions, while deterministic validators cross-check graph nodes, canonical slots, source modules, tool modules, artifact state fields, result kinds, HITL identifiers, and evidence references against executable code. Enriched snapshots become candidates; valid Agent/Workflow/Contract pages update automatically, registry drift becomes an Observation, and protected operational guidance remains proposal-only.

**Tech Stack:** Python 3.11, Pydantic v2, LangGraph, python-frontmatter, pytest, existing ResultEnvelope and control-knowledge services

## Global Constraints

- `08-YieldAgent/wiki/` and all domain Wiki modules must remain byte-for-byte unchanged.
- Canonical control pages contain only facts backed by registry source references and verified implementation symbols.
- User messages, LOT/product values, SQL, result rows, prompts, HTML, and artifact payloads are forbidden.
- No keyword, regex, phrase-list, or natural-language special case may repair planner or curator semantics.
- Registry drift blocks the affected Agent page update and produces an Observation candidate.
- Runbook, Decision, Policy, and governance changes remain proposal-only.
- Only one server process may set `CONTROL_KNOWLEDGE_WRITER=true`.
- Every task follows RED → GREEN → regression verification → commit.
- Test commands use `uv run python -m pytest`, never the global `pytest` executable.
- Run every `uv`, CLI, server, `cp`, and `git diff -- ... wiki` command from `08-YieldAgent/`. Run every `git add` and `git commit` command from the repository root. Shell blocks are grouped accordingly.

## Locked File Structure

- Create `08-YieldAgent/control_knowledge_registry.py`: typed profiles and deterministic registry validation.
- Modify `08-YieldAgent/models.py`: expose canonical structured HITL contract IDs.
- Modify `08-YieldAgent/control_knowledge_models.py`: snapshot-v2 profile and drift fields.
- Modify `08-YieldAgent/control_knowledge_collector.py`: enriched snapshot and drift candidates.
- Modify `08-YieldAgent/control_knowledge_curator.py`: operational Agent page shape validation.
- Modify `08-YieldAgent/control_knowledge_cli.py`: use validated current candidate collection.
- Modify `08-YieldAgent/agent_server.py`: submit validated startup candidates and drift observations.
- Modify `08-YieldAgent/tests/verify_control_knowledge_live.py`: require detailed compiled pages.
- Modify `08-YieldAgent/multiagent_knowledge/wiki/agents/*.md`: regenerate nine detailed pages.
- Modify `08-YieldAgent/multiagent_knowledge/wiki/contracts/*.md`: expand shared contracts.
- Modify `08-YieldAgent/multiagent_knowledge/wiki/workflows/orchestration-graph.md`: expand workflow responsibilities and state flow.
- Create `08-YieldAgent/tests/test_control_knowledge_registry.py`.
- Modify existing control-knowledge tests listed in the tasks below.

---

### Task 1: Define and validate the typed Agent registry

**Files:**
- Create: `08-YieldAgent/control_knowledge_registry.py`
- Modify: `08-YieldAgent/models.py`
- Create: `08-YieldAgent/tests/test_control_knowledge_registry.py`

**Interfaces:**
- Consumes: `AGENT_SLOT_RULES`, `YieldQueryState.__annotations__`, LangGraph `workflow.nodes`, repository module paths, `ResultKind`
- Produces: `AgentControlProfile`, `FailureMode`, `RegistryIssue`, `AGENT_CONTROL_PROFILES`, `validate_agent_registry(...)`

- [ ] **Step 1: Write failing registry contract tests**

```python
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from canonical_request import AGENT_SLOT_RULES
from control_knowledge_registry import (
    AGENT_CONTROL_PROFILES,
    AgentControlProfile,
    validate_agent_registry,
)
from models import HITL_CONTRACT_IDS
from query_state import YieldQueryState
from result_contracts import ResultKind
from supervisor import workflow

pytestmark = pytest.mark.no_server


def _issues(profiles=AGENT_CONTROL_PROFILES):
    return validate_agent_registry(
        profiles=profiles,
        agent_slot_rules=AGENT_SLOT_RULES,
        graph_nodes=set(workflow.nodes),
        state_fields=set(YieldQueryState.__annotations__),
        result_kinds={item.value for item in ResultKind},
        hitl_contract_ids=set(HITL_CONTRACT_IDS),
        module_root=Path(__file__).resolve().parent.parent,
    )


def test_registry_covers_exact_canonical_agents():
    assert set(AGENT_CONTROL_PROFILES) == set(AGENT_SLOT_RULES)
    assert not _issues()


def test_registry_profiles_have_operational_evidence():
    for agent_id, profile in AGENT_CONTROL_PROFILES.items():
        assert profile.agent_id == agent_id
        assert profile.responsibility
        assert profile.boundaries
        assert profile.source_refs
        assert profile.output_contracts
        assert profile.hitl_contracts
        assert profile.failure_modes


def test_slot_drift_is_reported_for_only_affected_agent():
    changed = dict(AGENT_CONTROL_PROFILES)
    profile = changed["yield_agent"]
    changed["yield_agent"] = profile.model_copy(
        update={"optional_slots": [*profile.optional_slots, "invented_slot"]}
    )
    issues = _issues(changed)
    assert [(issue.agent_id, issue.code) for issue in issues] == [
        ("yield_agent", "slot_mismatch")
    ]


def test_missing_profile_is_reported_as_registry_drift():
    changed = dict(AGENT_CONTROL_PROFILES)
    changed.pop("map_agent")
    issues = _issues(changed)
    assert ("map_agent", "missing_profile") in {
        (issue.agent_id, issue.code) for issue in issues
    }


def test_missing_tool_and_artifact_are_reported(tmp_path):
    profile = AgentControlProfile(
        agent_id="yield_agent",
        title="Yield Agent",
        responsibility="Yield analysis",
        boundaries=["Does not own WADS retrieval"],
        source_module="yield_query_agent",
        required_slots=["lotcd"],
        optional_slots=["periods", "ref_date", "time_range", "unit"],
        required_any_slots=[],
        output_contracts=["contracts/result-envelope"],
        result_kinds=["table", "summary"],
        artifact_channels=["missing_artifacts"],
        tool_modules=["missing_tool"],
        external_systems=["Oracle", "LLM"],
        hitl_contracts=["missing_param", "plan_review"],
        failure_modes=[{
            "code": "upstream_unavailable",
            "effect": "Returns an error envelope without changing other agents",
            "source_refs": ["yield_query_agent.py:216"],
        }],
        source_refs=["yield_query_agent.py", "canonical_request.py:6"],
    )
    issues = validate_agent_registry(
        profiles={"yield_agent": profile},
        agent_slot_rules={"yield_agent": AGENT_SLOT_RULES["yield_agent"]},
        graph_nodes={"yield_agent"},
        state_fields=set(YieldQueryState.__annotations__),
        result_kinds={item.value for item in ResultKind},
        hitl_contract_ids=set(HITL_CONTRACT_IDS),
        module_root=tmp_path,
    )
    assert {issue.code for issue in issues} == {
        "missing_artifact_channel",
        "missing_source_module",
        "missing_source_ref",
        "missing_tool_module",
    }


def test_out_of_range_evidence_line_is_reported():
    changed = dict(AGENT_CONTROL_PROFILES)
    profile = changed["yield_agent"]
    failure = profile.failure_modes[0].model_copy(
        update={"source_refs": ["yield_query_agent.py:999999"]}
    )
    changed["yield_agent"] = profile.model_copy(
        update={"failure_modes": [failure, *profile.failure_modes[1:]]}
    )
    issues = _issues(changed)
    assert ("yield_agent", "invalid_source_line") in {
        (issue.agent_id, issue.code) for issue in issues
    }
```

- [ ] **Step 2: Run the registry tests and verify RED**

Run:

```bash
cd 08-YieldAgent
uv run python -m pytest tests/test_control_knowledge_registry.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'control_knowledge_registry'`.

- [ ] **Step 3: Implement strict registry models and validation**

Create these public types and validation behavior:

```python
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FailureMode(BaseModel):
    model_config = STRICT
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    effect: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)


class AgentControlProfile(BaseModel):
    model_config = STRICT
    agent_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    responsibility: str = Field(min_length=1)
    boundaries: list[str] = Field(min_length=1)
    source_module: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    required_slots: list[str]
    optional_slots: list[str]
    required_any_slots: list[list[str]]
    output_contracts: list[str] = Field(min_length=1)
    result_kinds: list[str]
    artifact_channels: list[str]
    tool_modules: list[str]
    external_systems: list[str]
    hitl_contracts: list[str] = Field(min_length=1)
    failure_modes: list[FailureMode] = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def slots_do_not_overlap(self):
        if set(self.required_slots) & set(self.optional_slots):
            raise ValueError("required_slots and optional_slots must not overlap")
        declared = set(self.required_slots) | set(self.optional_slots)
        if any(not group or not set(group) <= declared for group in self.required_any_slots):
            raise ValueError("required_any_slots must be non-empty subsets of declared slots")
        return self


class RegistryIssue(BaseModel):
    model_config = STRICT
    agent_id: str
    code: str
    source_ref: str = ""


def validate_agent_registry(
    *,
    profiles: Mapping[str, AgentControlProfile],
    agent_slot_rules: Mapping[str, dict],
    graph_nodes: set[str],
    state_fields: set[str],
    result_kinds: set[str],
    hitl_contract_ids: set[str],
    module_root: Path,
) -> list[RegistryIssue]:
    issues: list[RegistryIssue] = []
    for agent_id in sorted(set(agent_slot_rules) - set(profiles)):
        issues.append(RegistryIssue(agent_id=agent_id, code="missing_profile"))
    for agent_id in sorted(set(profiles) - set(agent_slot_rules)):
        issues.append(RegistryIssue(agent_id=agent_id, code="unknown_profile"))
    for agent_id, profile in sorted(profiles.items()):
        rules = agent_slot_rules.get(agent_id) or {}
        allowed = set(rules.get("allowed") or [])
        required = set(rules.get("required") or [])
        required_any = sorted(sorted(group) for group in (rules.get("required_any") or []))
        if (
            set(profile.required_slots) != required
            or set(profile.optional_slots) != allowed - required
            or sorted(sorted(group) for group in profile.required_any_slots) != required_any
        ):
            issues.append(RegistryIssue(agent_id=agent_id, code="slot_mismatch"))
        if agent_id not in graph_nodes:
            issues.append(RegistryIssue(agent_id=agent_id, code="missing_graph_node"))
        if not (module_root / f"{profile.source_module}.py").is_file():
            issues.append(RegistryIssue(agent_id=agent_id, code="missing_source_module"))
        for module in profile.tool_modules:
            if not (module_root / f"{module}.py").is_file():
                issues.append(RegistryIssue(
                    agent_id=agent_id, code="missing_tool_module", source_ref=module
                ))
        for field in profile.artifact_channels:
            if field not in state_fields:
                issues.append(RegistryIssue(
                    agent_id=agent_id, code="missing_artifact_channel", source_ref=field
                ))
        for kind in profile.result_kinds:
            if kind not in result_kinds:
                issues.append(RegistryIssue(
                    agent_id=agent_id, code="invalid_result_kind", source_ref=kind
                ))
        for hitl in profile.hitl_contracts:
            if hitl not in hitl_contract_ids:
                issues.append(RegistryIssue(
                    agent_id=agent_id, code="invalid_hitl_contract", source_ref=hitl
                ))
        for ref in [*profile.source_refs, *(
            item for failure in profile.failure_modes for item in failure.source_refs
        )]:
            file_name, separator, line_text = ref.rpartition(":")
            if not separator or not line_text.isdigit():
                file_name = ref
                line_text = ""
            source_path = module_root / file_name
            if not source_path.is_file():
                issues.append(RegistryIssue(
                    agent_id=agent_id, code="missing_source_ref", source_ref=ref
                ))
            elif line_text and not 1 <= int(line_text) <= len(
                source_path.read_text(encoding="utf-8").splitlines()
            ):
                issues.append(RegistryIssue(
                    agent_id=agent_id, code="invalid_source_line", source_ref=ref
                ))
    return sorted(issues, key=lambda item: (item.agent_id, item.code, item.source_ref))
```

Add the canonical structured HITL identifiers to `models.py`:

```python
HITL_CONTRACT_IDS = frozenset({
    "missing_param", "plan_review", "task_confirm", "postwads_choice",
})
```

Define `AGENT_CONTROL_PROFILES` for the exact nine IDs below. Required/optional slots and machine fields must use this table; prose must cite the listed source files.

| agent_id | source module | tools | artifact channel | output contracts | result kinds | external systems |
|---|---|---|---|---|---|---|
| yield_agent | yield_query_agent | yield_db, yield_viz | yield_artifacts | contracts/result-envelope, contracts/artifact-delivery | table, summary | Oracle, LLM |
| wads_agent | wads_agent | wads_tools | wads_artifacts | contracts/result-envelope, contracts/artifact-delivery | table, report, summary | Oracle, LLM |
| map_agent | map_agent | none | map_artifacts | contracts/result-envelope, contracts/artifact-delivery | image, summary | Oracle |
| fail_history_agent | fail_history_agent | fail_history_tools | fail_history_artifacts | contracts/result-envelope, contracts/artifact-delivery | document, summary | OpenSearch |
| lot_history_agent | lot_history_agent | lot_history_tools | lot_history_artifacts | contracts/result-envelope, contracts/artifact-delivery | table, summary | Oracle |
| relation_tree_agent | relation_tree_agent | none | relation_tree_artifacts | contracts/result-envelope, contracts/artifact-delivery | report | Oracle |
| mining_agent | mining_agent | none | mining_artifacts | contracts/result-envelope, contracts/artifact-delivery | table, summary | Mining API, LLM |
| wt_resp_agent | wt_resp_agent | none | none | contracts/result-envelope | report | Oracle |
| ppt_export | ppt_export_agent | none | ppt_artifacts | contracts/artifact-delivery | none | Local filesystem |

Each profile has at least one verified failure mode whose source reference is an existing line-bearing file reference. Use `AGENT_SLOT_RULES` as the exact slot source: `required` becomes `required_slots`, `allowed - required` becomes `optional_slots`, and `required_any` becomes `required_any_slots`. This preserves `map_agent`'s conditional requirement that at least one of `lot_ids` or `groupkey` be present.

- [ ] **Step 4: Run registry and existing contract tests**

Run:

```bash
uv run python -m pytest tests/test_control_knowledge_registry.py tests/test_control_knowledge_models.py -v
```

Expected: all tests pass and all nine profiles validate without issues.

- [ ] **Step 5: Commit Task 1**

```bash
git add 08-YieldAgent/control_knowledge_registry.py 08-YieldAgent/models.py \
  08-YieldAgent/tests/test_control_knowledge_registry.py
git commit -m "feat(knowledge): define agent control registry"
```

---

### Task 2: Build enriched snapshots and drift candidates

**Files:**
- Modify: `08-YieldAgent/control_knowledge_models.py`
- Modify: `08-YieldAgent/control_knowledge_collector.py`
- Modify: `08-YieldAgent/tests/test_control_knowledge_collector.py`
- Create: `08-YieldAgent/multiagent_knowledge/wiki/contracts/artifact-delivery.md`
- Create: `08-YieldAgent/multiagent_knowledge/wiki/contracts/hitl-contracts.md`

**Interfaces:**
- Consumes: `AGENT_CONTROL_PROFILES`, `validate_agent_registry()`, graph topology, result/state contracts
- Produces: `SystemSnapshot.agent_profiles`, `SystemCollection`, `collect_current_system()`, `registry_drift_candidates()`

- [ ] **Step 1: Add failing enriched-snapshot and drift tests**

First update the existing `test_snapshot_is_sorted_and_split_into_stable_subjects()` call with `agent_profiles`, `result_fields`, `artifact_fields`, and `hitl_contracts` using the same values as the new test below. Extend its expected subjects with `contracts/artifact-delivery` and `contracts/hitl-contracts`, so the one-Agent fixture expects six subjects total. Then append tests that assert:

```python
from control_knowledge_registry import AGENT_CONTROL_PROFILES, RegistryIssue


def test_snapshot_candidate_contains_operational_profile_and_graph_position():
    snapshot = build_system_snapshot(
        workflow=FakeWorkflow(),
        agent_slot_rules={"wads_agent": {"allowed": {"fail_type", "lotcd"}}},
        agent_profiles={"wads_agent": AGENT_CONTROL_PROFILES["wads_agent"].model_copy(
            update={"required_slots": [], "optional_slots": ["fail_type", "lotcd"]}
        )},
        result_schema_version="result-envelope/v1",
        result_fields=["schema_version", "source_agent", "kind", "status"],
        artifact_fields=["wads_artifacts"],
        hitl_contracts=["missing_param", "plan_review"],
        trace_schema_version="local-trace/v1",
        followup_fields=["goal", "agent"],
        commit_sha="abc123",
    )
    candidate = next(
        item for item in system_snapshot_candidates(snapshot)
        if item.subjects == ["agents/wads-agent"]
    )
    facts = {fact.name: fact.value for fact in candidate.facts}
    assert facts["profile"]["responsibility"]
    assert facts["profile"]["tool_modules"] == ["wads_tools"]
    assert facts["workflow_position"] == {
        "predecessors": [],
        "successors": ["replanner"],
    }
    assert facts["related_pages"] == [
        "contracts/artifact-delivery",
        "contracts/hitl-contracts",
        "contracts/result-envelope",
        "workflows/orchestration-graph",
    ]


def test_drift_candidate_blocks_affected_agent_snapshot():
    snapshot = build_system_snapshot(
        workflow=FakeWorkflow(),
        agent_slot_rules={"wads_agent": {"allowed": {"fail_type", "lotcd"}}},
        agent_profiles={"wads_agent": AGENT_CONTROL_PROFILES["wads_agent"].model_copy(
            update={"required_slots": [], "optional_slots": ["fail_type", "lotcd"]}
        )},
        result_schema_version="result-envelope/v1",
        result_fields=["schema_version"],
        artifact_fields=["wads_artifacts"],
        hitl_contracts=["missing_param", "plan_review"],
        trace_schema_version="local-trace/v1",
        followup_fields=["agent"],
        commit_sha="abc123",
    )
    issues = [RegistryIssue(agent_id="wads_agent", code="slot_mismatch")]
    collection = system_collection(snapshot, issues)
    assert "agents/wads-agent" not in {
        item.subjects[0] for item in collection.candidates
    }
    drift = [item for item in collection.candidates if item.source_kind == "registry_drift"]
    assert drift[0].subjects == ["observations/registry-drift-wads-agent"]
    assert "slot_mismatch" in drift[0].model_dump_json()
```

- [ ] **Step 2: Run the collector test and verify RED**

Run:

```bash
uv run python -m pytest tests/test_control_knowledge_collector.py -v
```

Expected: failures report missing `agent_profiles`, `result_fields`, `SystemCollection`, and `registry_drift` support.

- [ ] **Step 3: Extend strict models**

Make these exact model changes. Modify only `KnowledgeCandidate.source_kind` on the existing model so its `Literal` also accepts `"registry_drift"`; retain every other existing field unchanged.

```python
class SystemSnapshot(BaseModel):
    model_config = STRICT
    schema_version: Literal["control-system-snapshot/v2"] = "control-system-snapshot/v2"
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
```

- [ ] **Step 4: Enrich collection and block drifted agents**

Update `build_system_snapshot()` to accept `agent_profiles`, `result_fields`, `artifact_fields`, and `hitl_contracts`, serialize profiles with `model_dump(mode="json")`, and include every new field in the stable snapshot hash. Add these helpers:

```python
def _workflow_position(snapshot: SystemSnapshot, agent: str) -> dict[str, list[str]]:
    return {
        "predecessors": sorted(source for source, target in snapshot.graph_edges if target == agent),
        "successors": sorted(target for source, target in snapshot.graph_edges if source == agent),
    }


def registry_drift_candidates(issues: list[RegistryIssue]) -> list[KnowledgeCandidate]:
    grouped: dict[str, list[RegistryIssue]] = {}
    for issue in issues:
        grouped.setdefault(issue.agent_id, []).append(issue)
    return [
        KnowledgeCandidate(
            source_kind="registry_drift",
            subjects=[f"observations/registry-drift-{agent.replace('_', '-')}"],
            suggested_page_type="Observation",
            summary=f"Registry verification drift for {agent}",
            facts=[CandidateFact(
                name="registry_issues",
                value=[item.model_dump(mode="json") for item in sorted(
                    agent_issues, key=lambda value: (value.code, value.source_ref)
                )],
                source_path="control_knowledge_registry.validate_agent_registry",
            )],
            evidence_refs=[_evidence(
                "snapshot", f"registry_drift_{agent}",
                [item.model_dump(mode="json") for item in agent_issues],
            )],
        )
        for agent, agent_issues in sorted(grouped.items())
    ]


def system_collection(
    snapshot: SystemSnapshot, issues: list[RegistryIssue]
) -> SystemCollection:
    blocked = {issue.agent_id for issue in issues}
    candidates = [
        item for item in system_snapshot_candidates(snapshot)
        if not (
            item.subjects[0].startswith("agents/")
            and item.subjects[0].removeprefix("agents/").replace("-", "_") in blocked
        )
    ]
    candidates.extend(registry_drift_candidates(issues))
    return SystemCollection(
        snapshot=snapshot,
        registry_issues=[issue.model_dump(mode="json") for issue in issues],
        candidates=candidates,
    )
```

Each Agent candidate must carry facts named `profile`, `slot_contract`, `workflow_position`, and `related_pages`. Produce four shared Contract candidates: `result-envelope` with `schema_fields`, `local-trace` with follow-up/event fields, `artifact-delivery` with `artifact_fields`, and `hitl-contracts` with `hitl_contracts`. The Workflow candidate retains complete nodes, edges, and follow-up fields. A clean collection therefore contains 14 candidates: nine Agent, four Contract, and one Workflow.

Create governance-valid seed pages for `artifact-delivery` and `hitl-contracts` before any enriched Agent candidate can reference them. Match the existing Contract frontmatter exactly: `type: Contract`, path-matching `page_id`, title/description/routing summary, `status: current`, `owner: yield-platform`, `source_status: code-backed`, `agent_use: read-and-propose`, the four matching `llmwiki_*` compatibility fields, `sensitivity: internal`, `last_reviewed: '2026-07-21'`, `review_cycle: P90D`, `version: 1`, `relations: {}`, and stable `code:` evidence references. Use these initial bodies:

```markdown
# Artifact Delivery

Defines the state channels through which agents expose generated artifacts without embedding artifact payloads in the control Wiki.
```

```markdown
# HITL Contracts

Defines the structured interrupt identifiers shared by the supervisor, API, and resume path.
```

Use `code:query_state.YieldQueryState` for artifact evidence and `code:models.HITL_CONTRACT_IDS` for HITL evidence. Add both links to the existing explicit `wiki/contracts/index.md`.

Add `collect_current_system()` that derives `result_fields` from `ResultEnvelopeV1.model_fields`, `artifact_fields` from the union of registry artifact channels, and `hitl_contracts` from `HITL_CONTRACT_IDS`; builds the real snapshot; validates `AGENT_CONTROL_PROFILES`; and returns `system_collection(snapshot, issues)`. Keep `current_system_snapshot()` as a compatibility wrapper returning `.snapshot`.

- [ ] **Step 5: Run collector, model, and payload-leak tests**

Run:

```bash
uv run python -m pytest tests/test_control_knowledge_collector.py \
  tests/test_control_knowledge_models.py \
  tests/test_control_knowledge_server.py -v
```

Expected: all tests pass; serialized candidates contain no forbidden payload keys or controlled domain sentinel.

- [ ] **Step 6: Commit Task 2**

```bash
git add 08-YieldAgent/control_knowledge_models.py \
  08-YieldAgent/control_knowledge_collector.py \
  08-YieldAgent/tests/test_control_knowledge_collector.py \
  08-YieldAgent/multiagent_knowledge/wiki/contracts/artifact-delivery.md \
  08-YieldAgent/multiagent_knowledge/wiki/contracts/hitl-contracts.md
git commit -m "feat(knowledge): collect operational profiles"
```

---

### Task 3: Enforce detailed Agent documents and governed drift writes

**Files:**
- Modify: `08-YieldAgent/control_knowledge_curator.py`
- Modify: `08-YieldAgent/tests/test_control_knowledge_curator.py`
- Modify: `08-YieldAgent/tests/test_control_knowledge_e2e.py`

**Interfaces:**
- Consumes: enriched Agent candidate facts and `registry_drift` candidates
- Produces: `AGENT_REQUIRED_SECTIONS`, `validate_operational_agent_draft()`, expanded write policy

- [ ] **Step 1: Add failing detailed-page tests**

```python
AGENT_SECTIONS = [
    "Responsibility", "Boundaries", "Inputs", "Outputs", "Workflow Position",
    "Tools and External Systems", "HITL Contracts", "Verified Failure Modes",
    "Source Evidence", "Related Knowledge",
]


def _operational_body(title="WADS Agent"):
    return "\n\n".join([
        f"# {title}",
        *[f"## {section}\n\nVerified content." for section in AGENT_SECTIONS],
    ]) + "\n"


def test_operational_agent_draft_requires_every_section(tmp_path):
    payload = _decision()
    payload["draft"]["body_markdown"] = "# WADS Agent\n\n## Inputs\n\nlotcd\n"
    entry = ControlKnowledgeCurator(
        ControlKnowledgeStore(tmp_path), FakeLLM(payload)
    ).curate(_candidate())
    assert entry.action == "invalid_decision"


def test_operational_agent_draft_accepts_all_sections(tmp_path):
    payload = _decision()
    payload["draft"]["body_markdown"] = _operational_body()
    entry = ControlKnowledgeCurator(
        ControlKnowledgeStore(tmp_path), FakeLLM(payload)
    ).curate(_candidate())
    assert entry.action == "created"


def test_operational_agent_draft_requires_registry_relations(tmp_path):
    payload = _decision()
    payload["draft"]["body_markdown"] = _operational_body()
    payload["draft"]["relations"] = {}
    entry = ControlKnowledgeCurator(
        ControlKnowledgeStore(tmp_path), FakeLLM(payload)
    ).curate(_candidate())
    assert entry.action == "invalid_decision"


def test_registry_drift_observation_is_auto_writable():
    assert write_disposition("registry_drift", "Observation") == "auto"
```

Update the local `_decision()` fixture so its default Agent body uses `_operational_body()`.

- [ ] **Step 2: Run curator tests and verify RED**

Run:

```bash
uv run python -m pytest tests/test_control_knowledge_curator.py -v
```

Expected: the incomplete Agent draft is currently accepted and `registry_drift` currently returns `deny`.

- [ ] **Step 3: Add deterministic Agent section validation**

```python
AGENT_REQUIRED_SECTIONS = (
    "Responsibility", "Boundaries", "Inputs", "Outputs", "Workflow Position",
    "Tools and External Systems", "HITL Contracts", "Verified Failure Modes",
    "Source Evidence", "Related Knowledge",
)


def validate_operational_agent_draft(candidate: KnowledgeCandidate, draft: PageDraft) -> None:
    if candidate.source_kind != "system_snapshot" or draft.page_type != PageType.agent:
        return
    headings = {
        line[3:].strip() for line in draft.body_markdown.splitlines()
        if line.startswith("## ")
    }
    missing = set(AGENT_REQUIRED_SECTIONS) - headings
    if missing:
        raise ValueError("operational Agent draft is missing required sections")
    facts = {fact.name: fact.value for fact in candidate.facts}
    related = set(facts.get("related_pages") or [])
    profile = facts["profile"]
    expected_relations = {
        "participates_in": sorted(
            f"[[{page_id}]]" for page_id in related if page_id.startswith("workflows/")
        ),
        "uses_contract": sorted(
            f"[[{page_id}]]" for page_id in profile["output_contracts"]
        ),
        "uses_hitl_contract": ["[[contracts/hitl-contracts]]"],
    }
    actual_relations = {
        key: sorted(values) for key, values in draft.relations.items() if values
    }
    if actual_relations != expected_relations:
        raise ValueError("operational Agent draft relations differ from registry facts")
```

Call this after candidate evidence validation and before `write_disposition()`. Add `("registry_drift", "Observation")` to `AUTO`.

Update the test `_candidate()` fixture to include a minimal `profile` fact with `output_contracts: ["contracts/result-envelope"]` plus a `related_pages` fact containing `contracts/hitl-contracts`, `contracts/result-envelope`, and `workflows/orchestration-graph`. Update the default Agent `_decision()` relations to the corresponding `uses_contract`, `uses_hitl_contract`, and `participates_in` wikilinks.

Expand `CURATOR_SYSTEM` with the exact ten-section Agent outline and the instruction that source evidence must be copied from `profile.source_refs` and `failure_modes[*].source_refs`. For Agent drafts, populate `participates_in` from the Workflow entry, `uses_contract` from `profile.output_contracts`, and `uses_hitl_contract` with `contracts/hitl-contracts`, with no extra relation keys. The actual Pydantic `decision_schema` continues to be supplied in the request payload.

- [ ] **Step 4: Strengthen the internal E2E assertion**

Update the E2E `build_system_snapshot()` call with the same new arguments used by the collector fixture: a `wads_agent` profile copied with `required_slots=[]`, `optional_slots=["lotcd"]`, and `required_any_slots=[]`; `list(ResultEnvelopeV1.model_fields)`; `artifact_fields=["wads_artifacts"]`; and `sorted(HITL_CONTRACT_IDS)`. Update `RoutingLLM` in `test_control_knowledge_e2e.py` to render all ten headings for Agent candidates and derive the three exact Agent relation keys from the candidate facts as specified in Step 3. Non-Agent candidates retain an empty relation map. After processing, load every candidate Agent page and assert:

```python
for path in root.joinpath("wiki/agents").glob("*.md"):
    if path.name == "index.md":
        continue
    headings = {
        line[3:].strip() for line in frontmatter.load(path).content.splitlines()
        if line.startswith("## ")
    }
    assert set(AGENT_SECTIONS).issubset(headings)
```

- [ ] **Step 5: Run curator/store/internal E2E tests**

Run:

```bash
uv run python -m pytest tests/test_control_knowledge_curator.py \
  tests/test_control_knowledge_store.py \
  tests/test_control_knowledge_e2e.py -v
```

Expected: all tests pass, protected types still create proposals, and Agent drafts missing any required section are rejected.

- [ ] **Step 6: Commit Task 3**

```bash
git add 08-YieldAgent/control_knowledge_curator.py \
  08-YieldAgent/tests/test_control_knowledge_curator.py \
  08-YieldAgent/tests/test_control_knowledge_e2e.py
git commit -m "feat(knowledge): require operational agent pages"
```

---

### Task 4: Connect validated collection to CLI and server lifecycle

**Files:**
- Modify: `08-YieldAgent/control_knowledge_cli.py`
- Modify: `08-YieldAgent/agent_server.py`
- Modify: `08-YieldAgent/tests/test_control_knowledge_service.py`
- Modify: `08-YieldAgent/tests/test_control_knowledge_server.py`

**Interfaces:**
- Consumes: `collect_current_system().candidates`
- Produces: startup and CLI snapshot paths that never submit a drifted Agent page

- [ ] **Step 1: Write failing integration tests with an injected collection**

Add a pure helper to the CLI plan and test it directly:

```python
def test_save_current_collection_persists_drift_not_blocked_agent(tmp_path, monkeypatch):
    from control_knowledge_cli import save_current_collection
    store = ControlKnowledgeStore(tmp_path)
    drift = KnowledgeCandidate.model_validate({
        "source_kind": "registry_drift",
        "subjects": ["observations/registry-drift-yield-agent"],
        "suggested_page_type": "Observation",
        "summary": "registry drift",
        "facts": [{
            "name": "registry_issues",
            "value": [{"agent_id": "yield_agent", "code": "slot_mismatch"}],
            "source_path": "control_knowledge_registry.validate_agent_registry",
        }],
        "evidence_refs": [{"kind": "snapshot", "ref": "drift", "sha256": "a" * 64}],
    })
    collection = type("Collection", (), {"candidates": [drift]})()
    paths = save_current_collection(store, collection)
    assert len(paths) == 1
    assert store.load_candidate(paths[0]).source_kind == "registry_drift"
```

Add a server helper test:

```python
def test_startup_candidates_use_validated_collection():
    from agent_server import _startup_control_candidates
    collection = type("Collection", (), {"candidates": ["safe"]})()
    assert _startup_control_candidates(collection) == ["safe"]
```

- [ ] **Step 2: Run CLI/server tests and verify RED**

Run:

```bash
uv run python -m pytest tests/test_control_knowledge_service.py \
  tests/test_control_knowledge_server.py -v
```

Expected: imports fail for `save_current_collection` and `_startup_control_candidates`.

- [ ] **Step 3: Replace direct snapshot conversion with validated collection**

Implement:

```python
# control_knowledge_cli.py
def save_current_collection(store: ControlKnowledgeStore, collection) -> list[Path]:
    return [store.save_candidate(candidate) for candidate in collection.candidates]
```

The `snapshot` command calls `collect_current_system()` and passes it to `save_current_collection()`.

Implement:

```python
# agent_server.py
def _startup_control_candidates(collection) -> list:
    return list(collection.candidates)
```

During lifespan startup, call `collect_current_system()` once and submit only `_startup_control_candidates(collection)`. Remove direct `current_system_snapshot()` plus `system_snapshot_candidates()` startup composition. Runtime candidate submission remains unchanged.

- [ ] **Step 4: Run service/server/CLI smoke checks**

Use a copied bundle so candidate evidence does not enter the repository:

```bash
CONTROL_SMOKE_ROOT=$(mktemp -d)/multiagent_knowledge
cp -R multiagent_knowledge "$CONTROL_SMOKE_ROOT"
uv run python -m pytest tests/test_control_knowledge_service.py \
  tests/test_control_knowledge_server.py -v
uv run python control_knowledge_cli.py --root "$CONTROL_SMOKE_ROOT" snapshot
uv run python control_knowledge_cli.py --root "$CONTROL_SMOKE_ROOT" lint
```

Expected: tests pass, snapshot prints `saved=14`, and lint exits 0. A clean registry produces nine Agent, four Contract, and one Workflow candidate with no drift candidate.

- [ ] **Step 5: Commit Task 4**

```bash
git add 08-YieldAgent/control_knowledge_cli.py \
  08-YieldAgent/agent_server.py \
  08-YieldAgent/tests/test_control_knowledge_service.py \
  08-YieldAgent/tests/test_control_knowledge_server.py
git commit -m "feat(knowledge): gate snapshots on registry validation"
```

---

### Task 5: Regenerate and verify the operational Wiki pages

**Files:**
- Modify: `08-YieldAgent/multiagent_knowledge/wiki/agents/*.md`
- Modify: `08-YieldAgent/multiagent_knowledge/wiki/contracts/local-trace.md`
- Modify: `08-YieldAgent/multiagent_knowledge/wiki/contracts/result-envelope.md`
- Modify: `08-YieldAgent/multiagent_knowledge/wiki/contracts/artifact-delivery.md`
- Modify: `08-YieldAgent/multiagent_knowledge/wiki/contracts/hitl-contracts.md`
- Modify: `08-YieldAgent/multiagent_knowledge/wiki/workflows/orchestration-graph.md`
- Modify: `08-YieldAgent/tests/test_control_knowledge_validator.py`

**Interfaces:**
- Consumes: enriched snapshot candidates and real curator LLM
- Produces: nine detailed Agent pages and expanded shared contract/workflow pages

- [ ] **Step 1: Add failing real-bundle content tests**

```python
import frontmatter

from control_knowledge_curator import AGENT_REQUIRED_SECTIONS
from control_knowledge_registry import AGENT_CONTROL_PROFILES
from control_knowledge_registry import AGENT_CONTROL_PROFILES


def test_real_bundle_has_operational_agent_pages():
    root = Path(__file__).resolve().parent.parent / "multiagent_knowledge"
    expected = {
        "yield-agent", "wads-agent", "map-agent", "fail-history-agent",
        "lot-history-agent", "relation-tree-agent", "mining-agent",
        "wt-resp-agent", "ppt-export",
    }
    paths = {path.stem: path for path in (root / "wiki/agents").glob("*.md")}
    assert expected <= set(paths)
    for name in expected:
        post = frontmatter.load(paths[name])
        headings = {
            line[3:].strip() for line in post.content.splitlines()
            if line.startswith("## ")
        }
        assert set(AGENT_REQUIRED_SECTIONS).issubset(headings), name
        assert post.metadata["relations"]["participates_in"] == [
            "[[workflows/orchestration-graph]]"
        ]
        agent_id = name.replace("-", "_")
        assert post.metadata["relations"]["uses_contract"] == [
            f"[[{contract}]]"
            for contract in sorted(AGENT_CONTROL_PROFILES[agent_id].output_contracts)
        ]
        assert post.metadata["relations"]["uses_hitl_contract"] == [
            "[[contracts/hitl-contracts]]"
        ]


def test_shared_pages_include_machine_contract_sections():
    root = Path(__file__).resolve().parent.parent / "multiagent_knowledge/wiki"
    result = frontmatter.load(root / "contracts/result-envelope.md").content
    trace = frontmatter.load(root / "contracts/local-trace.md").content
    artifact = frontmatter.load(root / "contracts/artifact-delivery.md").content
    hitl = frontmatter.load(root / "contracts/hitl-contracts.md").content
    workflow = frontmatter.load(root / "workflows/orchestration-graph.md").content
    assert "## Fields" in result and "## Producers and Consumers" in result
    assert "## Event Boundary" in trace and "## Redaction Boundary" in trace
    assert "## Artifact Channels" in artifact and "## Payload Boundary" in artifact
    assert "## Interrupt Types" in hitl and "## Resume Contract" in hitl
    assert "## State and Result Flow" in workflow and "## Dynamic Handoffs" in workflow
```

- [ ] **Step 2: Run content tests and verify RED**

Run:

```bash
uv run python -m pytest tests/test_control_knowledge_validator.py -v
```

Expected: current slot-oriented Agent pages are missing the required operational sections and relations.

- [ ] **Step 3: Generate pages in an isolated copy with the real curator**

```bash
CONTROL_POPULATE_ROOT=$(mktemp -d)/multiagent_knowledge
cp -R multiagent_knowledge "$CONTROL_POPULATE_ROOT"
uv run python control_knowledge_cli.py --root "$CONTROL_POPULATE_ROOT" snapshot
uv run python control_knowledge_cli.py --root "$CONTROL_POPULATE_ROOT" curate-once
uv run python control_knowledge_cli.py --root "$CONTROL_POPULATE_ROOT" lint
```

Expected ledger dispositions: no `invalid_decision` or `failed`; all nine Agent pages contain the ten required sections and all four Contract pages contain their required machine-boundary sections. Review every generated page against its profile facts. Copy only validated canonical Markdown into the repository via `apply_patch`; do not copy `raw/candidates` or `raw/curation-ledger.jsonl`.

For seed pages, replace ephemeral snapshot evidence references with stable code references from the profile. Preserve frontmatter governance fields and increment each changed page version exactly once.

- [ ] **Step 4: Run real-bundle validation and idempotency checks**

```bash
uv run python -m pytest tests/test_control_knowledge_validator.py \
  tests/test_control_knowledge_e2e.py -v
uv run python control_knowledge_cli.py --root multiagent_knowledge lint
git diff --exit-code -- wiki
```

Expected: tests and lint pass, Agent/Workflow/Contract relations resolve, internal repeated snapshot processing leaves versions stable, and the domain Wiki diff is empty.

- [ ] **Step 5: Commit Task 5**

```bash
git add 08-YieldAgent/multiagent_knowledge/wiki/agents \
  08-YieldAgent/multiagent_knowledge/wiki/contracts \
  08-YieldAgent/multiagent_knowledge/wiki/workflows \
  08-YieldAgent/tests/test_control_knowledge_validator.py
git commit -m "docs(knowledge): expand operational agent reference"
```

---

### Task 6: Strengthen live verification and run the real pipeline twice

**Files:**
- Modify: `08-YieldAgent/tests/verify_control_knowledge_live.py`
- Modify: `08-YieldAgent/multiagent_knowledge/wiki/runbooks/operating-curator.md`

**Interfaces:**
- Consumes: real server, real worker/tool path, enriched compiled Wiki, curation ledger
- Produces: live verification of content completeness, drift safety, redaction, and restart idempotency

- [ ] **Step 1: Add detailed-page checks to the live verifier**

Add:

```python
from control_knowledge_curator import AGENT_REQUIRED_SECTIONS


def validate_operational_pages(root: Path) -> None:
    agents = [
        path for path in (root / "wiki/agents").glob("*.md")
        if path.name != "index.md"
    ]
    if len(agents) != 9:
        raise SystemExit(f"expected 9 Agent pages, found {len(agents)}")
    for path in agents:
        post = frontmatter.load(path)
        headings = {
            line[3:].strip() for line in post.content.splitlines()
            if line.startswith("## ")
        }
        missing = set(AGENT_REQUIRED_SECTIONS) - headings
        if missing:
            raise SystemExit(f"operational sections missing in {path.name}: {sorted(missing)}")
        relations = post.metadata.get("relations") or {}
        if "[[workflows/orchestration-graph]]" not in relations.get("participates_in", []):
            raise SystemExit(f"workflow relation missing in {path.name}")
        agent_id = path.stem.replace("-", "_")
        expected_contracts = {
            f"[[{contract}]]"
            for contract in AGENT_CONTROL_PROFILES[agent_id].output_contracts
        }
        actual_contracts = set(relations.get("uses_contract", []))
        if actual_contracts != expected_contracts:
            raise SystemExit(
                f"contract relations differ in {path.name}: "
                f"expected={sorted(expected_contracts)} actual={sorted(actual_contracts)}"
            )
        if relations.get("uses_hitl_contract") != ["[[contracts/hitl-contracts]]"]:
            raise SystemExit(f"HITL contract relation missing in {path.name}")
```

Call it after bundle lint. Also reject any ledger entry with action `invalid_decision` or `failed`, as the current verifier already does.

- [ ] **Step 2: Document drift inspection and recovery**

Extend the curator runbook with exact commands:

```bash
# Inspect registry drift observations
find multiagent_knowledge/wiki/observations -name 'registry-drift-*.md' -maxdepth 1 -print

# Re-run registry, bundle, and operational-page checks
uv run python -m pytest tests/test_control_knowledge_registry.py \
  tests/test_control_knowledge_validator.py -v
uv run python control_knowledge_cli.py --root multiagent_knowledge lint
```

State that an affected Agent page remains unchanged until the registry/code mismatch is corrected and a new valid snapshot is processed.

- [ ] **Step 3: Run the complete automated suite**

```bash
uv run python -m pytest tests -m no_server -q
uv run python control_knowledge_cli.py --root multiagent_knowledge lint
git diff --exit-code -- wiki
git diff --check
```

Expected: every server-independent test passes, lint exits 0, the domain Wiki diff is empty, and no whitespace errors exist.

- [ ] **Step 4: Run the first live E2E in a copied bundle**

Confirm port 18001 is unused, then start and verify the server from one shell so the copied root and PID remain available:

```bash
CONTROL_E2E_ROOT=$(mktemp -d)/multiagent_knowledge
cp -R multiagent_knowledge "$CONTROL_E2E_ROOT"
if lsof -nP -iTCP:18001 -sTCP:LISTEN | grep -q LISTEN; then
  echo "port 18001 is already in use" >&2
  exit 1
fi
CONTROL_KNOWLEDGE_ROOT="$CONTROL_E2E_ROOT" \
CONTROL_KNOWLEDGE_ENABLED=true \
CONTROL_KNOWLEDGE_WRITER=true \
uv run uvicorn agent_server:app --host 127.0.0.1 --port 18001 \
  > /tmp/yield-agent-control-e2e.log 2>&1 &
CONTROL_E2E_SERVER_PID=$!
CONTROL_E2E_READY=false
for attempt in {1..30}; do
  if curl -fsS http://127.0.0.1:18001/health >/dev/null; then
    CONTROL_E2E_READY=true
    break
  fi
  sleep 1
done
if [ "$CONTROL_E2E_READY" != true ]; then
  cat /tmp/yield-agent-control-e2e.log
  kill "$CONTROL_E2E_SERVER_PID" 2>/dev/null || true
  wait "$CONTROL_E2E_SERVER_PID" 2>/dev/null || true
  exit 1
fi
CONTROL_KNOWLEDGE_ROOT="$CONTROL_E2E_ROOT" \
YIELD_AGENT_BASE_URL=http://127.0.0.1:18001 \
uv run python tests/verify_control_knowledge_live.py \
  || CONTROL_E2E_VERIFY_STATUS=$?
kill "$CONTROL_E2E_SERVER_PID"
wait "$CONTROL_E2E_SERVER_PID" || true
if [ "${CONTROL_E2E_VERIFY_STATUS:-0}" -ne 0 ]; then
  exit "$CONTROL_E2E_VERIFY_STATUS"
fi
printf 'Retained E2E bundle: %s\n' "$CONTROL_E2E_ROOT"
```

Expected: the live query reaches `stream_end`, Oracle and the real worker execute, the curator records no invalid decision, the compiled Wiki has nine detailed Agent pages, redaction passes, and `08-YieldAgent/wiki/` remains unchanged.

- [ ] **Step 5: Restart with the same bundle and verify idempotency**

Keep the `CONTROL_E2E_ROOT` shell variable from Step 4, capture the nine Agent versions before restart, and verify them after the second run:

```bash
uv run python - "$CONTROL_E2E_ROOT" <<'PY'
from pathlib import Path
import json, sys
import frontmatter
root = Path(sys.argv[1])
versions = {
    path.name: frontmatter.load(path).metadata["version"]
    for path in sorted((root / "wiki/agents").glob("*.md"))
    if path.name != "index.md"
}
(root / "raw/agent-versions-before-restart.json").write_text(
    json.dumps(versions, sort_keys=True), encoding="utf-8"
)
PY
CONTROL_KNOWLEDGE_ROOT="$CONTROL_E2E_ROOT" \
CONTROL_KNOWLEDGE_ENABLED=true \
CONTROL_KNOWLEDGE_WRITER=true \
uv run uvicorn agent_server:app --host 127.0.0.1 --port 18001 \
  > /tmp/yield-agent-control-e2e-restart.log 2>&1 &
CONTROL_E2E_SERVER_PID=$!
CONTROL_E2E_READY=false
for attempt in {1..30}; do
  if curl -fsS http://127.0.0.1:18001/health >/dev/null; then
    CONTROL_E2E_READY=true
    break
  fi
  sleep 1
done
if [ "$CONTROL_E2E_READY" != true ]; then
  cat /tmp/yield-agent-control-e2e-restart.log
  kill "$CONTROL_E2E_SERVER_PID" 2>/dev/null || true
  wait "$CONTROL_E2E_SERVER_PID" 2>/dev/null || true
  exit 1
fi
CONTROL_KNOWLEDGE_ROOT="$CONTROL_E2E_ROOT" \
YIELD_AGENT_BASE_URL=http://127.0.0.1:18001 \
uv run python tests/verify_control_knowledge_live.py \
  || CONTROL_E2E_VERIFY_STATUS=$?
kill "$CONTROL_E2E_SERVER_PID"
wait "$CONTROL_E2E_SERVER_PID" || true
if [ "${CONTROL_E2E_VERIFY_STATUS:-0}" -ne 0 ]; then
  exit "$CONTROL_E2E_VERIFY_STATUS"
fi
uv run python - "$CONTROL_E2E_ROOT" <<'PY'
from pathlib import Path
import json, sys
import frontmatter
root = Path(sys.argv[1])
before = json.loads(
    (root / "raw/agent-versions-before-restart.json").read_text(encoding="utf-8")
)
after = {
    path.name: frontmatter.load(path).metadata["version"]
    for path in sorted((root / "wiki/agents").glob("*.md"))
    if path.name != "index.md"
}
if after != before:
    raise SystemExit(f"Agent versions changed across restart: before={before} after={after}")
PY
```

Expected: previously processed fingerprints do not repeat, unchanged code-backed Agent versions remain stable, and new runtime evidence produces only a grounded Observation decision.

- [ ] **Step 6: Commit Task 6**

```bash
git add 08-YieldAgent/tests/verify_control_knowledge_live.py \
  08-YieldAgent/multiagent_knowledge/wiki/runbooks/operating-curator.md
git commit -m "test(knowledge): verify operational control reference"
```

## Final Acceptance Checklist

- [ ] Registry covers exactly the nine canonical agents.
- [ ] Registry slot, node, module, tool, artifact, result-kind, HITL, and evidence checks pass.
- [ ] Drift blocks only the affected Agent page and creates an Observation candidate.
- [ ] Every Agent page contains all ten operational sections.
- [ ] Agent relations resolve exactly to the orchestration Workflow, each registry-declared output Contract, and the shared HITL Contract.
- [ ] Workflow plus ResultEnvelope, local trace, artifact delivery, and HITL Contract pages document machine fields and data flow.
- [ ] Duplicate snapshots do not advance versions or invoke the curator again.
- [ ] Protected operational changes remain proposals.
- [ ] Runtime candidates contain no domain or user payload.
- [ ] Live Oracle, worker, LLM curator, writer, and restart paths pass.
- [ ] `08-YieldAgent/wiki/` has no diff.

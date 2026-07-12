# Hybrid Multi-Agent Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the existing deterministic LangGraph while adding an evidence-driven exploration lane with bounded autonomy, explicit HITL, external result storage, safe cancellation, and live E2E rollout gates.

**Architecture:** A Turn Gateway selects `direct`, `deterministic`, or `exploratory`. The existing `supervisor.py` workflow remains the deterministic lane; a new Exploration Graph calls four read-only workers through typed adapters, persists full results outside checkpoints, and keeps only bounded references in parent state. Mongo-backed run coordination enforces one run per session and generation-based cancellation.

**Tech Stack:** Python 3, FastAPI, Pydantic v2, LangGraph 1.x, LangChain, MongoDB/Motor, GridFS, pytest, React, TypeScript, Vite

**Design spec:** `docs/superpowers/specs/2026-07-12-hybrid-multi-agent-orchestration-design.md`

## Global Constraints

- Do not add keyword, regex, phrase-list, Korean-expression-table, or failure-string branches for semantic routing.
- Do not add planner few-shot examples to repair individual failures.
- Keep `08-YieldAgent/supervisor.py` node/edge behavior unchanged through the initial rollout.
- Exploration allowlist is exactly `yield_agent`, `wads_agent`, `map_agent`, `fail_history_agent`.
- Exploration workers are read-only and run sequentially.
- Worker executions 1 and 2 are automatic; executions 3, 4, and 5 require a new HITL approval each time.
- Hard limits: five logical worker executions per run, two executions per agent, 240 seconds active execution time. HITL wait time does not consume the 240-second budget.
- Use explicit `new_turn`, `resume`, and `cancel_and_start`; remove LLM answer-versus-new intent classification.
- Maintain at most one `running` or `waiting_hitl` run record per session.
- Keep only the latest three completed user/final-assistant turns and ten `ResultRef` entries in Exploration state.
- Store full `ResultEnvelopeV1` documents in MongoDB `agent_results`; store artifact bodies in GridFS. Never put artifact bodies in Exploration checkpoints.
- Required binding failure always skips the dependent task. `on_failure` cannot bypass this rule.
- Reuse `RetryPolicy(max_attempts=3, retry_on=is_transient_error)` for transient worker failures.
- No new third-party dependency is required; use packages already present in `requirements.txt`.
- Every implementation task follows red-green TDD and commits only its listed files.
- Completion requires live Oracle/OpenSearch, LLM, worker, MongoDB, GridFS, SSE, and frontend verification; lint or unit tests alone are insufficient.

## Locked File Structure

### New production files

- `08-YieldAgent/orchestration_contracts.py` — canonical request, state, task, binding, result-reference, controller-decision models
- `08-YieldAgent/result_store.py` — Mongo result documents, GridFS artifact backend, session cascade deletion
- `08-YieldAgent/agent_registry.py` — initial four worker input models, adapters, result extraction
- `08-YieldAgent/hybrid_router.py` — mode classifier, canary decision, Mongo run coordination, thread generation, gateway
- `08-YieldAgent/exploration_graph.py` — binding resolution, policy guard, controller, HITL, executor, synthesis, graph wiring

### Modified production files

- `08-YieldAgent/result_contracts.py` — additive metrics and provenance fields
- `08-YieldAgent/models.py` — canonical `ChatRequest` and additive SSE correlation fields
- `08-YieldAgent/local_trace.py` — hybrid orchestration event names
- `08-YieldAgent/agent_server.py` — dependency initialization, lane dispatch, run lifecycle, legacy result dual-write, checkpoint compaction, cascade deletion
- `08-YieldAgent/yield_frontend/src/types.ts` — canonical request/resume and hybrid interrupt types
- `08-YieldAgent/yield_frontend/src/lib/stream.ts` — canonical request body and 409 parsing
- `08-YieldAgent/yield_frontend/src/components/Hitl.tsx` — structured resume controls
- `08-YieldAgent/yield_frontend/src/App.tsx` — pending gate identity and cancel-and-start UX

### New or modified tests

- `08-YieldAgent/tests/test_orchestration_contracts.py`
- `08-YieldAgent/tests/test_result_contracts.py`
- `08-YieldAgent/tests/test_result_store.py`
- `08-YieldAgent/tests/test_agent_registry.py`
- `08-YieldAgent/tests/test_hybrid_router.py`
- `08-YieldAgent/tests/test_exploration_policy.py`
- `08-YieldAgent/tests/test_exploration_graph.py`
- `08-YieldAgent/tests/test_hybrid_server.py`
- `08-YieldAgent/tests/test_checkpoint_compaction.py`
- `08-YieldAgent/tests/test_exploration_evaluator.py`
- `08-YieldAgent/tests/test_hybrid_e2e.py`
- `08-YieldAgent/tests/e2e_client.py`
- `08-YieldAgent/tests/exploration_scenarios.json`
- `08-YieldAgent/tests/evaluate_exploration.py`

**Scope note:** The approved spec listed backend files as the minimum change set. Four existing Yield frontend files are added here because removing legacy `resume_value` after Phase 4 is unsafe unless the shipped client can send canonical gate identity and `cancel_and_start`. This task changes protocol wiring only; it does not redesign the UI.

---

### Task 1: Canonical orchestration contracts

**Files:**
- Create: `08-YieldAgent/orchestration_contracts.py`
- Create: `08-YieldAgent/tests/test_orchestration_contracts.py`

**Interfaces:**
- Consumes: Pydantic v2 `BaseModel`, `Field`, `JsonValue`, `model_validator`
- Produces: `TurnInput`, resume union models, `ModeDecision`, `TaskSpec`, `InputBinding`, `ResultRef`, `RunContext`, `TaskRecord`, `PendingGate`, `ControllerDecision`, `HybridState`, `append_result_ref()`, `result_ref_from_envelope()`

- [ ] **Step 1: Write failing contract tests**

```python
# 08-YieldAgent/tests/test_orchestration_contracts.py
from datetime import datetime, timezone
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestration_contracts import (
    ControllerDecision,
    FinalResponse,
    ExplorationContinueResume,
    InputBinding,
    ResultRef,
    TaskSpec,
    TurnInput,
    append_result_ref,
)

pytestmark = pytest.mark.no_server


def test_turn_input_requires_payload_for_resume():
    with pytest.raises(ValidationError):
        TurnInput(session_id="s1", input_type="resume")


def test_resume_is_discriminated_and_exact():
    turn = TurnInput.model_validate({
        "session_id": "s1",
        "input_type": "resume",
        "resume": {
            "interrupt_type": "exploration_continue",
            "run_id": "run_1",
            "gate_id": "gate_1",
            "action": "continue",
        },
    })
    assert isinstance(turn.resume, ExplorationContinueResume)
    assert turn.resume.action == "continue"


def test_task_binding_requires_dependency():
    with pytest.raises(ValidationError, match="depends_on"):
        TaskSpec(
            task_id="task_map_1",
            agent="map_agent",
            goal="map",
            input_bindings=[InputBinding(
                target_param="lot_ids",
                source_task_id="task_wads_1",
                source_path="entities.lot_ids",
                cardinality="many",
            )],
        )


def test_controller_action_fields_are_mutually_validated():
    with pytest.raises(ValidationError):
        ControllerDecision(action="run_agent", rationale="inspect")
    with pytest.raises(ValidationError):
        ControllerDecision(action="finish", rationale="done", task=TaskSpec(
            task_id="t", agent="yield_agent", goal="g"
        ))


def test_result_index_overwrites_at_ten():
    refs = []
    for i in range(12):
        refs = append_result_ref(refs, ResultRef(
            result_id=f"r{i}", task_id=f"t{i}", source_agent="yield_agent",
            status="success", summary_preview=str(i), entity_keys=[], artifact_refs=[],
        ))
    assert [r.result_id for r in refs] == [f"r{i}" for i in range(2, 12)]


def test_final_response_renders_only_grounded_claims():
    response = FinalResponse.model_validate({
        "claims": [{"text": "WADS 검출 lot 4SS0001 1건", "result_ids": ["r1"],
                    "entity_refs": {"lot_ids": ["4SS0001"]}}],
        "limitations": ["Map은 아직 실행하지 않음"],
    })
    assert response.result_ids == ["r1"]
    assert "4SS" in response.content
    assert "제한사항" in response.content
```

- [ ] **Step 2: Run tests and verify import failure**

Run: `cd 08-YieldAgent && pytest tests/test_orchestration_contracts.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'orchestration_contracts'`.

- [ ] **Step 3: Implement the complete contract module**

```python
# 08-YieldAgent/orchestration_contracts.py
from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, JsonValue, computed_field, model_validator

from result_contracts import ArtifactRef

STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)
AgentName = Literal["yield_agent", "wads_agent", "map_agent", "fail_history_agent"]
ResultStatusName = Literal["success", "partial", "empty", "error", "invalid"]


class ResumeBase(BaseModel):
    model_config = STRICT
    run_id: str = Field(min_length=1)
    gate_id: str = Field(min_length=1)


class ExplorationContinueResume(ResumeBase):
    interrupt_type: Literal["exploration_continue"]
    action: Literal["continue", "adjust", "stop"]
    adjustment: str = ""


class ExplorationQuestionResume(ResumeBase):
    interrupt_type: Literal["exploration_question"]
    answer: str | dict[str, JsonValue]


class MissingParamResume(ResumeBase):
    interrupt_type: Literal["missing_param"]
    values: dict[str, JsonValue]


class PlanReviewResume(ResumeBase):
    interrupt_type: Literal["plan_review"]
    action: Literal["approve", "modify", "cancel"]
    modification: str = ""


class DeterministicChoiceResume(ResumeBase):
    interrupt_type: Literal["task_confirm", "postwads_choice"]
    value: str


class LegacyResume(ResumeBase):
    interrupt_type: Literal["legacy"]
    gate_interrupt_type: str
    value: JsonValue


ResumeInput = (
    ExplorationContinueResume | ExplorationQuestionResume |
    MissingParamResume | PlanReviewResume | DeterministicChoiceResume | LegacyResume
)


class TurnInput(BaseModel):
    model_config = STRICT
    session_id: str = Field(min_length=1)
    user_id: str = ""
    input_type: Literal["new_turn", "resume", "cancel_and_start"]
    query: str = ""
    resume: ResumeInput | None = Field(default=None, discriminator="interrupt_type")
    expected_pending_run_id: str | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        if self.input_type == "resume" and self.resume is None:
            raise ValueError("resume payload is required")
        if self.input_type != "resume" and self.resume is not None:
            raise ValueError("resume payload is only valid for resume")
        if self.input_type in {"new_turn", "cancel_and_start"} and not self.query:
            raise ValueError("query is required")
        if self.input_type == "cancel_and_start" and not self.expected_pending_run_id:
            raise ValueError("expected_pending_run_id is required")
        return self


class ModeDecision(BaseModel):
    model_config = STRICT
    mode: Literal["direct", "deterministic", "exploratory"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    requested_capabilities: list[str] = Field(default_factory=list)


class InputBinding(BaseModel):
    model_config = STRICT
    target_param: str = Field(min_length=1)
    source_task_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    cardinality: Literal["one", "many"]
    required: bool = True


class TaskSpec(BaseModel):
    model_config = STRICT
    task_id: str = Field(min_length=1)
    agent: AgentName
    goal: str = Field(min_length=1)
    params: dict[str, JsonValue] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    input_bindings: list[InputBinding] = Field(default_factory=list)
    on_failure: Literal["stop", "ask_user", "continue_independent"] = "stop"

    @model_validator(mode="after")
    def bindings_are_dependencies(self):
        missing = {b.source_task_id for b in self.input_bindings} - set(self.depends_on)
        if missing:
            raise ValueError(f"binding sources must be in depends_on: {sorted(missing)}")
        if self.task_id in self.depends_on:
            raise ValueError("task cannot depend on itself")
        return self


class ResultRef(BaseModel):
    model_config = STRICT
    result_id: str
    task_id: str
    source_agent: AgentName
    status: ResultStatusName
    summary_preview: str = Field(max_length=500)
    entity_keys: list[str] = Field(default_factory=list)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list, max_length=10)


def append_result_ref(current: list[ResultRef], new: ResultRef) -> list[ResultRef]:
    return [*current, new][-10:]


def result_ref_from_envelope(envelope: dict) -> ResultRef:
    provenance = envelope.get("provenance") or {}
    return ResultRef(
        result_id=envelope["result_id"],
        task_id=provenance.get("task_id") or f"history_{envelope['result_id']}",
        source_agent=envelope["source_agent"],
        status=envelope["status"],
        summary_preview=str(envelope.get("summary") or "")[:500],
        entity_keys=sorted((envelope.get("entities") or {}).keys()),
        artifact_refs=(envelope.get("artifact_refs") or [])[:10],
    )


class ConversationTurn(BaseModel):
    user: str
    assistant: str


class TurnContext(BaseModel):
    query: str
    user_id: str = ""
    mode: Literal["direct", "deterministic", "exploratory"]
    started_at: datetime


class RunContext(BaseModel):
    run_id: str
    session_id: str
    generation: int = Field(ge=0)
    thread_id: str = ""
    lane: Literal["direct", "deterministic", "exploratory"]
    status: Literal["running", "waiting_hitl", "completed", "cancelled", "failed"]
    worker_executions: int = Field(default=0, ge=0, le=5)
    per_agent_executions: dict[str, int] = Field(default_factory=dict)
    started_at: datetime
    active_seconds: float = Field(default=0.0, ge=0.0)
    remaining_active_seconds: float = Field(default=240.0, ge=0.0, le=240.0)
    segment_deadline_at: datetime


class TaskRecord(BaseModel):
    spec: TaskSpec
    status: Literal[
        "proposed", "approved", "running", "success", "partial",
        "empty", "error", "skipped", "cancelled",
    ]
    result_id: str | None = None
    error_code: str | None = None
    fingerprint: str = ""


class PendingGate(BaseModel):
    gate_id: str
    interrupt_type: Literal["exploration_continue", "exploration_question"]
    task: TaskSpec | None = None
    findings: list[ResultRef] = Field(default_factory=list)


class GroundedClaim(BaseModel):
    text: str
    result_ids: list[str] = Field(min_length=1)
    entity_refs: dict[str, list[str]] = Field(default_factory=dict)


class FinalResponse(BaseModel):
    claims: list[GroundedClaim] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def result_ids(self) -> list[str]:
        return list(dict.fromkeys(r for claim in self.claims for r in claim.result_ids))

    @computed_field
    @property
    def content(self) -> str:
        lines = [f"- {claim.text}" for claim in self.claims]
        if self.limitations:
            lines.extend(["", "제한사항:", *[f"- {item}" for item in self.limitations]])
        return "\n".join(lines) or "확인된 증거가 없습니다."


class ControllerDecision(BaseModel):
    model_config = STRICT
    action: Literal["finish", "run_agent", "ask_user"]
    rationale: str
    task: TaskSpec | None = None
    question: str | None = None
    evidence_result_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_action_fields(self):
        if self.action == "run_agent" and (self.task is None or self.question is not None):
            raise ValueError("run_agent requires only task")
        if self.action == "ask_user" and (not self.question or self.task is not None):
            raise ValueError("ask_user requires only question")
        if self.action == "finish" and (self.task is not None or self.question is not None):
            raise ValueError("finish cannot include task or question")
        return self


class HybridState(TypedDict):
    conversation: list[dict]
    turn: dict
    run: dict
    tasks: list[dict]
    result_index: list[dict]
    pending_gate: dict | None
    response: dict | None
    controller_decision: dict | None
    controller_failures: int
    policy: dict | None
    current_task: dict | None
    controller_answer: str
    controller_cycles: int
    post_execute_route: str
    policy_violation: str
    policy_violation_count: int
    adjustment: str
```

- [ ] **Step 4: Run contract tests**

Run: `cd 08-YieldAgent && pytest tests/test_orchestration_contracts.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit contracts**

```bash
git add 08-YieldAgent/orchestration_contracts.py 08-YieldAgent/tests/test_orchestration_contracts.py
git commit -m "feat(yield): add orchestration contracts"
```

---

### Task 2: Extend the existing result envelope without breaking legacy workers

**Files:**
- Modify: `08-YieldAgent/result_contracts.py:569-862`
- Create: `08-YieldAgent/tests/test_result_contracts.py`

**Interfaces:**
- Consumes: existing `ResultEnvelopeV1`, `ResultProvenance`, `build_result_envelope()`
- Produces: additive `metrics` and orchestration provenance accepted by the Task 1 `result_ref_from_envelope()` helper

- [ ] **Step 1: Write failing backward-compatibility and extension tests**

```python
# 08-YieldAgent/tests/test_result_contracts.py
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from result_contracts import build_result_envelope, validate_result_envelope

pytestmark = pytest.mark.no_server


def test_legacy_envelope_still_validates():
    result = build_result_envelope(
        source_agent="yield_agent", kind="summary", summary="ok",
        provenance={"task_id": "legacy_task"},
    )
    validated = validate_result_envelope(result)
    assert validated.provenance.task_id == "legacy_task"
    assert validated.metrics == {}
    assert validated.provenance.input_result_ids == []


def test_orchestration_fields_round_trip():
    result = build_result_envelope(
        source_agent="wads_agent",
        kind="table",
        metrics={"detected_count": 4},
        provenance={
            "task_id": "task_wads_1",
            "run_id": "run_1",
            "input_result_ids": ["result_yield_1"],
            "execution_params": {"lotcd": "4SS"},
            "trace_ids": {"trace_id": "trace_1"},
            "agent_version": "wads_agent/v1",
        },
    )
    validated = validate_result_envelope(result)
    assert validated.metrics["detected_count"] == 4
    assert validated.provenance.run_id == "run_1"
    assert validated.provenance.execution_params == {"lotcd": "4SS"}


def test_metrics_reject_non_json_values():
    with pytest.raises(Exception):
        build_result_envelope(
            source_agent="yield_agent", kind="summary", metrics={"bad": object()}
        )
```

- [ ] **Step 2: Run tests and verify missing-field failure**

Run: `cd 08-YieldAgent && pytest tests/test_result_contracts.py -v`

Expected: FAIL because `metrics` is not accepted and new provenance attributes do not exist.

- [ ] **Step 3: Add fields and builder support**

```python
# Add to ResultProvenance
run_id: str = ""
input_result_ids: list[str] = Field(default_factory=list)
execution_params: dict[str, Any] = Field(default_factory=dict)
trace_ids: dict[str, str] = Field(default_factory=dict)
agent_version: str = ""
started_at: datetime | None = None
completed_at: datetime | None = None

# Add to ResultEnvelopeV1
metrics: dict[str, Any] = Field(default_factory=dict)

@field_validator("metrics")
@classmethod
def metrics_must_be_json_compatible(cls, value: dict[str, Any]) -> dict[str, Any]:
    _ensure_json_compatible(value, "metrics")
    return value

# Add to build_result_envelope signature
metrics: dict[str, Any] | None = None,

# Add to payload construction
"metrics": metrics or {},
```

Keep every existing field and validator. Do not change `RESULT_ENVELOPE_SCHEMA_VERSION`.

- [ ] **Step 4: Run focused and existing result tests**

Run: `cd 08-YieldAgent && pytest tests/test_result_contracts.py tests/test_confirm_edit.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit additive envelope changes**

```bash
git add 08-YieldAgent/result_contracts.py 08-YieldAgent/tests/test_result_contracts.py
git commit -m "feat(yield): extend result provenance"
```

---

### Task 3: External Result Store and GridFS artifact backend

**Files:**
- Create: `08-YieldAgent/result_store.py`
- Create: `08-YieldAgent/tests/test_result_store.py`

**Interfaces:**
- Consumes: validated `ResultEnvelopeV1` dictionaries and worker artifact dictionaries
- Produces: `ResultStore.save()`, `ResultStore.get()`, `ResultStore.get_field()`, `ResultStore.list_recent()`, `ResultStore.cancel_run()`, `ResultStore.delete_session()`, `GridFSArtifactBackend`, `InMemoryArtifactBackend`

- [ ] **Step 1: Write failing store tests**

```python
# 08-YieldAgent/tests/test_result_store.py
import asyncio
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from result_contracts import build_result_envelope
from result_store import InMemoryArtifactBackend, InMemoryResultStore

pytestmark = pytest.mark.no_server


def test_in_memory_store_round_trip_and_field_lookup():
    async def scenario():
        store = InMemoryResultStore(InMemoryArtifactBackend())
        envelope = build_result_envelope(
            source_agent="wads_agent", kind="table",
            entities={"lot_ids": ["4SS0001", "4SS0002"]},
            artifacts=[{"artifact_id": "a1", "type": "html", "data": "<b>x</b>"}],
        )
        saved = await store.save("s1", "run1", envelope, [
            {"artifact_id": "a1", "type": "html", "mime": "text/html", "data": "<b>x</b>"}
        ])
        assert await store.get(saved["result_id"]) == saved
        assert await store.get_field(saved["result_id"], "entities.lot_ids") == ["4SS0001", "4SS0002"]
        assert "data" not in saved["artifact_refs"][0]
        assert [item["result_id"] for item in await store.list_recent("s1", 10)] == [saved["result_id"]]

    asyncio.run(scenario())


def test_delete_session_cascades_results_and_artifacts():
    async def scenario():
        backend = InMemoryArtifactBackend()
        store = InMemoryResultStore(backend)
        envelope = build_result_envelope(
            source_agent="map_agent", kind="image",
            artifacts=[{"artifact_id": "a2", "type": "html", "data": "map"}],
        )
        saved = await store.save("s2", "run2", envelope, [
            {"artifact_id": "a2", "type": "html", "data": "map"}
        ])
        await store.delete_session("s2")
        assert await store.get(saved["result_id"]) is None
        assert await backend.get("a2") is None

    asyncio.run(scenario())


def test_artifact_failure_persists_partial_text_result():
    class FailingArtifacts(InMemoryArtifactBackend):
        async def put(self, artifact_id, artifact):
            raise RuntimeError("gridfs unavailable")

    async def scenario():
        store = InMemoryResultStore(FailingArtifacts())
        envelope = build_result_envelope(
            source_agent="map_agent", kind="image", status="success", summary="map text",
            artifacts=[{"artifact_id": "a3", "type": "html", "data": "map"}],
        )
        saved = await store.save("s3", "run3", envelope, [{"artifact_id": "a3", "data": "map"}])
        assert saved["status"] == "partial"
        assert saved["metadata"]["artifact_store_error"] == "RuntimeError"

    asyncio.run(scenario())


def test_cancelled_run_is_excluded_from_recent_context():
    async def scenario():
        store = InMemoryResultStore(InMemoryArtifactBackend())
        envelope = build_result_envelope(source_agent="yield_agent", kind="summary")
        await store.save("s4", "cancelled_run", envelope, [])
        await store.cancel_run("cancelled_run")
        assert await store.list_recent("s4", 10) == []
    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests and verify import failure**

Run: `cd 08-YieldAgent && pytest tests/test_result_store.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'result_store'`.

- [ ] **Step 3: Implement store protocols, in-memory test store, and GridFS backend**

```python
# 08-YieldAgent/result_store.py
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from result_contracts import dump_result_envelope, validate_result_envelope


class ArtifactBackend(Protocol):
    async def put(self, artifact_id: str, artifact: dict[str, Any]) -> None:
        raise NotImplementedError

    async def get(self, artifact_id: str) -> bytes | None:
        raise NotImplementedError

    async def delete(self, artifact_id: str) -> None:
        raise NotImplementedError


def _artifact_bytes(artifact: dict[str, Any]) -> bytes:
    data = artifact.get("data", b"")
    if isinstance(data, bytes):
        return data
    path = Path(str(data))
    if artifact.get("artifact_type") == "pptx" and path.is_file():
        return path.read_bytes()
    return str(data).encode("utf-8")


def _get_dotted(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


class GridFSArtifactBackend:
    def __init__(self, db):
        self.bucket = AsyncIOMotorGridFSBucket(db, bucket_name="agent_artifacts")

    async def put(self, artifact_id: str, artifact: dict[str, Any]) -> None:
        await self.bucket.upload_from_stream_with_id(
            artifact_id,
            artifact.get("title") or artifact_id,
            _artifact_bytes(artifact),
            metadata={k: v for k, v in artifact.items() if k != "data"},
        )

    async def get(self, artifact_id: str) -> bytes | None:
        try:
            stream = await self.bucket.open_download_stream(artifact_id)
        except Exception:
            return None
        return await stream.read()

    async def delete(self, artifact_id: str) -> None:
        try:
            await self.bucket.delete(artifact_id)
        except Exception:
            return


class InMemoryArtifactBackend:
    def __init__(self):
        self.items: dict[str, bytes] = {}

    async def put(self, artifact_id: str, artifact: dict[str, Any]) -> None:
        self.items[artifact_id] = _artifact_bytes(artifact)

    async def get(self, artifact_id: str) -> bytes | None:
        return self.items.get(artifact_id)

    async def delete(self, artifact_id: str) -> None:
        self.items.pop(artifact_id, None)


class ResultStore:
    def __init__(self, db, artifacts: ArtifactBackend):
        self.collection = db.agent_results
        self.artifacts = artifacts

    async def ensure_indexes(self) -> None:
        await self.collection.create_index("result_id", unique=True)
        await self.collection.create_index("session_id")
        await self.collection.create_index("run_id")

    async def save(self, session_id: str, run_id: str, envelope: dict, artifacts: list[dict]) -> dict:
        validated = dump_result_envelope(validate_result_envelope(envelope))
        artifact_error = None
        for artifact, ref in zip(artifacts, validated.get("artifact_refs", []), strict=False):
            artifact = {**artifact, "artifact_id": ref["artifact_id"]}
            try:
                await self.artifacts.put(ref["artifact_id"], artifact)
            except Exception as exc:
                artifact_error = type(exc).__name__
        if artifact_error:
            if validated["status"] == "success":
                validated["status"] = "partial"
            validated.setdefault("metadata", {})["artifact_store_error"] = artifact_error
            validated = dump_result_envelope(validate_result_envelope(validated))
        await self.collection.replace_one(
            {"result_id": validated["result_id"]},
            {"session_id": session_id, "run_id": run_id, "result_id": validated["result_id"],
             "source_agent": validated["source_agent"], "created_at": datetime.now(timezone.utc),
             "cancelled": False,
             "envelope": validated},
            upsert=True,
        )
        return validated

    async def get(self, result_id: str) -> dict | None:
        doc = await self.collection.find_one({"result_id": result_id}, {"_id": 0, "envelope": 1})
        return doc.get("envelope") if doc else None

    async def get_field(self, result_id: str, path: str) -> Any:
        envelope = await self.get(result_id)
        if envelope is None:
            raise KeyError(result_id)
        return _get_dotted(envelope, path)

    async def list_recent(self, session_id: str, limit: int = 10) -> list[dict]:
        cursor = self.collection.find(
            {"session_id": session_id, "cancelled": {"$ne": True}, "source_agent": {"$in": [
                "yield_agent", "wads_agent", "map_agent", "fail_history_agent",
            ]}},
            {"_id": 0, "envelope": 1},
        ).sort("created_at", -1).limit(min(10, max(0, limit)))
        docs = await cursor.to_list(length=min(10, max(0, limit)))
        return [doc["envelope"] for doc in reversed(docs)]

    async def cancel_run(self, run_id: str) -> None:
        await self.collection.update_many({"run_id": run_id}, {"$set": {"cancelled": True}})

    async def delete_session(self, session_id: str) -> None:
        artifact_ids: list[str] = []
        async for doc in self.collection.find({"session_id": session_id}, {"envelope.artifact_refs": 1}):
            artifact_ids.extend(r["artifact_id"] for r in doc.get("envelope", {}).get("artifact_refs", []))
        for artifact_id in artifact_ids:
            await self.artifacts.delete(artifact_id)
        await self.collection.delete_many({"session_id": session_id})


class InMemoryResultStore:
    def __init__(self, artifacts: ArtifactBackend):
        self.artifacts = artifacts
        self.items: dict[str, tuple[str, str, dict]] = {}
        self.cancelled_runs: set[str] = set()

    async def save(self, session_id: str, run_id: str, envelope: dict, artifacts: list[dict]) -> dict:
        validated = dump_result_envelope(validate_result_envelope(envelope))
        artifact_error = None
        for artifact, ref in zip(artifacts, validated.get("artifact_refs", []), strict=False):
            try:
                await self.artifacts.put(ref["artifact_id"], {**artifact, "artifact_id": ref["artifact_id"]})
            except Exception as exc:
                artifact_error = type(exc).__name__
        if artifact_error:
            if validated["status"] == "success":
                validated["status"] = "partial"
            validated.setdefault("metadata", {})["artifact_store_error"] = artifact_error
            validated = dump_result_envelope(validate_result_envelope(validated))
        self.items[validated["result_id"]] = (session_id, run_id, deepcopy(validated))
        return deepcopy(validated)

    async def get(self, result_id: str) -> dict | None:
        item = self.items.get(result_id)
        return deepcopy(item[2]) if item else None

    async def get_field(self, result_id: str, path: str) -> Any:
        envelope = await self.get(result_id)
        if envelope is None:
            raise KeyError(result_id)
        return _get_dotted(envelope, path)

    async def list_recent(self, session_id: str, limit: int = 10) -> list[dict]:
        values = [
            item[2] for item in self.items.values()
            if item[0] == session_id and item[1] not in self.cancelled_runs
        ]
        return deepcopy(values[-min(10, max(0, limit)):])

    async def cancel_run(self, run_id: str) -> None:
        self.cancelled_runs.add(run_id)

    async def delete_session(self, session_id: str) -> None:
        doomed = [rid for rid, item in self.items.items() if item[0] == session_id]
        for result_id in doomed:
            envelope = self.items.pop(result_id)[2]
            for ref in envelope.get("artifact_refs", []):
                await self.artifacts.delete(ref["artifact_id"])
```

- [ ] **Step 4: Add a Mongo/GridFS round-trip test guarded by local Mongo availability**

```python
from motor.motor_asyncio import AsyncIOMotorClient
from result_store import GridFSArtifactBackend, ResultStore


def test_mongo_gridfs_round_trip():
    async def scenario():
        client = AsyncIOMotorClient("mongodb://localhost:27017", serverSelectionTimeoutMS=500)
        try:
            await client.admin.command("ping")
        except Exception:
            pytest.skip("local MongoDB not reachable")
        db = client[f"yield_agent_test_{uuid.uuid4().hex}"]
        store = ResultStore(db, GridFSArtifactBackend(db))
        await store.ensure_indexes()
        envelope = build_result_envelope(
            source_agent="map_agent", kind="image",
            artifacts=[{"artifact_id": "grid_a", "type": "html", "data": "<div>map</div>"}],
        )
        saved = await store.save("s", "r", envelope, [{"artifact_id": "grid_a", "data": "<div>map</div>"}])
        assert (await store.get(saved["result_id"]))["result_id"] == saved["result_id"]
        assert await store.artifacts.get(saved["artifact_refs"][0]["artifact_id"]) == b"<div>map</div>"
        await client.drop_database(db.name)
        client.close()

    asyncio.run(scenario())
```

- [ ] **Step 5: Run store tests**

Run: `cd 08-YieldAgent && pytest tests/test_result_store.py -v`

Expected: in-memory tests PASS; Mongo test PASS when MongoDB is up or SKIP with the exact reason.

- [ ] **Step 6: Commit the Result Store**

```bash
git add 08-YieldAgent/result_store.py 08-YieldAgent/tests/test_result_store.py
git commit -m "feat(yield): add external result store"
```

---

### Task 4: Initial four-agent Registry and private-state adapters

**Files:**
- Create: `08-YieldAgent/agent_registry.py`
- Create: `08-YieldAgent/tests/test_agent_registry.py`

**Interfaces:**
- Consumes: `TaskSpec`, `ResultEnvelopeV1`, existing four worker node callables
- Produces: `AgentRegistry.get()`, `AgentRegistry.execute()`, `ExecutionOutput`

- [ ] **Step 1: Write failing adapter isolation tests with fake workers**

```python
# 08-YieldAgent/tests/test_agent_registry.py
import asyncio
import sys
from pathlib import Path

from langchain_core.messages import AIMessage
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_registry import AgentEntry, AgentRegistry
from orchestration_contracts import TaskSpec
from result_contracts import attach_result_envelope

pytestmark = pytest.mark.no_server


def _worker(state, config):
    assert set(state) == {"messages", "current_task", "current_task_id", "current_task_goal"}
    message = AIMessage(content="found", name="wads_agent")
    attach_result_envelope(
        message, source_agent="wads_agent", kind="table", status="success",
        entities={"lot_ids": ["4SS0001"]},
    )
    return {"messages": [message], "wads_artifacts": [{"type": "html", "data": "x"}]}


def test_adapter_uses_private_state_and_strips_followups():
    async def scenario():
        registry = AgentRegistry(entries={
            "wads_agent": AgentEntry("wads_agent", _worker, {"lotcd"}, "wads_artifacts")
        })
        output = await registry.execute(
            TaskSpec(task_id="t1", agent="wads_agent", goal="inspect", params={"lotcd": "4SS"}),
            run_id="r1", input_result_ids=[], config={},
        )
        assert output.envelope["provenance"]["run_id"] == "r1"
        assert output.envelope["provenance"]["task_id"] == "t1"
        assert output.envelope["followups"] == []
        assert output.artifacts[0]["data"] == "x"

    asyncio.run(scenario())


def test_registry_rejects_unknown_and_missing_params():
    async def scenario():
        registry = AgentRegistry(entries={
            "wads_agent": AgentEntry("wads_agent", _worker, {"lotcd"}, "wads_artifacts")
        })
        with pytest.raises(ValueError, match="required params"):
            await registry.execute(TaskSpec(task_id="t", agent="wads_agent", goal="g"), "r", [], {})

    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests and verify import failure**

Run: `cd 08-YieldAgent && pytest tests/test_agent_registry.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_registry'`.

- [ ] **Step 3: Implement Registry, extraction, provenance enrichment, and safe errors**

```python
# 08-YieldAgent/agent_registry.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from langchain_core.messages import HumanMessage

from common import is_transient_error
from orchestration_contracts import TaskSpec
from result_contracts import build_result_envelope, dump_result_envelope, validate_result_envelope


@dataclass(frozen=True)
class AgentEntry:
    name: str
    node: Callable[[dict, dict], dict]
    required_params: set[str]
    artifact_field: str
    risk: str = "low"
    required_any: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionOutput:
    envelope: dict[str, Any]
    artifacts: list[dict[str, Any]]


def _extract_envelope(update: dict[str, Any]) -> dict[str, Any]:
    for message in reversed(update.get("messages") or []):
        payload = (getattr(message, "additional_kwargs", None) or {}).get("result")
        if payload:
            return dump_result_envelope(validate_result_envelope(payload))
    raise ValueError("worker did not return ResultEnvelopeV1")


class AgentRegistry:
    def __init__(self, entries: dict[str, AgentEntry]):
        self.entries = entries

    def get(self, name: str) -> AgentEntry:
        if name not in self.entries:
            raise KeyError(f"agent not allowed: {name}")
        return self.entries[name]

    async def execute(
        self, task: TaskSpec, run_id: str, input_result_ids: list[str], config: dict,
    ) -> ExecutionOutput:
        entry = self.get(task.agent)
        missing = sorted(k for k in entry.required_params if task.params.get(k) in (None, "", []))
        if missing:
            raise ValueError(f"required params missing: {missing}")
        if entry.required_any and all(task.params.get(k) in (None, "", []) for k in entry.required_any):
            raise ValueError(f"one required param is missing: {entry.required_any}")
        state = {
            "messages": [HumanMessage(content=task.goal)],
            "current_task": task.model_dump(mode="json"),
            "current_task_id": task.task_id,
            "current_task_goal": task.goal,
        }
        started = datetime.now(timezone.utc)
        try:
            update = await asyncio.to_thread(entry.node, state, config)
            envelope = _extract_envelope(update)
        except Exception as exc:
            if is_transient_error(exc):
                raise
            envelope = build_result_envelope(
                source_agent=entry.name, kind="summary", status="error",
                summary=f"{type(exc).__name__}: worker execution failed",
            )
            update = {}
        provenance = envelope.setdefault("provenance", {})
        provenance.update({
            "task_id": task.task_id,
            "task_goal": task.goal,
            "run_id": run_id,
            "input_result_ids": input_result_ids,
            "execution_params": task.params,
            "agent_version": f"{entry.name}/v1",
            "started_at": started.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        envelope["metrics"] = {
            key: value for key, value in (envelope.get("metadata") or {}).items()
            if isinstance(value, int | float) and not isinstance(value, bool)
        }
        envelope["followups"] = []
        return ExecutionOutput(
            envelope=dump_result_envelope(validate_result_envelope(envelope)),
            artifacts=list(update.get(entry.artifact_field) or []),
        )


def build_default_registry() -> AgentRegistry:
    from fail_history_agent import fail_history_agent_node
    from map_agent import map_agent_node
    from wads_agent import wads_agent_node
    from yield_query_agent import yield_agent_node

    return AgentRegistry(entries={
        "yield_agent": AgentEntry("yield_agent", yield_agent_node, {"lotcd"}, "yield_artifacts"),
        "wads_agent": AgentEntry("wads_agent", wads_agent_node, {"lotcd"}, "wads_artifacts"),
        "map_agent": AgentEntry("map_agent", map_agent_node, set(), "map_artifacts", required_any=("lot_ids", "groupkey")),
        "fail_history_agent": AgentEntry("fail_history_agent", fail_history_agent_node, set(), "fail_history_artifacts"),
    })
```

The `map_agent` adapter accepts `groupkey` instead of `lot_ids` when provided. Implement its validation as `required_any=("lot_ids", "groupkey")` in `AgentEntry`; add a test before changing the dataclass. Do not add natural-language parsing.

- [ ] **Step 4: Add the required-any test and minimal dataclass support**

```python
def test_map_accepts_groupkey_as_required_alternative():
    entry = AgentEntry("map_agent", _worker, set(), "map_artifacts", required_any=("lot_ids", "groupkey"))
    assert entry.required_any == ("lot_ids", "groupkey")
```

The dataclass and `execute()` implementation in Step 3 already include `required_any`; this test pins the Map alternative contract.

- [ ] **Step 5: Run Registry tests**

Run: `cd 08-YieldAgent && pytest tests/test_agent_registry.py tests/test_result_contracts.py -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit Registry and adapters**

```bash
git add 08-YieldAgent/agent_registry.py 08-YieldAgent/tests/test_agent_registry.py
git commit -m "feat(yield): add exploration agent registry"
```

---

### Task 5: Mongo run coordination and generation-scoped graph threads

**Files:**
- Create: `08-YieldAgent/hybrid_router.py`
- Create: `08-YieldAgent/tests/test_hybrid_router.py`

**Interfaces:**
- Consumes: `TurnInput`, `RunContext`, MongoDB `session_runs`
- Produces: `MongoRunCoordinator.start()`, `pause()`, `resume()`, `cancel_and_start()`, `finish()`, `get()`, `thread_id()` and conflict exceptions

- [ ] **Step 1: Write failing concurrency and budget tests**

```python
# 08-YieldAgent/tests/test_hybrid_router.py
import asyncio
from datetime import datetime, timezone
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hybrid_router import InMemoryRunCoordinator, RunConflict, StaleGate

pytestmark = pytest.mark.no_server


def test_one_active_run_and_generation_cancel():
    async def scenario():
        runs = InMemoryRunCoordinator()
        first = await runs.start("s1", "exploratory")
        with pytest.raises(RunConflict) as exc:
            await runs.start("s1", "deterministic")
        assert exc.value.code == "run_in_progress"
        await runs.pause("s1", first.run_id, "gate_1", "exploration_continue")
        with pytest.raises(RunConflict) as pending:
            await runs.start("s1", "deterministic")
        assert pending.value.code == "pending_gate"
        second = await runs.cancel_and_start("s1", first.run_id, "deterministic")
        assert second.generation == first.generation + 1
        assert runs.thread_id(second) == f"s1:{second.generation}:deterministic"

    asyncio.run(scenario())


def test_resume_requires_exact_gate_and_pauses_active_timer():
    async def scenario():
        runs = InMemoryRunCoordinator()
        run = await runs.start("s2", "exploratory")
        await runs.pause("s2", run.run_id, "gate_ok", "exploration_continue", active_elapsed=12.5)
        with pytest.raises(StaleGate):
            await runs.resume("s2", run.run_id, "gate_bad", "exploration_continue")
        resumed = await runs.resume("s2", run.run_id, "gate_ok", "exploration_continue")
        assert resumed.remaining_active_seconds == pytest.approx(227.5)
        assert resumed.status == "running"

    asyncio.run(scenario())


def test_legacy_generation_zero_reuses_session_thread():
    async def scenario():
        runs = InMemoryRunCoordinator()
        await runs.adopt_legacy("legacy_session")
        run = await runs.start("legacy_session", "deterministic")
        assert runs.thread_id(run) == "legacy_session"
    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests and verify import failure**

Run: `cd 08-YieldAgent && pytest tests/test_hybrid_router.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'hybrid_router'`.

- [ ] **Step 3: Implement exceptions and an in-memory reference coordinator**

```python
# Initial section of 08-YieldAgent/hybrid_router.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from orchestration_contracts import RunContext


class RunConflict(RuntimeError):
    def __init__(self, code: str, run_id: str):
        super().__init__(code)
        self.code = code
        self.run_id = run_id


class StaleGate(RuntimeError):
    """Raised when run or gate identity no longer matches current session state."""


def _new_run(session_id: str, lane: str, generation: int, thread_id: str = "") -> RunContext:
    now = datetime.now(timezone.utc)
    return RunContext(
        run_id=f"run_{uuid.uuid4().hex}", session_id=session_id,
        generation=generation, thread_id=thread_id, lane=lane, status="running", started_at=now,
        segment_deadline_at=now + timedelta(seconds=240),
    )


class InMemoryRunCoordinator:
    def __init__(self):
        self.records: dict[str, dict] = {}

    async def start(self, session_id: str, lane: str) -> RunContext:
        current = self.records.get(session_id)
        if current and current["run"]["status"] in {"running", "waiting_hitl"}:
            code = "run_in_progress" if current["run"]["status"] == "running" else "pending_gate"
            raise RunConflict(code, current["run"]["run_id"])
        generation = (current or {}).get("run", {}).get("generation", 0)
        legacy_thread = (current or {}).get("legacy_thread_id", "")
        run = _new_run(
            session_id, lane, generation,
            thread_id=legacy_thread if generation == 0 and lane == "deterministic" else "",
        )
        self.records[session_id] = {
            "run": run.model_dump(), "gate": None, "legacy_thread_id": legacy_thread,
        }
        return run

    async def pause(self, session_id, run_id, gate_id, interrupt_type, active_elapsed=0.0):
        record = self.records[session_id]
        if record["run"]["run_id"] != run_id or record["run"]["status"] != "running":
            raise StaleGate(run_id)
        record["run"]["active_seconds"] += active_elapsed
        record["run"]["remaining_active_seconds"] = max(0.0, 240.0 - record["run"]["active_seconds"])
        record["run"]["status"] = "waiting_hitl"
        record["gate"] = {"gate_id": gate_id, "interrupt_type": interrupt_type}
        return RunContext.model_validate(record["run"])

    async def resume(self, session_id, run_id, gate_id, interrupt_type):
        record = self.records.get(session_id) or {}
        gate = record.get("gate") or {}
        run = record.get("run") or {}
        if run.get("run_id") != run_id or gate != {"gate_id": gate_id, "interrupt_type": interrupt_type}:
            raise StaleGate(gate_id)
        now = datetime.now(timezone.utc)
        run["status"] = "running"
        run["segment_deadline_at"] = now + timedelta(seconds=run["remaining_active_seconds"])
        record["gate"] = None
        return RunContext.model_validate(run)

    async def cancel_and_start(self, session_id, expected_run_id, lane):
        record = self.records.get(session_id) or {}
        run = record.get("run") or {}
        if run.get("run_id") != expected_run_id or run.get("status") != "waiting_hitl":
            raise StaleGate(expected_run_id)
        new_run = _new_run(session_id, lane, int(run["generation"]) + 1)
        self.records[session_id] = {"run": new_run.model_dump(), "gate": None}
        return new_run

    async def finish(self, session_id, run_id, status="completed", active_elapsed=0.0):
        record = self.records.get(session_id)
        if record and record["run"]["run_id"] == run_id:
            record["run"]["active_seconds"] = min(240.0, record["run"]["active_seconds"] + active_elapsed)
            record["run"]["remaining_active_seconds"] = max(0.0, 240.0 - record["run"]["active_seconds"])
            record["run"]["status"] = status

    async def get(self, session_id):
        record = self.records.get(session_id)
        return record.copy() if record else None

    async def adopt_legacy(self, session_id, pending=None):
        run = _new_run(session_id, "deterministic", 0, thread_id=session_id)
        gate = None
        if pending:
            run = run.model_copy(update={"status": "waiting_hitl"})
            gate = {"gate_id": "gate_legacy", "interrupt_type": pending.get(
                "interrupt_type", pending.get("type", "missing_param")
            )}
        else:
            run = run.model_copy(update={"status": "completed"})
        self.records[session_id] = {
            "run": run.model_dump(), "gate": gate, "legacy_thread_id": session_id,
        }
        return await self.get(session_id)

    async def is_current(self, session_id, run_id, generation):
        record = self.records.get(session_id) or {}
        run = record.get("run") or {}
        return run.get("run_id") == run_id and run.get("generation") == generation

    @staticmethod
    def thread_id(run: RunContext) -> str:
        return run.thread_id or f"{run.session_id}:{run.generation}:{run.lane}"
```

- [ ] **Step 4: Implement `MongoRunCoordinator` with atomic `_id=session_id` transitions**

Use `find_one_and_update` with `return_document=ReturnDocument.AFTER` and catch `DuplicateKeyError`. Implement every transition as follows:

```python
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


class MongoRunCoordinator:
    def __init__(self, db):
        self.collection = db.session_runs

    async def start(self, session_id: str, lane: str) -> RunContext:
        current = await self.collection.find_one({"_id": session_id})
        now = datetime.now(timezone.utc)
        current_run = (current or {}).get("run") or {}
        generation = int(current_run.get("generation", 0))
        if current_run.get("status") == "running" and current_run.get("segment_deadline_at") <= now:
            generation += 1
        legacy_thread = (current or {}).get("legacy_thread_id", "")
        candidate = _new_run(
            session_id, lane, generation=generation,
            thread_id=legacy_thread if generation == 0 and lane == "deterministic" else "",
        )
        thread_id = self.thread_id(candidate)
        try:
            doc = await self.collection.find_one_and_update(
                {"_id": session_id, "$or": [
                    {"run": {"$exists": False}},
                    {"run.status": {"$in": ["completed", "cancelled", "failed"]}},
                    {"run.status": "running", "run.segment_deadline_at": {"$lte": now}},
                ]},
                {"$set": {"run": candidate.model_dump(), "gate": None},
                 "$addToSet": {"thread_ids": thread_id}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            active = await self.collection.find_one({"_id": session_id})
            status = active["run"]["status"]
            raise RunConflict("run_in_progress" if status == "running" else "pending_gate", active["run"]["run_id"])
        return RunContext.model_validate(doc["run"])

    async def pause(self, session_id, run_id, gate_id, interrupt_type, active_elapsed=0.0):
        current = await self.collection.find_one({"_id": session_id, "run.run_id": run_id, "run.status": "running"})
        if not current:
            raise StaleGate(run_id)
        run = RunContext.model_validate(current["run"])
        active = min(240.0, run.active_seconds + max(0.0, active_elapsed))
        updated = run.model_copy(update={
            "status": "waiting_hitl",
            "active_seconds": active,
            "remaining_active_seconds": max(0.0, 240.0 - active),
        })
        doc = await self.collection.find_one_and_update(
            {"_id": session_id, "run.run_id": run_id, "run.status": "running"},
            {"$set": {"run": updated.model_dump(), "gate": {
                "gate_id": gate_id, "interrupt_type": interrupt_type,
            }}},
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            raise StaleGate(gate_id)
        return RunContext.model_validate(doc["run"])

    async def resume(self, session_id, run_id, gate_id, interrupt_type):
        current = await self.collection.find_one({
            "_id": session_id, "run.run_id": run_id, "run.status": "waiting_hitl",
            "gate.gate_id": gate_id, "gate.interrupt_type": interrupt_type,
        })
        if not current:
            raise StaleGate(gate_id)
        run = RunContext.model_validate(current["run"])
        now = datetime.now(timezone.utc)
        updated = run.model_copy(update={
            "status": "running",
            "segment_deadline_at": now + timedelta(seconds=run.remaining_active_seconds),
        })
        doc = await self.collection.find_one_and_update(
            {"_id": session_id, "run.run_id": run_id, "run.status": "waiting_hitl",
             "gate.gate_id": gate_id, "gate.interrupt_type": interrupt_type},
            {"$set": {"run": updated.model_dump(), "gate": None}},
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            raise StaleGate(gate_id)
        return RunContext.model_validate(doc["run"])

    async def cancel_and_start(self, session_id, expected_run_id, lane):
        current = await self.collection.find_one({
            "_id": session_id, "run.run_id": expected_run_id, "run.status": "waiting_hitl",
        })
        if not current:
            raise StaleGate(expected_run_id)
        generation = int(current["run"]["generation"]) + 1
        replacement = _new_run(session_id, lane, generation)
        doc = await self.collection.find_one_and_update(
            {"_id": session_id, "run.run_id": expected_run_id, "run.status": "waiting_hitl"},
            {"$set": {"run": replacement.model_dump(), "gate": None},
             "$addToSet": {"thread_ids": self.thread_id(replacement)}},
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            raise StaleGate(expected_run_id)
        return RunContext.model_validate(doc["run"])

    async def finish(self, session_id, run_id, status="completed", active_elapsed=0.0):
        current = await self.collection.find_one({"_id": session_id, "run.run_id": run_id})
        if not current:
            raise StaleGate(run_id)
        run = RunContext.model_validate(current["run"])
        active = min(240.0, run.active_seconds + max(0.0, active_elapsed))
        updated = run.model_copy(update={
            "status": status,
            "active_seconds": active,
            "remaining_active_seconds": max(0.0, 240.0 - active),
        })
        doc = await self.collection.find_one_and_update(
            {"_id": session_id, "run.run_id": run_id},
            {"$set": {"run": updated.model_dump(), "gate": None}},
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            raise StaleGate(run_id)
        return RunContext.model_validate(doc["run"])

    async def get(self, session_id):
        return await self.collection.find_one({"_id": session_id}, {"_id": 0})

    async def adopt_legacy(self, session_id, pending: dict | None = None):
        now = datetime.now(timezone.utc)
        run = _new_run(session_id, "deterministic", 0, thread_id=session_id)
        gate = None
        if pending:
            run = run.model_copy(update={"status": "waiting_hitl"})
            gate = {
                "gate_id": f"gate_{uuid.uuid4().hex}",
                "interrupt_type": pending.get("interrupt_type", pending.get("type", "missing_param")),
            }
        else:
            run = run.model_copy(update={"status": "completed", "segment_deadline_at": now})
        await self.collection.update_one(
            {"_id": session_id},
            {"$setOnInsert": {
                "run": run.model_dump(), "gate": gate,
                "legacy_thread_id": session_id, "thread_ids": [session_id],
            }},
            upsert=True,
        )
        return await self.get(session_id)

    async def is_current(self, session_id, run_id, generation):
        doc = await self.collection.find_one({
            "_id": session_id, "run.run_id": run_id, "run.generation": generation,
        }, {"_id": 1})
        return doc is not None

    @staticmethod
    def thread_id(run: RunContext) -> str:
        return run.thread_id or f"{run.session_id}:{run.generation}:{run.lane}"
```

- [ ] **Step 5: Add a local-Mongo race test**

```python
def test_mongo_coordinator_allows_only_one_start():
    async def scenario():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient("mongodb://localhost:27017", serverSelectionTimeoutMS=500)
        try:
            await client.admin.command("ping")
        except Exception:
            pytest.skip("local MongoDB not reachable")
        db = client["yield_agent_test_runs"]
        await db.session_runs.delete_one({"_id": "race_session"})
        runs = MongoRunCoordinator(db)
        results = await asyncio.gather(
            runs.start("race_session", "exploratory"),
            runs.start("race_session", "deterministic"),
            return_exceptions=True,
        )
        assert sum(isinstance(x, RunContext) for x in results) == 1
        assert sum(isinstance(x, RunConflict) for x in results) == 1
        await db.session_runs.delete_one({"_id": "race_session"})
        client.close()

    asyncio.run(scenario())
```

- [ ] **Step 6: Run coordinator tests**

Run: `cd 08-YieldAgent && pytest tests/test_hybrid_router.py -v`

Expected: in-memory tests PASS; Mongo test PASS or SKIP only when MongoDB is unavailable.

- [ ] **Step 7: Commit run coordination**

```bash
git add 08-YieldAgent/hybrid_router.py 08-YieldAgent/tests/test_hybrid_router.py
git commit -m "feat(yield): coordinate session runs"
```

---

### Task 6: Structured Mode Classifier, canary selection, and Gateway decisions

**Files:**
- Modify: `08-YieldAgent/hybrid_router.py`
- Modify: `08-YieldAgent/tests/test_hybrid_router.py`

**Interfaces:**
- Consumes: `ModeDecision`, `TurnInput`, run coordinator, recent `ConversationTurn` list
- Produces: `ModeClassifier.classify()`, `HybridRouter.prepare()`, `PreparedRoute`, deterministic fallback

- [ ] **Step 1: Add failing classifier and routing tests**

```python
from types import SimpleNamespace

from orchestration_contracts import ModeDecision, TurnInput
from hybrid_router import HybridRouter, ModeClassifier


class FakeModel:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)
        value = next(self.outputs)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(content=value)


def test_classifier_parses_structured_mode_without_keywords():
    async def scenario():
        model = FakeModel(['{"mode":"exploratory","confidence":0.93,"reason":"next step depends on evidence","requested_capabilities":["yield","wads"]}'])
        decision = await ModeClassifier(model).classify([], "원인을 끝까지 조사해줘")
        assert decision.mode == "exploratory"
        assert len(model.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("raw", ["not-json", RuntimeError("down"), '{"mode":"exploratory","confidence":0.2,"reason":"uncertain","requested_capabilities":[]}'])
def test_classifier_invalid_or_low_confidence_falls_back(raw):
    async def scenario():
        decision = await ModeClassifier(FakeModel([raw])).classify([], "anything")
        assert decision.mode == "deterministic"
    asyncio.run(scenario())


def test_resume_bypasses_classifier():
    async def scenario():
        model = FakeModel([AssertionError("classifier must not run")])
        runs = InMemoryRunCoordinator()
        active = await runs.start("s", "exploratory")
        await runs.pause("s", active.run_id, "g", "exploration_continue")
        router = HybridRouter(ModeClassifier(model), runs, canary_percent=100)
        prepared = await router.prepare(TurnInput.model_validate({
            "session_id": "s", "input_type": "resume",
            "resume": {"interrupt_type": "exploration_continue", "run_id": active.run_id, "gate_id": "g", "action": "continue"},
        }), [])
        assert prepared.mode == "exploratory"
        assert model.calls == []
    asyncio.run(scenario())
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `cd 08-YieldAgent && pytest tests/test_hybrid_router.py -k 'classifier or resume_bypasses' -v`

Expected: FAIL because `ModeClassifier`, `HybridRouter`, and `PreparedRoute` do not exist.

- [ ] **Step 3: Implement JSON-only classification and fallback**

```python
import asyncio
import hashlib
from dataclasses import dataclass
import json
import os

from json_repair import repair_json

from orchestration_contracts import ModeDecision, TurnInput

MODE_SYSTEM = """Classify one user turn. Return one JSON object matching:
{"mode":"direct|deterministic|exploratory","confidence":0.0,"reason":"brief reason","requested_capabilities":[]}
direct: no DB or tool required.
deterministic: concrete known lookup or explicit ordered task.
exploratory: the next worker depends on evidence not yet observed.
Do not create task parameters. Return JSON only."""


class ModeClassifier:
    def __init__(self, model):
        self.model = model

    async def classify(self, conversation: list[dict], query: str) -> ModeDecision:
        try:
            response = await asyncio.to_thread(self.model.invoke, [
                {"role": "system", "content": MODE_SYSTEM},
                {"role": "user", "content": json.dumps({"conversation": conversation[-3:], "query": query}, ensure_ascii=False)},
            ])
            parsed = json.loads(repair_json(str(response.content)))
            decision = ModeDecision.model_validate(parsed)
            if decision.confidence >= 0.65:
                return decision
        except Exception:
            return ModeDecision(mode="deterministic", confidence=0.0, reason="classifier_fallback", requested_capabilities=[])
        return ModeDecision(mode="deterministic", confidence=0.0, reason="classifier_fallback", requested_capabilities=[])


def canary_enabled(session_id: str, percent: int) -> bool:
    bounded = max(0, min(100, percent))
    bucket = int(hashlib.sha256(session_id.encode()).hexdigest()[:8], 16) % 100
    return bucket < bounded


@dataclass(frozen=True)
class PreparedRoute:
    mode: str
    run: RunContext
    thread_id: str
    resume_payload: dict | None = None
    applied_fallback: bool = False
    shadow_mode: str | None = None


class HybridRouter:
    def __init__(self, classifier, runs, canary_percent=0, shadow=False, enabled=True):
        self.classifier = classifier
        self.runs = runs
        self.canary_percent = canary_percent
        self.shadow = shadow
        self.enabled = enabled

    async def prepare(self, turn: TurnInput, conversation: list[dict]) -> PreparedRoute:
        if turn.input_type == "resume":
            gate_type = (
                turn.resume.gate_interrupt_type
                if turn.resume.interrupt_type == "legacy"
                else turn.resume.interrupt_type
            )
            resumed = await self.runs.resume(
                turn.session_id, turn.resume.run_id, turn.resume.gate_id, gate_type,
            )
            return PreparedRoute(
                resumed.lane, resumed, self.runs.thread_id(resumed),
                graph_resume_value(turn.resume),
            )

        decision = await self.classifier.classify(conversation, turn.query)
        selected = decision.mode
        fallback = False
        shadow_mode = selected if self.shadow else None
        if not self.enabled or self.shadow:
            selected, fallback = "deterministic", selected != "deterministic"
        elif selected == "exploratory" and not canary_enabled(turn.session_id, self.canary_percent):
            selected, fallback = "deterministic", True

        if turn.input_type == "cancel_and_start":
            run = await self.runs.cancel_and_start(turn.session_id, turn.expected_pending_run_id, selected)
        else:
            run = await self.runs.start(turn.session_id, selected)
        return PreparedRoute(
            selected, run, self.runs.thread_id(run),
            applied_fallback=fallback, shadow_mode=shadow_mode,
        )


def graph_resume_value(resume):
    if resume.interrupt_type == "exploration_continue":
        return {"action": resume.action, "adjustment": resume.adjustment}
    if resume.interrupt_type == "exploration_question":
        return {"answer": resume.answer}
    if resume.interrupt_type == "missing_param":
        return resume.values
    if resume.interrupt_type == "plan_review":
        return {
            "approve": "그대로 진행",
            "modify": resume.modification,
            "cancel": "취소",
        }[resume.action]
    if resume.interrupt_type in {"task_confirm", "postwads_choice"}:
        return resume.value
    if resume.interrupt_type == "legacy":
        return resume.value
    raise ValueError(f"unsupported interrupt type: {resume.interrupt_type}")
```

Read `HYBRID_ROUTING_ENABLED`, `EXPLORATION_SHADOW_ENABLED`, and `EXPLORATION_CANARY_PERCENT` in server initialization, not inside semantic classification. Pass `enabled=False` when hybrid routing is disabled.

- [ ] **Step 4: Add exact canary tests**

```python
def test_canary_bucket_is_stable_and_bounded():
    assert canary_enabled("same-session", 25) == canary_enabled("same-session", 25)
    assert not canary_enabled("same-session", 0)
    assert canary_enabled("same-session", 100)


def test_shadow_and_disabled_modes_never_execute_exploration():
    async def scenario():
        classifier = ModeClassifier(FakeModel([
            '{"mode":"exploratory","confidence":0.99,"reason":"evidence loop","requested_capabilities":[]}',
            '{"mode":"exploratory","confidence":0.99,"reason":"evidence loop","requested_capabilities":[]}',
        ]))
        shadow = await HybridRouter(classifier, InMemoryRunCoordinator(), 100, shadow=True).prepare(
            TurnInput(session_id="shadow", input_type="new_turn", query="q"), []
        )
        disabled = await HybridRouter(classifier, InMemoryRunCoordinator(), 100, enabled=False).prepare(
            TurnInput(session_id="disabled", input_type="new_turn", query="q"), []
        )
        assert shadow.mode == disabled.mode == "deterministic"
        assert shadow.shadow_mode == "exploratory"
    asyncio.run(scenario())
```

- [ ] **Step 5: Run all router tests**

Run: `cd 08-YieldAgent && pytest tests/test_hybrid_router.py -v`

Expected: all in-memory tests PASS; local-Mongo race PASS or SKIP.

- [ ] **Step 6: Commit classifier and Gateway**

```bash
git add 08-YieldAgent/hybrid_router.py 08-YieldAgent/tests/test_hybrid_router.py
git commit -m "feat(yield): route hybrid agent turns"
```

---

### Task 7: Deterministic binding resolution and Policy Guard

**Files:**
- Create: `08-YieldAgent/exploration_graph.py`
- Create: `08-YieldAgent/tests/test_exploration_policy.py`

**Interfaces:**
- Consumes: `TaskSpec`, prior `TaskRecord` values, `ResultStore.get_field()`, `RunContext`, Registry entries
- Produces: `resolve_bindings()`, `evaluate_policy()`, `PolicyDecision`, `task_fingerprint()`, `reserve_execution()`

- [ ] **Step 1: Write failing binding and safety tests**

```python
# 08-YieldAgent/tests/test_exploration_policy.py
import asyncio
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exploration_graph import evaluate_policy, resolve_bindings, reserve_execution
from orchestration_contracts import InputBinding, RunContext, TaskSpec
from result_contracts import build_result_envelope
from result_store import InMemoryArtifactBackend, InMemoryResultStore

pytestmark = pytest.mark.no_server


def _run_context(worker_executions=0, per_agent=None, remaining=240.0):
    now = datetime.now(timezone.utc)
    return RunContext(
        run_id="r", session_id="s", generation=0, lane="exploratory", status="running",
        worker_executions=worker_executions,
        per_agent_executions=per_agent or {}, started_at=now,
        remaining_active_seconds=remaining, segment_deadline_at=now + timedelta(seconds=remaining),
    )


def test_binding_reads_exact_dotted_path():
    async def scenario():
        store = InMemoryResultStore(InMemoryArtifactBackend())
        source = build_result_envelope(
            source_agent="wads_agent", kind="table", status="success",
            entities={"lot_ids": ["4SS0001", "4SS0002"]},
        )
        await store.save("s", "r", source, [])
        task = TaskSpec(
            task_id="map", agent="map_agent", goal="map", depends_on=["wads"],
            input_bindings=[InputBinding(
                target_param="lot_ids", source_task_id="wads",
                source_path="entities.lot_ids", cardinality="many",
            )],
        )
        bound = await resolve_bindings(task, {
            "wads": {"status": "success", "result_id": source["result_id"]}
        }, store)
        assert bound.params["lot_ids"] == ["4SS0001", "4SS0002"]
    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["empty", "error", "invalid", "skipped"])
def test_required_failed_source_skips_dependent(status):
    async def scenario():
        store = InMemoryResultStore(InMemoryArtifactBackend())
        task = TaskSpec(
            task_id="map", agent="map_agent", goal="map", depends_on=["wads"],
            input_bindings=[InputBinding(
                target_param="lot_ids", source_task_id="wads",
                source_path="entities.lot_ids", cardinality="many",
            )],
        )
        with pytest.raises(ValueError, match="required_upstream_unavailable"):
            await resolve_bindings(task, {"wads": {"status": status, "result_id": None}}, store)
    asyncio.run(scenario())


def test_policy_enforces_gate_budget_agent_limit_and_duplicate():
    task = TaskSpec(task_id="t3", agent="yield_agent", goal="inspect", params={"lotcd": "4SS"})
    assert evaluate_policy(task, _run_context(2), [], set()).needs_gate
    assert evaluate_policy(task, _run_context(5), [], set()).reason == "worker_budget_exhausted"
    assert evaluate_policy(task, _run_context(1, {"yield_agent": 2}), [], set()).reason == "agent_budget_exhausted"
    fingerprint = evaluate_policy(task, _run_context(), [], set()).fingerprint
    assert evaluate_policy(task, _run_context(), [], {fingerprint}).reason == "duplicate_task"


def test_reserve_execution_counts_every_logical_start():
    updated = reserve_execution(_run_context(1, {"yield_agent": 1}), "yield_agent")
    assert updated.worker_executions == 2
    assert updated.per_agent_executions["yield_agent"] == 2
```

- [ ] **Step 2: Run tests and verify import failure**

Run: `cd 08-YieldAgent && pytest tests/test_exploration_policy.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'exploration_graph'`.

- [ ] **Step 3: Implement exact binding, fingerprint, and policy functions**

```python
# Initial section of 08-YieldAgent/exploration_graph.py
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from orchestration_contracts import RunContext, TaskSpec


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""
    needs_gate: bool = False
    fingerprint: str = ""


def task_fingerprint(task: TaskSpec, input_result_ids: list[str]) -> str:
    canonical = json.dumps({
        "agent": task.agent,
        "params": task.params,
        "input_result_ids": sorted(input_result_ids),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def resolve_bindings(task: TaskSpec, records: dict[str, dict], store) -> TaskSpec:
    params = deepcopy(task.params)
    for binding in task.input_bindings:
        source = records.get(binding.source_task_id)
        if not source or source.get("status") not in {"success", "partial"}:
            if binding.required:
                raise ValueError("required_upstream_unavailable")
            continue
        try:
            value = await store.get_field(source["result_id"], binding.source_path)
        except KeyError:
            if binding.required:
                raise ValueError("required_binding_missing")
            continue
        if binding.cardinality == "many" and not isinstance(value, list):
            raise ValueError("binding_cardinality_mismatch")
        if binding.cardinality == "one" and isinstance(value, list):
            if len(value) != 1:
                raise ValueError("binding_cardinality_mismatch")
            value = value[0]
        if binding.required and value in (None, "", []):
            raise ValueError("required_binding_empty")
        params[binding.target_param] = value
    return task.model_copy(update={"params": params})


def evaluate_policy(
    task: TaskSpec,
    run: RunContext,
    input_result_ids: list[str],
    completed_fingerprints: set[str],
) -> PolicyDecision:
    fingerprint = task_fingerprint(task, input_result_ids)
    if run.worker_executions >= 5:
        return PolicyDecision(False, "worker_budget_exhausted", fingerprint=fingerprint)
    if run.per_agent_executions.get(task.agent, 0) >= 2:
        return PolicyDecision(False, "agent_budget_exhausted", fingerprint=fingerprint)
    segment_remaining = max(
        0.0, (run.segment_deadline_at - datetime.now(timezone.utc)).total_seconds()
    )
    if min(run.remaining_active_seconds, segment_remaining) <= 0:
        return PolicyDecision(False, "active_budget_exhausted", fingerprint=fingerprint)
    if fingerprint in completed_fingerprints:
        return PolicyDecision(False, "duplicate_task", fingerprint=fingerprint)
    return PolicyDecision(True, needs_gate=run.worker_executions >= 2, fingerprint=fingerprint)


def reserve_execution(run: RunContext, agent: str) -> RunContext:
    counts = dict(run.per_agent_executions)
    counts[agent] = counts.get(agent, 0) + 1
    return run.model_copy(update={
        "worker_executions": run.worker_executions + 1,
        "per_agent_executions": counts,
    })
```

The graph node that calls `evaluate_policy()` must separately verify that the Registry entry exists and has `risk == "low"`. This pure function must not inspect natural language.

- [ ] **Step 4: Run policy tests**

Run: `cd 08-YieldAgent && pytest tests/test_exploration_policy.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit pure policy logic**

```bash
git add 08-YieldAgent/exploration_graph.py 08-YieldAgent/tests/test_exploration_policy.py
git commit -m "feat(yield): guard exploration tasks"
```

---

### Task 8: Exploration Controller, HITL loop, executor, and grounded synthesis

**Files:**
- Modify: `08-YieldAgent/exploration_graph.py`
- Create: `08-YieldAgent/tests/test_exploration_graph.py`

**Interfaces:**
- Consumes: Registry, Result Store, controller/synthesis LLM, `HybridState`, policy functions
- Produces: `ExplorationDependencies`, `build_exploration_workflow()`, compiled workflow with controller/executor/HITL/synthesis nodes

- [ ] **Step 1: Write failing graph tests using fake model, Registry, and Result Store**

```python
# 08-YieldAgent/tests/test_exploration_graph.py
import asyncio
from datetime import datetime, timedelta, timezone
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from langgraph.checkpoint.memory import MemorySaver
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_registry import ExecutionOutput
from exploration_graph import ExplorationDependencies, StaleRunError, build_exploration_workflow
from orchestration_contracts import RunContext
from result_contracts import build_result_envelope
from result_store import InMemoryArtifactBackend, InMemoryResultStore

pytestmark = pytest.mark.no_server


class SequenceModel:
    def __init__(self, outputs):
        self.outputs = iter(outputs)

    def invoke(self, messages, **kwargs):
        return SimpleNamespace(content=next(self.outputs))


class FakeRegistry:
    def __init__(self, status="success"):
        self.calls = []
        self.status = status

    def get(self, name):
        return SimpleNamespace(risk="low")

    async def execute(self, task, run_id, input_result_ids, config):
        self.calls.append(task)
        envelope = build_result_envelope(
            source_agent=task.agent, kind="summary", status=self.status,
            summary=f"evidence from {task.agent}",
            entities={"lot_ids": ["4SS0001"]},
            provenance={"task_id": task.task_id, "run_id": run_id},
        )
        envelope["result_id"] = f"result_{task.task_id}"
        return ExecutionOutput(envelope, [])


def _initial_state(run_id="run1"):
    now = datetime.now(timezone.utc)
    run = RunContext(
        run_id=run_id, session_id="s", generation=0, lane="exploratory", status="running",
        started_at=now, segment_deadline_at=now + timedelta(seconds=240),
    )
    return {
        "conversation": [],
        "turn": {"query": "root cause", "user_id": "", "mode": "exploratory", "started_at": now.isoformat()},
        "run": run.model_dump(mode="json"), "tasks": [], "result_index": [],
        "pending_gate": None, "response": None, "controller_decision": None,
        "controller_failures": 0, "policy": None, "current_task": None,
        "controller_answer": "", "controller_cycles": 0,
        "post_execute_route": "controller",
        "policy_violation": "", "policy_violation_count": 0, "adjustment": "",
    }


def test_two_workers_execute_then_finish_with_result_ids():
    async def scenario():
        model = SequenceModel([
            json.dumps({"action":"run_agent","rationale":"need yield","task":{"task_id":"t1","agent":"yield_agent","goal":"yield","params":{"lotcd":"4SS"}}}),
            json.dumps({"action":"run_agent","rationale":"need wads","task":{"task_id":"t2","agent":"wads_agent","goal":"wads","params":{"lotcd":"4SS"}}}),
            json.dumps({"action":"finish","rationale":"enough","evidence_result_ids":["result_t1","result_t2"]}),
            json.dumps({"claims":[{"text":"grounded answer","result_ids":["result_t1"],"entity_refs":{"lot_ids":["4SS0001"]}}],"limitations":[]}),
        ])
        store = InMemoryResultStore(InMemoryArtifactBackend())
        registry = FakeRegistry()
        graph = build_exploration_workflow(ExplorationDependencies(model, registry, store)).compile(checkpointer=MemorySaver())
        result = await graph.ainvoke(_initial_state(), {"configurable": {"thread_id": "s:0:exploratory"}})
        assert len(registry.calls) == 2
        assert len(result["result_index"]) == 2
        assert result["response"]["content"] == "grounded answer"
    asyncio.run(scenario())


def test_third_worker_interrupts_before_execution():
    async def scenario():
        model = SequenceModel([
            json.dumps({"action":"run_agent","rationale":"one","task":{"task_id":"t1","agent":"yield_agent","goal":"g","params":{"lotcd":"4SS"}}}),
            json.dumps({"action":"run_agent","rationale":"two","task":{"task_id":"t2","agent":"wads_agent","goal":"g","params":{"lotcd":"4SS"}}}),
            json.dumps({"action":"run_agent","rationale":"three","task":{"task_id":"t3","agent":"map_agent","goal":"g","params":{"lot_ids":["4SS0001"]}}}),
        ])
        registry = FakeRegistry()
        graph = build_exploration_workflow(ExplorationDependencies(
            model, registry, InMemoryResultStore(InMemoryArtifactBackend())
        )).compile(checkpointer=MemorySaver())
        await graph.ainvoke(_initial_state(), {"configurable": {"thread_id": "gate-case"}})
        snapshot = graph.get_state({"configurable": {"thread_id": "gate-case"}})
        assert len(registry.calls) == 2
        assert snapshot.tasks[0].interrupts[0].value["type"] == "exploration_continue"
    asyncio.run(scenario())


def test_error_with_stop_policy_synthesizes_without_downstream_worker():
    async def scenario():
        model = SequenceModel([
            json.dumps({"action":"run_agent","rationale":"wads","task":{
                "task_id":"t1","agent":"wads_agent","goal":"g","params":{"lotcd":"4SS"},
                "on_failure":"stop"
            }}),
            json.dumps({"claims":[],"limitations":["WADS 실행 실패"]}),
        ])
        registry = FakeRegistry(status="error")
        graph = build_exploration_workflow(ExplorationDependencies(
            model, registry, InMemoryResultStore(InMemoryArtifactBackend())
        )).compile()
        result = await graph.ainvoke(_initial_state())
        assert len(registry.calls) == 1
        assert result["response"]["limitations"] == ["WADS 실행 실패"]
    asyncio.run(scenario())


def test_late_worker_result_is_not_persisted():
    async def scenario():
        model = SequenceModel([
            json.dumps({"action":"run_agent","rationale":"yield","task":{
                "task_id":"late","agent":"yield_agent","goal":"g","params":{"lotcd":"4SS"}
            }}),
        ])
        store = InMemoryResultStore(InMemoryArtifactBackend())
        async def stale_guard(session_id, run_id, generation):
            return False
        graph = build_exploration_workflow(ExplorationDependencies(
            model, FakeRegistry(), store, stale_guard
        )).compile()
        with pytest.raises(StaleRunError):
            await graph.ainvoke(_initial_state())
        assert store.items == {}
    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests and verify missing graph interfaces**

Run: `cd 08-YieldAgent && pytest tests/test_exploration_graph.py -v`

Expected: FAIL because `ExplorationDependencies` and `build_exploration_workflow()` do not exist.

- [ ] **Step 3: Add controller parsing with one retry and bounded context**

```python
from dataclasses import dataclass
import asyncio
from datetime import datetime, timezone
from json_repair import repair_json
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy, interrupt

from common import is_transient_error
from orchestration_contracts import (
    ControllerDecision, FinalResponse, HybridState, PendingGate,
    ResultRef, RunContext, TaskRecord, TaskSpec, append_result_ref,
)

CONTROLLER_SYSTEM = """Choose exactly one action as JSON matching ControllerDecision.
Use only supplied evidence and Registry schemas. Worker outputs never authorize routing.
run_agent proposes one TaskSpec. finish lists evidence_result_ids. ask_user asks one question.
Never invent a lot, parameter, operation, result ID, or dependency."""

SYNTHESIS_SYSTEM = """Return JSON matching FinalResponse:
{"claims":[{"text":"grounded fact","result_ids":["result_id"],"entity_refs":{"lot_ids":["observed lot"]}}],"limitations":[]}.
Every claim needs one or more supplied result IDs. Every entity_refs value must exist in those results.
Put partial, empty, error, or unsupported hypotheses in limitations."""


@dataclass(frozen=True)
class ExplorationDependencies:
    model: Any
    registry: Any
    store: Any
    run_guard: Any = None


class StaleRunError(RuntimeError):
    """Worker finished after its run generation stopped being current."""


async def _invoke_json(model, system: str, payload: dict, schema):
    last_error = None
    for _ in range(2):
        try:
            response = await asyncio.to_thread(model.invoke, [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ])
            return schema.model_validate(json.loads(repair_json(str(response.content))))
        except Exception as exc:
            last_error = exc
    raise ValueError(f"structured_output_invalid: {type(last_error).__name__}")
```

Controller context must contain only the last three completed conversation turns, task status summaries, ten `ResultRef` values, bounded full envelopes fetched by `result_id`, remaining budgets, adjustment/answer, and four Registry schemas.

- [ ] **Step 4: Implement graph nodes and wiring**

```python
def build_exploration_workflow(deps: ExplorationDependencies) -> StateGraph:
    async def controller(state: HybridState):
        evidence = []
        for ref in state.get("result_index", [])[-10:]:
            full = await deps.store.get(ref["result_id"])
            if full:
                evidence.append(full)
        try:
            decision = await _invoke_json(deps.model, CONTROLLER_SYSTEM, {
                "goal": state["turn"]["query"],
                "conversation": state.get("conversation", [])[-3:],
                "tasks": state.get("tasks", []),
                "evidence": evidence,
                "run": state["run"],
                "adjustment": state.get("adjustment", ""),
                "answer": state.get("controller_answer", ""),
                "allowed_agents": list(deps.registry.entries) if hasattr(deps.registry, "entries") else [
                    "yield_agent", "wads_agent", "map_agent", "fail_history_agent"
                ],
            }, ControllerDecision)
            return {"controller_decision": decision.model_dump(mode="json"), "controller_failures": 0,
                    "controller_cycles": state.get("controller_cycles", 0) + 1,
                    "adjustment": "", "controller_answer": ""}
        except ValueError:
            return {"controller_decision": {"action": "finish", "rationale": "controller_invalid",
                                            "task": None, "question": None, "evidence_result_ids": []},
                    "controller_failures": state.get("controller_failures", 0) + 1}

    def route_controller(state: HybridState):
        return {"run_agent": "policy", "ask_user": "ask_user", "finish": "synthesize"}[
            state["controller_decision"]["action"]
        ]

    async def policy(state: HybridState):
        task = TaskSpec.model_validate(state["controller_decision"]["task"])
        records = {r["spec"]["task_id"]: r for r in state.get("tasks", [])}
        if task.task_id in records:
            reason = "duplicate_task_id"
            same = state.get("policy_violation") == reason
            return {
                "policy": {"allowed": False, "reason": reason},
                "policy_violation": reason,
                "policy_violation_count": state.get("policy_violation_count", 0) + 1 if same else 1,
            }
        try:
            bound = await resolve_bindings(task, records, deps.store)
        except ValueError as exc:
            skipped = TaskRecord(spec=task, status="skipped", error_code=str(exc))
            reason = str(exc)
            same = state.get("policy_violation") == reason
            return {"tasks": [*state.get("tasks", []), skipped.model_dump(mode="json")],
                    "policy": {"allowed": False, "reason": reason},
                    "policy_violation": reason,
                    "policy_violation_count": state.get("policy_violation_count", 0) + 1 if same else 1}
        entry = deps.registry.get(bound.agent)
        input_ids = [records[d]["result_id"] for d in bound.depends_on if records.get(d, {}).get("result_id")]
        fingerprints = {r.get("fingerprint") for r in state.get("tasks", []) if r.get("fingerprint")}
        decision = evaluate_policy(bound, RunContext.model_validate(state["run"]), input_ids, fingerprints)
        if entry.risk != "low":
            decision = PolicyDecision(False, "agent_not_low_risk", fingerprint=decision.fingerprint)
        reason = decision.reason
        same = bool(reason) and state.get("policy_violation") == reason
        return {"policy": {**decision.__dict__, "input_result_ids": input_ids},
                "current_task": bound.model_dump(mode="json"),
                "policy_violation": reason,
                "policy_violation_count": (
                    state.get("policy_violation_count", 0) + 1 if same else (1 if reason else 0)
                )}

    def route_policy(state: HybridState):
        policy_value = state["policy"]
        if not policy_value["allowed"]:
            return "synthesize" if state.get("policy_violation_count", 0) >= 2 else "controller"
        return "depth_gate" if policy_value["needs_gate"] else "execute"

    def depth_gate(state: HybridState):
        task = TaskSpec.model_validate(state["current_task"])
        gate_id = f"gate_{uuid.uuid4().hex}"
        payload = {
            "type": "exploration_continue", "run_id": state["run"]["run_id"], "gate_id": gate_id,
            "findings": state.get("result_index", [])[-10:], "proposed_task": task.model_dump(mode="json"),
            "remaining_worker_executions": 5 - state["run"]["worker_executions"],
            "options": [{"value":"continue","label":"계속"}, {"value":"adjust","label":"조정"},
                        {"value":"stop","label":"중단 후 현재 결과 정리"}],
        }
        answer = interrupt(payload)
        action = answer.get("action")
        if action == "continue":
            return Command(goto="execute", update={"pending_gate": None})
        if action == "adjust":
            return Command(goto="controller", update={"pending_gate": None, "adjustment": answer.get("adjustment", "")})
        return Command(goto="synthesize", update={"pending_gate": None})

    def ask_user(state: HybridState):
        gate_id = f"gate_{uuid.uuid4().hex}"
        answer = interrupt({
            "type": "exploration_question", "run_id": state["run"]["run_id"], "gate_id": gate_id,
            "message": state["controller_decision"]["question"],
        })
        return {"controller_answer": answer.get("answer", "")}

    async def execute(state: HybridState, config):
        task = TaskSpec.model_validate(state["current_task"])
        run = reserve_execution(RunContext.model_validate(state["run"]), task.agent)
        output = await deps.registry.execute(task, run.run_id, state["policy"]["input_result_ids"], config)
        if deps.run_guard and not await deps.run_guard(run.session_id, run.run_id, run.generation):
            raise StaleRunError(run.run_id)
        saved = await deps.store.save(run.session_id, run.run_id, output.envelope, output.artifacts)
        ref = ResultRef(
            result_id=saved["result_id"], task_id=task.task_id, source_agent=task.agent,
            status=saved["status"], summary_preview=saved.get("summary", "")[:500],
            entity_keys=sorted((saved.get("entities") or {}).keys()), artifact_refs=saved.get("artifact_refs", [])[:10],
        )
        record = TaskRecord(
            spec=task, status=saved["status"], result_id=saved["result_id"],
            fingerprint=state["policy"]["fingerprint"],
        ).model_dump(mode="json")
        next_route = "controller"
        decision_update = state.get("controller_decision")
        if saved["status"] in {"error", "invalid"}:
            if task.on_failure == "stop":
                next_route = "synthesize"
            elif task.on_failure == "ask_user":
                next_route = "ask_user"
                decision_update = {
                    "action": "ask_user", "rationale": "worker_failed",
                    "task": None,
                    "question": f"{task.agent} 실행이 실패했습니다. 다른 증거 경로로 조사를 계속할까요?",
                    "evidence_result_ids": [],
                }
        return {"run": run.model_dump(mode="json"), "tasks": [*state.get("tasks", []), record],
                "controller_decision": decision_update, "post_execute_route": next_route,
                "result_index": [r.model_dump(mode="json") for r in append_result_ref(
                    [ResultRef.model_validate(r) for r in state.get("result_index", [])], ref
                )]}

    async def synthesize(state: HybridState):
        all_evidence = [await deps.store.get(r["result_id"]) for r in state.get("result_index", [])]
        all_evidence = [e for e in all_evidence if e]
        requested = set(state.get("controller_decision", {}).get("evidence_result_ids") or [])
        known_all = {e["result_id"] for e in all_evidence}
        invalid_requested = sorted(requested - known_all)
        evidence = [e for e in all_evidence if not requested or e["result_id"] in requested]
        try:
            response = await _invoke_json(deps.model, SYNTHESIS_SYSTEM, {
                "goal": state["turn"]["query"], "evidence": evidence,
                "statuses": [{"result_id": e["result_id"], "status": e["status"]} for e in evidence],
            }, FinalResponse)
        except ValueError:
            response = FinalResponse(
                claims=[], limitations=["확인된 증거만으로 답변을 합성하지 못했습니다."],
            )
        known = {e["result_id"] for e in evidence}
        by_id = {e["result_id"]: e for e in evidence}
        valid_claims = []
        rejected = []
        for claim in response.claims:
            if not set(claim.result_ids) <= known:
                rejected.append(f"근거 result ID가 유효하지 않아 제외: {claim.text}")
                continue
            referenced_entities: dict[str, set[str]] = {}
            for result_id in claim.result_ids:
                for key, values in (by_id[result_id].get("entities") or {}).items():
                    referenced_entities.setdefault(key, set()).update(str(v) for v in values)
            if any(
                not set(str(v) for v in values) <= referenced_entities.get(key, set())
                for key, values in claim.entity_refs.items()
            ):
                rejected.append(f"근거 없는 entity가 있어 제외: {claim.text}")
                continue
            valid_claims.append(claim)
        status_limitations = [
            f"{e['source_agent']} 결과 상태={e['status']}: {e.get('summary', '')}"
            for e in evidence if e["status"] in {"partial", "empty", "error", "invalid"}
        ]
        response = FinalResponse(
            claims=valid_claims,
            limitations=[
                *response.limitations,
                *status_limitations,
                *rejected,
                *([f"존재하지 않는 evidence result IDs 제외: {invalid_requested}"] if invalid_requested else []),
            ],
        )
        return {"response": response.model_dump(mode="json")}

    def route_after_execute(state: HybridState):
        return state.get("post_execute_route", "controller")

    workflow = StateGraph(HybridState)
    workflow.add_node("controller", controller)
    workflow.add_node("policy", policy)
    workflow.add_node("depth_gate", depth_gate)
    workflow.add_node("ask_user", ask_user)
    workflow.add_node("execute", execute, retry_policy=RetryPolicy(max_attempts=3, retry_on=is_transient_error))
    workflow.add_node("synthesize", synthesize)
    workflow.add_edge(START, "controller")
    workflow.add_conditional_edges("controller", route_controller)
    workflow.add_conditional_edges("policy", route_policy)
    workflow.add_conditional_edges("execute", route_after_execute, {
        "controller": "controller", "ask_user": "ask_user", "synthesize": "synthesize",
    })
    workflow.add_edge("ask_user", "controller")
    workflow.add_edge("synthesize", END)
    return workflow
```

Add imports `json`, `uuid`, and `Any`. Task 1 already defines every `HybridState` key used by these nodes. The code above records a first policy denial as count one, terminates on a repeated identical denial at count two, and resets a different reason to one.

- [ ] **Step 5: Add resume tests for continue, adjust with new gate, and stop**

Use `Command(resume={"action": "continue"})`, `Command(resume={"action": "adjust", "adjustment": "Map 대신 Fail History"})`, and `Command(resume={"action": "stop"})` against separate `MemorySaver` checkpoints. Assert:

```python
continued = await graph.ainvoke(Command(resume={"action": "continue"}), config)
assert len(registry.calls) == 3

adjusted = await graph.ainvoke(Command(resume={"action": "adjust", "adjustment": "Map 대신 Fail History"}), config)
assert adjusted["adjustment"] == ""  # consumed by Controller

stopped = await graph.ainvoke(Command(resume={"action": "stop"}), config)
assert stopped["response"] is not None
```

Create a fresh graph/thread per assertion. For adjust, assert the next interrupt has a different `gate_id` before resuming it.

- [ ] **Step 6: Run graph and policy tests**

Run: `cd 08-YieldAgent && pytest tests/test_exploration_policy.py tests/test_exploration_graph.py -v`

Expected: all tests PASS; no worker call occurs before the third-execution interrupt.

- [ ] **Step 7: Commit Exploration Graph**

```bash
git add 08-YieldAgent/exploration_graph.py 08-YieldAgent/orchestration_contracts.py 08-YieldAgent/tests/test_exploration_graph.py
git commit -m "feat(yield): add exploration graph"
```

---

### Task 9: Server lane integration and explicit request protocol

**Files:**
- Modify: `08-YieldAgent/models.py:14-109`
- Modify: `08-YieldAgent/agent_server.py:26-1054`
- Create: `08-YieldAgent/tests/test_hybrid_server.py`

**Interfaces:**
- Consumes: `HybridRouter`, deterministic `workflow`, exploration workflow, Result Store, run coordinator
- Produces: canonical `/chat/stream`, correlated SSE, direct/deterministic/exploratory lane streams, exact 409 responses

- [ ] **Step 1: Write failing request/SSE model tests**

```python
# 08-YieldAgent/tests/test_hybrid_server.py
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import ChatRequest, InterruptEvent, StreamStartEvent

pytestmark = pytest.mark.no_server


def test_canonical_request_and_correlated_events():
    request = ChatRequest(session_id="s", query="q", input_type="new_turn")
    assert request.to_turn_input().input_type == "new_turn"
    start = StreamStartEvent(session_id="s", query="q", run_id="r", generation=2, lane="exploratory")
    assert start.model_dump()["lane"] == "exploratory"


def test_interrupt_requires_gate_identity_for_canonical_flow():
    event = InterruptEvent(
        interrupt_type="exploration_continue", param="", message="continue?",
        run_id="r", gate_id="g", lane="exploratory",
    )
    assert event.gate_id == "g"


def test_cancel_and_start_requires_expected_pending_run():
    with pytest.raises(ValidationError):
        ChatRequest(session_id="s", query="new", input_type="cancel_and_start").to_turn_input()
```

- [ ] **Step 2: Run focused tests and verify model failure**

Run: `cd 08-YieldAgent && pytest tests/test_hybrid_server.py -v`

Expected: FAIL because canonical fields and correlation fields do not exist.

- [ ] **Step 3: Extend request and SSE models additively**

```python
# Replace ChatRequest in models.py; keep resume_value during migration
class ChatRequest(BaseModel):
    query: str = ""
    session_id: str
    user_id: str = ""
    input_type: Literal["new_turn", "resume", "cancel_and_start"] = "new_turn"
    resume: dict[str, Any] | None = None
    expected_pending_run_id: str | None = None
    resume_value: str | dict[str, Any] | None = None

    def to_turn_input(self) -> TurnInput:
        return TurnInput.model_validate({
            "session_id": self.session_id,
            "user_id": self.user_id,
            "input_type": self.input_type,
            "query": self.query,
            "resume": self.resume,
            "expected_pending_run_id": self.expected_pending_run_id,
        })

# Add defaults to SSE models so old clients remain valid
run_id: str = ""
generation: int = 0
lane: str = "deterministic"

# Add to InterruptEvent only
gate_id: str = ""

# Add to MessageEvent
result_ids: list[str] = Field(default_factory=list)
```

Apply the correlation fields to `StreamStartEvent`, `NodeCompleteEvent`, `MessageEvent`, `StatusEvent`, `InterruptEvent`, `StreamEndEvent`, and `ErrorEvent`.

- [ ] **Step 4: Initialize hybrid dependencies in FastAPI lifespan**

```python
# Inside lifespan after motor_db and MongoDBSaver are ready
artifact_backend = GridFSArtifactBackend(app.state.motor_db)
result_store = ResultStore(app.state.motor_db, artifact_backend)
await result_store.ensure_indexes()
runs = MongoRunCoordinator(app.state.motor_db)
classifier = ModeClassifier(get_llm())
router = HybridRouter(
    classifier,
    runs,
    canary_percent=int(os.getenv("EXPLORATION_CANARY_PERCENT", "0")),
    shadow=os.getenv("EXPLORATION_SHADOW_ENABLED", "0") == "1",
    enabled=os.getenv("HYBRID_ROUTING_ENABLED", "0") == "1",
)
registry = build_default_registry()
exploration_workflow = build_exploration_workflow(
    ExplorationDependencies(get_llm(), registry, result_store, runs.is_current)
)
app.state.deterministic_graph = workflow.compile(checkpointer=checkpointer)
app.state.exploration_graph = exploration_workflow.compile(checkpointer=checkpointer)
app.state.result_store = result_store
app.state.runs = runs
app.state.hybrid_router = router
app.state.direct_model = get_llm()
```

`enabled=False` forces new turns to deterministic while still accepting canonical resume for existing pending runs.

- [ ] **Step 5: Remove blank-drain intent behavior and prepare the route before streaming**

Delete the import and use of `_resume_is_interrupt_answer`. Delete the block that invokes `Command(resume="")` and mutates `request.resume_value`.

At the start of `chat_stream`:

```python
async def _adopt_legacy_session_if_needed(app, session_id: str) -> None:
    if await app.state.runs.get(session_id):
        return
    legacy_config = {"configurable": {"thread_id": session_id}}
    snapshot = await app.state.deterministic_graph.aget_state(legacy_config)
    if snapshot and snapshot.values:
        pending = _pending_interrupt_from_state(snapshot)
        await app.state.runs.adopt_legacy(session_id, pending or None)


await _adopt_legacy_session_if_needed(req.app, request.session_id)
conversation = await _load_completed_conversation(db, request.session_id, limit=3)
turn_input = _legacy_or_canonical_turn_input(request, pending_record=await runs.get(request.session_id))
try:
    prepared = await req.app.state.hybrid_router.prepare(turn_input, conversation)
except RunConflict as exc:
    raise HTTPException(status_code=409, detail={"code": exc.code, "run_id": exc.run_id})
except StaleGate:
    raise HTTPException(status_code=409, detail={"code": "stale_gate"})

if turn_input.input_type == "cancel_and_start":
    await req.app.state.result_store.cancel_run(turn_input.expected_pending_run_id)

graph = (
    req.app.state.exploration_graph
    if prepared.mode == "exploratory"
    else req.app.state.deterministic_graph
)
config = {
    "configurable": {
        "thread_id": prepared.thread_id,
        "trace_id": trace_id,
        "turn_id": turn_id,
        "run_id": prepared.run.run_id,
        "generation": prepared.run.generation,
        "lane": prepared.mode,
    },
    "recursion_limit": 30,
}
```

`_legacy_or_canonical_turn_input()` rules are mechanical:

1. If `request.resume` exists, use `request.to_turn_input()`.
2. If legacy `resume_value` exists and one pending record exists, pass it unchanged to the deterministic graph and construct a canonical internal `resume` using the pending record identity. Do not classify its text.
3. Otherwise use `request.to_turn_input()`.

```python
def _legacy_or_canonical_turn_input(request: ChatRequest, pending_record: dict | None) -> TurnInput:
    if request.resume is not None:
        return request.to_turn_input()
    if request.resume_value is not None:
        run = (pending_record or {}).get("run") or {}
        gate = (pending_record or {}).get("gate") or {}
        if run.get("status") != "waiting_hitl" or not gate.get("gate_id"):
            raise StaleGate(request.session_id)
        return TurnInput.model_validate({
            "session_id": request.session_id,
            "user_id": request.user_id,
            "input_type": "resume",
            "resume": {
                "interrupt_type": "legacy",
                "run_id": run["run_id"],
                "gate_id": gate["gate_id"],
                "gate_interrupt_type": gate["interrupt_type"],
                "value": request.resume_value,
            },
        })
    return request.to_turn_input()


async def _load_completed_conversation(db, session_id: str, limit: int = 3) -> list[dict]:
    docs = await db.chat_turns.find(
        {"session_id": session_id}, {"_id": 0, "query": 1, "messages": 1}
    ).sort("timestamp", -1).limit(limit).to_list(length=limit)
    turns = []
    for doc in reversed(docs):
        assistants = [m for m in doc.get("messages", []) if m.get("content")]
        if assistants:
            turns.append({"user": doc.get("query", ""), "assistant": assistants[-1]["content"]})
    return turns[-3:]


async def _initial_exploration_state(
    request: ChatRequest, run: RunContext, conversation: list[dict], result_store,
) -> dict:
    now = datetime.now(timezone.utc)
    recent_envelopes = await result_store.list_recent(request.session_id, 10)
    refs = [result_ref_from_envelope(envelope) for envelope in recent_envelopes]
    history_tasks = []
    for envelope, ref in zip(recent_envelopes, refs, strict=False):
        provenance = envelope.get("provenance") or {}
        spec = TaskSpec(
            task_id=ref.task_id, agent=ref.source_agent,
            goal=provenance.get("task_goal") or envelope.get("title") or ref.source_agent,
            params=provenance.get("execution_params") or {},
        )
        history_tasks.append(TaskRecord(
            spec=spec, status=ref.status, result_id=ref.result_id,
        ).model_dump(mode="json"))
    return {
        "conversation": conversation[-3:],
        "turn": {"query": request.query, "user_id": request.user_id,
                 "mode": "exploratory", "started_at": now.isoformat()},
        "run": run.model_dump(mode="json"), "tasks": history_tasks,
        "result_index": [ref.model_dump(mode="json") for ref in refs],
        "pending_gate": None, "response": None, "controller_decision": None,
        "controller_failures": 0, "policy": None, "current_task": None,
        "controller_answer": "", "controller_cycles": 0,
        "post_execute_route": "controller",
        "policy_violation": "", "policy_violation_count": 0, "adjustment": "",
    }
```

- [ ] **Step 6: Add direct and exploration stream branches without rewriting deterministic SSE parsing**

```python
if prepared.mode == "direct":
    async def direct_generate():
        segment_started = time.monotonic()
        yield _sse(StreamStartEvent(
            session_id=request.session_id, query=request.query,
            run_id=prepared.run.run_id, generation=prepared.run.generation, lane="direct",
        ))
        answer = await asyncio.to_thread(req.app.state.direct_model.invoke, [
            {"role": "system", "content": "Answer without tools. Do not claim database facts."},
            {"role": "user", "content": request.query},
        ])
        yield _sse(MessageEvent(
            agent="direct", content=str(answer.content),
            run_id=prepared.run.run_id, generation=prepared.run.generation, lane="direct",
        ))
        await req.app.state.motor_db.chat_turns.insert_one({
            "session_id": request.session_id, "query": request.query,
            "run_id": prepared.run.run_id, "generation": prepared.run.generation, "lane": "direct",
            "messages": [{"agent": "direct", "content": str(answer.content), "artifacts": []}],
            "artifacts": [], "suggestion": "", "step_count": 0,
            "elapsed": round(time.monotonic() - segment_started, 1),
            "timestamp": datetime.now(timezone.utc),
        })
        await runs.finish(
            request.session_id, prepared.run.run_id,
            active_elapsed=time.monotonic() - segment_started,
        )
        yield _sse(StreamEndEvent(run_id=prepared.run.run_id, generation=prepared.run.generation, lane="direct"))
    return StreamingResponse(direct_generate(), media_type="text/event-stream")

if prepared.mode == "exploratory":
    if turn_input.input_type == "resume":
        stream_input = Command(resume=prepared.resume_payload)
    else:
        stream_input = await _initial_exploration_state(
            request, prepared.run, conversation, req.app.state.result_store
        )
    return StreamingResponse(
        _stream_exploration(req.app, request, prepared, stream_input, config),
        media_type="text/event-stream",
    )
```

After these two early-return branches, the function is deterministic-only. Make this exact two-line replacement and keep the existing new-turn state literal unchanged:

```diff
-    if request.resume_value is not None:
-        stream_input = Command(resume=request.resume_value)
+    if prepared.resume_payload is not None:
+        stream_input = Command(resume=prepared.resume_payload)
     else:
         # existing fresh deterministic state literal remains unchanged
```

Implement `_stream_exploration()`:

```python
async def _stream_exploration(app, request, prepared, stream_input, config):
    graph = app.state.exploration_graph
    runs = app.state.runs
    store = app.state.result_store
    segment_started = time.monotonic()
    step = 0
    seen_results: set[str] = set()
    final_response = None
    interrupted = False

    yield _sse(StreamStartEvent(
        session_id=request.session_id, query=request.query,
        run_id=prepared.run.run_id, generation=prepared.run.generation, lane="exploratory",
    ))
    try:
        async for mode, data in graph.astream(
            stream_input, config=config, stream_mode=["updates", "custom"]
        ):
            if mode == "custom":
                kind = data.get("kind")
                event_data = {k: v for k, v in data.items() if k != "kind"}
                event_data.update({"run_id": prepared.run.run_id,
                                   "generation": prepared.run.generation, "lane": "exploratory"})
                if kind == "thinking": yield _sse(ThinkingEvent(**event_data))
                if kind == "token": yield _sse(TokenEvent(**event_data))
                if kind == "status": yield _sse(StatusEvent(**event_data))
                continue
            if not isinstance(data, dict):
                continue
            if "__interrupt__" in data:
                intr = next(iter(data["__interrupt__"]), None)
                payload = getattr(intr, "value", None) if intr else None
                if payload:
                    await runs.pause(
                        request.session_id, prepared.run.run_id,
                        payload["gate_id"], payload["type"],
                        active_elapsed=time.monotonic() - segment_started,
                    )
                    yield _sse(InterruptEvent(
                        interrupt_type=payload["type"], param=payload.get("param", ""),
                        message=payload.get("message", ""), route=payload.get("route", ""),
                        options=payload.get("options", []), fields=payload.get("fields", []),
                        run_id=prepared.run.run_id, gate_id=payload["gate_id"],
                        generation=prepared.run.generation, lane="exploratory",
                    ))
                    interrupted = True
                continue
            for node, update in data.items():
                if not isinstance(update, dict):
                    continue
                step += 1
                yield _sse(NodeCompleteEvent(
                    node=node, step=step, run_id=prepared.run.run_id,
                    generation=prepared.run.generation, lane="exploratory",
                ))
                for ref in update.get("result_index", []):
                    if ref["result_id"] in seen_results:
                        continue
                    seen_results.add(ref["result_id"])
                    envelope = await store.get(ref["result_id"])
                    for artifact_ref in (envelope or {}).get("artifact_refs", []):
                        body = await store.artifacts.get(artifact_ref["artifact_id"])
                        if body is None:
                            continue
                        mime = artifact_ref.get("mime", "text/html")
                        data_value = (
                            body.decode("utf-8", errors="replace")
                            if mime.startswith("text/")
                            else f"data:{mime};base64,{base64.b64encode(body).decode()}"
                        )
                        yield _sse(ArtifactEvent(
                            artifact_id=artifact_ref["artifact_id"],
                            artifact_type=artifact_ref.get("artifact_type") or "html",
                            mime=mime, title=artifact_ref.get("title", ""),
                            agent=ref["source_agent"], data=data_value, step=step,
                            run_id=prepared.run.run_id, generation=prepared.run.generation,
                            lane="exploratory",
                        ))
                if update.get("response"):
                    final_response = update["response"]
        if interrupted:
            return
        current = await runs.get(request.session_id)
        current_run = (current or {}).get("run") or {}
        if current_run.get("run_id") != prepared.run.run_id or current_run.get("generation") != prepared.run.generation:
            emit_trace_event("late_result_discarded", source="agent_server",
                             payload={"run_id": prepared.run.run_id, "generation": prepared.run.generation})
            return
        if final_response:
            yield _sse(MessageEvent(
                agent="exploration", content=final_response["content"],
                result_ids=final_response["result_ids"], step=step,
                run_id=prepared.run.run_id, generation=prepared.run.generation,
                lane="exploratory",
            ))
        elapsed = time.monotonic() - segment_started
        await app.state.motor_db.chat_turns.insert_one({
            "session_id": request.session_id, "query": request.query,
            "run_id": prepared.run.run_id, "generation": prepared.run.generation, "lane": "exploratory",
            "messages": [{"agent": "exploration", "content": final_response["content"],
                          "artifacts": [], "result_ids": final_response["result_ids"],
                          "claims": final_response["claims"]}]
            if final_response else [],
            "artifacts": [], "suggestion": "", "step_count": step,
            "elapsed": round(elapsed, 1), "timestamp": datetime.now(timezone.utc),
        })
        await runs.finish(request.session_id, prepared.run.run_id, "completed", active_elapsed=elapsed)
        yield _sse(StreamEndEvent(
            total_steps=step, elapsed=round(elapsed, 1), run_id=prepared.run.run_id,
            generation=prepared.run.generation, lane="exploratory",
        ))
    except StaleRunError:
        emit_trace_event(
            "late_result_discarded", source="agent_server",
            payload={"run_id": prepared.run.run_id, "generation": prepared.run.generation},
        )
        return
    except Exception as exc:
        await runs.finish(
            request.session_id, prepared.run.run_id, "failed",
            active_elapsed=time.monotonic() - segment_started,
        )
        yield _sse(ErrorEvent(
            message=to_user_message(exc), run_id=prepared.run.run_id,
            generation=prepared.run.generation, lane="exploratory",
        ))
```

Import `base64`. Extend `_interrupt_sse_events()` to accept `run_id`, `gate_id`, `generation`, and `lane`, and pass them into deterministic `InterruptEvent` objects. Exploration streaming emits:

- one `node_complete` per node
- artifact events by loading GridFS refs from Result Store
- one final `message` from `response.content` with `response.result_ids`
- one interrupt with `run_id`, `gate_id`, and lane

Do not duplicate the existing deterministic node/artifact parsing logic.

- [ ] **Step 7: Persist run state at every terminal or interrupt edge**

```python
segment_elapsed = time.monotonic() - segment_started
if interrupt_emitted:
    await runs.pause(
        request.session_id, prepared.run.run_id,
        pending_gate_id,
        pending_interrupt_data.get("interrupt_type", pending_interrupt_data.get("type", "missing_param")),
        active_elapsed=segment_elapsed,
    )
else:
    await runs.finish(
        request.session_id, prepared.run.run_id, "completed",
        active_elapsed=segment_elapsed,
    )
```

Generate `pending_gate_id = f"gate_{uuid.uuid4().hex}"` once when the first deterministic interrupt is observed, pass it to every SSE rendering path, and store it through `pause()`. In the existing exception block, call `runs.finish(request.session_id, prepared.run.run_id, status="failed", active_elapsed=segment_elapsed)` before yielding `ErrorEvent`. Before accepting any late worker result or writing a `ResultRef`, compare current session record `run_id` and `generation`; discard on mismatch.

Add these fields to the existing deterministic `turn_doc`:

```python
"run_id": prepared.run.run_id,
"generation": prepared.run.generation,
"lane": "deterministic",
```

- [ ] **Step 8: Run model tests and existing server-independent regression tests**

Run: `cd 08-YieldAgent && pytest tests/test_hybrid_server.py tests/test_orchestration_contracts.py tests/test_confirm_edit.py -v`

Expected: all tests PASS.

- [ ] **Step 9: Commit server protocol integration**

```bash
git add 08-YieldAgent/models.py 08-YieldAgent/agent_server.py 08-YieldAgent/tests/test_hybrid_server.py
git commit -m "feat(yield): integrate hybrid stream routing"
```

---

### Task 10: Legacy result dual-write, completed-checkpoint compaction, and cascade deletion

**Files:**
- Modify: `08-YieldAgent/agent_server.py:407-417,681-1054`
- Create: `08-YieldAgent/tests/test_checkpoint_compaction.py`

**Interfaces:**
- Consumes: deterministic turn messages/artifacts, Result Store, graph `aupdate_state`, session thread directory
- Produces: `_persist_legacy_results()`, `_compact_completed_checkpoint()`, complete session cascade deletion

- [ ] **Step 1: Write failing compaction tests against a fake graph**

```python
# 08-YieldAgent/tests/test_checkpoint_compaction.py
import asyncio
from types import SimpleNamespace
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_server import _compact_completed_checkpoint

pytestmark = pytest.mark.no_server


class FakeGraph:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.updates = []

    async def aget_state(self, config):
        return self.snapshot

    async def aupdate_state(self, config, update):
        self.updates.append(update)


def test_completed_checkpoint_keeps_three_real_turns_and_refs_only():
    async def scenario():
        snapshot = SimpleNamespace(tasks=[], values={
            "messages": [
                SimpleNamespace(type="human", content=f"u{i}", additional_kwargs={}) if j == 0
                else SimpleNamespace(type="ai", content=f"a{i}", name="supervisor", additional_kwargs={"result": {"rows": [{"large": "x" * 1000}]}})
                for i in range(5) for j in range(2)
            ],
            "yield_artifacts": [{"data": "large-html"}],
            "recent_results": [{"result_id": str(i)} for i in range(12)],
        })
        graph = FakeGraph(snapshot)
        await _compact_completed_checkpoint(graph, {"configurable": {"thread_id": "t"}})
        update = graph.updates[0]
        assert len(update["messages"].value) == 6
        assert len(update["recent_results"]) == 10
        assert update["yield_artifacts"].value == []
        assert all("result" not in getattr(m, "additional_kwargs", {}) for m in update["messages"].value)
    asyncio.run(scenario())


def test_pending_checkpoint_is_not_compacted():
    async def scenario():
        graph = FakeGraph(SimpleNamespace(tasks=[SimpleNamespace(interrupts=[1])], values={}))
        await _compact_completed_checkpoint(graph, {})
        assert graph.updates == []
    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests and verify helper failure**

Run: `cd 08-YieldAgent && pytest tests/test_checkpoint_compaction.py -v`

Expected: FAIL because `_compact_completed_checkpoint` does not exist.

- [ ] **Step 3: Implement deterministic dual-write before compaction**

```python
async def _persist_legacy_results(result_store, session_id, run_id, turn_messages, turn_artifacts):
    artifacts_by_agent: dict[str, list[dict]] = {}
    for artifact in turn_artifacts:
        artifacts_by_agent.setdefault(artifact.get("agent", ""), []).append(artifact)
    saved_ids = []
    for message in turn_messages:
        envelope = message.get("result")
        if not envelope:
            continue
        try:
            saved = await result_store.save(
                session_id, run_id, envelope,
                artifacts_by_agent.get(envelope.get("source_agent", ""), []),
            )
            saved_ids.append(saved["result_id"])
        except Exception as exc:
            logger.warning("legacy result dual-write failed: %s", exc)
    return saved_ids
```

When parsing deterministic AI messages, copy `additional_kwargs["result"]` into the internal `turn_messages` persistence record. Do not expose the full envelope in SSE.

- [ ] **Step 4: Implement completed-state compaction with `Overwrite`**

```python
def _completed_conversation(messages):
    turns = []
    pending_user = None
    pending_assistant = None
    for message in messages:
        if getattr(message, "type", "") == "human":
            if pending_user is not None and pending_assistant is not None:
                turns.append((pending_user, pending_assistant))
            pending_user = message
            pending_assistant = None
        elif (
            pending_user is not None
            and getattr(message, "type", "") == "ai"
            and getattr(message, "name", "") not in {"wads_sql_result", "planner", "replanner"}
        ):
            clean = message.model_copy(update={"additional_kwargs": {}}) if hasattr(message, "model_copy") else message
            pending_assistant = clean
    if pending_user is not None and pending_assistant is not None:
        turns.append((pending_user, pending_assistant))
    return [message for pair in turns[-3:] for message in pair]


async def _compact_completed_checkpoint(graph, config):
    snapshot = await graph.aget_state(config)
    if any(getattr(task, "interrupts", None) for task in snapshot.tasks or []):
        return
    values = snapshot.values or {}
    update = {
        "messages": Overwrite(_completed_conversation(values.get("messages", []))),
        "recent_results": list(values.get("recent_results", []))[-10:],
    }
    for field in (
        "yield_artifacts", "wads_artifacts", "map_artifacts", "fail_history_artifacts",
        "ppt_artifacts", "lot_history_artifacts", "relation_tree_artifacts", "mining_artifacts",
    ):
        update[field] = Overwrite([])
    await graph.aupdate_state(config, update)
```

Call this only after SSE delivery and successful attempt to dual-write. Compaction failure is logged and does not change the user response.

- [ ] **Step 5: Cascade session deletion across every generation thread, results, artifacts, run record, and chat history**

```python
@app.delete("/session/{session_id}")
async def delete_session(session_id: str, request: Request):
    db = request.app.state.motor_db
    directory = await db.session_runs.find_one({"_id": session_id}) or {}
    thread_ids = set(directory.get("thread_ids", [])) | {session_id}
    for thread_id in thread_ids:
        await request.app.state.deterministic_graph.checkpointer.adelete_thread(thread_id)
    await request.app.state.result_store.delete_session(session_id)
    await db.session_runs.delete_one({"_id": session_id})
    await db.chat_turns.delete_many({"session_id": session_id})
    return {"deleted": session_id}
```

Append every generated lane thread ID to `session_runs.thread_ids` with `$addToSet` when a run starts.

- [ ] **Step 6: Run compaction and store tests**

Run: `cd 08-YieldAgent && pytest tests/test_checkpoint_compaction.py tests/test_result_store.py -v`

Expected: all non-Mongo tests PASS; Mongo tests PASS or SKIP only for unavailable MongoDB.

- [ ] **Step 7: Commit persistence lifecycle**

```bash
git add 08-YieldAgent/agent_server.py 08-YieldAgent/tests/test_checkpoint_compaction.py
git commit -m "feat(yield): compact legacy checkpoints"
```

---

### Task 11: React canonical resume and cancel-and-start UX

**Files:**
- Modify: `08-YieldAgent/yield_frontend/src/types.ts:20-73`
- Modify: `08-YieldAgent/yield_frontend/src/lib/stream.ts:42-67`
- Modify: `08-YieldAgent/yield_frontend/src/components/Hitl.tsx:7-178`
- Modify: `08-YieldAgent/yield_frontend/src/App.tsx:31-207,290-343`

**Interfaces:**
- Consumes: canonical backend request and hybrid interrupt SSE
- Produces: `CanonicalResume`, `ChatArgs.inputType`, structured HITL callbacks, explicit cancel-and-start action

- [ ] **Step 1: Add canonical TypeScript request and interrupt types**

```typescript
// yield_frontend/src/types.ts
export type InputType = "new_turn" | "resume" | "cancel_and_start";

export type CanonicalResume =
  | { interrupt_type: "exploration_continue"; run_id: string; gate_id: string; action: "continue" | "adjust" | "stop"; adjustment?: string }
  | { interrupt_type: "exploration_question"; run_id: string; gate_id: string; answer: string | Record<string, unknown> }
  | { interrupt_type: "missing_param"; run_id: string; gate_id: string; values: Record<string, unknown> }
  | { interrupt_type: "plan_review"; run_id: string; gate_id: string; action: "approve" | "modify" | "cancel"; modification?: string }
  | { interrupt_type: "task_confirm" | "postwads_choice"; run_id: string; gate_id: string; value: string };

export interface InterruptPayload {
  interrupt_type: string;
  run_id: string;
  gate_id: string;
  lane: string;
  param: string;
  message: string;
  route: string;
  options: HitlOption[];
  fields: HitlField[];
  findings?: Array<{ result_id: string; summary_preview?: string }>;
  proposed_task?: { task_id: string; agent: string; goal: string };
  remaining_worker_executions?: number;
}
```

- [ ] **Step 2: Change the streaming client to send canonical bodies and preserve 409 codes**

```typescript
// yield_frontend/src/lib/stream.ts
export interface ChatArgs {
  query: string;
  sessionId: string;
  inputType: InputType;
  resume?: CanonicalResume;
  expectedPendingRunId?: string;
}

const body: Record<string, unknown> = {
  query,
  session_id: sessionId,
  input_type: inputType,
};
if (resume) body.resume = resume;
if (expectedPendingRunId) body.expected_pending_run_id = expectedPendingRunId;

if (!resp.ok) {
  const payload = await resp.json().catch(() => ({}));
  const detail = payload.detail ?? payload;
  throw new Error(detail.code ? `${detail.code}:${detail.run_id ?? ""}` : `HTTP ${resp.status}`);
}
```

- [ ] **Step 3: Replace raw `ResumeValue` callbacks with `CanonicalResume`**

```typescript
interface Props {
  payload: InterruptPayload;
  answered?: string;
  busy: boolean;
  onResume: (value: CanonicalResume, label: string) => void;
  onCancelAndStart: () => void;
}

const base = { run_id: payload.run_id, gate_id: payload.gate_id };

// plan review buttons
onResume({ ...base, interrupt_type: "plan_review", action: "approve" }, "그대로 진행");
onResume({ ...base, interrupt_type: "plan_review", action: "cancel" }, "취소");
onResume({ ...base, interrupt_type: "plan_review", action: "modify", modification: text }, text);

// missing param submit
onResume({ ...base, interrupt_type: "missing_param", values: dict }, label);

// exploration continue buttons
onResume({ ...base, interrupt_type: "exploration_continue", action: "continue" }, "계속");
onResume({ ...base, interrupt_type: "exploration_continue", action: "stop" }, "중단");
onResume({ ...base, interrupt_type: "exploration_continue", action: "adjust", adjustment: text }, text);

// existing deterministic choice options
onResume({ ...base, interrupt_type: payload.interrupt_type as "task_confirm" | "postwads_choice", value }, label);
```

Add an `ExplorationContinue` component showing `findings`, `proposed_task`, remaining count, continue/adjust/stop controls. Add an `ExplorationQuestion` free-text form that sends `exploration_question`. Route `task_confirm` and `postwads_choice` option clicks through the deterministic choice resume above. Keep existing missing-param and plan-review layouts.

- [ ] **Step 4: Make arbitrary input during pending state explicit**

Store the full last pending `InterruptPayload`, not only a boolean.

```typescript
const [pendingGate, setPendingGate] = useState<InterruptPayload | null>(null);

async function run(
  userText: string,
  inputType: InputType = "new_turn",
  resume?: CanonicalResume,
  expectedPendingRunId?: string,
) {
  await consume(streamChat({ query: userText, sessionId, inputType, resume, expectedPendingRunId }));
}

function answerInterrupt(value: CanonicalResume, label: string) {
  markLastInterruptAnswered(label);
  run(label, "resume", value);
}

function cancelAndStart() {
  const text = input.trim();
  if (!text || !pendingGate) return;
  setInput("");
  run(text, "cancel_and_start", undefined, pendingGate.run_id);
}

function submitInput() {
  const text = input.trim();
  if (!text || busy) return;
  if (pendingGate?.interrupt_type === "exploration_question") {
    setInput("");
    answerInterrupt({
      interrupt_type: "exploration_question",
      run_id: pendingGate.run_id,
      gate_id: pendingGate.gate_id,
      answer: text,
    }, text);
    return;
  }
  if (pendingGate) return;
  setInput("");
  run(text, "new_turn");
}
```

When a gate is pending, the normal submit button answers only `exploration_question`. For every other gate, disable raw submit and show two explicit controls: the gate card response controls and `새 요청으로 전환` calling `cancelAndStart()`.

- [ ] **Step 5: Build the frontend**

Run: `cd 08-YieldAgent/yield_frontend && npm run build`

Expected: TypeScript and Vite build exit 0 with generated `dist/` output. Do not stage generated `dist/` files.

- [ ] **Step 6: Commit frontend protocol changes**

```bash
git add 08-YieldAgent/yield_frontend/src/types.ts 08-YieldAgent/yield_frontend/src/lib/stream.ts 08-YieldAgent/yield_frontend/src/components/Hitl.tsx 08-YieldAgent/yield_frontend/src/App.tsx
git commit -m "feat(yield-ui): add canonical HITL actions"
```

---

### Task 12: Observability, evidence evaluator, live E2E, and rollout gates

**Files:**
- Modify: `08-YieldAgent/local_trace.py:26-47`
- Modify: `08-YieldAgent/tests/e2e_client.py:51-206`
- Create: `08-YieldAgent/tests/exploration_scenarios.json`
- Create: `08-YieldAgent/tests/evaluate_exploration.py`
- Create: `08-YieldAgent/tests/test_exploration_evaluator.py`
- Create: `08-YieldAgent/tests/test_hybrid_e2e.py`

**Interfaces:**
- Consumes: correlated SSE, Result Store documents, trace events, canonical client protocol
- Produces: claim provenance coverage, evidence recall, budget/checkpoint/concurrency gates, 12-scenario three-run evaluator

- [ ] **Step 1: Extend the trace event allowlist before emitting hybrid events**

```python
# Add to TRACE_EVENT_TYPES in local_trace.py
"mode_decided",
"mode_fallback",
"controller_decided",
"policy_allowed",
"policy_denied",
"exploration_gate_created",
"exploration_gate_resumed",
"run_cancelled",
"late_result_discarded",
"result_persisted",
"artifact_persisted",
"claim_grounded",
"active_budget_exhausted",
```

Emit these from Gateway, Exploration Graph, Result Store, and server using existing `emit_trace_event()`. Payloads contain IDs, status, counts, and reason codes only; do not place raw prompts, SQL, rows, artifact bodies, or user identifiers in trace payloads.

- [ ] **Step 2: Write failing evaluator unit tests**

```python
# 08-YieldAgent/tests/test_exploration_evaluator.py
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluate_exploration import evidence_recall, provenance_coverage, validate_limits

pytestmark = pytest.mark.no_server


def test_evidence_recall_counts_required_units_not_agents():
    expected = {"yield.metrics", "wads.entities.lot_ids", "map.artifact"}
    observed = {"yield.metrics", "wads.entities.lot_ids"}
    assert evidence_recall(expected, observed) == pytest.approx(2 / 3)


def test_provenance_coverage_requires_result_id_per_claim():
    claims = [{"text": "a", "result_ids": ["r1"]}, {"text": "b", "result_ids": []}]
    assert provenance_coverage(claims) == 0.5


def test_limit_validation_reports_each_hard_gate():
    failures = validate_limits({
        "worker_executions": 6, "per_agent_executions": {"yield_agent": 3},
        "active_seconds": 241, "result_index_size": 11, "checkpoint_bytes": 300_000,
    })
    assert set(failures) == {"worker_max", "agent_max", "active_time", "result_index", "checkpoint_size"}
```

- [ ] **Step 3: Run evaluator tests and verify import failure**

Run: `cd 08-YieldAgent && pytest tests/test_exploration_evaluator.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'evaluate_exploration'`.

- [ ] **Step 4: Implement deterministic scoring helpers**

```python
# 08-YieldAgent/tests/evaluate_exploration.py
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from statistics import mean

from bson import BSON
from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient

from e2e_client import Session
from exploration_graph import resolve_bindings
from orchestration_contracts import InputBinding, TaskSpec
from result_contracts import build_result_envelope
from result_store import InMemoryArtifactBackend, InMemoryResultStore

SCENARIOS = Path(__file__).with_name("exploration_scenarios.json")


def evidence_recall(expected: set[str], observed: set[str]) -> float:
    return len(expected & observed) / len(expected) if expected else 1.0


def provenance_coverage(claims: list[dict]) -> float:
    return sum(bool(c.get("result_ids")) for c in claims) / len(claims) if claims else 1.0


def validate_limits(run: dict) -> list[str]:
    failures = []
    if run["worker_executions"] > 5: failures.append("worker_max")
    if max(run["per_agent_executions"].values(), default=0) > 2: failures.append("agent_max")
    if run["active_seconds"] > 240: failures.append("active_time")
    if run["result_index_size"] > 10: failures.append("result_index")
    if run["checkpoint_bytes"] > 256 * 1024: failures.append("checkpoint_size")
    return failures


def evaluate_run(scenario: dict, run: dict) -> dict:
    return {
        "scenario_id": scenario["id"],
        "evidence_recall": evidence_recall(set(scenario["required_evidence"]), set(run["observed_evidence"])),
        "provenance_coverage": provenance_coverage(run["claims"]),
        "fabricated_entities": run["fabricated_entities"],
        "limit_failures": validate_limits(run),
        "wrong_downstream_calls": run["wrong_downstream_calls"],
        "stale_results": run["stale_results"],
    }


def _max_named_list(value, name: str) -> int:
    if isinstance(value, dict):
        own = len(value.get(name, [])) if isinstance(value.get(name), list) else 0
        return max([own, *[_max_named_list(v, name) for v in value.values()]])
    if isinstance(value, list):
        return max([0, *[_max_named_list(v, name) for v in value]])
    return 0


def collect_run_observation(session_id: str, run_id: str, thread_id: str) -> dict:
    client = MongoClient("mongodb://localhost:27017")
    db = client.yield_agent
    result_docs = list(db.agent_results.find(
        {"session_id": session_id, "cancelled": {"$ne": True}}, {"_id": 0}
    ))
    envelopes = [doc["envelope"] for doc in result_docs]
    observed = set()
    for envelope in envelopes:
        agent, status = envelope["source_agent"], envelope["status"]
        if agent == "yield_agent" and envelope.get("metrics"):
            observed.add("yield.metrics")
        if agent == "wads_agent":
            observed.add("wads.summary")
            if (envelope.get("entities") or {}).get("lot_ids"):
                observed.add("wads.entities.lot_ids")
            if status == "empty": observed.add("wads.empty")
            if status == "error": observed.add("wads.error")
        if agent == "map_agent" and envelope.get("artifact_refs"):
            observed.add("map.artifact")
        if agent == "fail_history_agent":
            observed.add("fail_history.summary")

    turn = db.chat_turns.find_one(
        {"session_id": session_id, "run_id": run_id}, sort=[("timestamp", -1)]
    ) or {}
    claims = [
        claim for message in turn.get("messages", []) for claim in message.get("claims", [])
    ]
    by_result = {envelope["result_id"]: envelope for envelope in envelopes}
    fabricated = 0
    for claim in claims:
        referenced = [by_result[rid] for rid in claim.get("result_ids", []) if rid in by_result]
        allowed: dict[str, set[str]] = {}
        for envelope in referenced:
            for key, values in (envelope.get("entities") or {}).items():
                allowed.setdefault(key, set()).update(str(v) for v in values)
        fabricated += sum(
            not set(str(v) for v in values) <= allowed.get(key, set())
            for key, values in claim.get("entity_refs", {}).items()
        )

    run_doc = db.session_runs.find_one({"_id": session_id}) or {}
    run = run_doc.get("run") or {}
    checkpoints = list(db.checkpoints.find({"thread_id": thread_id}))
    checkpoint_bytes = max([0, *[len(BSON.encode(doc)) for doc in checkpoints]])
    decoded_checkpoints = []
    with MongoDBSaver.from_conn_string(
        "mongodb://localhost:27017", db_name="yield_agent"
    ) as saver:
        for doc in checkpoints:
            decoded_checkpoints.append(
                saver.serde.loads_typed((doc["type"], doc["checkpoint"]))
            )
    result_index_size = max([
        0, *[_max_named_list(checkpoint, "result_index") for checkpoint in decoded_checkpoints]
    ])
    wads_failed = any(
        e["source_agent"] == "wads_agent" and e["status"] in {"empty", "error", "invalid"}
        for e in envelopes
    )
    map_called = any(e["source_agent"] == "map_agent" for e in envelopes)
    referenced_ids = {rid for claim in claims for rid in claim.get("result_ids", [])}
    stale_results = sum(rid not in by_result for rid in referenced_ids)
    client.close()
    return {
        "observed_evidence": sorted(observed), "claims": claims,
        "fabricated_entities": fabricated,
        "wrong_downstream_calls": int(wads_failed and map_called),
        "stale_results": stale_results,
        "worker_executions": run.get("worker_executions", 0),
        "per_agent_executions": run.get("per_agent_executions", {}),
        "active_seconds": run.get("active_seconds", 0.0),
        "result_index_size": result_index_size,
        "checkpoint_bytes": checkpoint_bytes,
    }


def run_live_scenario(scenario: dict) -> dict:
    session = Session()
    last = None
    for query in scenario["turns"]:
        last = session.run_to_completion(query)
    start = next(e for e in last.sse_events if e.get("type") == "stream_start")
    thread_id = f"{session.session_id}:{start['generation']}:{start['lane']}"
    return collect_run_observation(session.session_id, start["run_id"], thread_id)


def recorded_error_observation() -> dict:
    async def replay():
        store = InMemoryResultStore(InMemoryArtifactBackend())
        envelope = build_result_envelope(
            source_agent="wads_agent", kind="summary", status="error",
            summary="recorded WADS error",
        )
        saved = await store.save("recorded", "recorded_run", envelope, [])
        task = TaskSpec(
            task_id="map_after_error", agent="map_agent", goal="map",
            depends_on=["wads_error"],
            input_bindings=[InputBinding(
                target_param="lot_ids", source_task_id="wads_error",
                source_path="entities.lot_ids", cardinality="many",
            )],
        )
        blocked = False
        try:
            await resolve_bindings(task, {
                "wads_error": {"status": "error", "result_id": saved["result_id"]}
            }, store)
        except ValueError as exc:
            blocked = str(exc) == "required_upstream_unavailable"
        return blocked

    blocked = asyncio.run(replay())
    return {
        "observed_evidence": ["wads.error"], "claims": [],
        "fabricated_entities": 0, "wrong_downstream_calls": 0 if blocked else 1,
        "stale_results": 0,
        "worker_executions": 1, "per_agent_executions": {"wads_agent": 1},
        "active_seconds": 0.0, "result_index_size": 1, "checkpoint_bytes": 0,
    }


def run_twenty_turn_checkpoint_probe() -> dict:
    session = Session()
    last = None
    for _ in range(20):
        last = session.run_to_completion("4SS 최근 1주 수율을 조회해줘")
    start = next(e for e in last.sse_events if e.get("type") == "stream_start")
    thread_id = f"{session.session_id}:{start['generation']}:{start['lane']}"
    observed = collect_run_observation(session.session_id, start["run_id"], thread_id)
    return {
        "checkpoint_bytes": observed["checkpoint_bytes"],
        "result_index_size": observed["result_index_size"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    scores = []
    for scenario in scenarios:
        for run_number in range(args.runs):
            observation = (
                recorded_error_observation()
                if scenario["mode"] == "recorded_error"
                else run_live_scenario(scenario)
            )
            score = {**evaluate_run(scenario, observation), "run": run_number + 1}
            scores.append(score)
            print(json.dumps(score, ensure_ascii=False))
    failures = [s for s in scores if (
        s["provenance_coverage"] < 1.0 or s["fabricated_entities"] != 0 or
        s["limit_failures"] or s["wrong_downstream_calls"] != 0 or s["stale_results"] != 0
    )]
    recall = mean(s["evidence_recall"] for s in scores)
    checkpoint_probe = run_twenty_turn_checkpoint_probe()
    checkpoint_failed = (
        checkpoint_probe["checkpoint_bytes"] > 256 * 1024 or
        checkpoint_probe["result_index_size"] > 10
    )
    print(json.dumps({
        "mean_evidence_recall": recall, "failed_runs": len(failures),
        "checkpoint_probe": checkpoint_probe,
    }))
    return 0 if recall >= 0.80 and not failures and not checkpoint_failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Create the exact 12-scenario dataset**

```json
[
  {"id":"root_cause_4ss_all","mode":"live","turns":["최근 4주 4SS 수율 저하 원인을 관련 증거를 모아 분석해줘"],"required_evidence":["yield.metrics","wads.summary","fail_history.summary"],"forbidden_claims":["unseen_lot","unseen_parameter"],"expected_gate_before_execution":3},
  {"id":"root_cause_4ss_pt1h","mode":"live","turns":["최근 4주 4SS PT1H 수율 이상 원인을 단계적으로 조사해줘"],"required_evidence":["yield.metrics","wads.summary","fail_history.summary"],"forbidden_claims":["unseen_lot","unseen_parameter"],"expected_gate_before_execution":3},
  {"id":"root_cause_4ss_pt1c","mode":"live","turns":["최근 4주 4SS PT1C 수율 이상 원인을 단계적으로 조사해줘"],"required_evidence":["yield.metrics","wads.summary","fail_history.summary"],"forbidden_claims":["unseen_lot","unseen_parameter"],"expected_gate_before_execution":3},
  {"id":"root_cause_followup","mode":"live","turns":["4SS 최근 3주 수율 보여줘","왜 낮은지 확인된 결과를 바탕으로 더 조사해줘"],"required_evidence":["yield.metrics","wads.summary"],"forbidden_claims":["unseen_lot","unseen_parameter"],"expected_gate_before_execution":3},
  {"id":"wads_to_map_recent_week","mode":"live","turns":["최근 1주 4SS WADS 검출 lot을 찾고 wafer map까지 조사해줘"],"required_evidence":["wads.entities.lot_ids","map.artifact"],"forbidden_claims":["map_without_lot_ids"],"expected_gate_before_execution":null},
  {"id":"wads_to_map_pt1h","mode":"live","turns":["최근 1주 4SS PT1H WADS 검출 결과에서 lot을 얻어 map을 확인해줘"],"required_evidence":["wads.entities.lot_ids","map.artifact"],"forbidden_claims":["map_without_lot_ids"],"expected_gate_before_execution":null},
  {"id":"wads_to_map_pt1c","mode":"live","turns":["최근 1주 4SS PT1C WADS 검출 결과에서 lot을 얻어 map을 확인해줘"],"required_evidence":["wads.entities.lot_ids","map.artifact"],"forbidden_claims":["map_without_lot_ids"],"expected_gate_before_execution":null},
  {"id":"yield_fail_cross_recent","mode":"live","turns":["최근 4주 4SS 수율 이상과 관련 불량이력을 교차 확인해줘"],"required_evidence":["yield.metrics","fail_history.summary"],"forbidden_claims":["unseen_parameter"],"expected_gate_before_execution":null},
  {"id":"yield_fail_cross_followup","mode":"live","turns":["4SS 최근 4주 수율을 확인해줘","방금 확인된 이상 항목과 관련된 불량이력을 조사해줘"],"required_evidence":["yield.metrics","fail_history.summary"],"forbidden_claims":["unseen_parameter"],"expected_gate_before_execution":null},
  {"id":"yield_wads_fail_cross","mode":"live","turns":["최근 4주 4SS 수율, WADS, 불량이력 증거를 교차해서 원인을 정리해줘"],"required_evidence":["yield.metrics","wads.summary","fail_history.summary"],"forbidden_claims":["unseen_lot","unseen_parameter"],"expected_gate_before_execution":3},
  {"id":"wads_empty_blocks_map","mode":"live","turns":["1900-01-01부터 1900-01-02까지 4SS WADS 검출 lot을 찾고 map을 확인해줘"],"required_evidence":["wads.empty"],"forbidden_claims":["map_without_lot_ids"],"expected_gate_before_execution":null},
  {"id":"wads_error_blocks_map","mode":"recorded_error","turns":["4SS WADS 조회 실패 상황에서 map 후속을 안전하게 처리해줘"],"required_evidence":["wads.error"],"forbidden_claims":["map_without_lot_ids"],"expected_gate_before_execution":null}
]
```

The `recorded_error` case replays a validated `ResultEnvelopeV1(status="error")`; it does not add a production fault-injection branch.

- [ ] **Step 6: Upgrade the E2E client to canonical actions**

```python
# Add to Session in tests/e2e_client.py
def _drain_sse_payload(payload: dict, timeout: float) -> tuple[list[dict], str]:
    events = []
    with httpx.Client(timeout=httpx.Timeout(timeout, read=timeout)) as client:
        with client.stream("POST", f"{BASE_URL}/chat/stream", json=payload) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[len("data:"):].strip()
                if not raw:
                    continue
                try:
                    events.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    return events, "\n".join(json.dumps(e, ensure_ascii=False) for e in events)


def turn(self, query: str, *, input_type="new_turn", resume=None,
         expected_pending_run_id=None, timeout=240.0) -> TurnResult:
    payload = {"query": query, "session_id": self.session_id, "input_type": input_type}
    if resume is not None:
        payload["resume"] = resume
    if expected_pending_run_id is not None:
        payload["expected_pending_run_id"] = expected_pending_run_id
    sse_events, sse_blob = _drain_sse_payload(payload, timeout)
    new_events = []
    for _ in range(10):
        all_events = _load_trace_events(self.trace_id)
        new_events = [e for e in all_events if str(e.get("event_id")) not in self._seen]
        if new_events:
            break
        time.sleep(0.3)
    self._seen.update(str(e.get("event_id")) for e in new_events)
    return TurnResult(
        session_id=self.session_id, trace_id=self.trace_id,
        sse_events=sse_events, sse_blob=sse_blob, trace_events=new_events,
    )

def resume(self, interrupt: dict, action: str, adjustment: str = "") -> TurnResult:
    return self.turn("", input_type="resume", resume={
        "interrupt_type": interrupt["interrupt_type"],
        "run_id": interrupt["run_id"],
        "gate_id": interrupt["gate_id"],
        "action": action,
        "adjustment": adjustment,
    })

def run_to_completion(self, query: str, timeout: float = 240.0) -> TurnResult:
    result = self.turn(query, timeout=timeout)
    for _ in range(5):
        interrupts = result.sse_interrupts()
        if not interrupts:
            return result
        gate = interrupts[-1]
        base = {"run_id": gate["run_id"], "gate_id": gate["gate_id"]}
        kind = gate["interrupt_type"]
        if kind == "exploration_continue":
            payload = {**base, "interrupt_type": kind, "action": "continue"}
        elif kind == "plan_review":
            payload = {**base, "interrupt_type": kind, "action": "approve"}
        elif kind == "task_confirm":
            payload = {**base, "interrupt_type": kind, "value": "아니오"}
        elif kind == "postwads_choice":
            payload = {**base, "interrupt_type": kind, "value": "none"}
        else:
            raise AssertionError(f"scenario needs explicit input for interrupt: {kind}")
        result = self.turn("", input_type="resume", resume=payload, timeout=timeout)
    raise AssertionError("more than five HITL resumes in one run")
```

Keep legacy `resume_value` support in a separate `legacy_turn()` helper until Phase 4 compatibility verification finishes.

- [ ] **Step 7: Write the six mandatory live E2E tests**

```python
# 08-YieldAgent/tests/test_hybrid_e2e.py
from e2e_client import Session


def test_root_cause_auto_two_then_gate():
    result = Session().turn("최근 4주 4SS 수율 저하 원인을 끝까지 조사해줘")
    gate = result.sse_interrupts("exploration_continue")[0]
    assert gate["remaining_worker_executions"] == 3


def test_continue_executes_one_then_new_gate_if_needed():
    session = Session()
    first = session.turn("최근 4주 4SS 수율 저하 원인을 끝까지 조사해줘")
    resumed = session.resume(first.sse_interrupts("exploration_continue")[0], "continue")
    assert resumed.sse_interrupts("exploration_continue") or resumed.sse_contains("result_ids")


def test_stop_synthesizes_without_another_worker():
    session = Session()
    first = session.turn("최근 4주 4SS 수율 저하 원인을 끝까지 조사해줘")
    stopped = session.resume(first.sse_interrupts("exploration_continue")[0], "stop")
    assert stopped.sse_contains("stream_end")


def test_adjust_creates_new_gate_id():
    session = Session()
    first = session.turn("최근 4주 4SS 수율 저하 원인을 끝까지 조사해줘")
    gate = first.sse_interrupts("exploration_continue")[0]
    adjusted = session.resume(gate, "adjust", "Map 대신 Fail History를 확인")
    assert adjusted.sse_interrupts("exploration_continue")[0]["gate_id"] != gate["gate_id"]


def test_cancel_and_start_never_runs_old_task():
    session = Session()
    first = session.turn("최근 4주 4SS 수율 저하 원인을 끝까지 조사해줘")
    gate = first.sse_interrupts("exploration_continue")[0]
    fresh = session.turn("4SS 최근 1주 수율만 보여줘", input_type="cancel_and_start", expected_pending_run_id=gate["run_id"])
    assert gate["run_id"] not in {e.get("run_id") for e in fresh.sse_events if e.get("type") == "node_complete"}


def test_wads_empty_never_calls_map():
    result = Session().turn("1900-01-01부터 1900-01-02까지 4SS WADS 검출 lot을 찾고 map을 확인해줘")
    assert not any(e.get("node") == "map_agent" for e in result.sse_events)
```

- [ ] **Step 8: Run the complete server-independent suite**

Run: `cd 08-YieldAgent && pytest -m no_server -v`

Expected: all tests PASS; Mongo-dependent tests may SKIP only with explicit local-Mongo reason.

- [ ] **Step 9: Build frontend and start live services**

Run: `cd 08-YieldAgent/yield_frontend && npm run build`

Expected: exit 0.

Run in a separate terminal: `cd 08-YieldAgent && uvicorn agent_server:app --port 8001`

Expected: startup log confirms Mongo checkpointer, Result Store indexes, deterministic graph, and Exploration Graph.

- [ ] **Step 10: Run existing deterministic live regression before exploration canary**

Run: `cd 08-YieldAgent && HYBRID_ROUTING_ENABLED=0 pytest tests/test_e2e_regression.py -v`

Expected: existing deterministic baseline passes exactly; no new failure or changed xfail set.

Run: `cd 08-YieldAgent && HYBRID_ROUTING_ENABLED=0 python tests/golden_exploratory.py`

Expected: no regression versus the existing legacy baseline. This is compatibility-only; do not use `agent_recall` as the new quality gate.

- [ ] **Step 11: Run mandatory hybrid live E2E with real DB, LLM, tools, Mongo, and GridFS**

Restart server with: `cd 08-YieldAgent && HYBRID_ROUTING_ENABLED=1 EXPLORATION_CANARY_PERCENT=100 uvicorn agent_server:app --port 8001`

Run: `cd 08-YieldAgent && pytest tests/test_hybrid_e2e.py -v`

Expected: six mandatory scenarios PASS. Inspect Mongo `agent_results` and GridFS `agent_artifacts.files` to confirm actual writes; test teardown deletes its sessions.

- [ ] **Step 12: Run the 12-scenario evaluator three times each**

Run: `cd 08-YieldAgent && python tests/evaluate_exploration.py --runs 3`

Expected:

- mean evidence recall at least 0.80
- claim provenance coverage 1.00
- fabricated lot/parameter/operation count 0
- wrong downstream execution after failed required upstream 0
- stale result after cancellation 0
- worker executions at most 5
- each agent executions at most 2
- p95 active execution time at most 240 seconds
- completed 20-turn checkpoint BSON size at most 256 KiB
- checkpoint result index length at most 10

- [ ] **Step 13: Exercise shadow and canary controls**

Run shadow: `HYBRID_ROUTING_ENABLED=1 EXPLORATION_SHADOW_ENABLED=1 EXPLORATION_CANARY_PERCENT=0 uvicorn agent_server:app --port 8001`

Expected: user response comes from deterministic lane; trace contains shadow `ModeDecision`; no exploration worker side effect.

Run canary stages only after the previous stage records at least 100 eligible requests and reruns all 12 scenarios three times:

```bash
EXPLORATION_CANARY_PERCENT=5
EXPLORATION_CANARY_PERCENT=25
EXPLORATION_CANARY_PERCENT=100
```

At any gate failure, set `EXPLORATION_CANARY_PERCENT=0` and restart. This fallback changes routing only; it does not delete evidence or checkpoints.

- [ ] **Step 14: Commit observability and E2E gates**

```bash
git add 08-YieldAgent/local_trace.py 08-YieldAgent/tests/e2e_client.py 08-YieldAgent/tests/exploration_scenarios.json 08-YieldAgent/tests/evaluate_exploration.py 08-YieldAgent/tests/test_exploration_evaluator.py 08-YieldAgent/tests/test_hybrid_e2e.py
git commit -m "test(yield): gate hybrid exploration rollout"
```

---

## Final Verification Checklist

- [ ] `git diff --check` returns no errors.
- [ ] `cd 08-YieldAgent && pytest -m no_server -v` passes.
- [ ] `cd 08-YieldAgent/yield_frontend && npm run build` exits 0.
- [ ] Hybrid-disabled deterministic live suite has no new failure or changed xfail set.
- [ ] Six mandatory hybrid live E2E scenarios pass using real services.
- [ ] Twelve exploration scenarios run three times and satisfy every numeric gate.
- [ ] Mongo `agent_results` contains full validated envelopes; Exploration checkpoints contain only refs.
- [ ] GridFS contains artifact bodies; session deletion removes result documents, GridFS files, run directory, chat history, and all lane threads.
- [ ] Same-session concurrent request returns `409 run_in_progress` or `409 pending_gate`.
- [ ] `cancel_and_start` increments generation and old results never attach to the new run.
- [ ] No semantic keyword/regex/failure-string branch was added.
- [ ] `git status --short` shows only intended implementation files before final integration.

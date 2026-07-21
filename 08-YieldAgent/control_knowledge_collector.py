from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from control_knowledge_models import (
    CandidateFact,
    EvidenceRef,
    KnowledgeCandidate,
    SystemCollection,
    SystemSnapshot,
)
from control_knowledge_registry import (
    AGENT_CONTROL_PROFILES,
    AgentControlProfile,
    RegistryIssue,
    validate_agent_registry,
)
from result_contracts import validate_result_envelope


def _sha(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _evidence(kind: str, ref: str, value: Any) -> EvidenceRef:
    return EvidenceRef(kind=kind, ref=ref, sha256=_sha(value))


def build_system_snapshot(
    *,
    workflow,
    agent_slot_rules: dict,
    agent_profiles: dict[str, AgentControlProfile],
    result_schema_version: str,
    result_fields: list[str],
    artifact_fields: list[str],
    hitl_contracts: list[str],
    trace_schema_version: str,
    trace_event_types: list[str],
    trace_fields: list[str],
    trace_redacted_keys: list[str],
    hitl_resume_schema: dict,
    followup_fields: list[str],
    commit_sha: str,
) -> SystemSnapshot:
    nodes = sorted(str(name) for name in workflow.nodes)
    edges = sorted([[str(source), str(target)] for source, target in workflow.edges])
    slots = {
        agent: sorted(str(key) for key in (rules.get("allowed") or []))
        for agent, rules in sorted(agent_slot_rules.items())
    }
    stable = {
        "commit_sha": commit_sha,
        "graph_nodes": nodes,
        "graph_edges": edges,
        "agent_slots": slots,
        "agent_profiles": {
            agent: profile.model_dump(mode="json")
            for agent, profile in sorted(agent_profiles.items())
        },
        "result_schema_version": result_schema_version,
        "result_fields": sorted(result_fields),
        "artifact_fields": sorted(artifact_fields),
        "hitl_contracts": sorted(hitl_contracts),
        "trace_schema_version": trace_schema_version,
        "trace_event_types": sorted(trace_event_types),
        "trace_fields": sorted(trace_fields),
        "trace_redacted_keys": sorted(trace_redacted_keys),
        "hitl_resume_schema": hitl_resume_schema,
        "followup_fields": sorted(followup_fields),
    }
    return SystemSnapshot(snapshot_id=f"snapshot_{_sha(stable)[:16]}", **stable)


def collect_current_system() -> SystemCollection:
    from canonical_request import AGENT_SLOT_RULES
    from local_trace import (
        TRACE_EVENT_FIELDS,
        TRACE_EVENT_TYPES,
        TRACE_REDACTED_KEYS,
        TRACE_SCHEMA_VERSION,
    )
    from models import ChatRequest, HITL_CONTRACT_IDS
    from query_state import YieldQueryState
    from result_contracts import (
        Followup,
        RESULT_ENVELOPE_SCHEMA_VERSION,
        ResultEnvelopeV1,
        ResultKind,
    )
    from supervisor import workflow

    repo = Path(__file__).resolve().parent.parent
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )
    commit_sha = process.stdout.strip() if process.returncode == 0 else "unknown"
    snapshot = build_system_snapshot(
        workflow=workflow,
        agent_slot_rules=AGENT_SLOT_RULES,
        agent_profiles=AGENT_CONTROL_PROFILES,
        result_schema_version=RESULT_ENVELOPE_SCHEMA_VERSION,
        result_fields=list(ResultEnvelopeV1.model_fields),
        artifact_fields=sorted(
            {
                field
                for profile in AGENT_CONTROL_PROFILES.values()
                for field in profile.artifact_channels
            }
        ),
        hitl_contracts=sorted(HITL_CONTRACT_IDS),
        trace_schema_version=TRACE_SCHEMA_VERSION,
        trace_event_types=sorted(TRACE_EVENT_TYPES),
        trace_fields=sorted(TRACE_EVENT_FIELDS),
        trace_redacted_keys=sorted(TRACE_REDACTED_KEYS),
        hitl_resume_schema=ChatRequest.model_json_schema()["properties"][
            "resume_value"
        ],
        followup_fields=list(Followup.__annotations__),
        commit_sha=commit_sha,
    )
    issues = validate_agent_registry(
        profiles=AGENT_CONTROL_PROFILES,
        agent_slot_rules=AGENT_SLOT_RULES,
        graph_nodes=set(workflow.nodes),
        state_fields=set(YieldQueryState.__annotations__),
        result_kinds={item.value for item in ResultKind},
        hitl_contract_ids=set(HITL_CONTRACT_IDS),
        module_root=Path(__file__).resolve().parent,
    )
    return system_collection(snapshot, issues)


def current_system_snapshot() -> SystemSnapshot:
    return collect_current_system().snapshot


def _workflow_position(snapshot: SystemSnapshot, agent: str) -> dict[str, list[str]]:
    return {
        "predecessors": sorted(
            source for source, target in snapshot.graph_edges if target == agent
        ),
        "successors": sorted(
            target for source, target in snapshot.graph_edges if source == agent
        ),
    }


def system_snapshot_candidates(snapshot: SystemSnapshot) -> list[KnowledgeCandidate]:
    snapshot_value = snapshot.model_dump(mode="json", exclude={"created_at"})
    evidence = _evidence("snapshot", snapshot.snapshot_id, snapshot_value)
    candidates: list[KnowledgeCandidate] = []
    result_producers = sorted(
        agent
        for agent, profile in snapshot.agent_profiles.items()
        if "contracts/result-envelope" in profile["output_contracts"]
    )
    channels_by_agent = {
        agent: sorted(str(item) for item in profile["artifact_channels"])
        for agent, profile in sorted(snapshot.agent_profiles.items())
        if profile["artifact_channels"]
    }
    output_contracts_by_agent = {
        agent: [str(item) for item in profile["output_contracts"]]
        for agent, profile in sorted(snapshot.agent_profiles.items())
    }
    applicable_agents = {
        hitl: sorted(
            agent
            for agent, profile in snapshot.agent_profiles.items()
            if hitl in profile["hitl_contracts"]
        )
        for hitl in snapshot.hitl_contracts
    }
    for agent, profile in sorted(snapshot.agent_profiles.items()):
        output_contracts = [str(item) for item in profile["output_contracts"]]
        related_pages = sorted(
            {
                *output_contracts,
                "contracts/hitl-contracts",
                "workflows/orchestration-graph",
            }
        )
        candidates.append(
            KnowledgeCandidate(
                source_kind="system_snapshot",
                subjects=[f"agents/{agent.replace('_', '-')}"],
                suggested_page_type="Agent",
                summary=f"Structured snapshot for canonical agent {agent}",
                facts=[
                    CandidateFact(
                        name="profile",
                        value=profile,
                        source_path=f"control_knowledge_registry.{agent}",
                    ),
                    CandidateFact(
                        name="slot_contract",
                        value={
                            "allowed": snapshot.agent_slots[agent],
                            "required": profile["required_slots"],
                            "required_any": profile["required_any_slots"],
                        },
                        source_path=f"canonical_request.AGENT_SLOT_RULES.{agent}",
                    ),
                    CandidateFact(
                        name="workflow_position",
                        value=_workflow_position(snapshot, agent),
                        source_path="supervisor.workflow.edges",
                    ),
                    CandidateFact(
                        name="related_pages",
                        value=related_pages,
                        source_path=f"control_knowledge_registry.{agent}.output_contracts",
                    ),
                ],
                evidence_refs=[evidence],
            )
        )
    candidates.extend(
        [
            KnowledgeCandidate(
                source_kind="system_snapshot",
                subjects=["contracts/result-envelope"],
                suggested_page_type="Contract",
                summary="ResultEnvelope contract version snapshot",
                facts=[
                    CandidateFact(
                        name="schema_version",
                        value=snapshot.result_schema_version,
                        source_path="result_contracts.RESULT_ENVELOPE_SCHEMA_VERSION",
                    ),
                    CandidateFact(
                        name="schema_fields",
                        value=snapshot.result_fields,
                        source_path="result_contracts.ResultEnvelopeV1.model_fields",
                    ),
                    CandidateFact(
                        name="producers",
                        value=result_producers,
                        source_path="control_knowledge_registry.output_contracts",
                    ),
                ],
                evidence_refs=[evidence],
            ),
            KnowledgeCandidate(
                source_kind="system_snapshot",
                subjects=["contracts/local-trace"],
                suggested_page_type="Contract",
                summary="Local trace contract version snapshot",
                facts=[
                    CandidateFact(
                        name="schema_version",
                        value=snapshot.trace_schema_version,
                        source_path="local_trace.TRACE_SCHEMA_VERSION",
                    ),
                    CandidateFact(
                        name="event_types",
                        value=snapshot.trace_event_types,
                        source_path="local_trace.TRACE_EVENT_TYPES",
                    ),
                    CandidateFact(
                        name="fields",
                        value=snapshot.trace_fields,
                        source_path="local_trace.TRACE_EVENT_FIELDS",
                    ),
                    CandidateFact(
                        name="redacted_keys",
                        value=snapshot.trace_redacted_keys,
                        source_path="local_trace.TRACE_REDACTED_KEYS",
                    ),
                ],
                evidence_refs=[evidence],
            ),
            KnowledgeCandidate(
                source_kind="system_snapshot",
                subjects=["contracts/artifact-delivery"],
                suggested_page_type="Contract",
                summary="Artifact delivery state-channel snapshot",
                facts=[
                    CandidateFact(
                        name="artifact_fields",
                        value=snapshot.artifact_fields,
                        source_path="query_state.YieldQueryState.__annotations__",
                    ),
                    CandidateFact(
                        name="channels_by_agent",
                        value=channels_by_agent,
                        source_path="control_knowledge_registry.artifact_channels",
                    ),
                ],
                evidence_refs=[evidence],
            ),
            KnowledgeCandidate(
                source_kind="system_snapshot",
                subjects=["contracts/hitl-contracts"],
                suggested_page_type="Contract",
                summary="Structured HITL identifier snapshot",
                facts=[
                    CandidateFact(
                        name="hitl_contracts",
                        value=snapshot.hitl_contracts,
                        source_path="models.HITL_CONTRACT_IDS",
                    ),
                    CandidateFact(
                        name="applicable_agents",
                        value=applicable_agents,
                        source_path="control_knowledge_registry.hitl_contracts",
                    ),
                    CandidateFact(
                        name="resume_value_schema",
                        value=snapshot.hitl_resume_schema,
                        source_path="models.ChatRequest.resume_value",
                    ),
                ],
                evidence_refs=[evidence],
            ),
            KnowledgeCandidate(
                source_kind="system_snapshot",
                subjects=["workflows/orchestration-graph"],
                suggested_page_type="Workflow",
                summary="Explicit LangGraph topology snapshot",
                facts=[
                    CandidateFact(
                        name="graph_nodes",
                        value=snapshot.graph_nodes,
                        source_path="supervisor.workflow.nodes",
                    ),
                    CandidateFact(
                        name="graph_edges",
                        value=snapshot.graph_edges,
                        source_path="supervisor.workflow.edges",
                    ),
                    CandidateFact(
                        name="followup_fields",
                        value=snapshot.followup_fields,
                        source_path="result_contracts.Followup.__annotations__",
                    ),
                    CandidateFact(
                        name="output_contracts_by_agent",
                        value=output_contracts_by_agent,
                        source_path="control_knowledge_registry.output_contracts",
                    ),
                    CandidateFact(
                        name="artifact_fields",
                        value=snapshot.artifact_fields,
                        source_path="query_state.YieldQueryState.__annotations__",
                    ),
                ],
                evidence_refs=[evidence],
            ),
        ]
    )
    return candidates


def registry_drift_candidates(
    issues: list[RegistryIssue],
) -> list[KnowledgeCandidate]:
    grouped: dict[str, list[RegistryIssue]] = {}
    for issue in issues:
        grouped.setdefault(issue.agent_id, []).append(issue)
    return [
        KnowledgeCandidate(
            source_kind="registry_drift",
            subjects=[f"observations/registry-drift-{agent.replace('_', '-')}"],
            suggested_page_type="Observation",
            summary=f"Registry verification drift for {agent}",
            facts=[
                CandidateFact(
                    name="registry_issues",
                    value=[
                        item.model_dump(mode="json")
                        for item in sorted(
                            agent_issues,
                            key=lambda value: (value.code, value.source_ref),
                        )
                    ],
                    source_path="control_knowledge_registry.validate_agent_registry",
                )
            ],
            evidence_refs=[
                _evidence(
                    "snapshot",
                    f"registry_drift_{agent}",
                    [item.model_dump(mode="json") for item in agent_issues],
                )
            ],
        )
        for agent, agent_issues in sorted(grouped.items())
    ]


def system_collection(
    snapshot: SystemSnapshot, issues: list[RegistryIssue]
) -> SystemCollection:
    blocked = {issue.agent_id for issue in issues}
    candidates = [
        item
        for item in system_snapshot_candidates(snapshot)
        if not (
            item.subjects[0].startswith("agents/")
            and item.subjects[0].removeprefix("agents/").replace("-", "_")
            in blocked
        )
    ]
    candidates.extend(registry_drift_candidates(issues))
    return SystemCollection(
        snapshot=snapshot,
        registry_issues=[issue.model_dump(mode="json") for issue in issues],
        candidates=candidates,
    )


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
        shapes.append(
            {
                "result_id": envelope.result_id,
                "source_agent": envelope.source_agent,
                "kind": envelope.kind.value,
                "status": envelope.status.value,
                "row_count": len(envelope.rows),
                "column_count": len(envelope.columns),
                "artifact_ref_count": len(envelope.artifact_refs),
                "entity_counts": {
                    key: len(value) for key, value in entities.items() if value
                },
                "followup_count": len(envelope.followups),
                "schema_version": envelope.schema_version,
            }
        )
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
        for task in (state_values.get("task_plan") or [])
        if isinstance(task, dict)
    ]
    issue_shapes = [
        {key: str(issue.get(key) or "") for key in ("type", "agent", "param")}
        for issue in (state_values.get("task_validation_issues") or [])
        if isinstance(issue, dict)
    ]
    hitl_shapes = [
        {key: str(item.get(key) or "") for key in ("touchpoint", "decision", "agent")}
        for item in (state_values.get("hitl_responses") or [])
        if isinstance(item, dict)
    ]
    candidates: list[KnowledgeCandidate] = []
    structural = {
        "results": result_shapes,
        "tasks": task_shapes,
        "validation_issues": issue_shapes,
        "hitl": hitl_shapes,
    }
    if result_shapes or task_shapes or issue_shapes:
        facts = []
        if result_shapes:
            facts.append(
                CandidateFact(
                    name="result_shapes",
                    value=result_shapes,
                    source_path="result_contracts.ResultEnvelopeV1",
                )
            )
        if task_shapes:
            facts.append(
                CandidateFact(
                    name="task_shapes",
                    value=task_shapes,
                    source_path="query_state.task_plan.structure",
                )
            )
        if issue_shapes:
            facts.append(
                CandidateFact(
                    name="validation_issue_shapes",
                    value=issue_shapes,
                    source_path="query_state.task_validation_issues.structure",
                )
            )
        candidates.append(
            KnowledgeCandidate(
                source_kind="runtime_observation",
                subjects=["observations/runtime-behavior"],
                suggested_page_type="Observation",
                summary="Completed turn control-flow structure",
                facts=facts,
                evidence_refs=[_evidence("trace", trace_id, structural)],
            )
        )
    for item in hitl_shapes:
        if item["decision"] != "modify":
            continue
        agent = item["agent"] or "orchestration"
        candidates.append(
            KnowledgeCandidate(
                source_kind="human_correction",
                subjects=[f"runbooks/{agent.replace('_', '-')}-operations"],
                suggested_page_type="Runbook",
                summary="Structured HITL modification signal",
                facts=[
                    CandidateFact(
                        name="hitl_shape",
                        value=item,
                        source_path="query_state.hitl_responses.structure",
                    )
                ],
                evidence_refs=[_evidence("hitl", turn_id, item)],
            )
        )
    return candidates


def incident_candidate(
    exc: Exception,
    *,
    source: str,
    trace_id: str,
    turn_id: str,
    task_id: str,
) -> KnowledgeCandidate:
    source_slug = "-".join(
        filter(None, "".join(char.lower() if char.isalnum() else " " for char in source).split())
    ) or "runtime"
    shape = {
        "exception_type": type(exc).__name__,
        "source": source,
        "trace_id": trace_id,
        "turn_id": turn_id,
        "task_id": task_id,
    }
    return KnowledgeCandidate(
        source_kind="incident",
        subjects=[f"observations/incidents-{source_slug}"],
        suggested_page_type="Observation",
        summary="Structured runtime incident",
        facts=[
            CandidateFact(
                name="incident_shape",
                value=shape,
                source_path="agent_server.graph_exception",
            )
        ],
        evidence_refs=[
            _evidence("incident", trace_id or turn_id or task_id or source, shape)
        ],
    )

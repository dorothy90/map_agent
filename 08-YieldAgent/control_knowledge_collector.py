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
    SystemSnapshot,
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
    result_schema_version: str,
    trace_schema_version: str,
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
        "result_schema_version": result_schema_version,
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
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )
    commit_sha = process.stdout.strip() if process.returncode == 0 else "unknown"
    return build_system_snapshot(
        workflow=workflow,
        agent_slot_rules=AGENT_SLOT_RULES,
        result_schema_version=RESULT_ENVELOPE_SCHEMA_VERSION,
        trace_schema_version=TRACE_SCHEMA_VERSION,
        followup_fields=list(Followup.__annotations__),
        commit_sha=commit_sha,
    )


def system_snapshot_candidates(snapshot: SystemSnapshot) -> list[KnowledgeCandidate]:
    snapshot_value = snapshot.model_dump(mode="json", exclude={"created_at"})
    evidence = _evidence("snapshot", snapshot.snapshot_id, snapshot_value)
    candidates: list[KnowledgeCandidate] = []
    for agent, slots in sorted(snapshot.agent_slots.items()):
        candidates.append(
            KnowledgeCandidate(
                source_kind="system_snapshot",
                subjects=[f"agents/{agent.replace('_', '-')}"],
                suggested_page_type="Agent",
                summary=f"Structured snapshot for canonical agent {agent}",
                facts=[
                    CandidateFact(
                        name="canonical_agent",
                        value=agent,
                        source_path="canonical_request.AGENT_SLOT_RULES",
                    ),
                    CandidateFact(
                        name="slot_keys",
                        value=slots,
                        source_path=(
                            f"canonical_request.AGENT_SLOT_RULES.{agent}.allowed"
                        ),
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
                    )
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
                    )
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
                ],
                evidence_refs=[evidence],
            ),
        ]
    )
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

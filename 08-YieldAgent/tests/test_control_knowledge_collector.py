from pathlib import Path
import sys

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
        "agents/wads-agent",
        "contracts/result-envelope",
        "contracts/local-trace",
        "workflows/orchestration-graph",
    }


def test_runtime_candidate_keeps_shape_but_drops_rows_and_entities():
    message = AIMessage(
        content="domain answer",
        name="wads_agent",
        additional_kwargs={
            "result": {
                "schema_version": "result-envelope/v1",
                "result_id": "result_1",
                "source_agent": "wads_agent",
                "kind": "report",
                "status": "success",
                "summary": "contains domain values",
                "rows": [{"lot_id": "4SS0001"}],
                "entities": {"lot_ids": ["4SS0001"]},
                "artifact_refs": [],
                "columns": [],
                "provenance": {},
                "metadata": {},
                "extensions": {},
                "followups": [],
                "created_at": "2026-07-21T00:00:00Z",
            }
        },
    )
    candidates = runtime_candidates(
        {
            "trace_id": "trace_1",
            "turn_id": "turn_1",
            "messages": [message],
            "task_plan": [
                {
                    "task_id": "task_1",
                    "agent": "wads_agent",
                    "goal": "secret",
                    "params": {"lotcd": "4SS"},
                }
            ],
            "task_validation_issues": [],
            "hitl_responses": [],
        }
    )
    dumped = candidates[0].model_dump_json()
    assert "4SS0001" not in dumped
    assert "domain answer" not in dumped
    assert "secret" not in dumped
    assert '"row_count":1' in dumped


def test_human_correction_omits_raw_answer():
    candidates = runtime_candidates(
        {
            "trace_id": "trace_2",
            "turn_id": "turn_2",
            "messages": [],
            "task_plan": [],
            "task_validation_issues": [],
            "hitl_responses": [
                {
                    "touchpoint": "plan_review",
                    "decision": "modify",
                    "user_answer": "contains private correction",
                    "agent": "planner",
                }
            ],
        }
    )
    dumped = "".join(c.model_dump_json() for c in candidates)
    assert "private correction" not in dumped
    assert "human_correction" in dumped


def test_incident_keeps_exception_type_not_message():
    candidate = incident_candidate(
        RuntimeError("secret DB payload"),
        source="agent_server",
        trace_id="trace_3",
        turn_id="turn_3",
        task_id="task_1",
    )
    dumped = candidate.model_dump_json()
    assert "RuntimeError" in dumped
    assert "secret DB payload" not in dumped

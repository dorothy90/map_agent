import asyncio
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_knowledge_collector import incident_candidate, runtime_candidates

pytestmark = pytest.mark.no_server


def test_startup_candidates_use_validated_collection():
    from agent_server import _startup_control_candidates

    collection = type("Collection", (), {"candidates": ["safe"]})()
    assert _startup_control_candidates(collection) == ["safe"]


def test_completed_state_conversion_never_contains_query_or_rows():
    candidates = runtime_candidates(
        {
            "trace_id": "trace_x",
            "turn_id": "turn_x",
            "messages": [],
            "task_plan": [
                {
                    "task_id": "t1",
                    "agent": "yield_agent",
                    "goal": "4SS private",
                    "params": {"lotcd": "4SS"},
                }
            ],
            "task_validation_issues": [],
            "hitl_responses": [],
        }
    )
    dumped = "".join(item.model_dump_json() for item in candidates)
    assert "4SS" not in dumped


def test_incident_conversion_never_contains_exception_message():
    item = incident_candidate(
        ValueError("private lot 4SS0001"),
        source="agent_server",
        trace_id="tr",
        turn_id="tu",
        task_id="t1",
    )
    assert "4SS0001" not in item.model_dump_json()


def test_submit_control_candidates_swallows_service_failure():
    async def scenario():
        class BrokenService:
            async def submit(self, candidate):
                raise RuntimeError("down")

        from agent_server import _submit_control_candidates

        candidate = incident_candidate(
            ValueError("private"),
            source="agent_server",
            trace_id="tr",
            turn_id="tu",
            task_id="t1",
        )
        await _submit_control_candidates(BrokenService(), [candidate])

    asyncio.run(scenario())

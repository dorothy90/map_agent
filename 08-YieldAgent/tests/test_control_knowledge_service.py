import asyncio
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_knowledge_curator import CuratorCallError
from control_knowledge_models import (
    CurationLedgerEntry,
    KnowledgeCandidate,
    candidate_fingerprint,
)
from control_knowledge_service import ControlKnowledgeService
from control_knowledge_store import ControlKnowledgeStore

pytestmark = pytest.mark.no_server


def _candidate():
    return KnowledgeCandidate.model_validate(
        {
            "source_kind": "runtime_observation",
            "subjects": ["observations/runtime-behavior"],
            "suggested_page_type": "Observation",
            "summary": "runtime shape",
            "facts": [
                {
                    "name": "agents",
                    "value": ["wads_agent"],
                    "source_path": "state.messages",
                }
            ],
            "evidence_refs": [
                {"kind": "trace", "ref": "trace_1", "sha256": "a" * 64}
            ],
        }
    )


def test_save_current_collection_persists_drift_not_blocked_agent(tmp_path):
    from control_knowledge_cli import save_current_collection

    store = ControlKnowledgeStore(tmp_path)
    drift = KnowledgeCandidate.model_validate(
        {
            "source_kind": "registry_drift",
            "subjects": ["observations/registry-drift-yield-agent"],
            "suggested_page_type": "Observation",
            "summary": "registry drift",
            "facts": [
                {
                    "name": "registry_issues",
                    "value": [
                        {"agent_id": "yield_agent", "code": "slot_mismatch"}
                    ],
                    "source_path": (
                        "control_knowledge_registry.validate_agent_registry"
                    ),
                }
            ],
            "evidence_refs": [
                {"kind": "snapshot", "ref": "drift", "sha256": "a" * 64}
            ],
        }
    )
    collection = type("Collection", (), {"candidates": [drift]})()
    paths = save_current_collection(store, collection)
    assert len(paths) == 1
    assert store.load_candidate(paths[0]).source_kind == "registry_drift"


class RecordingCurator:
    def __init__(self, store, fail_once=False):
        self.store = store
        self.calls = 0
        self.fail_once = fail_once

    def curate(self, candidate):
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise CuratorCallError("offline")
        entry = CurationLedgerEntry(
            candidate_id=candidate.candidate_id,
            fingerprint=candidate_fingerprint(candidate),
            action="no_change",
            rationale="same",
        )
        self.store.append_ledger(entry)
        return entry


def test_disabled_service_does_not_persist(tmp_path):
    async def scenario():
        store = ControlKnowledgeStore(tmp_path)
        service = ControlKnowledgeService(
            store, RecordingCurator(store), enabled=False, writer=False
        )
        assert await service.submit(_candidate()) == "disabled"
        assert not (tmp_path / "raw/candidates").exists()

    asyncio.run(scenario())


def test_shadow_service_persists_without_curating(tmp_path):
    async def scenario():
        store = ControlKnowledgeStore(tmp_path)
        curator = RecordingCurator(store)
        service = ControlKnowledgeService(
            store, curator, enabled=True, writer=False
        )
        assert await service.submit(_candidate()) == "persisted"
        assert len(list((tmp_path / "raw/candidates").glob("*.json"))) == 1
        assert curator.calls == 0

    asyncio.run(scenario())


def test_writer_drains_and_retries_transient_curator_error(tmp_path):
    async def scenario():
        store = ControlKnowledgeStore(tmp_path)
        curator = RecordingCurator(store, fail_once=True)
        service = ControlKnowledgeService(
            store,
            curator,
            enabled=True,
            writer=True,
            max_retries=2,
            retry_base_seconds=0,
        )
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

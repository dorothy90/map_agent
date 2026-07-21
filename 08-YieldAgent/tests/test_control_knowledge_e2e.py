import asyncio
import json
from pathlib import Path
import shutil
import sys

import frontmatter
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_knowledge_collector import (
    build_system_snapshot,
    system_snapshot_candidates,
)
from control_knowledge_curator import ControlKnowledgeCurator
from control_knowledge_service import ControlKnowledgeService
from control_knowledge_store import ControlKnowledgeStore
from control_knowledge_validator import scan_bundle
from verify_control_knowledge_live import validate_ledger_entries

pytestmark = pytest.mark.no_server


def test_live_verifier_rejects_all_invalid_decisions():
    with pytest.raises(SystemExit, match="curation failures recorded"):
        validate_ledger_entries(
            [
                {"fingerprint": "a", "action": "invalid_decision"},
                {"fingerprint": "b", "action": "failed"},
            ]
        )


class FakeWorkflow:
    nodes = {"planner": object(), "wads_agent": object(), "replanner": object()}
    edges = {("__start__", "planner"), ("wads_agent", "replanner")}


class RoutingLLM:
    def __init__(self):
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        payload = json.loads(messages[-1]["content"])
        candidate = payload["candidate"]
        subject = candidate["subjects"][0]
        page_type = candidate["suggested_page_type"]
        title = subject.rsplit("/", 1)[-1].replace("-", " ").title()
        existing = bool(payload["existing_pages"])
        decision = {
            "action": "update" if existing else "create",
            "target_page_id": subject if existing else "",
            "rationale": "new snapshot subject",
            "draft": {
                "page_id": subject,
                "page_type": page_type,
                "title": title,
                "description": candidate["summary"],
                "body_markdown": (
                    f"# {title}\n\nGenerated from structured snapshot evidence.\n"
                ),
                "relations": {},
                "evidence_refs": [candidate["evidence_refs"][0]["ref"]],
            },
        }
        return type("Response", (), {"content": json.dumps(decision)})()


def test_snapshot_to_valid_okf_pages_is_idempotent(tmp_path):
    async def scenario():
        source_bundle = Path(__file__).resolve().parent.parent / "multiagent_knowledge"
        root = tmp_path / "bundle"
        shutil.copytree(source_bundle, root)
        store = ControlKnowledgeStore(root)
        llm = RoutingLLM()
        curator = ControlKnowledgeCurator(store, llm)
        service = ControlKnowledgeService(
            store, curator, enabled=True, writer=True, retry_base_seconds=0
        )
        snapshot = build_system_snapshot(
            workflow=FakeWorkflow(),
            agent_slot_rules={"wads_agent": {"allowed": {"lotcd"}}},
            result_schema_version="result-envelope/v1",
            trace_schema_version="local-trace/v1",
            followup_fields=["agent"],
            commit_sha="abc123",
        )
        candidates = system_snapshot_candidates(snapshot)

        await service.start()
        for candidate in candidates:
            await service.submit(candidate)
        await service.stop(timeout=3)
        candidate_subjects = {item.subjects[0] for item in candidates}
        versions = {
            path: frontmatter.load(path).metadata["version"]
            for path in root.joinpath("wiki").rglob("*.md")
            if path.name != "index.md"
            and frontmatter.load(path).metadata.get("page_id") in candidate_subjects
        }
        first_call_count = len(llm.calls)
        assert not scan_bundle(root)
        ledger_entries = [
            json.loads(line)
            for line in store.ledger.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert not {
            "invalid_decision",
            "failed",
        } & {entry["action"] for entry in ledger_entries}

        second = ControlKnowledgeService(
            store, curator, enabled=True, writer=True, retry_base_seconds=0
        )
        await second.start()
        for candidate in candidates:
            assert await second.submit(candidate) == "processed"
        await second.stop(timeout=3)
        assert {
            path: frontmatter.load(path).metadata["version"] for path in versions
        } == versions
        assert len(llm.calls) == first_call_count

    asyncio.run(scenario())

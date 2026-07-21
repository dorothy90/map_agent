from pathlib import Path
import sys

import frontmatter
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_knowledge_validator import scan_bundle
from control_knowledge_curator import AGENT_REQUIRED_SECTIONS
from control_knowledge_registry import AGENT_CONTROL_PROFILES

pytestmark = pytest.mark.no_server


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_root_index_allows_only_okf_version(tmp_path):
    _write(
        tmp_path / "wiki/index.md",
        "---\nokf_version: 1.0\nowner: bad\n---\n# Index\n",
    )
    issues = scan_bundle(tmp_path)
    assert any(i.code == "root_index_extra_frontmatter" for i in issues)


def test_nested_index_rejects_frontmatter(tmp_path):
    _write(tmp_path / "wiki/index.md", "---\nokf_version: 1.0\n---\n# Index\n")
    _write(
        tmp_path / "wiki/agents/index.md", "---\ntype: Agent\n---\n# Agents\n"
    )
    issues = scan_bundle(tmp_path)
    assert any(i.code == "nested_index_frontmatter" for i in issues)


def test_page_requires_governance_and_matching_identity(tmp_path):
    _write(tmp_path / "wiki/index.md", "---\nokf_version: 1.0\n---\n# Index\n")
    _write(
        tmp_path / "wiki/agents/wads-agent.md",
        "---\ntype: Agent\npage_id: wrong\n---\n# Other\n",
    )
    codes = {i.code for i in scan_bundle(tmp_path)}
    assert "missing_governance" in codes
    assert "page_id_path_mismatch" in codes


def test_relationship_requires_existing_wikilink_target(tmp_path):
    _write(tmp_path / "wiki/index.md", "---\nokf_version: 1.0\n---\n# Index\n")
    _write(
        tmp_path / "wiki/agents/a.md",
        """---
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
""",
    )
    issues = scan_bundle(tmp_path)
    assert any(i.code == "broken_relation" for i in issues)


def test_real_bundle_has_operational_agent_pages():
    root = Path(__file__).resolve().parent.parent / "multiagent_knowledge"
    expected = {
        "yield-agent",
        "wads-agent",
        "map-agent",
        "fail-history-agent",
        "lot-history-agent",
        "relation-tree-agent",
        "mining-agent",
        "wt-resp-agent",
        "ppt-export",
    }
    paths = {path.stem: path for path in (root / "wiki/agents").glob("*.md")}
    assert expected <= set(paths)
    for name in expected:
        post = frontmatter.load(paths[name])
        assert post.metadata["version"] == 2
        assert "snapshot_" not in paths[name].read_text(encoding="utf-8")
        headings = {
            line[3:].strip()
            for line in post.content.splitlines()
            if line.startswith("## ")
        }
        assert set(AGENT_REQUIRED_SECTIONS).issubset(headings), name
        assert post.metadata["relations"]["participates_in"] == [
            "[[workflows/orchestration-graph]]"
        ]
        agent_id = name.replace("-", "_")
        assert post.metadata["relations"]["uses_contract"] == [
            f"[[{contract}]]"
            for contract in sorted(
                AGENT_CONTROL_PROFILES[agent_id].output_contracts
            )
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
    assert "## State and Result Flow" in workflow
    assert "## Dynamic Handoffs" in workflow
    for path in [
        root / "contracts/result-envelope.md",
        root / "contracts/local-trace.md",
        root / "contracts/artifact-delivery.md",
        root / "contracts/hitl-contracts.md",
        root / "workflows/orchestration-graph.md",
    ]:
        post = frontmatter.load(path)
        assert post.metadata["version"] == 2
        assert "snapshot_" not in path.read_text(encoding="utf-8")

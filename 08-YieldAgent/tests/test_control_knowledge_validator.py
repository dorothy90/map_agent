from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_knowledge_validator import scan_bundle

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

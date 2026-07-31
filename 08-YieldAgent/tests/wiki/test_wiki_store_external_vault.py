import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_server


def test_store_writes_only_to_explicit_vault(tmp_path):
    vault = tmp_path / "YieldWiki"
    script = """
import json
import bootstrap_wiki_warmup
import wiki_store
import wiki_config

assert bootstrap_wiki_warmup._VAULT_PATH == wiki_store._VAULT
assert wiki_config.resolve_wiki_paths().root == wiki_store._VAULT

eid, status = wiki_store.upsert_episode({
    "query": "4SS EASY 이력",
    "filters": {"product": "4SS", "fail_type": "EASY", "cause_oper": "PRE METAL CLN"},
    "doc_ids": ["FH-1"],
    "body": "## 근거\\n\\n검증 본문",
    "summary": "검증",
})
print(json.dumps({"root": str(wiki_store._VAULT), "eid": eid, "status": status}))
"""
    env = {**os.environ, "WIKI_VAULT_PATH": str(vault)}
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout.strip())
    assert result["root"] == str(vault.resolve())
    assert result["status"] == "created"
    assert (vault / "episodes" / f"{result['eid']}.md").exists()
    assert (vault / "sources").is_dir()
    assert (vault / "reviews").is_dir()


def test_bootstrap_default_resolves_relative_external_vault(tmp_path):
    vault_name = "YieldWiki"
    script = """
import json
import bootstrap_wiki_warmup
import wiki_store

print(json.dumps({
    "bootstrap": str(bootstrap_wiki_warmup._VAULT_PATH),
    "store": str(wiki_store._VAULT),
}))
"""
    app_root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "PYTHONPATH": str(app_root),
        "WIKI_VAULT_PATH": vault_name,
    }
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout.strip())
    assert result["bootstrap"] == str((tmp_path / vault_name).resolve())
    assert result["store"] == result["bootstrap"]

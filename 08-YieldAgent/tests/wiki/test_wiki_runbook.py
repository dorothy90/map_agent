import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_server

APP_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = APP_ROOT / "docs" / "wiki-deployment-procedure.md"
LOCAL_CLIS = (
    "bootstrap_wiki_warmup.py",
    "sync_wiki.py",
    "migrate_v2_to_v3.py",
    "migrate_wiki_vault.py",
    "wiki_lint.py",
)


def _bash_blocks(text: str) -> list[str]:
    return re.findall(r"```bash\s*\n(.*?)```", text, flags=re.DOTALL)


def _logical_shell_lines(block: str) -> list[str]:
    return [line.strip() for line in block.replace("\\\n", " ").splitlines()]


def _documented_options(text: str, script: str) -> set[str]:
    lines = (
        line
        for block in _bash_blocks(text)
        for line in _logical_shell_lines(block)
        if script in line
    )
    return {option for line in lines for option in re.findall(r"--[a-z][a-z0-9-]*", line)}


def test_documented_local_cli_options_exist(tmp_path):
    text = RUNBOOK.read_text(encoding="utf-8")
    env = {**os.environ, "WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")}

    for script in LOCAL_CLIS:
        completed = subprocess.run(
            [sys.executable, script, "--help"],
            cwd=APP_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        supported = set(re.findall(r"--[a-z][a-z0-9-]*", completed.stdout))
        assert _documented_options(text, script) <= supported


def test_documented_python_imports_are_importable(tmp_path):
    text = RUNBOOK.read_text(encoding="utf-8")
    commands = []
    for block in _bash_blocks(text):
        commands.extend(
            match.group(2)
            for match in re.finditer(r"python\s+-c\s+(['\"])(.*?)\1", block, re.DOTALL)
        )
    statements = set()
    for command in commands:
        statements.update(
            match.group(0)
            for match in re.finditer(
                r"from\s+[A-Za-z_][\w.]*\s+import\s+[A-Za-z_][\w]*(?:\s*,\s*[A-Za-z_][\w]*)*",
                command,
            )
        )
        statements.update(
            f"import {name}"
            for name in re.findall(r"(?:^|;)\s*import\s+([A-Za-z_][\w.]*)", command)
        )

    env = {**os.environ, "WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")}
    for statement in sorted(statements):
        subprocess.run(
            [sys.executable, "-c", statement],
            cwd=APP_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )


def test_runbook_matches_current_single_writer_bootstrap_contract():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "--queries-per-triple" not in text
    assert "--drain-timeout" not in text
    assert "summarize_vault" not in text
    assert "cp 08-YieldAgent/wiki/foundations.yaml $WIKI_VAULT_PATH/foundations.yaml" not in text
    assert "migrate → bootstrap → validate/lint → server" in text
    assert "트리플당 LLM 호출 1회" in text
    assert "episode를 생성하지" in text
    assert "wiki_lint.py --vault $WIKI_VAULT_PATH --log" in text


def test_runbook_documents_incremental_sync_operations():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "sync_wiki.py --check" in text
    assert "sync_wiki.py --apply --limit 10" in text
    assert "sync_wiki.py --resume --limit 10" in text
    assert "bootstrap_wiki_warmup.py --apply" in text
    assert "--product 4SS" in text
    assert "--fail-type EASY" in text
    assert '--cause-oper "PRE METAL CLN"' in text
    assert "source_removal" in text
    assert "Cron" in text

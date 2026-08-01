from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import frontmatter
import pytest

from wiki_config import initialize_wiki_vault, resolve_wiki_paths


pytestmark = pytest.mark.no_server


def _snapshot(vault: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(vault)): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file()
    }


def _prepare_vault(tmp_path: Path):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(paths)
    post = frontmatter.Post(
        content="body",
        id="concept:4SS|PRE METAL CLN|EASY",
        type="concept",
        product="4SS",
        fail_type="EASY",
        cause_oper="PRE METAL CLN",
        citations=[{"doc_id": "FH-1", "source_file": "source.pptx"}],
    )
    paths.concepts.joinpath("4SS_PRE_METAL_CLN_EASY.md").write_text(
        frontmatter.dumps(post), encoding="utf-8"
    )
    return paths


def _run_cli(paths, *args):
    app_root = Path(__file__).resolve().parents[2]
    return subprocess.run(
        [sys.executable, "materialize_obsidian_wiki.py", *args],
        cwd=app_root,
        env={**os.environ, "WIKI_VAULT_PATH": str(paths.root)},
        check=False,
        capture_output=True,
        text=True,
    )


def test_check_previews_changes_without_writing(tmp_path):
    paths = _prepare_vault(tmp_path)
    before = _snapshot(paths.root)

    completed = _run_cli(paths, "--check")

    assert completed.returncode == 0
    assert "created: products/4SS.md" in completed.stdout
    assert _snapshot(paths.root) == before


def test_apply_materializes_the_vault(tmp_path):
    paths = _prepare_vault(tmp_path)

    completed = _run_cli(paths, "--apply")

    assert completed.returncode == 0
    assert (paths.products / "4SS.md").exists()
    assert (paths.sources / "FH-1.md").exists()


@pytest.mark.parametrize("args", [(), ("--check", "--apply")])
def test_requires_exactly_one_mode(tmp_path, args):
    paths = _prepare_vault(tmp_path)

    completed = _run_cli(paths, *args)

    assert completed.returncode != 0

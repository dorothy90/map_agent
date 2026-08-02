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


def _add_invalid_relation(paths) -> None:
    concept_path = paths.concepts / "4SS_PRE_METAL_CLN_EASY.md"
    post = frontmatter.load(concept_path)
    post.metadata["entities"] = [
        {"canonical_name": "Queue time 초과", "entity_type": "condition"},
        {"canonical_name": "자연 산화", "entity_type": "mechanism"},
    ]
    post.metadata["relations"] = [
        {
            "subject": "Queue time 초과",
            "predicate": "causes",
            "object": "없는 Entity",
            "confidence": 0.8,
            "source_doc_ids": ["FH-1"],
        }
    ]
    concept_path.write_text(frontmatter.dumps(post), encoding="utf-8")


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


def test_check_previews_create_only_docs_before_apply_initializes_vault(tmp_path):
    paths = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    paths.concepts.mkdir(parents=True)
    post = frontmatter.Post(
        content="body",
        id="concept:4SS|PRE METAL CLN|EASY",
        type="concept",
        product="4SS",
        fail_type="EASY",
        cause_oper="PRE METAL CLN",
        citations=[],
    )
    paths.concepts.joinpath("4SS_PRE_METAL_CLN_EASY.md").write_text(
        frontmatter.dumps(post), encoding="utf-8"
    )
    before = _snapshot(paths.root)

    completed = _run_cli(paths, "--check")

    assert completed.returncode == 0
    assert "created: purpose.md" in completed.stdout
    assert "created: schema.md" in completed.stdout
    assert _snapshot(paths.root) == before


def test_apply_materializes_the_vault(tmp_path):
    paths = _prepare_vault(tmp_path)

    completed = _run_cli(paths, "--apply")

    assert completed.returncode == 0
    assert (paths.products / "4SS.md").exists()
    assert (paths.sources / "FH-1.md").exists()


def test_warning_is_printed_separately_without_failing_apply(tmp_path):
    paths = _prepare_vault(tmp_path)
    _add_invalid_relation(paths)

    completed = _run_cli(paths, "--apply")

    assert completed.returncode == 0
    assert "warning:" in completed.stdout
    assert "warnings=1" in completed.stdout
    assert "errors=0" in completed.stdout


@pytest.mark.parametrize("args", [(), ("--check", "--apply")])
def test_requires_exactly_one_mode(tmp_path, args):
    paths = _prepare_vault(tmp_path)

    completed = _run_cli(paths, *args)

    assert completed.returncode != 0

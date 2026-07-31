import sys
from pathlib import Path

import pytest

from migrate_wiki_vault import main, migrate_vault, sha256_file


pytestmark = pytest.mark.no_server


def _source_vault(root: Path) -> Path:
    source = root / "source"
    (source / "concepts").mkdir(parents=True)
    (source / "concepts" / "one.md").write_text(
        "---\nid: concept:one\n---\nbody\n", encoding="utf-8"
    )
    (source / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    return source


def test_dry_run_does_not_create_target(tmp_path):
    source = _source_vault(tmp_path)
    target = tmp_path / "target"

    report = migrate_vault(source, target, apply=False)

    assert report.planned == 2
    assert not target.exists()


def test_apply_copies_and_verifies_checksums(tmp_path):
    source = _source_vault(tmp_path)
    target = tmp_path / "target"

    report = migrate_vault(source, target, apply=True)

    assert report.copied == 2
    assert sha256_file(source / "concepts" / "one.md") == sha256_file(
        target / "concepts" / "one.md"
    )


def test_apply_is_idempotent_for_a_separate_target(tmp_path):
    source = _source_vault(tmp_path)
    target = tmp_path / "target"

    migrate_vault(source, target, apply=True)
    report = migrate_vault(source, target, apply=True)

    assert report.copied == 0
    assert report.identical == 2


def test_apply_refuses_target_nested_inside_source(tmp_path):
    source = _source_vault(tmp_path)
    target = source / "export"

    with pytest.raises(ValueError, match="target.*source"):
        migrate_vault(source, target, apply=True)

    assert not target.exists()


def test_cli_reports_nested_target_as_a_migration_failure(tmp_path, monkeypatch, capsys):
    source = _source_vault(tmp_path)
    target = source / "export"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "migrate_wiki_vault.py",
            "--source",
            str(source),
            "--target",
            str(target),
            "--apply",
        ],
    )

    assert main() == 1
    assert "migration failed: target Vault must not be inside source Vault" in capsys.readouterr().out


def test_apply_refuses_different_existing_target(tmp_path):
    source = _source_vault(tmp_path)
    target = tmp_path / "target"
    (target / "concepts").mkdir(parents=True)
    (target / "concepts" / "one.md").write_text("different", encoding="utf-8")

    with pytest.raises(FileExistsError, match="different target file"):
        migrate_vault(source, target, apply=True)


def test_temporary_files_are_not_migrated(tmp_path):
    source = _source_vault(tmp_path)
    (source / "concepts" / "partial.md.tmp").write_text("partial", encoding="utf-8")
    target = tmp_path / "target"

    migrate_vault(source, target, apply=True)

    assert not (target / "concepts" / "partial.md.tmp").exists()

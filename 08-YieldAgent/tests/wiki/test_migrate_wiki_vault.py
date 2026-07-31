import os
import sys
from pathlib import Path

import pytest
import migrate_wiki_vault as migration

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


def _single_file_source(root: Path) -> Path:
    source = root / "source"
    (source / "concepts").mkdir(parents=True)
    (source / "concepts" / "one.md").write_text("source body\n", encoding="utf-8")
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


def test_rejects_source_file_symlink_without_copying_external_content(tmp_path):
    source = _source_vault(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    (source / "concepts" / "linked.md").symlink_to(outside)
    target = tmp_path / "target"

    with pytest.raises(ValueError, match="source.*symlink"):
        migrate_vault(source, target, apply=True)

    assert not target.exists()


def test_rejects_source_directory_symlink_that_escapes_resolved_root(tmp_path):
    source = _source_vault(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    (source / "linked-directory").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="source.*symlink"):
        migrate_vault(source, tmp_path / "target", apply=False)


def test_rejects_symlink_in_source_root_path(tmp_path):
    source = _source_vault(tmp_path)
    linked_source = tmp_path / "linked-source"
    linked_source.symlink_to(source, target_is_directory=True)

    with pytest.raises(ValueError, match="source.*symlink"):
        migrate_vault(linked_source, tmp_path / "target", apply=False)


def test_rejects_dangling_target_file_symlink_without_creating_referent(tmp_path):
    source = _single_file_source(tmp_path)
    target = tmp_path / "target"
    (target / "concepts").mkdir(parents=True)
    outside = tmp_path / "outside.md"
    (target / "concepts" / "one.md").symlink_to(outside)

    with pytest.raises(ValueError, match="target.*symlink"):
        migrate_vault(source, target, apply=True)

    assert not outside.exists()
    assert (target / "concepts" / "one.md").is_symlink()


def test_rejects_target_directory_symlink_without_writing_outside_root(tmp_path):
    source = _single_file_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (target / "concepts").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="target.*symlink"):
        migrate_vault(source, target, apply=True)

    assert not (outside / "one.md").exists()


def test_rejects_symlink_in_target_root_path(tmp_path):
    source = _single_file_source(tmp_path)
    real_target = tmp_path / "real-target"
    real_target.mkdir()
    linked_target = tmp_path / "linked-target"
    linked_target.symlink_to(real_target, target_is_directory=True)

    with pytest.raises(ValueError, match="target.*symlink"):
        migrate_vault(source, linked_target, apply=True)

    assert list(real_target.iterdir()) == []


def _install_concurrent_link_hook(monkeypatch, content: bytes) -> None:
    original_link = os.link

    def publish_after_concurrent_create(source, destination, *args, **kwargs):
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=kwargs["dst_dir_fd"],
        )
        try:
            os.write(destination_fd, content)
        finally:
            os.close(destination_fd)
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", publish_after_concurrent_create)


def test_concurrent_identical_destination_is_counted_without_overwrite(
    tmp_path, monkeypatch
):
    source = _single_file_source(tmp_path)
    target = tmp_path / "target"
    _install_concurrent_link_hook(
        monkeypatch, (source / "concepts" / "one.md").read_bytes()
    )

    report = migrate_vault(source, target, apply=True)

    assert report.copied == 0
    assert report.identical == 1
    assert (target / "concepts" / "one.md").read_text(encoding="utf-8") == "source body\n"


def test_concurrent_different_destination_is_preserved_as_conflict(
    tmp_path, monkeypatch
):
    source = _single_file_source(tmp_path)
    target = tmp_path / "target"
    _install_concurrent_link_hook(monkeypatch, b"concurrent body\n")

    with pytest.raises(FileExistsError, match="different target file"):
        migrate_vault(source, target, apply=True)

    assert (target / "concepts" / "one.md").read_text(encoding="utf-8") == "concurrent body\n"


def test_checksum_failure_leaves_no_target_and_cleans_only_owned_staging(
    tmp_path, monkeypatch
):
    source = _single_file_source(tmp_path)
    target = tmp_path / "target"
    (target / "concepts").mkdir(parents=True)
    sentinel = target / "concepts" / ".wiki-migrate-unowned.tmp"
    sentinel.write_text("keep", encoding="utf-8")
    original_sha256 = migration.sha256_file
    calls = 0

    def mismatch_staged_copy(path):
        nonlocal calls
        calls += 1
        digest = original_sha256(path)
        return digest if calls == 1 else f"mismatch-{digest}"

    monkeypatch.setattr(migration, "sha256_file", mismatch_staged_copy)

    with pytest.raises(OSError, match="checksum mismatch"):
        migrate_vault(source, target, apply=True)

    assert not (target / "concepts" / "one.md").exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert list((target / "concepts").glob(".wiki-migrate-*.tmp")) == [sentinel]


def test_target_parent_replacement_during_staging_verification_aborts_publication(
    tmp_path, monkeypatch
):
    source = _single_file_source(tmp_path)
    target = tmp_path / "target"
    displaced = target / "displaced-concepts"
    original_sha256 = migration.sha256_file
    calls = 0

    def replace_parent_while_hashing_staging(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_sha256(path)
        path.parent.rename(displaced)
        path.parent.mkdir()
        return original_sha256(displaced / path.name)

    monkeypatch.setattr(migration, "sha256_file", replace_parent_while_hashing_staging)

    with pytest.raises(ValueError, match="target path changed"):
        migrate_vault(source, target, apply=True)

    assert not (target / "concepts" / "one.md").exists()
    assert not (displaced / "one.md").exists()
    assert not list(displaced.glob(".wiki-migrate-*.tmp"))

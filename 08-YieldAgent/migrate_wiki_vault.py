from __future__ import annotations

import argparse
import hashlib
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MigrationReport:
    planned: int
    copied: int
    identical: int


def sha256_file(path: Path) -> str:
    path = _absolute_path(path)
    _reject_symlink_components(path, "file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"not a regular file: {path}")
        return _sha256_descriptor(descriptor)
    finally:
        os.close(descriptor)


def _sha256_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    for chunk in iter(lambda: os.read(descriptor, 1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _reject_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ValueError(f"{label} path contains symlink: {current}")


def _assert_below(path: Path, root: Path, label: str) -> None:
    if not path.is_relative_to(root):
        raise ValueError(f"{label} path escapes resolved Vault root: {path}")


def plan_migration(source: Path) -> list[Path]:
    source = _absolute_path(source)
    _reject_symlink_components(source, "source")
    source_root = source.resolve(strict=True)
    files: list[Path] = []
    for path in source.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError(f"source path contains symlink: {path}")
        _assert_below(path.resolve(strict=True), source_root, "source")
        if stat.S_ISREG(mode) and not path.name.endswith(".tmp"):
            files.append(path)
    return sorted(files)


def _open_target_parent(root_descriptor: int, relative: Path, target: Path) -> int:
    descriptor = os.dup(root_descriptor)
    current = target
    try:
        for part in relative.parts:
            current /= part
            try:
                os.mkdir(part, dir_fd=descriptor)
            except FileExistsError:
                pass
            try:
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
            except OSError as exc:
                raise ValueError(
                    f"target path contains symlink or non-directory: {current}"
                ) from exc
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _validate_target_anchor(
    target: Path,
    target_root: Path,
    root_descriptor: int,
) -> None:
    _reject_symlink_components(target, "target")
    if target.resolve(strict=True) != target_root:
        raise ValueError(f"target path escapes resolved Vault root: {target}")
    path_stat = target.stat(follow_symlinks=False)
    descriptor_stat = os.fstat(root_descriptor)
    if (path_stat.st_dev, path_stat.st_ino) != (
        descriptor_stat.st_dev,
        descriptor_stat.st_ino,
    ):
        raise ValueError(f"target Vault path changed during migration: {target}")


def _validate_target_parent(
    target_file: Path,
    target_root: Path,
    parent_descriptor: int,
) -> None:
    _reject_symlink_components(target_file.parent, "target")
    resolved_parent = target_file.parent.resolve(strict=True)
    _assert_below(resolved_parent, target_root, "target")
    path_stat = target_file.parent.stat(follow_symlinks=False)
    descriptor_stat = os.fstat(parent_descriptor)
    if (path_stat.st_dev, path_stat.st_ino) != (
        descriptor_stat.st_dev,
        descriptor_stat.st_ino,
    ):
        raise ValueError(f"target path changed during migration: {target_file.parent}")


def _target_checksum(parent_descriptor: int, name: str, target_file: Path) -> str:
    info = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"target file is a symlink: {target_file}")
    if not stat.S_ISREG(info.st_mode):
        raise FileExistsError(f"non-file target entry: {target_file}")
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW,
        dir_fd=parent_descriptor,
    )
    try:
        return _sha256_descriptor(descriptor)
    finally:
        os.close(descriptor)


def _copy_to_staging(
    source_file: Path,
    source_root: Path,
    parent_descriptor: int,
    staging_name: str,
) -> None:
    _reject_symlink_components(source_file, "source")
    _assert_below(source_file.resolve(strict=True), source_root, "source")
    source_descriptor = os.open(source_file, os.O_RDONLY | os.O_NOFOLLOW)
    staging_descriptor = -1
    try:
        source_stat = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_stat.st_mode):
            raise ValueError(f"source is not a regular file: {source_file}")
        staging_descriptor = os.open(
            staging_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=parent_descriptor,
        )
        for chunk in iter(lambda: os.read(source_descriptor, 1024 * 1024), b""):
            view = memoryview(chunk)
            while view:
                written = os.write(staging_descriptor, view)
                view = view[written:]
        os.fchmod(staging_descriptor, stat.S_IMODE(source_stat.st_mode))
        os.fsync(staging_descriptor)
        os.utime(
            staging_name,
            ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    finally:
        os.close(source_descriptor)
        if staging_descriptor >= 0:
            os.close(staging_descriptor)


def _migrate_file(
    source_file: Path,
    source_root: Path,
    target_file: Path,
    target_root: Path,
    parent_descriptor: int,
) -> str:
    source_checksum = sha256_file(source_file)
    try:
        os.stat(target_file.name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if _target_checksum(parent_descriptor, target_file.name, target_file) != source_checksum:
            raise FileExistsError(f"different target file: {target_file}")
        return "identical"

    staging_name = f".wiki-migrate-{uuid.uuid4().hex}.tmp"
    staging_file = target_file.with_name(staging_name)
    try:
        _copy_to_staging(
            source_file,
            source_root,
            parent_descriptor,
            staging_name,
        )
        _validate_target_parent(target_file, target_root, parent_descriptor)
        if sha256_file(staging_file) != source_checksum:
            raise OSError(f"checksum mismatch: {target_file}")
        _validate_target_parent(target_file, target_root, parent_descriptor)
        try:
            os.link(
                staging_name,
                target_file.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            if _target_checksum(
                parent_descriptor, target_file.name, target_file
            ) != source_checksum:
                raise FileExistsError(f"different target file: {target_file}")
            return "identical"
        return "copied"
    finally:
        try:
            os.unlink(staging_name, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass


def migrate_vault(source: Path, target: Path, *, apply: bool) -> MigrationReport:
    source = _absolute_path(source)
    target = _absolute_path(target)
    _reject_symlink_components(source, "source")
    _reject_symlink_components(target, "target")
    if not source.is_dir():
        raise FileNotFoundError(f"source Vault not found: {source}")
    source_root = source.resolve(strict=True)
    target_root = target.resolve(strict=False)
    if target_root.is_relative_to(source_root):
        raise ValueError(f"target Vault must not be inside source Vault: {target_root}")
    files = plan_migration(source)
    if not apply:
        return MigrationReport(planned=len(files), copied=0, identical=0)

    target.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(target, "target")
    if target.resolve(strict=True) != target_root:
        raise ValueError(f"target path escapes resolved Vault root: {target}")
    root_descriptor = os.open(
        target,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    copied = 0
    identical = 0
    try:
        for source_file in files:
            _validate_target_anchor(target, target_root, root_descriptor)
            relative = source_file.relative_to(source)
            target_file = target / relative
            parent_descriptor = _open_target_parent(
                root_descriptor, relative.parent, target
            )
            try:
                result = _migrate_file(
                    source_file,
                    source_root,
                    target_file,
                    target_root,
                    parent_descriptor,
                )
            finally:
                os.close(parent_descriptor)
            if result == "copied":
                copied += 1
            else:
                identical += 1
    finally:
        os.close(root_descriptor)
    return MigrationReport(planned=len(files), copied=copied, identical=identical)


def main() -> int:
    parser = argparse.ArgumentParser(description="copy and verify a Wiki Vault")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        report = migrate_vault(args.source, args.target, apply=args.apply)
    except (FileNotFoundError, FileExistsError, OSError, ValueError) as exc:
        print(f"migration failed: {exc}")
        return 1
    print(f"source={args.source.resolve()}")
    print(f"target={args.target.resolve()}")
    print(
        f"planned={report.planned} copied={report.copied} "
        f"identical={report.identical}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Descriptor-relative Wiki writes that resist symlink and directory-swap races."""
from __future__ import annotations

import errno
import hashlib
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import frontmatter

from wiki_config import WikiConfigurationError, WikiPaths


@dataclass(frozen=True)
class FileSnapshot:
    exists: bool
    device: int = 0
    inode: int = 0
    mode: int = 0
    size: int = 0
    mtime_ns: int = 0
    sha256: str = ""
    content: bytes = b""


@dataclass(frozen=True)
class GeneratedOwner:
    generated_by: str
    node_type: str
    node_id: str


def _noop_before_commit(operation: str, path: Path) -> None:
    del operation, path


_before_commit: Callable[[str, Path], None] = _noop_before_commit
_after_snapshot: Callable[[str, Path], None] = _noop_before_commit


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


class PinnedWikiMutation:
    def __init__(self, paths: WikiPaths) -> None:
        self.paths = paths
        self.root_fd = -1
        self.root_identity: tuple[int, int] | None = None
        self.directory_fds: dict[Path, int] = {}

    def __enter__(self) -> "PinnedWikiMutation":
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if not nofollow:
            raise WikiConfigurationError("O_NOFOLLOW is required for Wiki mutations")
        try:
            self.root_fd = os.open(
                self.paths.root,
                os.O_RDONLY | os.O_DIRECTORY | nofollow,
            )
        except OSError as exc:
            raise WikiConfigurationError(
                f"Wiki Vault root is not safe: {self.paths.root}"
            ) from exc
        root_info = os.fstat(self.root_fd)
        self.root_identity = _identity(root_info)
        self._validate_root()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        for descriptor in self.directory_fds.values():
            os.close(descriptor)
        self.directory_fds.clear()
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1

    def _validate_root(self) -> None:
        try:
            path_info = self.paths.root.stat(follow_symlinks=False)
        except OSError as exc:
            raise WikiConfigurationError(
                f"Wiki Vault root is unavailable: {self.paths.root}"
            ) from exc
        descriptor_info = os.fstat(self.root_fd)
        if (
            not stat.S_ISDIR(path_info.st_mode)
            or _identity(path_info) != self.root_identity
            or _identity(descriptor_info) != self.root_identity
        ):
            raise WikiConfigurationError(
                f"Wiki Vault root path changed: {self.paths.root}"
            )

    def _relative_parts(self, path: Path) -> tuple[str, ...]:
        try:
            relative = path.relative_to(self.paths.root)
        except ValueError as exc:
            raise WikiConfigurationError(
                f"Wiki Vault mutation escapes root: {path}"
            ) from exc
        if len(relative.parts) not in {1, 2}:
            raise WikiConfigurationError(
                f"Wiki Vault mutation target is not directly managed: {path}"
            )
        return relative.parts

    def _directory(self, path: Path) -> tuple[int, str]:
        parts = self._relative_parts(path)
        self._validate_root()
        if len(parts) == 1:
            return self.root_fd, parts[0]

        directory = self.paths.root / parts[0]
        return self._managed_directory_fd(directory), parts[1]

    def _managed_directory_fd(self, directory: Path) -> int:
        parts = self._relative_parts(directory)
        if len(parts) != 1:
            raise WikiConfigurationError(
                f"Wiki Vault directory is not directly managed: {directory}"
            )
        self._validate_root()
        descriptor = self.directory_fds.get(directory)
        if descriptor is None:
            try:
                descriptor = os.open(
                    parts[0],
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=self.root_fd,
                )
            except OSError as exc:
                raise WikiConfigurationError(
                    f"Wiki Vault managed directory is not safe: {directory}"
                ) from exc
            self.directory_fds[directory] = descriptor
        self._validate_directory(directory, descriptor)
        return descriptor

    def _validate_directory(self, directory: Path, descriptor: int) -> None:
        self._validate_root()
        try:
            entry = os.stat(
                directory.name,
                dir_fd=self.root_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise WikiConfigurationError(
                f"Wiki Vault managed directory is unavailable: {directory}"
            ) from exc
        opened = os.fstat(descriptor)
        if (
            stat.S_ISLNK(entry.st_mode)
            or not stat.S_ISDIR(entry.st_mode)
            or _identity(entry) != _identity(opened)
        ):
            raise WikiConfigurationError(
                f"Wiki Vault managed directory changed: {directory}"
            )

    def snapshot(self, path: Path) -> FileSnapshot:
        directory_fd, name = self._directory(path)
        try:
            entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return FileSnapshot(exists=False)
            raise WikiConfigurationError(
                f"Wiki file is unavailable: {path}"
            ) from exc
        if stat.S_ISLNK(entry.st_mode) or not stat.S_ISREG(entry.st_mode):
            raise WikiConfigurationError(f"Wiki file is not a regular file: {path}")
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
        except OSError as exc:
            raise WikiConfigurationError(f"Wiki file is not safe: {path}") from exc
        try:
            opened = os.fstat(descriptor)
            if _identity(opened) != _identity(entry) or not stat.S_ISREG(opened.st_mode):
                raise WikiConfigurationError(f"Wiki file changed while opening: {path}")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            content = b"".join(chunks)
        finally:
            os.close(descriptor)
        return FileSnapshot(
            exists=True,
            device=opened.st_dev,
            inode=opened.st_ino,
            mode=opened.st_mode,
            size=opened.st_size,
            mtime_ns=opened.st_mtime_ns,
            sha256=hashlib.sha256(content).hexdigest(),
            content=content,
        )

    def list_paths(self, directory: Path, *, suffix: str = "") -> tuple[Path, ...]:
        """List direct managed-directory entries through its pinned descriptor."""
        descriptor = self._managed_directory_fd(directory)
        names = sorted(os.listdir(descriptor))
        self._validate_directory(directory, descriptor)
        return tuple(
            directory / name
            for name in names
            if name not in {".", ".."}
            and Path(name).name == name
            and (not suffix or name.endswith(suffix))
        )

    def open_lock_file(self, path: Path) -> int:
        """Open or create a regular lock file relative to a pinned directory."""
        directory_fd, name = self._directory(path)
        try:
            try:
                descriptor = os.open(
                    name,
                    os.O_RDWR | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                try:
                    descriptor = os.open(
                        name,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=directory_fd,
                    )
                except FileExistsError:
                    descriptor = os.open(
                        name,
                        os.O_RDWR | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
        except OSError as exc:
            raise WikiConfigurationError(f"Wiki lock file is not safe: {path}") from exc
        try:
            opened = os.fstat(descriptor)
            entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(entry.st_mode)
                or _identity(opened) != _identity(entry)
            ):
                raise WikiConfigurationError(f"Wiki lock file changed: {path}")
            self._directory(path)
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    @staticmethod
    def _validate_owner(
        path: Path, snapshot: FileSnapshot, owner: GeneratedOwner | None
    ) -> None:
        if owner is None or not snapshot.exists:
            return
        try:
            metadata = frontmatter.loads(snapshot.content.decode("utf-8")).metadata
        except Exception as exc:
            raise WikiConfigurationError(
                f"generated path collision: {path} is unreadable"
            ) from exc
        actual = (
            metadata.get("generated_by"),
            metadata.get("type"),
            metadata.get("id"),
        )
        expected = (owner.generated_by, owner.node_type, owner.node_id)
        if actual != expected:
            raise WikiConfigurationError(
                f"generated path collision: {path} owner {actual!r} != {expected!r}"
            )

    def replace_text(
        self,
        path: Path,
        content: str,
        *,
        expected: FileSnapshot,
        owner: GeneratedOwner | None = None,
    ) -> None:
        directory_fd, name = self._directory(path)
        temporary_name = f".{name}.{uuid.uuid4().hex}.tmp"
        quarantine_name: str | None = None
        quarantine_snapshot: FileSnapshot | None = None
        descriptor = -1
        try:
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            payload = content.encode("utf-8")
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError("Wiki temporary write made no progress")
                offset += written
            os.fchmod(descriptor, 0o644)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1

            _before_commit("replace", path)
            current = self.snapshot(path)
            if current != expected:
                raise WikiConfigurationError(
                    f"Wiki file changed before replacement: {path}"
                )
            self._validate_owner(path, current, owner)
            _after_snapshot("replace", path)
            directory_fd, name = self._directory(path)
            if expected.exists:
                quarantine_name, quarantine_snapshot = self._quarantine(
                    path,
                    directory_fd=directory_fd,
                    name=name,
                    expected=expected,
                    owner=owner,
                    operation="replacement",
                )
            try:
                os.link(
                    temporary_name,
                    name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                if quarantine_name is not None:
                    self._restore_quarantine(
                        path,
                        directory_fd=directory_fd,
                        name=name,
                        quarantine_name=quarantine_name,
                    )
                    quarantine_name = None
                raise WikiConfigurationError(
                    f"Wiki file appeared before replacement: {path}"
                ) from exc
            except OSError:
                if quarantine_name is not None:
                    self._restore_quarantine(
                        path,
                        directory_fd=directory_fd,
                        name=name,
                        quarantine_name=quarantine_name,
                    )
                    quarantine_name = None
                raise
            if quarantine_name is not None and quarantine_snapshot is not None:
                self._remove_quarantine(
                    path,
                    directory_fd=directory_fd,
                    quarantine_name=quarantine_name,
                    expected=quarantine_snapshot,
                )
                quarantine_name = None
            self._directory(path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except OSError as exc:
                if exc.errno != errno.ENOENT:
                    raise

    def _quarantine(
        self,
        path: Path,
        *,
        directory_fd: int,
        name: str,
        expected: FileSnapshot,
        owner: GeneratedOwner | None,
        operation: str,
    ) -> tuple[str, FileSnapshot]:
        quarantine_name = f".{name}.{uuid.uuid4().hex}.quarantine"
        try:
            os.rename(
                name,
                quarantine_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        except OSError as exc:
            raise WikiConfigurationError(
                f"Wiki file changed before {operation}: {path}"
            ) from exc
        quarantine_path = path.with_name(quarantine_name)
        try:
            moved = self.snapshot(quarantine_path)
        except Exception as exc:
            self._restore_quarantine(
                path,
                directory_fd=directory_fd,
                name=name,
                quarantine_name=quarantine_name,
            )
            raise WikiConfigurationError(
                f"Wiki file changed before {operation}: {path}"
            ) from exc
        if moved != expected:
            self._restore_quarantine(
                path,
                directory_fd=directory_fd,
                name=name,
                quarantine_name=quarantine_name,
            )
            raise WikiConfigurationError(
                f"Wiki file changed before {operation}: {path}"
            )
        try:
            self._validate_owner(path, moved, owner)
        except Exception:
            self._restore_quarantine(
                path,
                directory_fd=directory_fd,
                name=name,
                quarantine_name=quarantine_name,
            )
            raise
        return quarantine_name, moved

    def _restore_quarantine(
        self,
        path: Path,
        *,
        directory_fd: int,
        name: str,
        quarantine_name: str,
    ) -> None:
        try:
            os.link(
                quarantine_name,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise WikiConfigurationError(
                f"Wiki file changed while restoring quarantine: {path}; "
                f"preserved as {quarantine_name}"
            ) from exc
        os.unlink(quarantine_name, dir_fd=directory_fd)

    def _remove_quarantine(
        self,
        path: Path,
        *,
        directory_fd: int,
        quarantine_name: str,
        expected: FileSnapshot,
    ) -> None:
        quarantine_path = path.with_name(quarantine_name)
        if self.snapshot(quarantine_path) != expected:
            raise WikiConfigurationError(
                f"Wiki quarantine changed before cleanup: {quarantine_path}"
            )
        os.unlink(quarantine_name, dir_fd=directory_fd)

    def delete(
        self,
        path: Path,
        *,
        expected: FileSnapshot,
        owner: GeneratedOwner,
    ) -> None:
        _before_commit("delete", path)
        current = self.snapshot(path)
        if current != expected:
            raise WikiConfigurationError(f"Wiki file changed before deletion: {path}")
        self._validate_owner(path, current, owner)
        _after_snapshot("delete", path)
        directory_fd, name = self._directory(path)
        quarantine_name, quarantine_snapshot = self._quarantine(
            path,
            directory_fd=directory_fd,
            name=name,
            expected=expected,
            owner=owner,
            operation="deletion",
        )
        self._remove_quarantine(
            path,
            directory_fd=directory_fd,
            quarantine_name=quarantine_name,
            expected=quarantine_snapshot,
        )
        self._directory(path)

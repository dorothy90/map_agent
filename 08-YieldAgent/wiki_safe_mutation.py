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
        return descriptor, parts[1]

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
            directory_fd, name = self._directory(path)
            os.replace(
                temporary_name,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            self._directory(path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except OSError as exc:
                if exc.errno != errno.ENOENT:
                    raise

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
        directory_fd, name = self._directory(path)
        os.unlink(name, dir_fd=directory_fd)
        self._directory(path)

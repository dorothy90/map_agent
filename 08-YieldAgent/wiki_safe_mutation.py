"""Descriptor-relative Wiki writes that resist symlink and directory-swap races."""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
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


@dataclass(frozen=True)
class _MutationTransaction:
    record_name: str
    record_snapshot: FileSnapshot
    target_name: str
    quarantine_name: str
    operation: str
    expected: dict[str, int | str]
    proposed_sha256: str
    descriptor: int


_TRANSACTION_SUFFIX = ".yield-wiki-transaction"


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

    @staticmethod
    def _fsync_directory(directory_fd: int) -> None:
        os.fsync(directory_fd)

    @staticmethod
    def _snapshot_fingerprint(snapshot: FileSnapshot) -> dict[str, int | str]:
        return {
            "device": snapshot.device,
            "inode": snapshot.inode,
            "mode": snapshot.mode,
            "size": snapshot.size,
            "mtime_ns": snapshot.mtime_ns,
            "sha256": snapshot.sha256,
        }

    @staticmethod
    def _matches_fingerprint(
        snapshot: FileSnapshot,
        fingerprint: dict[str, int | str],
    ) -> bool:
        return snapshot.exists and all(
            getattr(snapshot, key) == value for key, value in fingerprint.items()
        )

    @staticmethod
    def _snapshot_from_descriptor(descriptor: int) -> FileSnapshot:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise WikiConfigurationError("Wiki file descriptor is not regular")
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        content = b"".join(chunks)
        final = os.fstat(descriptor)
        if (
            _identity(final) != _identity(opened)
            or final.st_size != opened.st_size
            or final.st_mtime_ns != opened.st_mtime_ns
        ):
            raise WikiConfigurationError("Wiki file changed while reading")
        return FileSnapshot(
            exists=True,
            device=final.st_dev,
            inode=final.st_ino,
            mode=final.st_mode,
            size=final.st_size,
            mtime_ns=final.st_mtime_ns,
            sha256=hashlib.sha256(content).hexdigest(),
            content=content,
        )

    def _snapshot_at(
        self,
        path: Path,
        *,
        directory_fd: int,
        name: str,
    ) -> FileSnapshot:
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
            snapshot = self._snapshot_from_descriptor(descriptor)
        finally:
            os.close(descriptor)
        return snapshot

    def snapshot(self, path: Path) -> FileSnapshot:
        directory_fd, name = self._directory(path)
        self._recover_transaction(path, directory_fd=directory_fd, name=name)
        return self._snapshot_at(path, directory_fd=directory_fd, name=name)

    def list_paths(self, directory: Path, *, suffix: str = "") -> tuple[Path, ...]:
        """List direct managed-directory entries through its pinned descriptor."""
        descriptor = self._managed_directory_fd(directory)
        names = sorted(os.listdir(descriptor))
        for record_name in (
            name for name in names if name.endswith(_TRANSACTION_SUFFIX)
        ):
            self._recover_record(
                directory,
                directory_fd=descriptor,
                record_name=record_name,
            )
        names = sorted(os.listdir(descriptor))
        self._validate_directory(directory, descriptor)
        return tuple(
            directory / name
            for name in names
            if name not in {".", ".."}
            and Path(name).name == name
            and (not suffix or name.endswith(suffix))
        )

    @staticmethod
    def _transaction_record_name(name: str) -> str:
        return f".{name}{_TRANSACTION_SUFFIX}"

    @staticmethod
    def _safe_entry_name(value: object) -> str:
        name = str(value or "")
        if not name or name in {".", ".."} or Path(name).name != name:
            raise WikiConfigurationError("Wiki transaction contains an unsafe name")
        return name

    def _start_transaction(
        self,
        path: Path,
        *,
        directory_fd: int,
        name: str,
        quarantine_name: str,
        operation: str,
        expected: FileSnapshot,
        proposed_sha256: str,
    ) -> _MutationTransaction:
        record_name = self._transaction_record_name(name)
        temporary_name = f".{record_name}.{uuid.uuid4().hex}.tmp"
        payload = json.dumps(
            {
                "version": 1,
                "operation": operation,
                "target": name,
                "quarantine": quarantine_name,
                "expected": self._snapshot_fingerprint(expected),
                "proposed_sha256": proposed_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor = -1
        published = False
        record_snapshot = FileSnapshot(exists=False)
        try:
            descriptor = os.open(
                temporary_name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError("Wiki transaction write made no progress")
                offset += written
            os.fsync(descriptor)
            try:
                os.link(
                    temporary_name,
                    record_name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise WikiConfigurationError(
                    f"Wiki mutation transaction already exists: {path}"
                ) from exc
            published = True
            self._fsync_directory(directory_fd)
            record_path = path.with_name(record_name)
            record_snapshot = self._snapshot_at(
                record_path,
                directory_fd=directory_fd,
                name=record_name,
            )
            if record_snapshot != self._snapshot_from_descriptor(descriptor):
                raise WikiConfigurationError(
                    f"Wiki mutation transaction changed while publishing: {path}"
                )
            os.unlink(temporary_name, dir_fd=directory_fd)
            self._fsync_directory(directory_fd)
            temporary_name = ""
            return _MutationTransaction(
                record_name=record_name,
                record_snapshot=record_snapshot,
                target_name=name,
                quarantine_name=quarantine_name,
                operation=operation,
                expected=self._snapshot_fingerprint(expected),
                proposed_sha256=proposed_sha256,
                descriptor=descriptor,
            )
        except BaseException:
            if published and record_snapshot.exists:
                try:
                    self._remove_bound_entry(
                        path.with_name(record_name),
                        directory_fd=directory_fd,
                        name=record_name,
                        expected=record_snapshot,
                        bound_descriptor=descriptor,
                    )
                except Exception:
                    pass
            if descriptor >= 0:
                os.close(descriptor)
            raise
        finally:
            if temporary_name:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                    self._fsync_directory(directory_fd)
                except OSError as exc:
                    if exc.errno != errno.ENOENT:
                        raise

    def _load_transaction(
        self,
        directory: Path,
        *,
        directory_fd: int,
        record_name: str,
        expected_target: str | None = None,
    ) -> _MutationTransaction:
        record_path = directory / record_name
        record_snapshot = self._snapshot_at(
            record_path,
            directory_fd=directory_fd,
            name=record_name,
        )
        if not record_snapshot.exists:
            raise WikiConfigurationError(
                f"Wiki mutation transaction disappeared: {record_path}"
            )
        try:
            descriptor = os.open(
                record_name,
                os.O_RDWR | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
        except OSError as exc:
            raise WikiConfigurationError(
                f"Wiki mutation transaction is not safe: {record_path}"
            ) from exc
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WikiConfigurationError(
                    f"Wiki mutation transaction is active: {record_path}"
                ) from exc
            opened = self._snapshot_from_descriptor(descriptor)
            current = self._snapshot_at(
                record_path,
                directory_fd=directory_fd,
                name=record_name,
            )
            if opened != record_snapshot or current != record_snapshot:
                raise WikiConfigurationError(
                    f"Wiki mutation transaction changed: {record_path}"
                )
            try:
                data = json.loads(opened.content.decode("utf-8"))
                target_name = self._safe_entry_name(data["target"])
                quarantine_name = self._safe_entry_name(data["quarantine"])
                operation = str(data["operation"])
                expected = dict(data["expected"])
                proposed_sha256 = str(data.get("proposed_sha256") or "")
            except Exception as exc:
                raise WikiConfigurationError(
                    f"Wiki mutation transaction is invalid: {record_path}"
                ) from exc
            if (
                data.get("version") != 1
                or operation not in {"replacement", "deletion"}
                or record_name != self._transaction_record_name(target_name)
                or (expected_target is not None and target_name != expected_target)
                or not quarantine_name.endswith(".quarantine")
                or set(expected)
                != {"device", "inode", "mode", "size", "mtime_ns", "sha256"}
            ):
                raise WikiConfigurationError(
                    f"Wiki mutation transaction is invalid: {record_path}"
                )
            return _MutationTransaction(
                record_name=record_name,
                record_snapshot=record_snapshot,
                target_name=target_name,
                quarantine_name=quarantine_name,
                operation=operation,
                expected=expected,
                proposed_sha256=proposed_sha256,
                descriptor=descriptor,
            )
        except BaseException:
            os.close(descriptor)
            raise

    def _recover_transaction(
        self,
        path: Path,
        *,
        directory_fd: int,
        name: str,
    ) -> None:
        record_name = self._transaction_record_name(name)
        record_path = path.with_name(record_name)
        if not self._snapshot_at(
            record_path,
            directory_fd=directory_fd,
            name=record_name,
        ).exists:
            return
        transaction = self._load_transaction(
            path.parent,
            directory_fd=directory_fd,
            record_name=record_name,
            expected_target=name,
        )
        try:
            self._recover_loaded_transaction(
                path,
                directory_fd=directory_fd,
                transaction=transaction,
            )
        finally:
            os.close(transaction.descriptor)

    def _recover_record(
        self,
        directory: Path,
        *,
        directory_fd: int,
        record_name: str,
    ) -> None:
        transaction = self._load_transaction(
            directory,
            directory_fd=directory_fd,
            record_name=record_name,
        )
        try:
            self._recover_loaded_transaction(
                directory / transaction.target_name,
                directory_fd=directory_fd,
                transaction=transaction,
            )
        finally:
            os.close(transaction.descriptor)

    def _recover_loaded_transaction(
        self,
        path: Path,
        *,
        directory_fd: int,
        transaction: _MutationTransaction,
    ) -> None:
        target = self._snapshot_at(
            path,
            directory_fd=directory_fd,
            name=transaction.target_name,
        )
        quarantine_path = path.with_name(transaction.quarantine_name)
        quarantine = self._snapshot_at(
            quarantine_path,
            directory_fd=directory_fd,
            name=transaction.quarantine_name,
        )
        target_is_expected = self._matches_fingerprint(target, transaction.expected)
        quarantine_is_expected = self._matches_fingerprint(
            quarantine, transaction.expected
        )
        target_is_proposed = (
            transaction.operation == "replacement"
            and target.exists
            and target.sha256 == transaction.proposed_sha256
        )

        if not target.exists and quarantine_is_expected:
            self._restore_quarantine(
                path,
                directory_fd=directory_fd,
                name=transaction.target_name,
                quarantine_name=transaction.quarantine_name,
                expected=quarantine,
            )
            self._finish_transaction(path, directory_fd, transaction)
            return
        if not quarantine.exists and (
            target_is_expected
            or target_is_proposed
            or (transaction.operation == "deletion" and not target.exists)
        ):
            self._finish_transaction(path, directory_fd, transaction)
            return
        if quarantine_is_expected and (target_is_expected or target_is_proposed):
            self._remove_quarantine(
                path,
                directory_fd=directory_fd,
                quarantine_name=transaction.quarantine_name,
                expected=quarantine,
            )
            self._finish_transaction(path, directory_fd, transaction)
            return
        raise WikiConfigurationError(
            f"Wiki mutation recovery conflict: {path}; operator data preserved"
        )

    def _finish_transaction(
        self,
        path: Path,
        directory_fd: int,
        transaction: _MutationTransaction,
    ) -> None:
        self._remove_bound_entry(
            path.with_name(transaction.record_name),
            directory_fd=directory_fd,
            name=transaction.record_name,
            expected=transaction.record_snapshot,
            bound_descriptor=transaction.descriptor,
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
        transaction: _MutationTransaction | None = None
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
                transaction, quarantine_snapshot = self._quarantine(
                    path,
                    directory_fd=directory_fd,
                    name=name,
                    expected=expected,
                    owner=owner,
                    operation="replacement",
                    proposed_sha256=hashlib.sha256(payload).hexdigest(),
                )
            try:
                os.link(
                    temporary_name,
                    name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                self._fsync_directory(directory_fd)
            except FileExistsError as exc:
                if transaction is not None and quarantine_snapshot is not None:
                    self._restore_quarantine(
                        path,
                        directory_fd=directory_fd,
                        name=name,
                        quarantine_name=transaction.quarantine_name,
                        expected=quarantine_snapshot,
                    )
                    self._finish_transaction(path, directory_fd, transaction)
                raise WikiConfigurationError(
                    f"Wiki file appeared before replacement: {path}"
                ) from exc
            except OSError:
                if transaction is not None and quarantine_snapshot is not None:
                    self._restore_quarantine(
                        path,
                        directory_fd=directory_fd,
                        name=name,
                        quarantine_name=transaction.quarantine_name,
                        expected=quarantine_snapshot,
                    )
                    self._finish_transaction(path, directory_fd, transaction)
                raise
            if transaction is not None and quarantine_snapshot is not None:
                self._remove_quarantine(
                    path,
                    directory_fd=directory_fd,
                    quarantine_name=transaction.quarantine_name,
                    expected=quarantine_snapshot,
                )
                self._finish_transaction(path, directory_fd, transaction)
            self._directory(path)
        finally:
            if transaction is not None:
                os.close(transaction.descriptor)
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
                self._fsync_directory(directory_fd)
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
        proposed_sha256: str,
    ) -> tuple[_MutationTransaction, FileSnapshot]:
        quarantine_name = f".{name}.{uuid.uuid4().hex}.quarantine"
        transaction = self._start_transaction(
            path,
            directory_fd=directory_fd,
            name=name,
            quarantine_name=quarantine_name,
            operation=operation,
            expected=expected,
            proposed_sha256=proposed_sha256,
        )
        try:
            os.rename(
                name,
                quarantine_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            self._fsync_directory(directory_fd)
        except BaseException as exc:
            os.close(transaction.descriptor)
            if isinstance(exc, OSError):
                raise WikiConfigurationError(
                    f"Wiki file changed before {operation}: {path}"
                ) from exc
            raise
        quarantine_path = path.with_name(quarantine_name)
        try:
            moved = self.snapshot(quarantine_path)
        except Exception as exc:
            os.close(transaction.descriptor)
            raise WikiConfigurationError(
                f"Wiki file changed before {operation}: {path}"
            ) from exc
        if moved != expected:
            try:
                self._restore_quarantine(
                    path,
                    directory_fd=directory_fd,
                    name=name,
                    quarantine_name=quarantine_name,
                    expected=moved,
                )
                self._finish_transaction(path, directory_fd, transaction)
            finally:
                os.close(transaction.descriptor)
            raise WikiConfigurationError(
                f"Wiki file changed before {operation}: {path}"
            )
        try:
            self._validate_owner(path, moved, owner)
        except Exception:
            try:
                self._restore_quarantine(
                    path,
                    directory_fd=directory_fd,
                    name=name,
                    quarantine_name=quarantine_name,
                    expected=moved,
                )
                self._finish_transaction(path, directory_fd, transaction)
            finally:
                os.close(transaction.descriptor)
            raise
        return transaction, moved

    def _remove_bound_entry(
        self,
        path: Path,
        *,
        directory_fd: int,
        name: str,
        expected: FileSnapshot,
        bound_descriptor: int | None = None,
    ) -> None:
        if self.snapshot(path) != expected:
            raise WikiConfigurationError(f"Wiki file changed before cleanup: {path}")
        descriptor = bound_descriptor
        close_descriptor = descriptor is None
        if descriptor is None:
            try:
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
            except OSError as exc:
                raise WikiConfigurationError(
                    f"Wiki file changed before cleanup: {path}"
                ) from exc
        try:
            if self._snapshot_from_descriptor(descriptor) != expected:
                raise WikiConfigurationError(
                    f"Wiki file changed before cleanup: {path}"
                )
            cleanup_name = f".{name}.{uuid.uuid4().hex}.cleanup"
            os.rename(
                name,
                cleanup_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            self._fsync_directory(directory_fd)
            try:
                entry = os.stat(
                    cleanup_name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                opened = os.fstat(descriptor)
                moved = self._snapshot_at(
                    path.with_name(cleanup_name),
                    directory_fd=directory_fd,
                    name=cleanup_name,
                )
            except OSError as exc:
                raise WikiConfigurationError(
                    f"Wiki file changed before cleanup: {path}"
                ) from exc
            if _identity(entry) != _identity(opened) or moved != expected:
                try:
                    os.link(
                        cleanup_name,
                        name,
                        src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    self._fsync_directory(directory_fd)
                except FileExistsError:
                    pass
                raise WikiConfigurationError(
                    f"Wiki file changed before cleanup: {path}; data preserved"
                )
            os.unlink(cleanup_name, dir_fd=directory_fd)
            self._fsync_directory(directory_fd)
        finally:
            if close_descriptor:
                os.close(descriptor)

    def _restore_quarantine(
        self,
        path: Path,
        *,
        directory_fd: int,
        name: str,
        quarantine_name: str,
        expected: FileSnapshot,
    ) -> None:
        try:
            os.link(
                quarantine_name,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
            self._fsync_directory(directory_fd)
        except FileExistsError as exc:
            raise WikiConfigurationError(
                f"Wiki file changed while restoring quarantine: {path}; "
                f"preserved as {quarantine_name}"
            ) from exc
        self._remove_bound_entry(
            path.with_name(quarantine_name),
            directory_fd=directory_fd,
            name=quarantine_name,
            expected=expected,
        )

    def _remove_quarantine(
        self,
        path: Path,
        *,
        directory_fd: int,
        quarantine_name: str,
        expected: FileSnapshot,
    ) -> None:
        quarantine_path = path.with_name(quarantine_name)
        self._remove_bound_entry(
            quarantine_path,
            directory_fd=directory_fd,
            name=quarantine_name,
            expected=expected,
        )

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
        transaction: _MutationTransaction | None = None
        try:
            transaction, quarantine_snapshot = self._quarantine(
                path,
                directory_fd=directory_fd,
                name=name,
                expected=expected,
                owner=owner,
                operation="deletion",
                proposed_sha256="",
            )
            self._remove_quarantine(
                path,
                directory_fd=directory_fd,
                quarantine_name=transaction.quarantine_name,
                expected=quarantine_snapshot,
            )
            self._finish_transaction(path, directory_fd, transaction)
            self._directory(path)
        finally:
            if transaction is not None:
                os.close(transaction.descriptor)

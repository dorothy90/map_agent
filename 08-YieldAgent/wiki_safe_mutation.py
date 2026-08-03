"""Descriptor-relative Wiki writes that resist symlink and directory-swap races."""
from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import stat
import sys
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


@dataclass
class _MutationTransaction:
    record_name: str
    record_snapshot: FileSnapshot
    target_name: str
    quarantine_name: str
    tombstone_name: str
    record_tombstone_name: str
    proposal_name: str
    operation: str
    expected: dict[str, int | str]
    proposed_sha256: str
    proposal_expected: dict[str, int | str]
    state: str
    outcome: str
    descriptor: int


_TRANSACTION_SUFFIX = ".yield-wiki-transaction"
_TOMBSTONE_SUFFIX = ".yield-wiki-tombstone"
_TRANSACTION_TOMBSTONE_SUFFIX = ".yield-wiki-transaction-tombstone"
_ATTEMPT_SUFFIX = ".yield-wiki-attempt"


def _rename_noreplace(
    source: str,
    destination: str,
    *,
    src_dir_fd: int,
    dst_dir_fd: int,
) -> None:
    """Atomically move one directory entry without replacing another."""
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        rename = libc.renameatx_np
        rename.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename.restype = ctypes.c_int
        result = rename(
            src_dir_fd,
            source_bytes,
            dst_dir_fd,
            destination_bytes,
            0x00000004,  # RENAME_EXCL from sys/stdio.h.
        )
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename.restype = ctypes.c_int
        result = rename(
            src_dir_fd,
            source_bytes,
            dst_dir_fd,
            destination_bytes,
            0x00000001,  # RENAME_NOREPLACE from linux/fs.h.
        )
    else:
        raise WikiConfigurationError(
            "Atomic no-replace rename is required for Wiki mutations"
        )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination)


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

    def _start_attempt(
        self,
        path: Path,
        *,
        directory_fd: int,
        name: str,
        proposal_name: str,
        expected: FileSnapshot,
        proposed_sha256: str,
    ) -> tuple[str, int]:
        attempt_name = f".{name}.{uuid.uuid4().hex}{_ATTEMPT_SUFFIX}"
        payload = json.dumps(
            {
                "version": 1,
                "kind": "proposal_attempt",
                "record": attempt_name,
                "target": name,
                "proposal": proposal_name,
                "expected": self._snapshot_fingerprint(expected),
                "proposed_sha256": proposed_sha256,
                "state": "preparing",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        descriptor = -1
        try:
            descriptor = os.open(
                attempt_name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError("Wiki mutation attempt write made no progress")
                offset += written
            os.fsync(descriptor)
            self._fsync_directory(directory_fd)
            opened = self._snapshot_from_descriptor(descriptor)
            current = self._snapshot_at(
                path.with_name(attempt_name),
                directory_fd=directory_fd,
                name=attempt_name,
            )
            if opened != current:
                raise WikiConfigurationError(
                    f"Wiki mutation attempt changed while publishing: {path}"
                )
            return attempt_name, descriptor
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    def _record_attempt_state(
        self,
        path: Path,
        *,
        directory_fd: int,
        attempt_name: str,
        descriptor: int,
        state: str,
        proposal_expected: FileSnapshot | None = None,
    ) -> None:
        opened = self._snapshot_from_descriptor(descriptor)
        current = self._snapshot_at(
            path.with_name(attempt_name),
            directory_fd=directory_fd,
            name=attempt_name,
        )
        if opened != current:
            raise WikiConfigurationError(f"Wiki mutation attempt changed: {path}")
        event: dict[str, object] = {"event": "state", "state": state}
        if proposal_expected is not None:
            event["proposal_expected"] = self._snapshot_fingerprint(
                proposal_expected
            )
        payload = json.dumps(
            event,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        os.lseek(descriptor, 0, os.SEEK_END)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("Wiki mutation attempt update made no progress")
            offset += written
        os.fsync(descriptor)
        opened = self._snapshot_from_descriptor(descriptor)
        current = self._snapshot_at(
            path.with_name(attempt_name),
            directory_fd=directory_fd,
            name=attempt_name,
        )
        if opened != current:
            raise WikiConfigurationError(f"Wiki mutation attempt changed: {path}")

    def _publish_proposal(
        self,
        path: Path,
        *,
        directory_fd: int,
        proposal_name: str,
        proposal_descriptor: int,
        proposal_expected: FileSnapshot,
        proposed_sha256: str,
    ) -> None:
        conflict = (
            f"Wiki proposal publication conflict: {path}; operator data preserved"
        )
        try:
            opened = self._snapshot_from_descriptor(proposal_descriptor)
            proposal = self._snapshot_at(
                path.with_name(proposal_name),
                directory_fd=directory_fd,
                name=proposal_name,
            )
        except WikiConfigurationError as exc:
            raise WikiConfigurationError(conflict) from exc
        if (
            opened != proposal_expected
            or proposal != proposal_expected
            or proposal_expected.sha256 != proposed_sha256
        ):
            raise WikiConfigurationError(conflict)
        _rename_noreplace(
            proposal_name,
            path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        self._fsync_directory(directory_fd)
        try:
            opened = self._snapshot_from_descriptor(proposal_descriptor)
            published = self._snapshot_at(
                path,
                directory_fd=directory_fd,
                name=path.name,
            )
        except WikiConfigurationError as exc:
            raise WikiConfigurationError(conflict) from exc
        if (
            opened != proposal_expected
            or published != proposal_expected
            or published.sha256 != proposed_sha256
        ):
            raise WikiConfigurationError(conflict)

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
        proposal_name: str,
        proposal_expected: FileSnapshot | None = None,
        initial_state: str = "quarantining",
    ) -> _MutationTransaction:
        record_name = self._transaction_record_name(name)
        transaction_id = uuid.uuid4().hex
        temporary_name = f".{name}.{transaction_id}{_ATTEMPT_SUFFIX}"
        tombstone_name = f".{name}.{transaction_id}{_TOMBSTONE_SUFFIX}"
        record_tombstone_name = (
            f".{name}.{transaction_id}{_TRANSACTION_TOMBSTONE_SUFFIX}"
        )
        payload = json.dumps(
            {
                "version": 3,
                "kind": "mutation_transaction",
                "draft": temporary_name,
                "operation": operation,
                "target": name,
                "quarantine": quarantine_name,
                "tombstone": tombstone_name,
                "record_tombstone": record_tombstone_name,
                "proposal": proposal_name,
                "expected": self._snapshot_fingerprint(expected),
                "proposed_sha256": proposed_sha256,
                "proposal_expected": (
                    self._snapshot_fingerprint(proposal_expected)
                    if proposal_expected is not None
                    else {}
                ),
                "state": initial_state,
                "outcome": "",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        descriptor = -1
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
            self._fsync_directory(directory_fd)
            try:
                _rename_noreplace(
                    temporary_name,
                    record_name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
            except FileExistsError as exc:
                raise WikiConfigurationError(
                    f"Wiki mutation transaction already exists: {path}"
                ) from exc
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
            return _MutationTransaction(
                record_name=record_name,
                record_snapshot=record_snapshot,
                target_name=name,
                quarantine_name=quarantine_name,
                tombstone_name=tombstone_name,
                record_tombstone_name=record_tombstone_name,
                proposal_name=proposal_name,
                operation=operation,
                expected=self._snapshot_fingerprint(expected),
                proposed_sha256=proposed_sha256,
                proposal_expected=(
                    self._snapshot_fingerprint(proposal_expected)
                    if proposal_expected is not None
                    else {}
                ),
                state=initial_state,
                outcome="",
                descriptor=descriptor,
            )
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
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
                content = opened.content
                if b"\n" in content:
                    complete = content
                    if not complete.endswith(b"\n"):
                        complete = complete[: complete.rfind(b"\n") + 1]
                    entries = [
                        json.loads(line.decode("utf-8"))
                        for line in complete.splitlines()
                        if line
                    ]
                else:
                    entries = [json.loads(content.decode("utf-8"))]
                data = entries[0]
                target_name = self._safe_entry_name(data["target"])
                quarantine_name = self._safe_entry_name(data["quarantine"])
                operation = str(data["operation"])
                expected = dict(data["expected"])
                proposed_sha256 = str(data.get("proposed_sha256") or "")
                version = int(data["version"])
                if version == 1:
                    transaction_id = hashlib.sha256(
                        quarantine_name.encode("utf-8")
                    ).hexdigest()[:32]
                    tombstone_name = self._safe_entry_name(
                        f"{quarantine_name.removesuffix('.quarantine')}"
                        f"{_TOMBSTONE_SUFFIX}"
                    )
                    record_tombstone_name = self._safe_entry_name(
                        f".{target_name}.{transaction_id}"
                        f"{_TRANSACTION_TOMBSTONE_SUFFIX}"
                    )
                    state = "legacy"
                    outcome = ""
                    proposal_name = ""
                    proposal_expected: dict[str, int | str] = {}
                elif version in {2, 3}:
                    tombstone_name = self._safe_entry_name(data["tombstone"])
                    record_tombstone_name = self._safe_entry_name(
                        data["record_tombstone"]
                    )
                    state = str(data["state"])
                    outcome = str(data.get("outcome") or "")
                    proposal_name = str(data.get("proposal") or "")
                    proposal_expected = (
                        dict(data.get("proposal_expected") or {})
                        if version == 3
                        else {}
                    )
                else:
                    raise ValueError("unsupported transaction version")
                for event in entries[1:]:
                    if event.get("event") != "state":
                        raise ValueError("unsupported transaction event")
                    state = str(event["state"])
                    outcome = str(event.get("outcome") or "")
            except Exception as exc:
                raise WikiConfigurationError(
                    f"Wiki mutation transaction is invalid: {record_path}"
                ) from exc
            if (
                operation not in {"replacement", "creation", "deletion"}
                or record_name != self._transaction_record_name(target_name)
                or (expected_target is not None and target_name != expected_target)
                or not quarantine_name.endswith(".quarantine")
                or not tombstone_name.endswith(_TOMBSTONE_SUFFIX)
                or not record_tombstone_name.endswith(
                    _TRANSACTION_TOMBSTONE_SUFFIX
                )
                or state
                not in {
                    "legacy",
                    "quarantining",
                    "publishing",
                    "restoring",
                    "deleting",
                    "tombstoning",
                    "record_tombstoning",
                }
                or outcome
                not in {"", "replacement", "creation", "restoration", "deletion"}
                or (
                    proposal_name
                    and (
                        Path(proposal_name).name != proposal_name
                        or not proposal_name.endswith(".tmp")
                    )
                )
                or set(expected)
                != {"device", "inode", "mode", "size", "mtime_ns", "sha256"}
                or (
                    proposal_expected
                    and set(proposal_expected)
                    != {"device", "inode", "mode", "size", "mtime_ns", "sha256"}
                )
                or (
                    operation in {"replacement", "creation"}
                    and version == 3
                    and not proposal_expected
                )
                or (operation == "creation" and version != 3)
                or (
                    proposal_expected
                    and proposed_sha256 != proposal_expected["sha256"]
                )
            ):
                raise WikiConfigurationError(
                    f"Wiki mutation transaction is invalid: {record_path}"
                )
            return _MutationTransaction(
                record_name=record_name,
                record_snapshot=record_snapshot,
                target_name=target_name,
                quarantine_name=quarantine_name,
                tombstone_name=tombstone_name,
                record_tombstone_name=record_tombstone_name,
                proposal_name=proposal_name,
                operation=operation,
                expected=expected,
                proposed_sha256=proposed_sha256,
                proposal_expected=proposal_expected,
                state=state,
                outcome=outcome,
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

    def _record_transaction_state(
        self,
        path: Path,
        *,
        directory_fd: int,
        transaction: _MutationTransaction,
        state: str,
        outcome: str = "",
    ) -> None:
        opened = self._snapshot_from_descriptor(transaction.descriptor)
        current = self._snapshot_at(
            path.with_name(transaction.record_name),
            directory_fd=directory_fd,
            name=transaction.record_name,
        )
        if opened != current:
            raise WikiConfigurationError(
                f"Wiki mutation transaction changed: {path}"
            )
        if b"\n" in opened.content and not opened.content.endswith(b"\n"):
            durable_length = opened.content.rfind(b"\n") + 1
            os.ftruncate(transaction.descriptor, durable_length)
            os.fsync(transaction.descriptor)
            opened = self._snapshot_from_descriptor(transaction.descriptor)
            transaction.record_snapshot = opened
        event = json.dumps(
            {"event": "state", "state": state, "outcome": outcome},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if opened.content and not opened.content.endswith(b"\n"):
            event = b"\n" + event
        event += b"\n"
        os.lseek(transaction.descriptor, 0, os.SEEK_END)
        offset = 0
        while offset < len(event):
            written = os.write(transaction.descriptor, event[offset:])
            if written <= 0:
                raise OSError("Wiki transaction update made no progress")
            offset += written
        os.fsync(transaction.descriptor)
        transaction.state = state
        transaction.outcome = outcome
        transaction.record_snapshot = self._snapshot_from_descriptor(
            transaction.descriptor
        )
        current = self._snapshot_at(
            path.with_name(transaction.record_name),
            directory_fd=directory_fd,
            name=transaction.record_name,
        )
        if current != transaction.record_snapshot:
            raise WikiConfigurationError(
                f"Wiki mutation transaction changed: {path}"
            )

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
        tombstone = self._snapshot_at(
            path.with_name(transaction.tombstone_name),
            directory_fd=directory_fd,
            name=transaction.tombstone_name,
        )
        target_is_expected = self._matches_fingerprint(target, transaction.expected)
        quarantine_is_expected = self._matches_fingerprint(
            quarantine, transaction.expected
        )
        tombstone_is_expected = self._matches_fingerprint(
            tombstone, transaction.expected
        )
        target_is_proposed = (
            transaction.operation in {"replacement", "creation"}
            and target.exists
            and target.sha256 == transaction.proposed_sha256
            and (
                self._matches_fingerprint(target, transaction.proposal_expected)
                if transaction.proposal_expected
                else True
            )
        )

        if transaction.state in {"legacy", "quarantining"}:
            if target_is_expected and not quarantine.exists and not tombstone.exists:
                self._finish_transaction(
                    path, directory_fd, transaction, outcome="restoration"
                )
                return
            if not target.exists and quarantine_is_expected:
                self._restore_quarantine(
                    path,
                    directory_fd=directory_fd,
                    transaction=transaction,
                    expected=quarantine,
                )
                self._finish_transaction(
                    path, directory_fd, transaction, outcome="restoration"
                )
                return
            if transaction.state == "legacy" and not quarantine.exists:
                if target_is_expected:
                    outcome = "restoration"
                elif target_is_proposed:
                    outcome = "replacement"
                elif transaction.operation == "deletion" and not target.exists:
                    outcome = "deletion"
                else:
                    outcome = ""
                if outcome:
                    self._finish_transaction(
                        path, directory_fd, transaction, outcome=outcome
                    )
                    return
            if transaction.state == "legacy" and quarantine_is_expected:
                if target_is_expected:
                    outcome = "restoration"
                elif target_is_proposed:
                    outcome = "replacement"
                else:
                    outcome = ""
                if outcome:
                    self._tombstone_quarantine(
                        path,
                        directory_fd=directory_fd,
                        transaction=transaction,
                        expected=quarantine,
                        outcome=outcome,
                    )
                    self._finish_transaction(
                        path, directory_fd, transaction, outcome=outcome
                    )
                    return

        if transaction.state == "publishing":
            if transaction.operation == "creation":
                if target_is_proposed and not quarantine.exists and not tombstone.exists:
                    self._finish_transaction(
                        path, directory_fd, transaction, outcome="creation"
                    )
                    return
                if (
                    not target.exists
                    and not quarantine.exists
                    and not tombstone.exists
                    and transaction.proposal_name
                    and transaction.proposal_expected
                ):
                    proposal_path = path.with_name(transaction.proposal_name)
                    proposal = self._snapshot_at(
                        proposal_path,
                        directory_fd=directory_fd,
                        name=transaction.proposal_name,
                    )
                    if self._matches_fingerprint(
                        proposal, transaction.proposal_expected
                    ):
                        try:
                            descriptor = os.open(
                                transaction.proposal_name,
                                os.O_RDONLY | os.O_NOFOLLOW,
                                dir_fd=directory_fd,
                            )
                        except OSError as exc:
                            raise WikiConfigurationError(
                                f"Wiki mutation recovery conflict: {path}; "
                                "operator data preserved"
                            ) from exc
                        try:
                            self._publish_proposal(
                                path,
                                directory_fd=directory_fd,
                                proposal_name=transaction.proposal_name,
                                proposal_descriptor=descriptor,
                                proposal_expected=proposal,
                                proposed_sha256=transaction.proposed_sha256,
                            )
                        finally:
                            os.close(descriptor)
                        self._finish_transaction(
                            path, directory_fd, transaction, outcome="creation"
                        )
                        return
            if target_is_proposed and (quarantine_is_expected or tombstone_is_expected):
                expected = quarantine if quarantine_is_expected else tombstone
                self._tombstone_quarantine(
                    path,
                    directory_fd=directory_fd,
                    transaction=transaction,
                    expected=expected,
                    outcome="replacement",
                )
                self._finish_transaction(
                    path, directory_fd, transaction, outcome="replacement"
                )
                return
            if (not target.exists or target_is_expected) and (
                quarantine_is_expected or tombstone_is_expected
            ):
                expected = quarantine if quarantine_is_expected else tombstone
                self._restore_quarantine(
                    path,
                    directory_fd=directory_fd,
                    transaction=transaction,
                    expected=expected,
                )
                self._finish_transaction(
                    path, directory_fd, transaction, outcome="restoration"
                )
                return

        if transaction.state == "restoring" and (
            quarantine_is_expected or tombstone_is_expected
        ):
            if not target.exists or target_is_expected:
                expected = quarantine if quarantine_is_expected else tombstone
                self._restore_quarantine(
                    path,
                    directory_fd=directory_fd,
                    transaction=transaction,
                    expected=expected,
                )
                self._finish_transaction(
                    path, directory_fd, transaction, outcome="restoration"
                )
                return

        if (
            transaction.state == "deleting"
            and not target.exists
            and (quarantine_is_expected or tombstone_is_expected)
        ):
            expected = quarantine if quarantine_is_expected else tombstone
            self._tombstone_quarantine(
                path,
                directory_fd=directory_fd,
                transaction=transaction,
                expected=expected,
                outcome="deletion",
            )
            self._finish_transaction(
                path, directory_fd, transaction, outcome="deletion"
            )
            return

        if transaction.state in {"tombstoning", "record_tombstoning"}:
            if (
                transaction.outcome == "creation"
                and target_is_proposed
                and not quarantine.exists
                and not tombstone.exists
            ):
                self._finish_transaction(
                    path,
                    directory_fd,
                    transaction,
                    outcome="creation",
                )
                return
            outcome_matches = (
                (transaction.outcome == "replacement" and target_is_proposed)
                or (transaction.outcome == "restoration" and target_is_expected)
                or (transaction.outcome == "deletion" and not target.exists)
            )
            if outcome_matches and (quarantine_is_expected or tombstone_is_expected):
                expected = quarantine if quarantine_is_expected else tombstone
                self._tombstone_quarantine(
                    path,
                    directory_fd=directory_fd,
                    transaction=transaction,
                    expected=expected,
                    outcome=transaction.outcome,
                )
                self._finish_transaction(
                    path,
                    directory_fd,
                    transaction,
                    outcome=transaction.outcome,
                )
                return
        raise WikiConfigurationError(
            f"Wiki mutation recovery conflict: {path}; operator data preserved"
        )

    def _finish_transaction(
        self,
        path: Path,
        directory_fd: int,
        transaction: _MutationTransaction,
        *,
        outcome: str,
    ) -> None:
        if (
            transaction.state != "record_tombstoning"
            or transaction.outcome != outcome
        ):
            self._record_transaction_state(
                path,
                directory_fd=directory_fd,
                transaction=transaction,
                state="record_tombstoning",
                outcome=outcome,
            )
        opened = self._snapshot_from_descriptor(transaction.descriptor)
        record_path = path.with_name(transaction.record_name)
        current = self._snapshot_at(
            record_path,
            directory_fd=directory_fd,
            name=transaction.record_name,
        )
        tombstone_path = path.with_name(transaction.record_tombstone_name)
        tombstone = self._snapshot_at(
            tombstone_path,
            directory_fd=directory_fd,
            name=transaction.record_tombstone_name,
        )
        if not current.exists:
            if tombstone == opened:
                return
            raise WikiConfigurationError(
                f"Wiki transaction record changed before tombstoning: {path}; "
                "operator data preserved"
            )
        if current != opened or tombstone.exists:
            raise WikiConfigurationError(
                f"Wiki transaction record changed before tombstoning: {path}; "
                "operator data preserved"
            )
        _rename_noreplace(
            transaction.record_name,
            transaction.record_tombstone_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        self._fsync_directory(directory_fd)
        moved = self._snapshot_at(
            tombstone_path,
            directory_fd=directory_fd,
            name=transaction.record_tombstone_name,
        )
        if moved != opened:
            raise WikiConfigurationError(
                f"Wiki transaction record changed while tombstoning: {path}; "
                "operator data preserved"
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
        payload = content.encode("utf-8")
        proposed_sha256 = hashlib.sha256(payload).hexdigest()
        temporary_name = f".{name}.{uuid.uuid4().hex}.tmp"
        attempt_name = ""
        attempt_descriptor = -1
        transaction: _MutationTransaction | None = None
        quarantine_snapshot: FileSnapshot | None = None
        proposal_descriptor = -1

        def record_attempt_state(
            state: str,
            proposal_expected: FileSnapshot | None = None,
        ) -> None:
            self._record_attempt_state(
                path,
                directory_fd=directory_fd,
                attempt_name=attempt_name,
                descriptor=attempt_descriptor,
                state=state,
                proposal_expected=proposal_expected,
            )

        try:
            attempt_name, attempt_descriptor = self._start_attempt(
                path,
                directory_fd=directory_fd,
                name=name,
                proposal_name=temporary_name,
                expected=expected,
                proposed_sha256=proposed_sha256,
            )
            proposal_descriptor = os.open(
                temporary_name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            offset = 0
            while offset < len(payload):
                written = os.write(proposal_descriptor, payload[offset:])
                if written <= 0:
                    raise OSError("Wiki temporary write made no progress")
                offset += written
            os.fchmod(proposal_descriptor, 0o644)
            os.fsync(proposal_descriptor)
            self._fsync_directory(directory_fd)
            proposal_snapshot = self._snapshot_from_descriptor(proposal_descriptor)
            if proposal_snapshot.sha256 != proposed_sha256:
                raise WikiConfigurationError(
                    f"Wiki proposal changed while preparing: {path}"
                )
            record_attempt_state("proposal_ready", proposal_snapshot)

            _before_commit("replace", path)
            try:
                current = self.snapshot(path)
            except BaseException:
                record_attempt_state("snapshot_conflict")
                raise
            if current != expected:
                record_attempt_state("snapshot_conflict")
                raise WikiConfigurationError(
                    f"Wiki file changed before replacement: {path}"
                )
            try:
                self._validate_owner(path, current, owner)
            except BaseException:
                record_attempt_state("owner_conflict")
                raise
            _after_snapshot("replace", path)
            directory_fd, name = self._directory(path)
            try:
                if expected.exists:
                    transaction, quarantine_snapshot = self._quarantine(
                        path,
                        directory_fd=directory_fd,
                        name=name,
                        expected=expected,
                        owner=owner,
                        operation="replacement",
                        proposed_sha256=proposed_sha256,
                        proposal_name=temporary_name,
                        proposal_expected=proposal_snapshot,
                    )
                    self._record_transaction_state(
                        path,
                        directory_fd=directory_fd,
                        transaction=transaction,
                        state="publishing",
                    )
                else:
                    transaction_id = uuid.uuid4().hex
                    transaction = self._start_transaction(
                        path,
                        directory_fd=directory_fd,
                        name=name,
                        quarantine_name=f".{name}.{transaction_id}.quarantine",
                        operation="creation",
                        expected=expected,
                        proposed_sha256=proposed_sha256,
                        proposal_name=temporary_name,
                        proposal_expected=proposal_snapshot,
                        initial_state="publishing",
                    )
            except BaseException:
                record_attempt_state("transaction_conflict")
                raise
            record_attempt_state("publishing")
            try:
                self._publish_proposal(
                    path,
                    directory_fd=directory_fd,
                    proposal_name=temporary_name,
                    proposal_descriptor=proposal_descriptor,
                    proposal_expected=proposal_snapshot,
                    proposed_sha256=proposed_sha256,
                )
            except FileExistsError as exc:
                record_attempt_state("publication_conflict")
                if transaction is not None and quarantine_snapshot is not None:
                    self._restore_quarantine(
                        path,
                        directory_fd=directory_fd,
                        transaction=transaction,
                        expected=quarantine_snapshot,
                    )
                    self._finish_transaction(
                        path,
                        directory_fd,
                        transaction,
                        outcome="restoration",
                    )
                raise WikiConfigurationError(
                    f"Wiki file appeared before replacement: {path}"
                ) from exc
            except WikiConfigurationError:
                record_attempt_state("publication_conflict")
                raise
            except OSError:
                record_attempt_state("publication_error")
                if transaction is not None and quarantine_snapshot is not None:
                    self._restore_quarantine(
                        path,
                        directory_fd=directory_fd,
                        transaction=transaction,
                        expected=quarantine_snapshot,
                    )
                    self._finish_transaction(
                        path,
                        directory_fd,
                        transaction,
                        outcome="restoration",
                    )
                raise
            record_attempt_state("published")
            if transaction is not None and quarantine_snapshot is not None:
                self._tombstone_quarantine(
                    path,
                    directory_fd=directory_fd,
                    transaction=transaction,
                    expected=quarantine_snapshot,
                    outcome="replacement",
                )
                self._finish_transaction(
                    path,
                    directory_fd,
                    transaction,
                    outcome="replacement",
                )
            elif transaction is not None:
                self._finish_transaction(
                    path,
                    directory_fd,
                    transaction,
                    outcome="creation",
                )
            record_attempt_state("completed")
            self._directory(path)
        finally:
            if transaction is not None:
                os.close(transaction.descriptor)
            if proposal_descriptor >= 0:
                os.close(proposal_descriptor)
            if attempt_descriptor >= 0:
                os.close(attempt_descriptor)

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
        proposal_name: str,
        proposal_expected: FileSnapshot | None = None,
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
            proposal_name=proposal_name,
            proposal_expected=proposal_expected,
        )
        try:
            _rename_noreplace(
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
                    transaction=transaction,
                    expected=moved,
                )
                self._finish_transaction(
                    path,
                    directory_fd,
                    transaction,
                    outcome="restoration",
                )
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
                    transaction=transaction,
                    expected=moved,
                )
                self._finish_transaction(
                    path,
                    directory_fd,
                    transaction,
                    outcome="restoration",
                )
            finally:
                os.close(transaction.descriptor)
            raise
        return transaction, moved

    def _restore_quarantine(
        self,
        path: Path,
        *,
        directory_fd: int,
        transaction: _MutationTransaction,
        expected: FileSnapshot,
    ) -> None:
        if transaction.state != "restoring":
            self._record_transaction_state(
                path,
                directory_fd=directory_fd,
                transaction=transaction,
                state="restoring",
                outcome="restoration",
            )
        quarantine = self._snapshot_at(
            path.with_name(transaction.quarantine_name),
            directory_fd=directory_fd,
            name=transaction.quarantine_name,
        )
        tombstone = self._snapshot_at(
            path.with_name(transaction.tombstone_name),
            directory_fd=directory_fd,
            name=transaction.tombstone_name,
        )
        if quarantine == expected:
            source_name = transaction.quarantine_name
        elif tombstone == expected:
            source_name = transaction.tombstone_name
        else:
            raise WikiConfigurationError(
                f"Wiki file changed while restoring quarantine: {path}; "
                "operator data preserved"
            )
        target = self._snapshot_at(
            path,
            directory_fd=directory_fd,
            name=transaction.target_name,
        )
        if not target.exists:
            try:
                os.link(
                    source_name,
                    transaction.target_name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                self._fsync_directory(directory_fd)
            except FileExistsError as exc:
                raise WikiConfigurationError(
                    f"Wiki file changed while restoring quarantine: {path}; "
                    f"preserved as {source_name}"
                ) from exc
            target = self._snapshot_at(
                path,
                directory_fd=directory_fd,
                name=transaction.target_name,
            )
        if target != expected:
            raise WikiConfigurationError(
                f"Wiki file changed while restoring quarantine: {path}; "
                f"preserved as {source_name}"
            )
        self._tombstone_quarantine(
            path,
            directory_fd=directory_fd,
            transaction=transaction,
            expected=expected,
            outcome="restoration",
        )

    def _tombstone_quarantine(
        self,
        path: Path,
        *,
        directory_fd: int,
        transaction: _MutationTransaction,
        expected: FileSnapshot,
        outcome: str,
    ) -> None:
        if transaction.state not in {"tombstoning", "record_tombstoning"}:
            self._record_transaction_state(
                path,
                directory_fd=directory_fd,
                transaction=transaction,
                state="tombstoning",
                outcome=outcome,
            )
        elif transaction.outcome != outcome:
            raise WikiConfigurationError(
                f"Wiki mutation outcome changed while tombstoning: {path}; "
                "operator data preserved"
            )
        quarantine_path = path.with_name(transaction.quarantine_name)
        quarantine = self.snapshot(quarantine_path)
        tombstone_path = path.with_name(transaction.tombstone_name)
        tombstone = self._snapshot_at(
            tombstone_path,
            directory_fd=directory_fd,
            name=transaction.tombstone_name,
        )
        if not quarantine.exists:
            if tombstone == expected:
                return
            raise WikiConfigurationError(
                f"Wiki tombstone changed during finalization: {path}; "
                "operator data preserved"
            )
        if quarantine != expected or tombstone.exists:
            raise WikiConfigurationError(
                f"Wiki file changed before tombstoning: {path}; "
                "operator data preserved"
            )
        try:
            descriptor = os.open(
                transaction.quarantine_name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
        except OSError as exc:
            raise WikiConfigurationError(
                f"Wiki file changed before tombstoning: {path}; "
                "operator data preserved"
            ) from exc
        try:
            opened = self._snapshot_from_descriptor(descriptor)
            if opened != expected:
                raise WikiConfigurationError(
                    f"Wiki file changed before tombstoning: {path}; "
                    "operator data preserved"
                )
            _rename_noreplace(
                transaction.quarantine_name,
                transaction.tombstone_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            self._fsync_directory(directory_fd)
            moved = self._snapshot_at(
                tombstone_path,
                directory_fd=directory_fd,
                name=transaction.tombstone_name,
            )
            if moved != opened or moved != expected:
                raise WikiConfigurationError(
                    f"Wiki file changed while tombstoning: {path}; "
                    "operator data preserved"
                )
        finally:
            os.close(descriptor)

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
                proposal_name="",
            )
            self._record_transaction_state(
                path,
                directory_fd=directory_fd,
                transaction=transaction,
                state="deleting",
                outcome="deletion",
            )
            self._tombstone_quarantine(
                path,
                directory_fd=directory_fd,
                transaction=transaction,
                expected=quarantine_snapshot,
                outcome="deletion",
            )
            self._finish_transaction(
                path,
                directory_fd,
                transaction,
                outcome="deletion",
            )
            self._directory(path)
        finally:
            if transaction is not None:
                os.close(transaction.descriptor)

"""Session-scoped Windows analysis programs and host-published query inputs."""
import asyncio
from contextlib import suppress
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid

from .backend import IsolationError, RETENTION_SCAN_LIMIT, RETENTION_SECONDS
from . import windows_launcher


def scope_hash(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


class WindowsIsolationBackend:
    def __init__(self, config, run_id, principal_id, session_id):
        if not all(isinstance(key, str) and key for key in (run_id, principal_id, session_id)):
            raise IsolationError("Authenticated employee and chat are required for Windows analysis")
        self.config = config
        self.run_id = run_id
        self.principal_id = principal_id
        self.scope = scope_hash(principal_id, session_id)
        self.session_root = config.work_root / "sessions" / self.scope
        self.results_dir = self.session_root / "inputs"
        self.run_root = self.session_root / "runs" / scope_hash(run_id)
        self.workdir = self.run_root / "work"
        self.programs = self.run_root / "programs"

    def _inputs(self):
        self.config.home.mkdir(parents=True, exist_ok=True)
        windows_launcher.ensure_private_root(self.config.work_root)
        # The container receives only RX on inputs, never write on its parent.
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def python_program(self, source, arguments=(), *, name):
        self._inputs()
        self.programs.mkdir(parents=True, exist_ok=True)
        self.workdir.mkdir(exist_ok=True)
        if name not in {"kernel.py", "collect.py", "probe.py", "cleanup.py"}:
            raise ValueError("Unknown analysis program")
        program = self.programs / name
        # Only host processes can write programs. Publish complete bootstrap
        # files atomically before a restricted child can open them.
        encoded = source.encode("utf-8")
        if not program.exists():
            with tempfile.NamedTemporaryFile(dir=self.programs, delete=False) as out:
                temporary = Path(out.name)
            try:
                temporary.write_bytes(encoded)
                os.replace(temporary, program)
            finally:
                temporary.unlink(missing_ok=True)
        elif program.read_bytes() != encoded:
            raise IsolationError("The staged Windows analysis program changed")
        command = windows_launcher.command(
            scope=self.scope, python=self.config.python_executable,
            cwd=self.workdir, read_dirs=(self.results_dir, self.programs),
            write_dirs=(self.workdir,),
            argv=["-I", "-B", "-X", "utf8", "-u", str(program), *map(str, arguments)],
        )
        return command, {}

    async def upload_bytes(self, data: bytes, timeout=60):
        with tempfile.TemporaryFile() as source:
            source.write(data)
            source.flush()
            return await self.upload_file(source, timeout)

    async def upload_file(self, source, timeout=60):
        """Publish a complete, immutable CSV readable by this chat on its next turn."""
        cancelled = threading.Event()

        def copy():
            self._inputs()
            path = self.results_dir / ("query-" + uuid.uuid4().hex + ".csv")
            temporary = path.with_name("." + path.name + ".pending")
            try:
                source.seek(0)
                with temporary.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        if cancelled.is_set():
                            return
                        output.write(chunk)
                    if output.tell() != os.fstat(source.fileno()).st_size:
                        raise IsolationError("Incomplete Windows CSV upload")
                os.replace(temporary, path)
                return path
            finally:
                temporary.unlink(missing_ok=True)

        # The caller owns source. Keep it alive until any cancelled copy has
        # stopped and remove a publication that raced the cancellation.
        task = asyncio.create_task(asyncio.to_thread(copy))
        try:
            return str(await asyncio.wait_for(asyncio.shield(task), timeout))
        except (TimeoutError, asyncio.CancelledError):
            cancelled.set()
            with suppress(OSError):
                published = await asyncio.shield(task)
                if published is not None:
                    published.unlink(missing_ok=True)
            raise

    def cleanup_results(self, *, now=None):
        self._inputs()
        cutoff = (time.time() if now is None else now) - RETENTION_SECONDS
        with os.scandir(self.results_dir) as entries:
            for index, entry in enumerate(entries):
                if index >= RETENTION_SCAN_LIMIT:
                    break
                if not (entry.name.startswith("query-") and entry.name.endswith(".csv")
                        or entry.name.startswith(".query-") and entry.name.endswith(".pending")):
                    continue
                with suppress(FileNotFoundError, PermissionError):
                    if entry.is_file(follow_symlinks=False) and entry.stat(follow_symlinks=False).st_mtime < cutoff:
                        os.unlink(entry.path)

    def verify(self):
        self._inputs()
        # This file exists and is readable by the parent but has no container ACE.
        with tempfile.TemporaryDirectory(dir=self.session_root) as private, socket.socket() as listener:
            canary = Path(private) / "host-only"
            canary.write_bytes(b"private")
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            probe = """import ctypes, pathlib, sys, socket
from ctypes import wintypes as w
k = ctypes.WinDLL('kernel32', use_last_error=True)
a = ctypes.WinDLL('advapi32', use_last_error=True)
k.GetCurrentProcess.restype = w.HANDLE
a.OpenProcessToken.argtypes = (w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE))
a.GetTokenInformation.argtypes = (w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD))
token, value, length = w.HANDLE(), w.DWORD(), w.DWORD()
if not a.OpenProcessToken(k.GetCurrentProcess(), 8, ctypes.byref(token)): raise ctypes.WinError()
if not a.GetTokenInformation(token, 29, ctypes.byref(value), 4, ctypes.byref(length)): raise ctypes.WinError()
if value.value != 1: raise PermissionError('Expected AppContainer token')
a.GetTokenInformation(token, 30, None, 0, ctypes.byref(length))
if not length.value: raise ctypes.WinError()
capabilities = ctypes.create_string_buffer(length.value)
if not a.GetTokenInformation(token, 30, capabilities, length, ctypes.byref(length)): raise ctypes.WinError()
if w.DWORD.from_buffer(capabilities).value: raise PermissionError('Unexpected AppContainer capabilities')
p = pathlib.Path('probe.tmp')
p.write_bytes(b'probe')
p.unlink()
try: pathlib.Path(sys.argv[1]).read_bytes()
except PermissionError: pass
else: raise PermissionError('Host-only file is readable')
try: socket.create_connection(('127.0.0.1', int(sys.argv[2])), timeout=2).close()
except OSError: pass
else: raise PermissionError('Unexpected loopback connectivity')
print('verified-appcontainer')
"""
            command, options = self.python_program(probe, (canary, listener.getsockname()[1]), name="probe.py")
            try:
                result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, timeout=30, **options)
            except (OSError, subprocess.SubprocessError) as exc:
                raise IsolationError("Windows AppContainer startup check failed") from exc
            if result.returncode != 0 or result.stdout.strip() != b"verified-appcontainer":
                raise IsolationError("Windows AppContainer probe failed: " +
                                     result.stderr[-1200:].decode("utf-8", "replace"))
        return {**self.config.model_dump(mode="json"), "workdir": str(self.workdir), "run_id": self.run_id,
                "principal_id": self.principal_id, "verified_user": self.scope,
                "verified_uid": self.scope, "selected_sandbox": "appcontainer"}

    def _terminate(self, process):
        with suppress(ProcessLookupError):
            process.kill()  # Closing the supervisor's Job Object terminates its entire process tree.

    def cleanup_workspace(self):
        # The host must never recursively traverse a directory populated by model code.
        if not self.run_root.exists():
            return
        if self.workdir.exists():
            script = """import pathlib, shutil
for p in pathlib.Path.cwd().iterdir():
    if p.is_dir() and not p.is_symlink() and not (getattr(p.lstat(), "st_file_attributes", 0) & 0x400):
        shutil.rmtree(p)
    elif p.is_dir() and not p.is_symlink():
        p.rmdir()
    else:
        p.unlink()
"""
            command, options = self.python_program(script, name="cleanup.py")
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, timeout=30, **options)
            if result.returncode != 0:
                raise IsolationError("Windows analysis workspace cleanup failed")
            self.workdir.rmdir()
        if self.programs.exists():
            shutil.rmtree(self.programs)
        self.run_root.rmdir()

    cleanup = cleanup_workspace

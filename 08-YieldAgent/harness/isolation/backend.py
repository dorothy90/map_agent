from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import io
import json
import logging
import os
from pathlib import Path
import shlex
import signal
import subprocess
import tarfile
import time
import uuid

from ..config import IsolationSettings, LocalIsolationSettings, SeatbeltIsolationSettings


class IsolationError(RuntimeError):
    pass


LOGGER = logging.getLogger(__name__)
RETENTION_SECONDS = 7 * 24 * 60 * 60
RETENTION_SCAN_LIMIT = 1000


def principal_hash(principal_id: str) -> str:
    return hashlib.sha256(principal_id.encode()).hexdigest()


def _secure_directory_function():
    return (
        "def open_tree(base,parts,create):\n"
        " components=[item for item in os.path.abspath(base).split(os.sep) if item]\n"
        " fd=os.open(os.sep,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)\n"
        " try:\n"
        "  for item in components:\n"
        "   child=os.open(item,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)\n"
        "   os.close(fd);fd=child\n"
        "  for item in parts:\n"
        "   if create:\n"
        "    try: os.mkdir(item,0o700,dir_fd=fd)\n"
        "    except FileExistsError: pass\n"
        "   child=os.open(item,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)\n"
        "   os.fchmod(child,0o700)\n"
        "   os.close(fd);fd=child\n"
        "  return fd\n"
        " except BaseException:\n"
        "  os.close(fd);raise\n"
    )


class ScopedProcessBackend:
    def __init__(self, config: IsolationSettings | SeatbeltIsolationSettings | LocalIsolationSettings, run_id: str, principal_id: str | None = None):
        self.config = config
        self.run_id = run_id
        self.run_hash = hashlib.sha256(json.dumps([principal_id, run_id], ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
        self.workdir = config.work_root / "runs" / self.run_hash
        self.principal_id = principal_id
        self.results_dir = (
            config.work_root / "profiles" / principal_hash(principal_id) / "results"
            if principal_id is not None else None
        )

    def _require_results_dir(self):
        if self.results_dir is None:
            raise IsolationError("authenticated principal is required for CSV storage")
        return self.results_dir

    def _results_parts(self):
        self._require_results_dir()
        return ("profiles", principal_hash(self.principal_id), "results")

    def client_environment(self):
        return {"PATH": os.defpath, "LANG": os.environ.get("LANG", "C.UTF-8")}

    def _request(self, command, *, operation="command"):
        if self.principal_id is None:
            raise IsolationError("authenticated principal is required for isolated execution")
        request = {
            "v": 1, "principal": principal_hash(self.principal_id), "run": self.run_hash,
            "op": operation, "command": command,
        }
        payload = json.dumps(request, separators=(",", ":"), ensure_ascii=True).encode()
        return "yieldrun-v1 " + base64.urlsafe_b64encode(payload).rstrip(b"=").decode()

    def _terminate(self, process):
        process.kill()

    async def execute_python(self, code, datasets, timeout):
        invocation = "python-" + uuid.uuid4().hex
        remote_dir = self.workdir / invocation
        worker = Path(__file__).resolve().parents[1] / "runtime" / "worker.py"
        payload = json.dumps(datasets, ensure_ascii=False, default=str, allow_nan=False).encode()
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as bundle:
            for name, content in (("worker.py", worker.read_bytes()), ("code.py", code.encode()),
                                  ("data.json", payload)):
                info = tarfile.TarInfo(name)
                info.mode = 0o600
                info.size = len(content)
                bundle.addfile(info, io.BytesIO(content))
        quoted = shlex.quote(str(remote_dir))
        cleanup = "" if getattr(self.config, "retain_python_files", False) else f"trap 'rm -rf {quoted}' EXIT HUP INT TERM; "
        command = (f"umask 077; mkdir {quoted}; {cleanup}"
                   f"tar xf - -C {quoted}; mkdir {quoted}/output; "
                   f"cd {quoted}; python3 -I -B worker.py --input-dir . --output-dir output --minimal-statistics; "
                   f"cat output/result.json")
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command(command), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=self.client_environment(), start_new_session=True)
        except OSError as exc:
            raise IsolationError("isolated process backend unavailable") from exc
        try:
            stdout, _stderr = await asyncio.wait_for(process.communicate(archive.getvalue()), timeout)
        except TimeoutError:
            self._terminate(process)
            await asyncio.shield(process.wait())
            return {"status": "timeout", "tables": [], "plots": [], "stdout": "", "stderr": "",
                    "error": {"type": "execution_timeout"}}
        except asyncio.CancelledError:
            self._terminate(process)
            await asyncio.shield(process.wait())
            raise
        if process.returncode != 0:
            raise IsolationError("isolated Python worker failed")
        if len(stdout) > 20 * 1024 * 1024:
            raise ValueError("Python output exceeds limit")
        try:
            result = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IsolationError("isolated Python worker returned an invalid result") from exc
        if result.get("status") not in {"success", "error"} or not isinstance(result.get("tables"), list):
            raise IsolationError("isolated Python worker returned an invalid protocol")
        if getattr(self.config, "retain_python_files", False):
            result["workspace"] = {"directory": str(remote_dir), "input": str(remote_dir / "data.json"),
                                   "code": str(remote_dir / "code.py"), "result": str(remote_dir / "output" / "result.json")}
        return result

    def _results_directory_script(self, create):
        return (_secure_directory_function() + f"base={str(self.config.work_root)!r}\n"
                f"parts={self._results_parts()!r}\n"
                f"directory=open_tree(base,parts,{create!r})\n")

    async def upload_bytes(self, data: bytes, timeout=60):
        results_dir = self._require_results_dir()
        name = "query-" + uuid.uuid4().hex + ".csv"
        pending = "." + name + ".pending"
        path = results_dir / name
        digest = hashlib.sha256(data).hexdigest()
        script = (
            "import hashlib,os,sys\n"
            + self._results_directory_script(True) +
            f"name={name!r}\n"
            f"pending={pending!r}\n"
            f"expected_size={len(data)!r}\n"
            f"expected_digest={digest!r}\n"
            "fd=None;published=False\n"
            "try:\n"
            " fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)\n"
            " with os.fdopen(fd,'wb',closefd=True) as output:\n"
            "  fd=None\n"
            "  data=sys.stdin.buffer.read()\n"
            "  if len(data)!=expected_size or hashlib.sha256(data).hexdigest()!=expected_digest: raise ValueError('invalid upload')\n"
            "  output.write(data)\n"
            "  output.flush()\n"
            "  os.fsync(output.fileno())\n"
            " os.link(pending,name,src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False)\n"
            " published=True\n"
            " os.unlink(pending,dir_fd=directory)\n"
            "except BaseException:\n"
            " if fd is not None: os.close(fd)\n"
            " try: os.unlink(pending,dir_fd=directory)\n"
            " except FileNotFoundError: pass\n"
            " if published:\n"
            "  try: os.unlink(name,dir_fd=directory)\n"
            "  except FileNotFoundError: pass\n"
            " raise\n"
            "finally:\n"
            " os.close(directory)\n"
        )
        command = "python3 -I -B -c " + shlex.quote(script)
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command(command), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=self.client_environment(), start_new_session=True,
            )
        except OSError as exc:
            raise IsolationError("isolated CSV upload unavailable") from exc
        try:
            await asyncio.wait_for(process.communicate(data), timeout)
        except TimeoutError as exc:
            self._terminate(process)
            await asyncio.shield(process.wait())
            try:
                await asyncio.shield(self._cleanup_upload(name, pending, len(data), digest))
            except Exception:
                pass
            raise IsolationError("isolated CSV upload timed out") from exc
        except asyncio.CancelledError:
            self._terminate(process)
            await asyncio.shield(process.wait())
            try:
                await asyncio.shield(self._cleanup_upload(name, pending, len(data), digest))
            except Exception:
                pass
            raise
        except OSError as exc:
            self._terminate(process)
            await asyncio.shield(process.wait())
            try:
                await asyncio.shield(self._cleanup_upload(name, pending, len(data), digest))
            except Exception:
                pass
            raise IsolationError("isolated CSV upload unavailable") from exc
        if process.returncode != 0:
            try:
                await self._cleanup_upload(name, pending, len(data), digest)
            except Exception:
                pass
            raise IsolationError("isolated CSV upload failed")
        return str(path)

    async def _cleanup_upload(self, name, pending, size, digest):
        script = (
            "import hashlib,os,stat\n"
            + self._results_directory_script(False) +
            f"names={(pending, name)!r}\n"
            f"size={size!r};digest={digest!r}\n"
            "try:\n"
            " for item in names:\n"
            "  try: info=os.stat(item,dir_fd=directory,follow_symlinks=False)\n"
            "  except FileNotFoundError: continue\n"
            "  if not stat.S_ISREG(info.st_mode): continue\n"
            "  if item==names[1]:\n"
            "   fd=os.open(item,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)\n"
            "   try: data=os.read(fd,size+1)\n"
            "   finally: os.close(fd)\n"
            "   if len(data)!=size or hashlib.sha256(data).hexdigest()!=digest: continue\n"
            "  os.unlink(item,dir_fd=directory)\n"
            "finally: os.close(directory)\n"
        )
        command = "python3 -I -B -c " + shlex.quote(script)
        process = await asyncio.create_subprocess_exec(
            *self.command(command), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=self.client_environment(), start_new_session=True,
        )
        try:
            await asyncio.wait_for(process.communicate(b""), 20)
        except BaseException:
            self._terminate(process)
            await asyncio.shield(process.wait())
            raise
        if process.returncode != 0:
            raise IsolationError("isolated CSV cleanup failed")

    def cleanup_results(self, *, now=None):
        self._require_results_dir()
        cutoff = (time.time() if now is None else now) - RETENTION_SECONDS
        script = (
            "import os,stat,sys\n"
            + "try:\n" + "".join(" " + line + "\n" for line in self._results_directory_script(False).splitlines())
            + "except FileNotFoundError: raise SystemExit(0)\n" +
            f"cutoff={cutoff!r};limit={RETENTION_SCAN_LIMIT!r}\n"
            "try:\n"
            " for index,entry in enumerate(os.scandir(directory)):\n"
            "  if index>=limit: break\n"
            "  name=entry.name\n"
            "  if not ((name.startswith('query-') and name.endswith('.csv')) or (name.startswith('.query-') and name.endswith('.csv.pending'))): continue\n"
            "  try: info=entry.stat(follow_symlinks=False)\n"
            "  except FileNotFoundError: continue\n"
            "  if not stat.S_ISREG(info.st_mode) or info.st_mtime>=cutoff: continue\n"
            "  try:\n"
            "   os.unlink(entry.name,dir_fd=directory)\n"
            "  except FileNotFoundError: pass\n"
            "  except OSError as exc: print(f'{name}: {exc}',file=sys.stderr)\n"
            "finally: os.close(directory)\n"
        )
        command = "python3 -I -B -c " + shlex.quote(script)
        try:
            result = subprocess.run(
                self.command(command), input="", text=True, capture_output=True,
                timeout=20, check=False, env=self.client_environment(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            LOGGER.warning("CSV retention transport failed: %s", exc)
            return
        if result.returncode != 0:
            LOGGER.warning("CSV retention cleanup failed: %s", result.stderr.strip())
        elif result.stderr.strip():
            LOGGER.warning("CSV retention deletion failed: %s", result.stderr.strip())

class IsolatedSSHBackend(ScopedProcessBackend):
    def ssh_command(self, remote_command: str | None = None):
        command = [
            "ssh", "-F", "/dev/null",
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "ForwardAgent=no",
            "-o", "PasswordAuthentication=no",
            "-o", "KbdInteractiveAuthentication=no",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={self.config.known_hosts}",
            "-o", "ConnectTimeout=10",
            "-p", str(self.config.ssh_port),
            "-i", str(self.config.ssh_key),
            f"{self.config.ssh_user}@{self.config.ssh_host}",
        ]
        if remote_command is not None:
            command.append(self._request(remote_command))
        return command

    command = ssh_command

    def install_engine_adapter(self):
        try:
            backends = importlib.import_module("tools.terminal_tool_backends")
        except ImportError as exc:
            raise IsolationError("Hermes SSH backend is unavailable") from exc
        original = backends._SSHEnvironment
        if getattr(original, "_yield_isolated", False):
            return
        boundary = self

        class HarnessSSHEnvironment(original):
            _yield_isolated = True

            def __init__(self, *args, **kwargs):
                host = kwargs.get("host", args[0] if args else None)
                user = kwargs.get("user", args[1] if len(args) > 1 else None)
                port = kwargs.get("port", 22)
                key_path = kwargs.get("key_path", "")
                if (host != boundary.config.ssh_host or user != boundary.config.ssh_user
                        or port != boundary.config.ssh_port or Path(key_path) != boundary.config.ssh_key):
                    raise IsolationError("Hermes attempted to replace the isolated SSH target")
                kwargs["sync_files"] = False
                super().__init__(*args, **kwargs)

            def _build_ssh_command(self, extra_args=None, send_env=()):
                raise IsolationError("unscoped SSH argv execution is disabled")

            def _run_ssh(self, remote_cmd, timeout):
                from tools.environments.remote_common import run_capture
                return run_capture(boundary.ssh_command(remote_cmd), timeout=timeout)

            def get_temp_dir(self):
                return str(boundary.workdir / "tmp")

            def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
                from tools.environments.base_output import _popen_bash
                command = boundary.ssh_command(cmd_string)
                return _popen_bash(command, stdin_data, env=boundary.client_environment())

        backends._SSHEnvironment = HarnessSSHEnvironment

    def cleanup(self):
        if getattr(self.config, "retain_python_files", False):
            return None
        try:
            command = self.ssh_command()
            command.append(self._request("", operation="cleanup"))
            result = subprocess.run(command, input="", text=True,
                                    capture_output=True, timeout=20, check=False,
                                    env=self.client_environment())
        except (OSError, subprocess.SubprocessError) as exc:
            raise IsolationError("isolated run cleanup unavailable") from exc
        if result.returncode != 0:
            raise IsolationError("isolated run cleanup failed")


class LocalIsolationBackend(ScopedProcessBackend):
    """Run the authenticated command directly using the local OS sandbox launcher."""

    def _results_directory_script(self, create):
        profile = self._require_results_dir().parent
        return ("flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW\n"
                f"profile=os.open({str(profile)!r},flags)\n"
                "try:\n"
                + (" try: os.mkdir('results',0o700,dir_fd=profile)\n"
                   " except FileExistsError: pass\n" if create else "")
                + " directory=os.open('results',flags,dir_fd=profile)\n"
                  "finally: os.close(profile)\n")

    def _terminate(self, process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def client_environment(self):
        return {
            "PATH": str(self.config.analysis_venv / "bin") + ":/usr/bin:/bin",
            "LANG": "C", "LC_ALL": "C", "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1", "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def command(self, remote_command: str | None = None):
        if remote_command is None:
            raise IsolationError("unscoped local execution is disabled")
        return [
            str(self.config.analysis_venv / "bin" / "python3"),
            "-I", "-B", str(self.config.launcher), "--home", str(self.config.home),
            "--work-root", str(self.config.work_root), "--venv", str(self.config.analysis_venv),
            self._request(remote_command),
        ]

    def install_engine_adapter(self):
        try:
            backends = importlib.import_module("tools.terminal_tool_backends")
        except ImportError as exc:
            raise IsolationError("Hermes local backend is unavailable") from exc
        original = backends._LocalEnvironment
        if getattr(original, "_yield_local_isolated", False):
            return
        boundary = self

        class HarnessLocalEnvironment(original):
            _yield_local_isolated = True
            _yield_seatbelt_isolated = isinstance(boundary.config, SeatbeltIsolationSettings)
            uses_host_filesystem = False
            is_local = False

            def __init__(self, *args, **kwargs):
                kwargs.update(cwd=str(boundary.workdir), env={}, persistent=False)
                super().__init__(*args, **kwargs)

            def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
                if login:
                    login = False
                from tools.environments.base_output import _popen_bash
                return _popen_bash(boundary.command(cmd_string), stdin_data,
                                   env=boundary.client_environment())

            def get_temp_dir(self):
                return str(boundary.workdir / "tmp")

        backends._LocalEnvironment = HarnessLocalEnvironment

    def cleanup(self):
        if self.config.retain_python_files:
            return
        # The generated command can touch only the run and profile bind mounts.
        result = subprocess.run(self.command("find . -mindepth 1 -delete"), stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=20, env=self.client_environment())
        if result.returncode != 0:
            raise IsolationError("isolated run cleanup failed")


class SeatbeltIsolationBackend(LocalIsolationBackend):
    ssh_command = LocalIsolationBackend.command

    def _results_directory_script(self, create):
        return ("import stat\n"
                "directory=os.dup(int(os.environ['YIELD_RESULTS_FD']))\n"
                "info=os.fstat(directory)\n"
                "if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode & 0o077: raise PermissionError('invalid results directory')\n")

    def cleanup(self):
        return None


def isolation_backend(config, run_id: str, principal_id: str | None = None):
    if isinstance(config, SeatbeltIsolationSettings):
        return SeatbeltIsolationBackend(config, run_id, principal_id)
    if isinstance(config, LocalIsolationSettings):
        return LocalIsolationBackend(config, run_id, principal_id)
    if isinstance(config, IsolationSettings):
        return IsolatedSSHBackend(config, run_id, principal_id)
    raise TypeError("unsupported isolation backend configuration")


def prepare_isolated_run(config: IsolationSettings | LocalIsolationSettings | SeatbeltIsolationSettings,
                         run_id: str, principal_id: str | None = None):
    backend = isolation_backend(config, run_id, principal_id)
    probe = (
        "import getpass,json,os,pathlib,sys;"
        "import pandas,numpy,plotly;"
        f"p=pathlib.Path({str(backend.workdir)!r});p.mkdir(parents=True,exist_ok=True);"
        "q=p/'.probe';q.write_text('ok');q.unlink();"
        "print(json.dumps({'marker':os.environ.get('YIELD_ISOLATED'),'launcher':os.environ.get('YIELD_LAUNCHER'),"
        "'sandbox':os.environ.get('YIELD_SANDBOX'),'user':getpass.getuser(),'uid':os.getuid(),"
        "'home':os.environ.get('HOME'),'python':sys.executable}))"
    )
    remote_command = "python3 -I -B -c " + shlex.quote(probe)
    if isinstance(backend, LocalIsolationBackend):
        from .seatbelt_local import file_read_probe
        # This existing, non-secret source file is outside the sandbox read roots.
        linux_local = isinstance(config, LocalIsolationSettings)
        read_probe = file_read_probe(Path(__file__).resolve(), absent_ok=linux_local)
        network_check = (
            f"denied['network']=os.readlink('/proc/self/ns/net') != {os.readlink('/proc/self/ns/net')!r};"
            if linux_local else
            "s=socket.socket();s.settimeout(.2);"
            "exec(\"try: s.connect(('127.0.0.1',27017));denied['network']=False\\nexcept OSError: denied['network']=True\");"
        )
        repo_target = Path(__file__).resolve().parents[2]
        # An in-tree venv makes its ancestors visible as empty mountpoint shells.
        if linux_local:
            repo_target = Path(__file__).resolve().parent
        probe = (
            "import json,os,pathlib,socket,sys;"
            f"p=pathlib.Path({str(backend.workdir)!r});p.mkdir(parents=True,exist_ok=True);"
            "q=p/'.probe';q.write_text('ok');q.unlink();"
            "denied={};"
            f"targets={{'repo':pathlib.Path({str(repo_target)!r}),"
            f"'other_run':pathlib.Path({str(config.work_root / 'runs' / ('0' * 64))!r}),"
            f"'other_principal':pathlib.Path({str(config.work_root / 'profiles' / ('0' * 64))!r})}};"
            "exec(\"for k,v in targets.items():\\n try: list(v.iterdir());denied[k]=False\\n except OSError: denied[k]=True\");"
            f"exec({read_probe!r});{network_check}"
            "print(json.dumps({'marker':os.environ.get('YIELD_ISOLATED'),"
            "'launcher':os.environ.get('YIELD_LAUNCHER'),'sandbox':os.environ.get('YIELD_SANDBOX'),"
            "'uid':os.getuid(),'home':os.environ.get('HOME'),'python':sys.executable,"
            "'own_write':True,'denied':denied}))"
        )
        remote_command = "python3 -I -B -c " + shlex.quote(probe)
    try:
        result = subprocess.run(backend.command(remote_command), input="", text=True,
                                capture_output=True, timeout=20, check=False,
                                env=backend.client_environment())
    except (OSError, subprocess.SubprocessError) as exc:
        raise IsolationError("isolated backend unavailable") from exc
    if result.returncode != 0:
        if isinstance(config, LocalIsolationSettings):
            raise IsolationError("local analysis probe failed; check /usr/bin/bwrap, Linux user namespaces and the analysis venv")
        raise IsolationError("isolated backend unavailable or misconfigured")
    try:
        evidence = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise IsolationError("isolated backend verification failed") from exc
    expected_python = str(config.analysis_venv / "bin" / "python3")
    if isinstance(backend, LocalIsolationBackend):
        verified = (
            evidence.get("marker") == "1"
            and evidence.get("launcher") == str(config.launcher)
            and evidence.get("sandbox") == ("bwrap" if linux_local else "sandbox-exec")
            and evidence.get("uid") == os.geteuid()
            and evidence.get("home") == str(backend.results_dir.parent)
            and evidence.get("python") == expected_python
            and evidence.get("own_write") is True
            and evidence.get("denied") == {
                "repo": True, "other_run": True, "other_principal": True, "network": True,
                "file_open": True, "child_file_open": True,
            }
        )
    else:
        verified = (
        evidence.get("marker") == "1"
        and evidence.get("launcher") == str(config.launcher)
        and evidence.get("user") == "yieldrun"
        and isinstance(evidence.get("uid"), int) and evidence["uid"] > 0
        and evidence["uid"] != os.geteuid()
        and evidence.get("home") == str(backend.results_dir.parent)
        and evidence.get("python") == expected_python
        and evidence.get("sandbox") in {"sandbox-exec", "bwrap"}
        )
    if not verified:
        raise IsolationError("isolated backend verification failed")
    return {
        **config.model_dump(mode="json"),
        "workdir": str(backend.workdir),
        "verified_user": evidence.get("user") or str(evidence["uid"]),
        "verified_uid": evidence["uid"],
        "selected_sandbox": evidence.get("sandbox") or "none",
        "run_id": run_id,
        "principal_id": principal_id,
    }


def cleanup_isolated_run(config: IsolationSettings | LocalIsolationSettings | SeatbeltIsolationSettings,
                         run_id: str, principal_id: str):
    isolation_backend(config, run_id, principal_id).cleanup()


def cleanup_profile_results(config: IsolationSettings | LocalIsolationSettings | SeatbeltIsolationSettings,
                            principal_id: str, *, now=None):
    isolation_backend(config, "retention", principal_id).cleanup_results(now=now)

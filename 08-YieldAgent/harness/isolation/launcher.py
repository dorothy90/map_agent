#!/usr/bin/env python3
"""Root-installed forced-command launcher for the yieldrun SSH key."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import subprocess
import sys
import time


CONFIG = Path("/etc/yieldrun-isolation.json")
HEX64 = re.compile(r"[0-9a-f]{64}")


def fail(message):
    print("yieldrun isolation refused: " + message, file=sys.stderr)
    raise SystemExit(126)


def root_owned_readonly(path, *, executable=False, parents=False):
    paths = (path, *path.parents) if parents else (path,)
    for candidate in paths:
        if candidate.is_symlink():
            fail(f"path must not be a symlink: {candidate}")
        try:
            info = candidate.stat()
        except OSError:
            fail(f"required path is unavailable: {candidate}")
        if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            fail(f"path must be root-owned and not group/world-writable: {candidate}")
    if executable and not os.access(path, os.X_OK):
        fail(f"path is not executable: {path}")


def load_config():
    root_owned_readonly(CONFIG, parents=True)
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        fail("invalid launcher configuration")
    required = {"home", "work_root", "analysis_venv", "launcher", "sandbox"}
    if not required <= config.keys() or config["sandbox"] not in {"auto", "required"}:
        fail("incomplete launcher configuration")
    return config


def resolve_without_symlinks(value):
    path = Path(value)
    if any(candidate.is_symlink() for candidate in (path, *path.parents)):
        fail(f"configured path must not contain symlinks: {path}")
    try:
        return str(path.resolve(strict=True))
    except OSError:
        fail(f"configured isolation path is unavailable: {path}")


def validate_request(request):
    if not isinstance(request, dict) or set(request) != {"v", "principal", "run", "op", "command"}:
        fail("invalid scoped request")
    if request["v"] != 1 or request["op"] not in {"command", "cleanup"}:
        fail("unsupported scoped request")
    if not isinstance(request["principal"], str) or not HEX64.fullmatch(request["principal"]):
        fail("invalid principal scope")
    if not isinstance(request["run"], str) or not HEX64.fullmatch(request["run"]):
        fail("invalid run scope")
    if not isinstance(request["command"], str) or (request["op"] == "command" and not request["command"]):
        fail("invalid scoped command")
    if request["op"] == "cleanup" and request["command"]:
        fail("cleanup does not accept a command")
    return request


def parse_request(original):
    if not original or "\x00" in original:
        fail("SSH forced command is required")
    parts = original.split()
    if len(parts) != 2 or parts[0] != "yieldrun-v1":
        fail("invalid scoped request envelope")
    try:
        encoded = parts[1]
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        request = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        fail("invalid scoped request envelope")
    return validate_request(request)


@dataclass(frozen=True)
class Scope:
    profile: Path
    workdir: Path


def scope_paths(config, request):
    validate_request(request)
    root = Path(config["work_root"])
    return Scope(root / "profiles" / request["principal"], root / "runs" / request["run"])


def _private_directory(path):
    if path.is_symlink():
        fail(f"scope path must not be a symlink: {path}")
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    try:
        info = path.stat(follow_symlinks=False)
    except OSError:
        fail(f"scope path is unavailable: {path}")
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        fail(f"scope path has an invalid owner or type: {path}")
    os.chmod(path, 0o700, follow_symlinks=False)


def ensure_scope(config, scope):
    root = Path(config["work_root"])
    for parent in (scope.profile.parent, scope.workdir.parent):
        if parent.parent != root or parent.is_symlink():
            fail("scope parent is invalid")
        _private_directory(parent)
    _private_directory(scope.profile)
    _private_directory(scope.workdir)
    for directory in (scope.profile / "tmp", scope.profile / "cache", scope.workdir / "tmp"):
        _private_directory(directory)


def sandbox_command(config, scope, shell_command, clean_env):
    if config.get("sandbox") not in {"auto", "required"}:
        fail("OS sandbox is required")
    if sys.platform == "darwin":
        binary = Path(config.get("sandbox_binary", "/usr/bin/sandbox-exec"))
        if binary.is_file():
            root_owned_readonly(binary, executable=True, parents=True)
            profile = Path(config.get("sandbox_profile", "/etc/yieldrun.sb"))
            root_owned_readonly(profile, parents=True)
            clean_env["YIELD_SANDBOX"] = "sandbox-exec"
            return [str(binary), "-D", f"PROFILE={scope.profile}", "-D", f"WORKDIR={scope.workdir}",
                    "-f", str(profile), "/bin/bash", "--noprofile", "--norc", "-c", shell_command]
    elif sys.platform.startswith("linux"):
        binary = Path(config.get("sandbox_binary", "/usr/bin/bwrap"))
        if binary.is_file():
            root_owned_readonly(binary, executable=True, parents=True)
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            profile_fd = None
            try:
                profile_fd = os.open(scope.profile, flags)
                workdir_fd = os.open(scope.workdir, flags)
            except OSError:
                if profile_fd is not None:
                    os.close(profile_fd)
                fail("scope mount source is unavailable")
            home = str(config["home"])
            work_root = str(config["work_root"])
            clean_env["YIELD_SANDBOX"] = "bwrap"
            clean_env["_YIELD_PASS_FDS"] = f"{profile_fd},{workdir_fd}"
            command = [str(binary), "--unshare-all", "--die-with-parent", "--new-session",
                       "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                       "--tmpfs", home, "--dir", work_root,
                       "--dir", str(scope.profile.parent), "--dir", str(scope.workdir.parent),
                       "--dir", str(scope.profile), "--dir", str(scope.workdir)]
            for item in ("/usr", "/bin", "/lib", "/lib64", "/etc/ssl", "/etc/passwd",
                         "/etc/group", "/etc/localtime", config["analysis_venv"]):
                if Path(item).exists():
                    command.extend(("--ro-bind", item, item))
            command.extend(("--bind", f"/proc/self/fd/{profile_fd}", str(scope.profile),
                            "--bind", f"/proc/self/fd/{workdir_fd}", str(scope.workdir),
                            "--chdir", str(scope.workdir), "/bin/bash", "--noprofile", "--norc",
                            "-c", shell_command))
            return command
    fail("required OS sandbox is unavailable")


def run_supervised(command, clean_env):
    pass_fds = tuple(int(value) for value in clean_env.pop("_YIELD_PASS_FDS", "").split(",") if value)
    try:
        child = subprocess.Popen(command, env=clean_env, start_new_session=True, pass_fds=pass_fds)
    finally:
        for descriptor in pass_fds:
            os.close(descriptor)
    stopping = False

    def stop(signum, _frame):
        nonlocal stopping
        stopping = True
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)
    try:
        returncode = child.wait()
    finally:
        for signum in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                break
            if signum == signal.SIGTERM:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    try:
                        os.killpg(child.pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(.05)
    return 128 + signal.SIGTERM if stopping and returncode == 0 else returncode


def main():
    config = load_config()
    if os.geteuid() == 0 or pwd.getpwuid(os.geteuid()).pw_name != "yieldrun":
        fail("launcher must run as the unprivileged yieldrun account")
    for name in ("home", "work_root", "analysis_venv", "launcher"):
        config[name] = resolve_without_symlinks(config[name])
    launcher = Path(config["launcher"])
    root_owned_readonly(launcher, executable=True, parents=True)
    home = Path(config["home"])
    work_root = Path(config["work_root"])
    venv = Path(config["analysis_venv"])
    try:
        python = (venv / "bin" / "python3").resolve(strict=True)
    except OSError:
        fail("analysis Python is unavailable")
    root_owned_readonly(home, parents=True)
    root_owned_readonly(venv, parents=True)
    root_owned_readonly(python, executable=True)
    if not home.is_dir() or not work_root.is_dir() or work_root.is_symlink() or work_root.parent != home:
        fail("dedicated HOME/work root is invalid")
    info = work_root.stat()
    if info.st_uid != os.geteuid() or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        fail("work root must be a private yieldrun directory")
    request = parse_request(os.environ.get("SSH_ORIGINAL_COMMAND"))
    scope = scope_paths(config, request)
    ensure_scope(config, scope)
    clean_env = {
        "HOME": str(scope.profile), "TMPDIR": str(scope.workdir / "tmp"),
        "XDG_CACHE_HOME": str(scope.profile / "cache"),
        "PATH": str(venv / "bin") + ":/usr/bin:/bin",
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "PYTHONNOUSERSITE": "1",
        "YIELD_ISOLATED": "1", "YIELD_LAUNCHER": str(launcher),
    }
    shell_command = request["command"] if request["op"] == "command" else "find . -mindepth 1 -delete"
    command = sandbox_command(config, scope, shell_command, clean_env)
    os.chdir(scope.workdir)
    os.umask(0o077)
    raise SystemExit(run_supervised(command, clean_env))


if __name__ == "__main__":
    main()

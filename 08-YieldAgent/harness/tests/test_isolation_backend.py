import asyncio
import base64
import hashlib
import logging
import os
import json
from pathlib import Path
import shlex
import stat
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest

from harness.config import IsolationSettings
from harness.isolation import backend as isolation_backend
from harness.isolation.backend import (
    IsolationError, IsolatedSSHBackend, prepare_isolated_run, principal_hash,
)


def isolation(tmp_path):
    key = tmp_path / "key"
    known_hosts = tmp_path / "known_hosts"
    key.write_text("key")
    known_hosts.write_text("host-key")
    home = tmp_path / "yieldrun-home"
    work_root = home / "work"
    analysis_venv = tmp_path / "analysis-venv"
    launcher = tmp_path / "yieldrun-launcher"
    work_root.mkdir(parents=True)
    analysis_venv.mkdir()
    launcher.write_text("launcher")
    return IsolationSettings(
        ssh_host="localhost",
        ssh_user="yieldrun",
        ssh_port=22,
        ssh_key=key,
        known_hosts=known_hosts,
        home=home, work_root=work_root, analysis_venv=analysis_venv, launcher=launcher,
        sandbox="auto",
    )


def scoped_request(envelope):
    prefix, encoded = envelope.split()
    assert prefix == "yieldrun-v1"
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


def test_ssh_command_uses_pinned_identity_and_ignores_user_config(tmp_path):
    backend = IsolatedSSHBackend(isolation(tmp_path), "run-1", "alice")

    command = backend.ssh_command("true")

    assert command[:3] == ["ssh", "-F", "/dev/null"]
    assert "StrictHostKeyChecking=yes" in command
    assert "IdentitiesOnly=yes" in command
    assert "ForwardAgent=no" in command
    assert str(backend.config.known_hosts) in " ".join(command)
    assert command[-2] == "yieldrun@localhost"
    request = scoped_request(command[-1])
    assert request == {
        "v": 1,
        "principal": hashlib.sha256(b"alice").hexdigest(),
        "run": hashlib.sha256(b'["alice","run-1"]').hexdigest(),
        "op": "command",
        "command": "true",
    }


def test_backend_requires_authenticated_scope(tmp_path):
    backend = IsolatedSSHBackend(isolation(tmp_path), "run-1")

    with pytest.raises(IsolationError, match="principal"):
        backend.ssh_command("true")


def test_backend_scrubs_host_environment(monkeypatch, tmp_path):
    backend = IsolatedSSHBackend(isolation(tmp_path), "run-1", "alice")
    monkeypatch.setenv("HARNESS_API_KEY", "secret")
    monkeypatch.setenv("ORACLE_PASSWORD", "secret")
    monkeypatch.setenv("PYTHONPATH", "/repo")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent")

    env = backend.client_environment()

    assert env == {"PATH": os.defpath, "LANG": env["LANG"]}
    assert "secret" not in repr(env)


def test_prepare_isolated_run_fails_closed_without_local_fallback(monkeypatch, tmp_path):
    config = isolation(tmp_path)
    calls = []

    def failed(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("ssh unavailable")

    monkeypatch.setattr("harness.isolation.backend.subprocess.run", failed)
    monkeypatch.setattr("harness.isolation.backend.asyncio.create_subprocess_exec",
                        lambda *args, **kwargs: pytest.fail("local fallback attempted"))

    with pytest.raises(IsolationError, match="unavailable"):
        prepare_isolated_run(config, "run-1", "alice")
    assert len(calls) == 1


def test_prepare_isolated_run_requires_launcher_marker_and_expected_identity(monkeypatch, tmp_path):
    config = isolation(tmp_path)
    response = SimpleNamespace(returncode=0, stdout='{"marker":"wrong","user":"yieldrun"}\n', stderr="")
    monkeypatch.setattr("harness.isolation.backend.subprocess.run", lambda *args, **kwargs: response)

    with pytest.raises(IsolationError, match="verification"):
        prepare_isolated_run(config, "run-1", "alice")


def test_engine_adapter_forces_sync_off(monkeypatch, tmp_path):
    captured = {}

    class SSHEnvironment:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    module = SimpleNamespace(_SSHEnvironment=SSHEnvironment)
    monkeypatch.setitem(__import__("sys").modules, "tools.terminal_tool_backends", module)
    backend = IsolatedSSHBackend(isolation(tmp_path), "run-1", "alice")

    backend.install_engine_adapter()
    module._SSHEnvironment(host="localhost", user="yieldrun", port=22,
                           key_path=str(backend.config.ssh_key))

    assert captured["sync_files"] is False
    with pytest.raises(IsolationError, match="replace"):
        module._SSHEnvironment(host="localhost", user="root", port=22,
                               key_path=str(backend.config.ssh_key))


def test_engine_adapter_wraps_probes_commands_and_temp_dir_in_one_scope(monkeypatch, tmp_path):
    captured = []

    class SSHEnvironment:
        def __init__(self, **kwargs):
            pass

    tools = ModuleType("tools")
    tools.__path__ = []
    environments = ModuleType("tools.environments")
    environments.__path__ = []
    backends = ModuleType("tools.terminal_tool_backends")
    backends._SSHEnvironment = SSHEnvironment
    remote = ModuleType("tools.environments.remote_common")
    remote.run_capture = lambda command, timeout: captured.append(command) or SimpleNamespace(returncode=0)
    output = ModuleType("tools.environments.base_output")
    output._popen_bash = lambda command, stdin_data, env: captured.append(command) or SimpleNamespace()
    for name, module in {
        "tools": tools,
        "tools.environments": environments,
        "tools.terminal_tool_backends": backends,
        "tools.environments.remote_common": remote,
        "tools.environments.base_output": output,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    backend = IsolatedSSHBackend(isolation(tmp_path), "run-1", "alice")

    backend.install_engine_adapter()
    environment = backends._SSHEnvironment(
        host="localhost", user="yieldrun", port=22, key_path=str(backend.config.ssh_key))
    environment._run_ssh("probe", 5)
    environment._run_bash("pwd")

    requests = [scoped_request(command[-1]) for command in captured]
    assert [request["command"] for request in requests] == ["probe", "pwd"]
    assert {request["principal"] for request in requests} == {principal_hash("alice")}
    assert {request["run"] for request in requests} == {backend.run_hash}
    assert environment.get_temp_dir() == str(backend.workdir / "tmp")
    with pytest.raises(IsolationError, match="unscoped"):
        environment._build_ssh_command()


def test_stock_chat_refuses_to_spawn_worker_when_isolation_preflight_fails(monkeypatch, tmp_path):
    import asyncio
    from unittest.mock import AsyncMock
    from harness import hermes_chat, native_hermes
    from harness.config import Settings

    monkeypatch.setattr(hermes_chat, 'ROOT', tmp_path)
    monkeypatch.setattr(native_hermes, 'ROOT', tmp_path)
    settings = Settings(isolation=isolation(tmp_path), api_key='fixture')
    store = SimpleNamespace(runs=SimpleNamespace(update_one=AsyncMock()),
        db=SimpleNamespace(name='test', gateway_sessions=SimpleNamespace(find_one=AsyncMock(return_value=None))))
    context = SimpleNamespace(settings=settings, store=store,
        run={'principal_id': 'alice', 'session_id': 's', 'run_id': 'r', 'epoch': 1,
             'created_at': '2026-11-01T00:00:00Z'})
    monkeypatch.setattr(hermes_chat, 'prepare_isolated_run',
                        lambda *args: (_ for _ in ()).throw(IsolationError('preflight denied')))
    monkeypatch.setattr(hermes_chat, 'cancel_run_prompts', AsyncMock())
    monkeypatch.setattr(hermes_chat.asyncio, 'create_subprocess_exec',
                        lambda *args, **kwargs: pytest.fail('worker spawned after failed preflight'))
    with pytest.raises(IsolationError, match='preflight denied'):
        asyncio.run(hermes_chat.run_chat(context))


def test_required_sandbox_rejects_none_evidence(monkeypatch, tmp_path):
    config = isolation(tmp_path)
    config = config.model_copy(update={"sandbox": "required"})
    evidence = {"marker": "1", "launcher": str(config.launcher), "sandbox": "none",
                "user": "yieldrun", "uid": os.geteuid() + 1, "home": str(config.home),
                "python": str(config.analysis_venv / "bin/python3")}
    response = SimpleNamespace(returncode=0, stdout=json.dumps(evidence), stderr="")
    monkeypatch.setattr("harness.isolation.backend.subprocess.run", lambda *args, **kwargs: response)

    with pytest.raises(IsolationError, match="verification"):
        prepare_isolated_run(config, "run-1", "alice")


def test_cleanup_removes_only_the_hashed_remote_workdir(monkeypatch, tmp_path):
    backend = IsolatedSSHBackend(isolation(tmp_path), "run-1", "alice")
    calls = []

    def completed(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("harness.isolation.backend.subprocess.run", completed)

    backend.cleanup()

    assert len(calls) == 1
    request = scoped_request(calls[0][-1])
    assert request["run"] == backend.run_hash
    assert request["op"] == "cleanup" and request["command"] == ""


def test_profile_results_directory_is_stable_distinct_and_survives_run_cleanup(monkeypatch, tmp_path):
    config = isolation(tmp_path)
    first = IsolatedSSHBackend(config, "run-1", "alice")
    second = IsolatedSSHBackend(config, "run-2", "alice")
    other = IsolatedSSHBackend(config, "run-3", "bob")
    assert first.results_dir == second.results_dir
    assert first.results_dir == (
        config.work_root / "profiles" / hashlib.sha256(b"alice").hexdigest() / "results"
    )
    assert first.results_dir != other.results_dir

    first.workdir.mkdir(parents=True)
    first.results_dir.mkdir(parents=True)
    csv_path = first.results_dir / "query-stable.csv"
    csv_path.write_text("a\n1\n")
    def local_cleanup(command, **kwargs):
        assert scoped_request(command[-1])["op"] == "cleanup"
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("harness.isolation.backend.subprocess.run", local_cleanup)
    first.cleanup()

    assert first.workdir.exists()
    assert csv_path.read_text() == "a\n1\n"


def test_byte_upload_uses_stdin_and_returns_generated_absolute_path(monkeypatch, tmp_path):
    backend = IsolatedSSHBackend(isolation(tmp_path), "run-1", "alice")
    captured = {}

    class Process:
        returncode = 0

        async def communicate(self, data):
            captured["data"] = data
            return b"", b""

        def kill(self):
            captured["killed"] = True

        async def wait(self):
            return 0

    async def spawn(*command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr("harness.isolation.backend.asyncio.create_subprocess_exec", spawn)

    path = asyncio.run(backend.upload_bytes(b"a,b\n1,2\n", timeout=2))

    assert Path(path).parent == backend.results_dir
    assert Path(path).name.startswith("query-") and Path(path).suffix == ".csv"
    assert captured["data"] == b"a,b\n1,2\n"
    remote = scoped_request(captured["command"][-1])["command"]
    assert "O_EXCL" in remote and "O_NOFOLLOW" in remote and "dir_fd" in remote
    assert captured["kwargs"]["stdin"] == asyncio.subprocess.PIPE


def test_byte_upload_enforces_private_result_permissions(monkeypatch, tmp_path):
    backend = IsolatedSSHBackend(isolation(tmp_path), "run-1", "alice")
    backend.results_dir.mkdir(parents=True)
    backend.results_dir.parent.chmod(0o755)
    backend.results_dir.chmod(0o755)
    original_spawn = asyncio.create_subprocess_exec

    async def local_spawn(*command, **kwargs):
        remote = shlex.split(scoped_request(command[-1])["command"])
        return await original_spawn(sys.executable, *remote[1:], **kwargs)

    monkeypatch.setattr("harness.isolation.backend.asyncio.create_subprocess_exec", local_spawn)
    path = Path(asyncio.run(backend.upload_bytes(b"a\n1\n")))

    source = tmp_path / "large.csv"
    data = "번호,이름\n".encode() * 40000
    source.write_bytes(data)
    with source.open("rb") as stream:
        uploaded = Path(asyncio.run(backend.upload_file(stream)))
    assert uploaded.read_bytes() == data
    assert stat.S_IMODE(backend.results_dir.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(backend.results_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("outcome", ["failure", "timeout", "cancel"])
def test_byte_upload_failures_never_return_a_path(monkeypatch, tmp_path, outcome):
    backend = IsolatedSSHBackend(isolation(tmp_path), "run-1", "alice")

    class Process:
        returncode = 1

        async def communicate(self, data):
            if outcome in {"timeout", "cancel"}:
                await asyncio.Future()
            return b"", b"failed"

        def kill(self):
            pass

        async def wait(self):
            return 0

    class CleanupProcess:
        returncode = 0

        async def communicate(self, data):
            return b"", b""

        def kill(self):
            pass

        async def wait(self):
            return 0

    calls = 0

    async def spawn(*args, **kwargs):
        nonlocal calls
        calls += 1
        return Process() if calls == 1 else CleanupProcess()

    monkeypatch.setattr("harness.isolation.backend.asyncio.create_subprocess_exec", spawn)

    async def scenario():
        task = asyncio.create_task(backend.upload_bytes(b"csv", timeout=.01))
        if outcome == "cancel":
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(IsolationError):
                await task

    asyncio.run(scenario())


def test_byte_upload_wraps_transport_error_and_reaps_process(monkeypatch, tmp_path):
    backend = IsolatedSSHBackend(isolation(tmp_path), "run-1", "alice")
    state = {"killed": False, "waited": False}

    class Process:
        async def communicate(self, data):
            raise OSError("ssh pipe lost")

        def kill(self):
            state["killed"] = True

        async def wait(self):
            state["waited"] = True

    async def spawn(*args, **kwargs):
        return Process()

    monkeypatch.setattr("harness.isolation.backend.asyncio.create_subprocess_exec", spawn)

    with pytest.raises(IsolationError, match="unavailable"):
        asyncio.run(backend.upload_bytes(b"csv"))
    assert state == {"killed": True, "waited": True}


def test_cancelled_byte_upload_removes_partial_remote_files(monkeypatch, tmp_path):
    backend = IsolatedSSHBackend(isolation(tmp_path), "run-1", "alice")
    backend.results_dir.mkdir(parents=True)
    monkeypatch.setattr("harness.isolation.backend.uuid.uuid4",
                        lambda: SimpleNamespace(hex="fixed"))
    started = asyncio.Event()
    final = backend.results_dir / "query-fixed.csv"
    pending = backend.results_dir / ".query-fixed.csv.pending"

    class UploadProcess:
        returncode = None

        async def communicate(self, data):
            pending.write_bytes(data[:2])
            started.set()
            await asyncio.Future()

        def kill(self):
            self.returncode = -9

        async def wait(self):
            return self.returncode

    class CleanupProcess:
        returncode = 0

        async def communicate(self, data=None):
            pending.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
            return b"", b""

        def kill(self):
            self.returncode = -9

        async def wait(self):
            return self.returncode

    calls = 0

    async def spawn(*args, **kwargs):
        nonlocal calls
        calls += 1
        return UploadProcess() if calls == 1 else CleanupProcess()

    monkeypatch.setattr("harness.isolation.backend.asyncio.create_subprocess_exec", spawn)

    async def scenario():
        task = asyncio.create_task(backend.upload_bytes(b"abcdef"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert calls == 2
    assert not pending.exists() and not final.exists()


@pytest.mark.parametrize("attack", ["ancestor", "results", "destination"])
def test_byte_upload_rejects_symlinks_behaviorally(monkeypatch, tmp_path, attack):
    config = isolation(tmp_path)
    backend = IsolatedSSHBackend(config, "run-1", "alice")
    original_spawn = asyncio.create_subprocess_exec
    target = tmp_path / "target"
    target.mkdir()
    protected = target / "protected"
    protected.write_text("safe")
    monkeypatch.setattr("harness.isolation.backend.uuid.uuid4",
                        lambda: SimpleNamespace(hex="fixed"))
    if attack == "ancestor":
        (config.work_root / "profiles").symlink_to(target, target_is_directory=True)
    elif attack == "results":
        backend.results_dir.parent.mkdir(parents=True)
        backend.results_dir.symlink_to(target, target_is_directory=True)
    else:
        backend.results_dir.mkdir(parents=True)
        (backend.results_dir / "query-fixed.csv").symlink_to(protected)

    async def local_spawn(*command, **kwargs):
        remote = shlex.split(scoped_request(command[-1])["command"])
        return await original_spawn(sys.executable, *remote[1:], **kwargs)

    monkeypatch.setattr("harness.isolation.backend.asyncio.create_subprocess_exec", local_spawn)

    with pytest.raises(IsolationError):
        asyncio.run(backend.upload_bytes(b"a,b\n1,2\n"))
    assert protected.read_text() == "safe"
    assert not (target / ".query-fixed.csv.pending").exists()


def _run_retention_locally(monkeypatch, *, race_name=None, fail_name=None):
    original_run = __import__("subprocess").run

    def run(command, **kwargs):
        remote = shlex.split(scoped_request(command[-1])["command"])
        script = remote[-1]
        if race_name:
            needle = "   os.unlink(entry.name,dir_fd=directory)"
            replacement = (
                f"   if entry.name == {race_name!r}: os.unlink(entry.name,dir_fd=directory)\n"
                + needle
            )
            script = script.replace(needle, replacement)
        if fail_name:
            needle = "   os.unlink(entry.name,dir_fd=directory)"
            replacement = (
                f"   if entry.name == {fail_name!r}: raise PermissionError('denied')\n"
                + needle
            )
            script = script.replace(needle, replacement)
        return original_run(
            [sys.executable, "-I", "-B", "-c", script],
            **kwargs,
        )

    monkeypatch.setattr("harness.isolation.backend.subprocess.run", run)


def test_retention_removes_only_older_csvs_and_pending_files(monkeypatch, tmp_path):
    config = isolation(tmp_path)
    backend = IsolatedSSHBackend(config, "run-1", "alice")
    backend.results_dir.mkdir(parents=True)
    now = 2_000_000_000.0
    cutoff = now - 7 * 24 * 60 * 60
    paths = {
        "old": backend.results_dir / "query-old.csv",
        "exact": backend.results_dir / "query-exact.csv",
        "new": backend.results_dir / "query-new.csv",
        "pending": backend.results_dir / ".query-old.csv.pending",
        "other": backend.results_dir / "notes.txt",
    }
    for path in paths.values():
        path.write_text(path.name)
    os.utime(paths["old"], (cutoff - 1, cutoff - 1))
    os.utime(paths["pending"], (cutoff - 1, cutoff - 1))
    os.utime(paths["other"], (cutoff - 1, cutoff - 1))
    os.utime(paths["exact"], (cutoff, cutoff))
    os.utime(paths["new"], (cutoff + 1, cutoff + 1))
    symlink = backend.results_dir / "query-link.csv"
    symlink.symlink_to(paths["old"])
    _run_retention_locally(monkeypatch)

    isolation_backend.cleanup_profile_results(config, "alice", now=now)
    isolation_backend.cleanup_profile_results(config, "alice", now=now)

    assert not paths["old"].exists()
    assert not paths["pending"].exists()
    assert paths["exact"].exists() and paths["new"].exists()
    assert paths["other"].exists() and symlink.is_symlink()


def test_retention_tolerates_concurrent_disappearance(monkeypatch, tmp_path, caplog):
    config = isolation(tmp_path)
    backend = IsolatedSSHBackend(config, "run-1", "alice")
    backend.results_dir.mkdir(parents=True)
    path = backend.results_dir / "query-race.csv"
    path.write_text("csv")
    now = 2_000_000_000.0
    cutoff = now - 7 * 24 * 60 * 60
    os.utime(path, (cutoff - 1, cutoff - 1))
    _run_retention_locally(monkeypatch, race_name=path.name)

    with caplog.at_level(logging.WARNING):
        isolation_backend.cleanup_profile_results(config, "alice", now=now)

    assert not path.exists()
    assert not caplog.records


def test_retention_logs_transport_failure_without_failing_run(monkeypatch, tmp_path, caplog):
    config = isolation(tmp_path)

    def failed(*args, **kwargs):
        raise OSError("ssh unavailable")

    monkeypatch.setattr("harness.isolation.backend.subprocess.run", failed)
    with caplog.at_level(logging.WARNING):
        isolation_backend.cleanup_profile_results(config, "alice", now=2_000_000_000.0)

    assert "retention" in caplog.text.lower()


def test_retention_logs_deletion_failure_and_continues(monkeypatch, tmp_path, caplog):
    config = isolation(tmp_path)
    backend = IsolatedSSHBackend(config, "run-1", "alice")
    backend.results_dir.mkdir(parents=True)
    blocked = backend.results_dir / "query-blocked.csv"
    removable = backend.results_dir / "query-removable.csv"
    blocked.write_text("blocked")
    removable.write_text("removable")
    now = 2_000_000_000.0
    cutoff = now - 7 * 24 * 60 * 60
    os.utime(blocked, (cutoff - 1, cutoff - 1))
    os.utime(removable, (cutoff - 1, cutoff - 1))
    _run_retention_locally(monkeypatch, fail_name=blocked.name)

    with caplog.at_level(logging.WARNING):
        isolation_backend.cleanup_profile_results(config, "alice", now=now)

    assert blocked.exists() and not removable.exists()
    assert blocked.name in caplog.text


def test_launcher_uses_root_configured_sandbox_binary_not_path(monkeypatch, tmp_path):
    from harness.isolation import launcher

    binary = tmp_path / "bwrap"
    binary.write_text("binary")
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setattr(launcher, "root_owned_readonly", lambda *args, **kwargs: None)
    monkeypatch.setenv("PATH", str(tmp_path / "attacker"))
    home = tmp_path / "home"
    work_root = home / "work"
    scope = launcher.Scope(work_root / "profiles" / ("a" * 64),
                           work_root / "runs" / ("b" * 64))
    scope.profile.mkdir(parents=True)
    scope.workdir.mkdir(parents=True)
    config = {"sandbox": "auto", "sandbox_binary": str(binary),
              "analysis_venv": "/opt/yieldrun/analysis-venv", "home": str(home),
              "work_root": str(work_root)}

    clean_env = {}
    command = launcher.sandbox_command(config, scope, "true", clean_env)

    assert command[0] == str(binary)
    for descriptor in clean_env["_YIELD_PASS_FDS"].split(","):
        os.close(int(descriptor))


@pytest.mark.parametrize("name", ["setup_macos.sh", "setup_ubuntu.sh"])
def test_setup_installs_root_owned_external_key_and_force_command(name):
    script = Path(__file__).parents[1] / "isolation" / name
    content = script.read_text()

    assert "/etc/ssh/yieldrun_authorized_keys" in content
    assert "ForceCommand /usr/local/libexec/yieldrun-launcher" in content
    assert "PermitUserEnvironment no" in content
    assert ".ssh/authorized_keys" not in content


def test_retained_python_workspace_survives_run_cleanup(monkeypatch, tmp_path):
    config = isolation(tmp_path)
    config.retain_python_files = True
    backend = IsolatedSSHBackend(config, "run-kept", "alice")
    output = backend.workdir / "python-call" / "output" / "result.json"
    output.parent.mkdir(parents=True)
    output.write_text('{"status":"success"}')
    def unexpected_cleanup(*args, **kwargs):
        pytest.fail("retained workspace must not invoke remote cleanup")
    monkeypatch.setattr("harness.isolation.backend.subprocess.run", unexpected_cleanup)
    backend.cleanup()
    assert output.read_text() == '{"status":"success"}'

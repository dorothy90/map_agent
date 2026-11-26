import asyncio
from pathlib import Path

import pytest

from harness.config import IsolationSettings


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
        ssh_host="localhost", ssh_user="yieldrun", ssh_key=key, known_hosts=known_hosts,
        home=home, work_root=work_root, analysis_venv=analysis_venv,
        launcher=launcher, sandbox="auto")


def test_process_runtime_passes_session_id_to_backend(monkeypatch, tmp_path):
    from harness.runtime.process import ProcessRuntime

    calls = []
    backend = object()

    def capture(*args):
        calls.append(args)
        return backend

    monkeypatch.setattr("harness.runtime.process.isolation_backend", capture)
    config = isolation(tmp_path)
    runtime = ProcessRuntime(isolation=config, run_id="run-1", principal_id="alice", session_id="session-1")

    assert calls == [(config, "run-1", "alice", "session-1")]
    assert runtime.isolated is backend


def test_process_runtime_uses_only_isolated_backend(monkeypatch, tmp_path):
    from harness.runtime.process import ProcessRuntime

    calls = []

    async def remote(self, code, datasets, timeout):
        calls.append((code, datasets, timeout))
        return {"status": "success", "tables": [], "plots": [], "stdout": "remote",
                "stderr": "", "error": None}

    monkeypatch.setattr("harness.isolation.backend.IsolatedSSHBackend.execute_python", remote)
    monkeypatch.setattr("harness.runtime.process.asyncio.create_subprocess_exec",
                        lambda *args, **kwargs: pytest.fail("local process attempted"))

    result = asyncio.run(ProcessRuntime(isolation=isolation(tmp_path), run_id="run-1", principal_id="alice")
                         .execute("print('ok')", {"r": []}, timeout=7))

    assert result["stdout"] == "remote"
    assert calls == [("print('ok')", {"r": []}, 7)]


def test_isolated_runtime_failure_does_not_retry_locally(monkeypatch, tmp_path):
    from harness.isolation.backend import IsolationError
    from harness.runtime.process import ProcessRuntime

    async def failed(*args, **kwargs):
        raise IsolationError("backend lost")

    monkeypatch.setattr("harness.isolation.backend.IsolatedSSHBackend.execute_python", failed)
    monkeypatch.setattr("harness.runtime.process.asyncio.create_subprocess_exec",
                        lambda *args, **kwargs: pytest.fail("local retry attempted"))

    with pytest.raises(IsolationError, match="backend lost"):
        asyncio.run(ProcessRuntime(isolation=isolation(tmp_path), run_id="run-1", principal_id="alice").execute("1", {}))


def test_minimal_worker_does_not_require_scipy_or_statsmodels(monkeypatch):
    import builtins
    from harness.runtime import worker

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name in {"scipy", "statsmodels", "statsmodels.api"}:
            raise ModuleNotFoundError(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    namespace = worker.analysis_namespace({}, [], [])

    assert set(("pd", "np", "px", "go", "emit_table", "emit_plot")) <= namespace.keys()
    assert "scipy" not in namespace and "sm" not in namespace

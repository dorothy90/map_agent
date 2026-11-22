from __future__ import annotations

import os
from pathlib import Path

import pytest

from harness.config import SeatbeltIsolationSettings, Settings
from harness.isolation.backend import IsolationError, IsolatedSSHBackend, isolation_backend, prepare_isolated_run


def seatbelt_paths(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "seatbelt-home"
    work = home / "work"
    venv = tmp_path / "venv"
    launcher = tmp_path / "launcher.py"
    for directory in (home, work, venv):
        directory.mkdir()
    launcher.write_text("# launcher\n", encoding="utf-8")
    return {
        "home": str(home),
        "work_root": str(work),
        "analysis_venv": str(venv),
        "launcher": str(launcher),
    }


def test_macos_defaults_to_seatbelt_without_ssh(monkeypatch, tmp_path):
    monkeypatch.setattr("harness.config.sys.platform", "darwin")
    paths = seatbelt_paths(tmp_path)
    monkeypatch.setenv("HARNESS_ISOLATION_BACKEND", "seatbelt")
    monkeypatch.setenv("HARNESS_ISOLATION_SANDBOX", "required")
    for key, value in paths.items():
        monkeypatch.setenv("HARNESS_ISOLATION_" + key.upper(), value)

    settings = Settings.from_env()

    assert settings.no_yield_tools is True
    assert settings.isolation.sandbox == "required"
    assert isinstance(settings.isolation, SeatbeltIsolationSettings)
    assert settings.isolation.backend == "seatbelt"

    monkeypatch.delenv("HARNESS_ISOLATION_BACKEND")
    for name in (
        "HARNESS_ISOLATION_SSH_HOST", "HARNESS_ISOLATION_SSH_USER",
        "HARNESS_ISOLATION_SSH_KEY", "HARNESS_ISOLATION_KNOWN_HOSTS",
    ):
        monkeypatch.delenv(name, raising=False)
    assert isinstance(Settings.from_env().isolation, SeatbeltIsolationSettings)


def test_seatbelt_rejects_non_macos_and_symlinked_scope(monkeypatch, tmp_path):
    paths = seatbelt_paths(tmp_path)
    monkeypatch.setattr("harness.config.sys.platform", "linux")
    with pytest.raises(ValueError, match="macOS"):
        SeatbeltIsolationSettings(**paths)

    monkeypatch.setattr("harness.config.sys.platform", "darwin")
    linked = tmp_path / "linked-work"
    linked.symlink_to(paths["work_root"], target_is_directory=True)
    paths["work_root"] = str(linked)
    with pytest.raises(ValueError, match="symlink"):
        SeatbeltIsolationSettings(**paths)


def test_seatbelt_rejects_mutable_launcher(monkeypatch, tmp_path):
    paths = seatbelt_paths(tmp_path)
    Path(paths["launcher"]).chmod(0o666)

    with pytest.raises(ValueError, match="writable by another user"):
        SeatbeltIsolationSettings(**paths)


def test_backend_selection_has_no_auto_or_fallback(tmp_path):
    config = SeatbeltIsolationSettings(**seatbelt_paths(tmp_path))
    backend = isolation_backend(config, "run-1", "alice")

    assert backend.__class__.__name__ == "SeatbeltIsolationBackend"

    with pytest.raises(TypeError, match="unsupported isolation backend"):
        isolation_backend(object(), "run-1", "alice")


def test_seatbelt_launch_failure_is_closed_without_local_execution(tmp_path):
    config = SeatbeltIsolationSettings(**seatbelt_paths(tmp_path))

    with pytest.raises(IsolationError, match="unavailable"):
        prepare_isolated_run(config, "run-1", "alice")

    backend = isolation_backend(config, "run-1", "alice")
    assert not backend.workdir.exists()


def test_seatbelt_client_environment_is_allowlisted(tmp_path):
    config = SeatbeltIsolationSettings(**seatbelt_paths(tmp_path))
    backend = isolation_backend(config, "run-1", "alice")
    os.environ["HARNESS_API_KEY"] = "must-not-cross"
    os.environ["ORACLE_PASSWORD"] = "must-not-cross"
    try:
        environment = backend.client_environment()
    finally:
        os.environ.pop("HARNESS_API_KEY")
        os.environ.pop("ORACLE_PASSWORD")

    assert set(environment) <= {
        "PATH", "LANG", "LC_ALL", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS",
        "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE",
    }
    assert "must-not-cross" not in environment.values()


def test_fixed_local_isolation_health_route_is_registered():
    from harness.app import app

    paths = {route.path for route in app.routes}
    assert "/health/isolation" in paths


@pytest.mark.parametrize('parent_readable,child_code,expected', [
    (False, 0, (True, True)), (True, 0, (False, True)),
    (False, 1, (True, False)), (False, 2, (True, False)),
])
def test_file_read_probe_checks_parent_and_child(monkeypatch, tmp_path, parent_readable, child_code, expected):
    import builtins
    from types import SimpleNamespace
    from harness.isolation.seatbelt_local import file_read_probe
    sentinel = tmp_path / 'sentinel.txt'
    sentinel.write_text('synthetic sentinel')
    real_open = builtins.open
    def guarded_open(path, *args, **kwargs):
        if not parent_readable and str(path) == str(sentinel):
            raise PermissionError('sandbox denied')
        return real_open(path, *args, **kwargs)
    def child(command, **kwargs):
        assert command[1:4] == ['-I', '-B', '-c']
        assert 'open(' in command[-1] and str(sentinel) in command[-1]
        assert 'PermissionError' in command[-1]
        return SimpleNamespace(returncode=child_code)
    monkeypatch.setattr(builtins, 'open', guarded_open)
    monkeypatch.setattr('subprocess.run', child)
    scope = {'denied': {}}
    exec(file_read_probe(sentinel), scope)
    assert (scope['denied']['file_open'], scope['denied']['child_file_open']) == expected


def test_file_read_probe_does_not_accept_missing_file(tmp_path):
    from harness.isolation.seatbelt_local import file_read_probe
    with pytest.raises(FileNotFoundError):
        exec(file_read_probe(tmp_path / 'missing'), {'denied': {}})


@pytest.mark.parametrize('file_open,child_file_open', [(True, True), (False, True), (True, False), (None, None)])
def test_prepare_requires_file_read_denials(monkeypatch, tmp_path, file_open, child_file_open):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr('harness.config.sys.platform', 'darwin')
    config = SeatbeltIsolationSettings(**seatbelt_paths(tmp_path))
    backend = isolation_backend(config, 'run-1', 'alice')
    denied = dict(repo=True, other_run=True, other_principal=True, network=True)
    if file_open is not None:
        denied.update(file_open=file_open, child_file_open=child_file_open)
    def run(command, **kwargs):
        from harness.tests.test_isolation_backend import scoped_request
        probe = scoped_request(command[-1])['command']
        assert 'file_open' in probe and 'child_file_open' in probe
        evidence = dict(marker='1', launcher=str(config.launcher), sandbox='sandbox-exec',
                        uid=os.geteuid(), home=str(backend.results_dir.parent),
                        python=str(config.analysis_venv / 'bin/python3'), own_write=True, denied=denied)
        return SimpleNamespace(returncode=0, stdout=json.dumps(evidence))
    monkeypatch.setattr('harness.isolation.backend.subprocess.run', run)
    if file_open and child_file_open:
        assert prepare_isolated_run(config, 'run-1', 'alice')['backend'] == 'seatbelt'
    else:
        with pytest.raises(IsolationError):
            prepare_isolated_run(config, 'run-1', 'alice')


def test_real_seatbelt_denies_file_open_in_parent_and_child(tmp_path):
    import json
    import subprocess
    import sys
    from harness.isolation.seatbelt_local import file_read_probe, policy_for
    if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').exists():
        pytest.skip('macOS sandbox-exec unavailable')
    available = subprocess.run(['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)',
                                '/usr/bin/true'], capture_output=True)
    if available.returncode:
        pytest.skip('sandbox-exec unavailable within this execution sandbox')
    sentinel = tmp_path / 'outside.txt'
    sentinel.write_text('synthetic sentinel')
    profile, workdir = tmp_path / 'profile', tmp_path / 'work'
    profile.mkdir()
    workdir.mkdir()
    # Production analysis venvs link to the CommandLineTools Python (a read root);
    # the /usr/bin/python3 shim needs xcode-select state outside the policy.
    base = Path('/Library/Developer/CommandLineTools/usr/bin/python3')
    if not base.exists():
        pytest.skip('CommandLineTools python3 unavailable')
    venv = tmp_path / 'venv'
    subprocess.run([str(base), '-m', 'venv', '--without-pip', str(venv)], check=True, timeout=60)
    python = venv / 'bin/python3'
    policy = policy_for(tmp_path, workdir, venv, profile, workdir)
    script = "import json\ndenied={}\n" + file_read_probe(sentinel) + "print(json.dumps(denied))"
    result = subprocess.run(['/usr/bin/sandbox-exec', '-p', policy, str(python),
                             '-I', '-B', '-c', script], cwd=workdir,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'file_open': True, 'child_file_open': True}

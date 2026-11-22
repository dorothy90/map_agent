import asyncio
from pathlib import Path
import os
import shlex
import subprocess
import sys

import pytest

from harness.config import LocalIsolationSettings
from harness.isolation.backend import IsolationError, isolation_backend, prepare_isolated_run


LAUNCHER = Path(__file__).resolve().parents[1] / 'isolation' / 'launcher.py'


def local_paths(root, venv):
    home = root / 'analysis'
    work = home / 'work'
    home.mkdir(mode=0o700)
    work.mkdir(mode=0o700)
    return dict(home=home, work_root=work, analysis_venv=venv, launcher=LAUNCHER)


def test_local_command_executes_launcher_without_ssh(tmp_path):
    config = LocalIsolationSettings.model_construct(**local_paths(tmp_path, Path(sys.prefix)))
    boundary = isolation_backend(config, 'run-a', 'alice')
    command = boundary.command('true')
    assert command[:4] == [str(Path(sys.prefix) / 'bin' / 'python3'), '-I', '-B', str(LAUNCHER)]
    assert 'ssh' not in command
    with pytest.raises(IsolationError):
        isolation_backend(config, 'run-a').command('true')


def test_unavailable_sandbox_refuses_execution(tmp_path, monkeypatch):
    from harness.isolation import launcher

    config = local_paths(tmp_path, Path(sys.prefix))
    marker = tmp_path / 'ran'
    scope = launcher.Scope(config['home'] / 'alice', config['work_root'] / 'run-a')
    monkeypatch.setattr(launcher.sys, 'platform', 'linux')
    with pytest.raises(SystemExit) as refusal:
        launcher.sandbox_command(dict(config, sandbox='required', sandbox_binary=tmp_path / 'absent'),
                                 scope, f'touch {shlex.quote(str(marker))}', {})
    assert refusal.value.code != 0
    assert not marker.exists()


@pytest.mark.skipif(sys.platform != 'linux', reason='Requires Linux user and network namespaces')
def test_actual_local_analysis_scope(tmp_path):
    venv = os.getenv('YIELD_TEST_ANALYSIS_VENV')
    if not venv:
        pytest.skip('Set YIELD_TEST_ANALYSIS_VENV to run the deployed local isolation check')
    config = LocalIsolationSettings(**local_paths(tmp_path.resolve(), Path(venv)))
    evidence = prepare_isolated_run(config, 'run-a', 'alice')
    assert evidence['selected_sandbox'] == 'bwrap'
    assert evidence['verified_uid'] == os.geteuid()
    boundary = isolation_backend(config, 'run-a', 'alice')
    csv = asyncio.run(boundary.upload_bytes(b'value\n42\n'))
    result = asyncio.run(boundary.execute_python(f"print(pd.read_csv({csv!r}).value.sum())", {}, timeout=60))
    assert result['status'] == 'success'
    assert result['stdout'].strip() == '42'
    other = isolation_backend(config, 'run-a', 'bob')
    read = subprocess.run(other.command(f"cat {shlex.quote(csv)}"), env=other.client_environment(),
                          capture_output=True, timeout=20)
    assert read.returncode != 0
    assert not read.stdout
    boundary.cleanup()
    assert Path(csv).read_bytes() == b'value\n42\n'


@pytest.mark.parametrize("cancel", [False, True])
def test_chat_cleanup_preserves_probe_failure_and_cancellation(tmp_path, monkeypatch, cancel):
    import threading
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from harness import hermes_chat
    from harness.config import Settings

    settings = Settings.model_construct(isolation=LocalIsolationSettings.model_construct(
        **local_paths(tmp_path, Path(sys.prefix))))
    store = SimpleNamespace(db=SimpleNamespace(name='test', gateway_sessions=SimpleNamespace(
        find_one=AsyncMock(return_value=None))), runs=SimpleNamespace(update_one=AsyncMock()))
    context = SimpleNamespace(settings=settings, run=dict(principal_id='p', session_id='s',
        epoch=1, run_id='r', created_at='2026-01-01T00:00:00'), store=store)
    (tmp_path / 'config.yaml').write_text('{}')
    monkeypatch.setattr(hermes_chat, 'prepare_profile', lambda principal: tmp_path)
    monkeypatch.setattr(hermes_chat, 'ROOT', tmp_path)
    monkeypatch.setattr(hermes_chat, 'cancel_run_prompts', AsyncMock())
    cleanup = Mock(side_effect=IsolationError('cleanup failed'))
    monkeypatch.setattr(hermes_chat, 'cleanup_isolated_run', cleanup)
    ready = threading.Event()
    release = threading.Event()

    def prepare(*args):
        ready.set()
        if cancel:
            assert release.wait(5)
            return {}
        raise IsolationError('local probe failed')

    monkeypatch.setattr(hermes_chat, 'prepare_isolated_run', prepare)

    async def verify():
        task = asyncio.create_task(hermes_chat.run_chat(context))
        assert await asyncio.to_thread(ready.wait, 5)
        if cancel:
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            cleanup.assert_called_once()
        else:
            with pytest.raises(IsolationError, match='local probe failed'):
                await task
            cleanup.assert_not_called()

    asyncio.run(verify())


def test_non_system_python_mounts_only_selected_runtime_files(tmp_path, monkeypatch):
    from harness.isolation import launcher

    base = tmp_path / 'uv' / 'cpython'
    python = base / 'bin' / 'python3'
    python.parent.mkdir(parents=True)
    python.touch()
    venv = tmp_path / 'repo' / '.venv'
    (venv / 'bin').mkdir(parents=True)
    (venv / 'bin/python3').symlink_to(python)
    stdlib = str(base / 'lib/python3.11')
    monkeypatch.setattr(launcher.sysconfig, 'get_path', lambda name: stdlib)
    monkeypatch.setattr(launcher.sysconfig, 'get_config_var', {
        'LIBDIR': str(base / 'lib'), 'LDLIBRARY': 'libpython3.11.so', 'INSTSONAME': 'libpython3.11.so.1.0',
    }.get)

    paths = set(launcher.local_read_paths(venv))
    assert {str(python), stdlib, str(base / 'lib/libpython3.11.so.1.0'), str(venv)} <= paths
    assert paths.isdisjoint({'/usr', str(base), str(base.parent), str(venv.parent), str(tmp_path)})


def test_local_mounts_cannot_expose_private_workspaces(tmp_path, monkeypatch):
    from harness.isolation import launcher
    config = dict(local_paths(tmp_path, Path(sys.prefix)), backend='local', sandbox='required')
    config['sandbox_binary'] = str(Path(sys.executable))
    monkeypatch.setattr(launcher.sys, 'platform', 'linux')
    monkeypatch.setattr(launcher, 'root_owned_readonly', lambda *a, **kw: None)
    monkeypatch.setattr(launcher, 'local_read_paths', lambda venv: [str(tmp_path)])
    scope = launcher.Scope(config['work_root'] / 'profiles' / ('a' * 64),
                           config['work_root'] / 'runs' / ('b' * 64))
    with pytest.raises(SystemExit):
        launcher.sandbox_command(config, scope, "true", {})

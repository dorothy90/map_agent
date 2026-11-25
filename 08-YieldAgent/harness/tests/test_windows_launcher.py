from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import uuid

import pytest

from harness.isolation import windows_launcher as launcher


def test_command_launches_isolated_supervisor_and_preserves_argument_boundaries(tmp_path):
    scope = 'ab' * 32
    argv = ['-I', str(tmp_path / 'two words.py'), '4SA 맵.xlsx']
    command = launcher.command(scope=scope, python=Path(sys.executable), cwd=tmp_path,
                               read_dirs=(), write_dirs=(tmp_path,), argv=argv)
    assert command[:4] == [sys.executable, '-I', '-B', str(Path(launcher.__file__).resolve())]
    assert command[command.index('--') + 1:] == argv
    assert command[command.index('--parent') + 1] == str(os.getpid())
    with pytest.raises(ValueError, match='SHA-256'):
        launcher.command(scope='other/employee', python=Path(sys.executable), cwd=tmp_path,
                         read_dirs=(), write_dirs=(tmp_path,), argv=argv)
    with pytest.raises(ValueError, match='writable'):
        launcher.command(scope=scope, python=Path(sys.executable), cwd=tmp_path.parent,
                         read_dirs=(), write_dirs=(tmp_path,), argv=argv)


def test_analysis_environment_contains_no_service_credentials(tmp_path):
    env = launcher._child_environment(tmp_path, sys.executable, 'C:/Windows')
    assert set(env) == {'SystemRoot', 'WINDIR', 'PATH', 'TMP', 'TEMP', 'HOME', 'USERPROFILE',
                        'MPLCONFIGDIR', 'OPENBLAS_NUM_THREADS'}
    assert env['USERPROFILE'] == env['TEMP'] == str(tmp_path)


def test_analysis_runtime_paths_reports_real_interpreter():
    result = subprocess.run([sys.executable, '-I', launcher.__file__, 'runtime-paths',
                             '--python', sys.executable], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {'prefix': sys.prefix, 'base_prefix': sys.base_prefix}


@pytest.mark.skipif(os.name != 'nt', reason='AppContainer token and NTFS enforced on native Windows')
def test_windows_private_root_refuses_existing_unprotected_directory(tmp_path):
    root = tmp_path / 'private'
    launcher.ensure_private_root(root)
    launcher.ensure_private_root(root)
    unmanaged = tmp_path / 'existing'
    unmanaged.mkdir()
    with pytest.raises(PermissionError):
        launcher.ensure_private_root(unmanaged)


@pytest.mark.skipif(os.name != 'nt', reason='AppContainer token and NTFS enforced on native Windows')
def test_windows_appcontainer_launch_enforces_paths_network_and_env(tmp_path):
    private = tmp_path / 'private'
    launcher.ensure_private_root(private)
    inputs, work, other = (private / name for name in ('inputs', 'work', 'other-session'))
    for directory in (inputs, work, other):
        directory.mkdir()
    (inputs / 'source.csv').write_text('source')
    (other / 'other.csv').write_text('other')
    script = """
import os, socket, sys
from pathlib import Path
source, other, output, port = sys.argv[1:]
assert Path(source).read_text() == 'source'
for f in (lambda: Path(source).write_text('changed'), lambda: Path(other).read_text()):
    try:
        f()
    except PermissionError:
        pass
    else:
        raise RuntimeError('unexpected access')
assert 'YIELD_SENTINEL_SECRET' not in os.environ
try:
    socket.create_connection(('127.0.0.1', int(port)), timeout=2)
except OSError:
    pass
else:
    raise RuntimeError('unexpected loopback access')
Path(output).write_text('done')
print('ok')
"""
    analysis = Path(os.environ.get('HARNESS_WINDOWS_TEST_PYTHON', sys.executable))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        args = [ '-I', '-B', '-c', script, inputs / 'source.csv', other / 'other.csv', work / 'out.txt',
                 str(listener.getsockname()[1])]
        argv = launcher.command(scope=uuid.uuid4().hex * 2, python=analysis, cwd=work,
                                read_dirs=(inputs,), write_dirs=(work,), argv=args)
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=55,
                              env={**os.environ, 'YIELD_SENTINEL_SECRET': 'only-for-host'})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == 'ok'
    assert (work / 'out.txt').read_text() == 'done'

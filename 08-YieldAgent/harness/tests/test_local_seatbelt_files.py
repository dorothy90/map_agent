import asyncio
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from harness.config import SeatbeltIsolationSettings
from harness.isolation.backend import IsolationError, isolation_backend, prepare_isolated_run
from harness.tests.local_test_resources import MAC_TEST_RESOURCES


@pytest.fixture
def boundary(tmp_path):
    if sys.platform != 'darwin':
        pytest.skip('Actual macOS Seatbelt test')
    configured = os.getenv('YIELD_TEST_ANALYSIS_VENV') or os.getenv('HARNESS_ISOLATION_ANALYSIS_VENV')
    venv = Path(configured) if configured else (MAC_TEST_RESOURCES / 'analysis-venv').resolve()
    if not venv.is_dir():
        pytest.fail(f'Analysis environment missing at {venv}. Set YIELD_TEST_ANALYSIS_VENV or '
                    f'link {MAC_TEST_RESOURCES / "analysis-venv"} to the macOS analysis environment')
    root = tmp_path.resolve()
    home = root / 'nas-home'
    work = home / 'work'
    work.mkdir(parents=True)
    launcher = root / 'seatbelt_local.py'
    shutil.copyfile(Path(__file__).parents[1] / 'isolation' / 'seatbelt_local.py', launcher)
    config = SeatbeltIsolationSettings(home=home, work_root=work, analysis_venv=venv, launcher=launcher)
    return isolation_backend(config, 'request-a', 'employee-a')


def test_actual_seatbelt_csv_and_cross_user_denial(boundary):
    evidence = prepare_isolated_run(boundary.config, 'request-a', 'employee-a')
    assert evidence['selected_sandbox'] == 'sandbox-exec'
    path = Path(asyncio.run(boundary.upload_bytes(b'value\n42\n')))
    assert path.read_bytes() == b'value\n42\n'
    other = isolation_backend(boundary.config, 'request-b', 'employee-b')
    prepare_isolated_run(other.config, 'request-b', 'employee-b')
    script = f"from pathlib import Path; print(Path({str(path)!r}).read_text())"
    result = subprocess.run(other.ssh_command('python3 -I -B -c ' + shlex.quote(script)),
        env=other.client_environment(), capture_output=True, text=True)
    assert result.returncode != 0
    assert 'PermissionError' in result.stderr
    data = path.read_bytes()
    asyncio.run(boundary._cleanup_upload(path.name, '.' + path.name + '.pending', len(data), hashlib.sha256(data).hexdigest()))
    assert not path.exists()


def test_actual_seatbelt_rejects_symlink_results(boundary, tmp_path):
    prepare_isolated_run(boundary.config, 'request-a', 'employee-a')
    boundary.results_dir.rmdir()
    target = tmp_path / 'outside'
    target.mkdir()
    boundary.results_dir.symlink_to(target, target_is_directory=True)
    with pytest.raises(IsolationError):
        asyncio.run(boundary.upload_bytes(b'value\n42\n'))
    assert list(target.iterdir()) == []


def test_actual_seatbelt_retains_python_files_when_enabled(boundary):
    boundary.config.retain_python_files = True
    result = asyncio.run(boundary.execute_python("print('kept')", {}, 30))
    assert result['status'] == 'success'
    assert Path(result['workspace']['input']).is_file()
    assert Path(result['workspace']['result']).is_file()


def test_same_request_identifier_has_separate_user_workdirs(boundary):
    other = isolation_backend(boundary.config, 'request-a', 'employee-b')
    assert boundary.workdir != other.workdir


def test_actual_seatbelt_denies_other_request_and_fd_escape(boundary):
    prepare_isolated_run(boundary.config, 'request-a', 'employee-a')
    sibling = isolation_backend(boundary.config, 'request-b', 'employee-a')
    prepare_isolated_run(sibling.config, 'request-b', 'employee-a')
    marker = sibling.workdir / 'private.txt'
    marker.write_text('not shared')
    scripts = [
        f"from pathlib import Path; print(Path({str(marker)!r}).read_text())",
        "import os; os.open('../..',os.O_RDONLY|os.O_DIRECTORY,dir_fd=int(os.environ['YIELD_RESULTS_FD']))",
    ]
    for script in scripts:
        result = subprocess.run(boundary.ssh_command('python3 -I -B -c ' + shlex.quote(script)),
            env=boundary.client_environment(), capture_output=True, text=True)
        assert result.returncode != 0
        assert 'PermissionError' in result.stderr


def test_actual_seatbelt_python_reads_uploaded_csv(boundary):
    path = asyncio.run(boundary.upload_bytes(b'value\n42\n'))
    result = asyncio.run(boundary.execute_python(f"print(pd.read_csv({path!r})['value'].sum())", {}, 30))
    assert result['status'] == 'success'
    assert result['stdout'].strip() == '42'
    assert 'workspace' not in result
    assert not list(boundary.workdir.glob('python-*'))


def test_actual_seatbelt_only_results_directory_fd_is_inherited(boundary):
    script = """import os,stat,json
fds=[]
for fd in range(3,128):
 try:
  info=os.fstat(fd)
 except OSError:
  continue
 if stat.S_ISDIR(info.st_mode):
  fds.append(fd)
print(json.dumps(fds))
"""
    result = subprocess.run(boundary.ssh_command('python3 -I -B -c ' + shlex.quote(script)),
        env=boundary.client_environment(), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    import json
    assert len(json.loads(result.stdout)) == 1


def test_local_launcher_rejects_traversal_envelope():
    import base64
    import json
    from harness.isolation.seatbelt_local import parse_request
    for field in ('principal', 'run'):
        request = {'v': 1, 'principal': 'a' * 64, 'run': 'b' * 64, 'op': 'command', 'command': 'true'}
        request[field] = '../other'
        token = 'yieldrun-v1 ' + base64.urlsafe_b64encode(json.dumps(request).encode()).decode()
        with pytest.raises(ValueError, match='invalid scoped request'):
            parse_request(token)


def test_actual_seatbelt_retention_removes_only_expired_csv(boundary):
    import time
    from harness.isolation.backend import RETENTION_SECONDS
    old = Path(asyncio.run(boundary.upload_bytes(b'value\n1\n')))
    fresh = Path(asyncio.run(boundary.upload_bytes(b'value\n2\n')))
    now = time.time()
    expired = now - RETENTION_SECONDS - 10
    os.utime(old, (expired, expired))
    boundary.cleanup_results(now=now)
    assert not old.exists()
    assert fresh.read_bytes() == b'value\n2\n'


def test_actual_seatbelt_cancellation_stops_descendant_writes(boundary):
    boundary.config.retain_python_files = True
    child = "import time;from pathlib import Path;time.sleep(2);Path('late-write').write_text('escaped')"
    code = f"import subprocess,time;from pathlib import Path;subprocess.Popen(['python3','-c',{child!r}]);Path('started').write_text('ready');time.sleep(30)"
    async def exercise():
        task = asyncio.create_task(boundary.execute_python(code, {}, 30))
        try:
            for _ in range(100):
                if list(boundary.workdir.glob('python-*/started')):
                    break
                await asyncio.sleep(.1)
            else:
                pytest.fail('Worker did not start')
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(2.5)
            assert not list(boundary.workdir.glob('python-*/late-write'))
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    asyncio.run(exercise())

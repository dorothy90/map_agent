"""Portable contracts also run on the Windows developer checkout."""
import json
import os
from pathlib import Path
import subprocess
import sys


PROJECT = Path(__file__).resolve().parents[2]
ENGINE = PROJECT / 'harness' / 'engine'


def test_windows_chat_discovery_has_no_unscoped_host_toolsets(tmp_path):
    script = """
import asyncio, json, sys
sys.path[:0] = sys.argv[1:3]
from harness.hermes_chat_worker import WINDOWS_WEB_TOOLSETS
from harness.native_tools import register_yield_tools
from harness.tools.registry import native_domain_registry
from tools.registry import registry
from model_tools import _select_tool_names
from types import SimpleNamespace
loop = asyncio.new_event_loop()
try:
    executor = SimpleNamespace(registry=native_domain_registry(), execute=None)
    selected = set(executor.registry.tools) - {'run_python', 'execute_code'}
    names = register_yield_tools(registry, executor, loop, allowed=selected)
    available = _select_tool_names([*WINDOWS_WEB_TOOLSETS, 'code_execution'], [], True)
    assert set(names).issubset(available)
    print(json.dumps(sorted(available)))
finally:
    loop.close()
"""
    env = {**os.environ, 'HERMES_HOME': str(tmp_path), 'HERMES_DISABLE_LAZY_INSTALLS': '1'}
    result = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', script, str(ENGINE), str(PROJECT)],
                            env=env, cwd=tmp_path, capture_output=True, timeout=60, check=True)
    available = set(json.loads(result.stdout.splitlines()[-1]))
    assert {'load_wafer_maps', 'query_db', 'clarify', 'execute_code'} <= available
    assert not available.intersection({'terminal', 'run_python', 'read_file', 'write_file', 'patch',
                                       'delegate_task', 'manage_connections', 'cronjob_manage'})


def test_profile_migrations_and_edits_serialize_across_processes(tmp_path):
    from harness.native_hermes import profile_write_lock

    entered = tmp_path / 'entered'
    command = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from harness.native_hermes import profile_write_lock
path = Path(sys.argv[2])
print('ready', flush=True)
with profile_write_lock(path):
    (path / 'entered').touch()
"""
    with profile_write_lock(tmp_path):
        proc = subprocess.Popen([sys.executable, '-I', '-c', command, str(PROJECT), str(tmp_path)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            assert proc.stdout.readline().strip() == b'ready'
            try:
                proc.wait(timeout=.25)
            except subprocess.TimeoutExpired:
                pass
            assert not entered.exists()
        except BaseException:
            proc.kill()
            proc.wait()
            raise
    output, stderr = proc.communicate(timeout=15)
    assert proc.returncode == 0, stderr
    assert entered.is_file()


def test_private_skill_catalog_prunes_linked_directory_and_hard_link(tmp_path):
    skills = tmp_path / 'skills'
    local = skills / 'local-skill'
    outside = tmp_path / 'foreign'
    local.mkdir(parents=True)
    outside.mkdir()
    (local / 'SKILL.md').write_text('---\nname: local-skill\ndescription: Local\n---\n# 수율 스킬',
                                    encoding='utf-8')
    secret = outside / 'SKILL.md'
    secret.write_text('---\nname: external\ndescription: External\n---\nprivate', encoding='utf-8')
    if os.name == 'nt':
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(skills / 'junction'), str(outside)],
                       check=True, capture_output=True)
    else:
        (skills / 'junction').symlink_to(outside, target_is_directory=True)
    (skills / 'hardlink').mkdir()
    os.link(secret, skills / 'hardlink' / 'SKILL.md')
    worker = PROJECT / 'harness/skill_catalog_worker.py'
    result = subprocess.run([sys.executable, '-I', '-X', 'utf8', str(worker)], cwd=tmp_path,
                            env={**os.environ, 'HERMES_HOME': str(tmp_path)}, capture_output=True,
                            check=True, timeout=20)
    cards = json.loads(result.stdout)['skills']
    assert [(c['name'], c['title']) for c in cards] == [('local-skill', '수율 스킬')]

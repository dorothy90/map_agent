import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from harness.config import Settings
from harness.tools.registry import ToolRegistry


@pytest.mark.parametrize('profile_zone,explicit,expected', [
    (None, None, 'Asia/Seoul'), ('Europe/London', None, 'Europe/London'),
    ('Europe/London', 'Pacific/Honolulu', 'Pacific/Honolulu'),
])
def test_native_settings_and_profile_skill_snapshot(monkeypatch, tmp_path, profile_zone, explicit, expected):
    import harness.native_hermes as native
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    profile = native.prepare_profile('alice')
    config_path = profile / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    if profile_zone:
        config['timezone'] = profile_zone
        config_path.write_text(yaml.safe_dump(config))
    skill = profile / 'skills/yield-analysis/SKILL.md'
    skill.write_text(skill.read_text() + '\nCustomized profile instruction.\n')
    expected_content = skill.read_text()
    updates = []
    captured = {}
    class Runs:
        async def update_one(self, query, update):
            updates.append(update)
    class Stdin:
        def write(self, data):
            captured['startup'] = json.loads(data)
            snapshot = Path(captured['startup']['skills_path'])
            assert snapshot != profile / 'skills'
            skill.write_text('Changed while the run is active')
            assert (snapshot / 'yield-analysis/SKILL.md').read_text() == expected_content
        async def drain(self): pass
    class Stdout:
        async def readline(self):
            if getattr(self, 'sent', False):
                return b''
            self.sent = True
            return b'{"type":"error","message":"synthetic-stop"}\n'
    process = SimpleNamespace(stdin=Stdin(), stdout=Stdout(), returncode=0)
    async def spawn(*args, **kwargs):
        captured.update(kwargs)
        return process
    async def check(): pass
    monkeypatch.setattr(native.asyncio, 'create_subprocess_exec', spawn)
    settings = Settings(engine='hermes', temperature=.73, reasoning_effort='high', call_timeout=17, timezone=explicit)
    run = {'principal_id': 'alice', 'session_id': 's', 'run_id': 'r', 'epoch': 1, 'query': 'synthetic'}
    context = SimpleNamespace(settings=settings, run=run, store=SimpleNamespace(runs=Runs()),
                              remaining_seconds=lambda: 60, check=check)
    with pytest.raises(RuntimeError, match='synthetic-stop'):
        asyncio.run(native.run_native(context, ToolRegistry()))
    startup = captured['startup']
    assert (startup['temperature'], startup['reasoning_effort'], startup['call_timeout']) == (.73, 'high', 17)
    assert startup['no_yield_tools'] is False
    assert startup['allowed_tools'] == []
    from harness.tests.test_no_yield_tools import worker_module
    worker = worker_module()
    assert worker.enabled_toolsets(startup) == ['yield_backend', 'yield_evidence']
    assert worker.enabled_toolsets({**startup, 'no_yield_tools': True}) == [
        'yield_backend', 'yield_evidence', 'clarify']
    assert captured['env']['HERMES_TIMEZONE'] == expected
    assert captured['env']['HERMES_HOME'] == str(profile)
    recorded = next(update['$set'] for update in updates if 'skill_versions' in update.get('$set', {}))
    assert recorded['skill_versions']['yield-analysis'] == hashlib.sha256(expected_content.encode()).hexdigest()
    assert recorded['skill_snapshot']['yield-analysis']['resources']['SKILL.md']['content'] == expected_content
    assert not Path(startup['skills_path']).exists()


def test_timezone_env_alias(monkeypatch):
    monkeypatch.setenv('HERMES_TIMEZONE', 'Europe/London')
    monkeypatch.delenv('HARNESS_TIMEZONE', raising=False)
    assert Settings.from_env().timezone == 'Europe/London'
    monkeypatch.setenv('HARNESS_TIMEZONE', 'Asia/Seoul')
    assert Settings.from_env().timezone == 'Asia/Seoul'


@pytest.mark.parametrize('key,value', [('external_dirs', ['/some/skill/directory']), ('create_dir', '/some/skill/directory')])
def test_untracked_external_skill_roots_are_rejected(monkeypatch, tmp_path, key, value):
    import harness.native_hermes as native
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    profile = native.prepare_profile('alice')
    config_path = profile / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['skills'][key] = value
    config_path.write_text(yaml.safe_dump(config))
    context = SimpleNamespace(settings=Settings(engine='hermes'), run={'principal_id': 'alice'})
    with pytest.raises(ValueError, match='external.*skill|skill.*external'):
        asyncio.run(native.run_native(context, ToolRegistry()))


def test_native_usage_replaces_reservation_with_actual_usage(monkeypatch, tmp_path):
    import harness.native_hermes as native
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    updates = []
    class Runs:
        async def update_one(self, query, update): updates.append(update)
    class Stdin:
        def write(self, data): pass
        async def drain(self): pass
    class Stdout:
        def __init__(self):
            self.messages = iter([
                {'type': 'step', 'tokens': 1000, 'models': 1},
                {'type': 'step', 'tokens': 50, 'models': 1},
                {'type': 'result', 'result': {'completed': True, 'final_response': 'synthetic', 'total_tokens': 50, 'api_calls': 1}},
            ])
        async def readline(self): return (json.dumps(next(self.messages)) + '\n').encode()
    async def spawn(*args, **kwargs):
        return SimpleNamespace(stdin=Stdin(), stdout=Stdout(), returncode=0)
    async def noop(*args, **kwargs): pass
    monkeypatch.setattr(native.asyncio, 'create_subprocess_exec', spawn)
    context = SimpleNamespace(settings=Settings(engine='hermes'),
        run={'principal_id': 'alice', 'session_id': 's', 'run_id': 'r', 'epoch': 1, 'query': 'synthetic',
             'usage': {'tokens': 10, 'models': 2}},
        store=SimpleNamespace(runs=Runs(), event=noop), remaining_seconds=lambda: 60, check=noop)
    asyncio.run(native.run_native(context, ToolRegistry()))
    usage_updates = [u for u in updates if any('usage.tokens' in fields for fields in u.values())]
    assert [u.get('$set', {}).get('usage.tokens') for u in usage_updates] == [1010, 60, 60]
    assert all(u['$set']['usage.models'] == 3 for u in usage_updates)


def test_worker_uses_snapshot_for_skill_tools_and_prompt_imports(tmp_path):
    """Exercise real worker bootstrap/import order without making an LLM request."""
    import os
    import subprocess
    import sys
    import harness.native_hermes as native
    profile, snapshot = tmp_path / 'profile', tmp_path / 'snapshot'
    profile.mkdir()
    (profile / 'config.yaml').write_text('agent:\n  environment_probe: false\n')
    for root, content in [(profile / 'skills', 'PROFILE_ONLY'), (snapshot, 'SNAPSHOT_ONLY')]:
        skill = root / 'audit-skill'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: audit-skill\ndescription: Audit reference\n---\n' + content)
    startup = dict(model='synthetic', base_url='http://127.0.0.1:1/v1', api_key='synthetic',
        max_iterations=5, token_limit=120000, max_tokens=8192, run_budget_seconds=30,
        call_timeout=17, temperature=.73, reasoning_effort='high', skills_path=str(snapshot),
        session_id='synthetic', tools=[])
    script = tmp_path / 'probe.py'
    script.write_text('''import io, json, runpy, sys, types
worker, startup_json = sys.argv[1:]
startup = json.loads(startup_json)
class StopProbe(Exception): pass
class AIAgent:
    def __init__(self, **kwargs):
        import hermes_constants
        from tools import skills_tool
        from agent import skill_utils, prompt_builder
        from agent.skill_commands import build_auto_load_prompt
        evidence = dict(type='audit', roots=[str(getter()) for getter in
            (hermes_constants.get_skills_dir, skill_utils.get_skills_dir,
             prompt_builder.get_skills_dir, skills_tool._skills_dir)],
            skill=skills_tool.skill_view('audit-skill', task_id='view-test'),
            auto_load=build_auto_load_prompt(user_config={'skills': {'auto_load': ['audit-skill']}},
                task_id='auto-test', home_override=hermes_constants.get_hermes_home())[0],
            reasoning_config=kwargs['reasoning_config'], request_overrides=kwargs['request_overrides'])
        sys.__stdout__.write(json.dumps(evidence) + '\\n')
        raise StopProbe('probe completed')
sys.modules['run_agent'] = types.SimpleNamespace(AIAgent=AIAgent)
sys.stdin = io.StringIO(startup_json + '\\n')
runpy.run_path(worker, run_name='__main__')
''')
    result = subprocess.run([sys.executable, '-I', str(script),
        str(Path(native.__file__).with_name('native_worker.py')), json.dumps(startup)],
        cwd=tmp_path, env={**os.environ, 'HERMES_HOME': str(profile)}, text=True,
        capture_output=True, timeout=30, check=True)
    messages = [json.loads(line) for line in result.stdout.splitlines()]
    evidence = next((item for item in messages if item.get('type') == 'audit'), None)
    assert evidence is not None, messages
    assert evidence['roots'] == [str(snapshot)] * 4
    assert 'SNAPSHOT_ONLY' in evidence['skill'] and 'PROFILE_ONLY' not in evidence['skill']
    assert 'SNAPSHOT_ONLY' in evidence['auto_load'] and 'PROFILE_ONLY' not in evidence['auto_load']
    assert evidence['reasoning_config'] == {'enabled': True, 'effort': 'high'}
    assert evidence['request_overrides']['temperature'] == .73


@pytest.mark.parametrize('created_at', ['2026-10-20T23:30:00+00:00', 'invalid', None])
def test_request_context_is_identical_for_planner_verifier_and_saved_run(monkeypatch, tmp_path, created_at):
    from datetime import datetime, timezone
    import harness.native_hermes as native
    from harness.types import ToolObservation
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    observation = ToolObservation(principal_id='p', session_id='s', run_id='r', invocation_id='i',
        result_id='required-result', source_result_ids=['required-source'], tool_name='query',
        validated_arguments={'large': 'x' * 30_000}, status='success',
        preview_rows=[{'large': 'x' * 30_000}], total_rows=1).model_dump(mode='json')
    bridge = SimpleNamespace(observations=[observation], schemas=lambda: [], allowed=set())
    monkeypatch.setattr(native, 'NativeBridge', lambda *args: bridge)
    updates, sent = [], []
    class Runs:
        async def update_one(self, query, update): updates.append(update)
    class Stdin:
        def write(self, data): sent.append(json.loads(data))
        async def drain(self): pass
    class Stdout:
        def __init__(self):
            self.messages = iter([
                {'type': 'call', 'method': 'verification_start', 'call_id': 'call-rpc', 'review_id': 'review', 'candidate': 'synthetic', 'attempt': 1},
                {'type': 'error', 'message': 'synthetic-stop'},
            ])
        async def readline(self): return (json.dumps(next(self.messages)) + '\n').encode()
    async def spawn(*args, **kwargs): return SimpleNamespace(stdin=Stdin(), stdout=Stdout(), returncode=0)
    async def noop(*args, **kwargs): pass
    monkeypatch.setattr(native.asyncio, 'create_subprocess_exec', spawn)
    run = {'principal_id': 'alice', 'session_id': 's', 'run_id': 'r', 'epoch': 1, 'query': 'synthetic', 'created_at': created_at}
    context = SimpleNamespace(settings=Settings(engine='hermes', timezone='Asia/Seoul'), run=run,
        store=SimpleNamespace(runs=Runs(), event=noop), remaining_seconds=lambda: 60, check=noop)
    before = datetime.now(timezone.utc)
    with pytest.raises(RuntimeError, match='synthetic-stop'):
        asyncio.run(native.run_native(context, ToolRegistry()))
    contract = sent[0]['request_context']
    assert contract == sent[1]['result']['request_context'] == run['request_context']
    assert len(json.dumps(sent[1]['result']['evidence'], ensure_ascii=False)) <= 20_000
    assert sent[1]['result']['known_result_ids'] == ['required-result', 'required-source']
    assert any(update.get('$set', {}).get('request_context') == contract for update in updates)
    assert json.dumps(contract, ensure_ascii=False) in sent[0]['instructions']
    assert contract['timezone'] == 'Asia/Seoul'
    stamp = datetime.fromisoformat(contract['requested_at'])
    if created_at and created_at != 'invalid':
        assert stamp.isoformat() == '2026-10-21T08:30:00+09:00'
    else:
        assert before <= stamp <= datetime.now(timezone.utc)

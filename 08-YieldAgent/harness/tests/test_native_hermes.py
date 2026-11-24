import asyncio
import json
from types import SimpleNamespace

import pytest

from harness.tools.registry import ToolRegistry, ToolSpec
from harness.types import Contract, ToolObservation


def test_executor_completion_uses_history_tool_call_id_and_hermes_payload():
    from harness.executor import ToolExecutor

    events = []
    class Runs:
        async def update_one(self, *args, **kwargs):
            return None
    class Store:
        runs = Runs()
        async def event(self, run_id, kind, payload, **kwargs):
            events.append((kind, payload))

    observation = ToolObservation(principal_id='p', session_id='s', run_id='r',
        invocation_id='call-1', result_id='stored-result', tool_name='query_db',
        validated_arguments={'lot': 'L-1'}, status='error', summary='query failed',
        provenance={'elapsed_seconds': 1.25}, error={'code': 'QueryError'})
    executor = ToolExecutor(ToolRegistry(), SimpleNamespace(store=Store()))

    asyncio.run(executor.publish(observation, {'run_id': 'r', 'epoch': 1}))

    assert events == [('tool_finished', {
        'name': 'query_db', 'status': 'error', 'state': 'error',
        'invocation_id': 'call-1', 'parent_invocation_id': None,
        'elapsed_seconds': 1.25, 'result_id': 'stored-result',
        'summary': 'query failed', 'tool_id': 'call-1',
        'args': {'lot': 'L-1'}, 'result': {
            'result_id': 'stored-result', 'tool_name': 'query_db',
            'status': 'error', 'summary': 'query failed', 'error': {'code': 'QueryError'}},
        'duration_s': 1.25})]


def test_executor_auth_failure_completes_started_hermes_card():
    from harness.config import Settings
    from harness.executor import ExecutionContext, ToolAuthenticationError, ToolExecutor

    class Unauthorized(RuntimeError):
        status_code = 401
    async def fail(_args, _context):
        raise Unauthorized('private provider response')
    registry = ToolRegistry()
    registry.add(ToolSpec('query_db', 'query', Contract, fail))
    events = []
    run = {'_id': 'r', 'run_id': 'r', 'epoch': 1, 'principal_id': 'p', 'session_id': 's',
        'status': 'running', 'active': True, 'usage': {}}
    class Calls:
        async def find_one(self, *args, **kwargs): return None
        async def count_documents(self, *args, **kwargs): return 0
        async def update_one(self, *args, **kwargs): return None
    class Store:
        calls = Calls()
        async def fence(self, *args, **kwargs): return run
        async def claim_invocation(self, *args, **kwargs): return None
        async def reserve_tool_call(self, *args, **kwargs): return None
        async def event(self, run_id, kind, payload, **kwargs):
            events.append((kind, payload))
    executor = ToolExecutor(registry, ExecutionContext(Store(), Settings(), run))

    with pytest.raises(ToolAuthenticationError):
        asyncio.run(executor.execute({'id': 'history-call', 'name': 'query_db', 'args': {}}))

    assert [kind for kind, _ in events] == ['tool_started', 'tool_finished']
    assert [payload['tool_id'] for _, payload in events] == ['history-call', 'history-call']
    assert events[-1][1]['result'] == {
        'tool_name': 'query_db', 'status': 'error',
        'summary': 'Tool authentication failed',
        'error': {'code': 'authentication_error', 'status_code': 401}}


def test_native_result_does_not_turn_failed_or_interrupted_into_completed():
    from harness.native_hermes import result_status
    assert result_status({'completed': True}) == 'completed'
    for flag in ('failed', 'partial', 'interrupted'):
        assert result_status({'completed': True, flag: True}) != 'completed'
    assert result_status({'completed': False}) == 'partial'
    assert result_status({'error': 'compression deferred', 'partial': True, 'failed': False}) == 'partial'


def test_native_history_preserves_legacy_and_interrupted_turns():
    from harness.native_hermes import native_history
    legacy = {'run_id': 'r', 'query': '4SS 수율', 'answer': '확인한 수율은 90%입니다.'}
    history = native_history([legacy])
    assert history == [{'role': 'user', 'content': legacy['query']},
        {'role': 'assistant', 'content': legacy['answer']}]
    prior = {**legacy, 'native_messages': history}
    cancelled = {'query': '9월로', 'goal_request': '4SS 수율\n9월로', 'answer': '중지했습니다.'}
    assert native_history([cancelled, prior])[-2]['content'] == cancelled['goal_request']


def test_stock_worker_resumes_only_requested_session_from_hermes_db(monkeypatch, tmp_path):
    from harness import hermes_chat_worker

    monkeypatch.syspath_prepend(str(hermes_chat_worker.HERE / 'engine'))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    from hermes_state import SessionDB

    path = tmp_path / 'state.db'
    db = SessionDB(path)
    try:
        for session in ('current', 'other'):
            db.create_session(session, source='yield_web')
        db.append_message('other', 'user', 'another conversation')
        assert asyncio.run(hermes_chat_worker.resume_session(db, None, {'session_id': 'current'})) == ('current', [])
        db.append_message('current', 'user', 'first request')
        db.append_message('current', 'assistant', 'first answer')
    finally:
        db.close()

    db = SessionDB(path)
    try:
        session, messages = asyncio.run(hermes_chat_worker.resume_session(db, None, {'session_id': 'current'}))
        assert session == 'current'
        assert [(m['role'], m['content']) for m in messages] == [
            ('user', 'first request'), ('assistant', 'first answer')]
    finally:
        db.close()


def test_bundled_source_matches_unmodified_upstream():
    import hashlib
    import json
    from pathlib import Path
    import harness.native_hermes as native
    source = Path(native.__file__).with_name('engine')
    manifest = json.loads((source / 'UPSTREAM.json').read_text())
    assert manifest['commit'] == native.UPSTREAM_COMMIT
    assert 'MIT License' in (source / 'LICENSE').read_text()
    assert not manifest['local_files'] and not manifest['adaptations']
    assert not (source / 'PATCHES.diff').read_bytes()
    for path, upstream_hash in manifest['files'].items():
        assert hashlib.sha256((source / path).read_bytes()).hexdigest() == upstream_hash, path


def test_embedded_profile_disables_host_package_probe_without_losing_settings(monkeypatch, tmp_path):
    import yaml
    import harness.native_hermes as native
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    profile = native.prepare_profile('p')
    config = profile / 'config.yaml'
    assert yaml.safe_load(config.read_text())['agent']['environment_probe'] is False
    assert yaml.safe_load(config.read_text())['delegation']['orchestrator_enabled'] is False
    config.write_text('skills:\n  auto_load: [custom-skill]\nagent:\n  environment_probe: true\n  stall_guards: false\ndelegation:\n  orchestrator_enabled: true\n  max_spawn_depth: 1\n')
    assert native.prepare_profile('p') == profile
    saved = yaml.safe_load(config.read_text())
    assert saved['skills']['auto_load'] == ['custom-skill']
    assert saved['agent'] == {'environment_probe': False, 'stall_guards': False}
    assert saved['delegation'] == {'orchestrator_enabled': False, 'max_spawn_depth': 1}
    assert 'model_upgrade_enabled' not in saved.get('auxiliary', {}).get('title_generation', {})


@pytest.mark.parametrize('custom', [False, True])
def test_stock_profile_migrates_only_harness_managed_ssh_terminal(monkeypatch, tmp_path, custom):
    import yaml
    import harness.native_hermes as native

    monkeypatch.setattr(native, 'ROOT', tmp_path)
    profile = native.prepare_profile('alice')
    config = profile / 'config.yaml'
    content = yaml.safe_load(config.read_text())
    content['terminal'] = {
        'backend': 'ssh', 'cwd': '/old/runs/test', 'ssh_host': 'localhost',
        'ssh_user': 'yieldrun', 'ssh_port': 22, 'ssh_key': '/old/key',
        'env_passthrough': [], 'persistent_shell': False,
    }
    if custom:
        content['terminal']['timeout'] = 150
    content['timezone'] = 'Asia/Seoul'
    config.write_text(yaml.safe_dump(content))
    native.prepare_profile('alice')
    saved = yaml.safe_load(config.read_text())
    if custom:
        assert saved['terminal'] == content['terminal']
    else:
        assert 'terminal' not in saved
    assert saved['timezone'] == 'Asia/Seoul'


@pytest.mark.parametrize('managed', [True, False])
def test_profile_migrates_only_managed_title_disable_once(monkeypatch, tmp_path, managed):
    import yaml
    import harness.native_hermes as native
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    profile = tmp_path / '.runtime/hermes/profiles' / native.principal_hash('p')
    profile.mkdir(parents=True)
    config = profile / 'config.yaml'
    config.write_text(yaml.safe_dump({
        'agent': {'environment_probe': not managed},
        'delegation': {'orchestrator_enabled': False},
        'auxiliary': {'title_generation': {'model_upgrade_enabled': False, 'language': 'ko'}},
        'tools': {'tool_search': {'enabled': 'off', 'defer': [], 'listing': 'off'}},
    }))
    native.prepare_profile('p')
    migrated = yaml.safe_load(config.read_text())
    expected = {'language': 'ko'} if managed else {'language': 'ko', 'model_upgrade_enabled': False}
    assert migrated['auxiliary']['title_generation'] == expected
    original_search = {'enabled': 'off', 'defer': [], 'listing': 'off'}
    assert migrated['tools']['tool_search'] == ({'listing': 'off'} if managed else original_search)
    migrated['tools']['tool_search'] = original_search
    migrated['auxiliary']['title_generation']['model_upgrade_enabled'] = False
    config.write_text(yaml.safe_dump(migrated))
    native.prepare_profile('p')
    assert yaml.safe_load(config.read_text()) == migrated

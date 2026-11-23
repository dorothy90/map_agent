import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from harness.tools.registry import ToolRegistry, ToolSpec
from harness.types import Contract, ToolObservation


def test_verification_evidence_is_compact_complete_and_does_not_mutate_inputs():
    import harness.native_hermes as native
    from harness.types import TableRef
    long = 'x' * 1000
    observations = [ToolObservation(principal_id='p', session_id='s', run_id='r',
        invocation_id='i', result_id='multi', source_result_ids=['source'], tool_name='query',
        validated_arguments={'nested': {'value': long}}, status='success', summary=long,
        tables=[
            TableRef(table_id='a', title='A', columns=['one', 'two'], total_rows=3,
                preview_rows=[{'one': i, 'two': long} for i in range(3)], complete=True, data_ref='a'),
            TableRef(table_id='b', title='B', columns=['two', 'three'], total_rows=4,
                preview_rows=[{'two': {'nested': long}, 'three': i} for i in range(4)], complete=True, data_ref='b'),
        ])]
    observations += [ToolObservation(principal_id='p', session_id='s', run_id='r',
        invocation_id=f'i-{i}', result_id=f'wide-{i}', source_result_ids=[f'lineage-{i}'],
        tool_name='query', validated_arguments={'value': long}, status='success', summary=long,
        columns=[f'column-{j}' for j in range(80)],
        preview_rows=[{f'column-{j}': long for j in range(80)} for _ in range(10)], total_rows=10)
        for i in range(20)]
    observations += [ToolObservation(principal_id='p', session_id='s', run_id='r',
        invocation_id=f'm-{i}', result_id=f'metadata-{i}-' + 'r' * 100,
        tool_name='query', status='empty') for i in range(300)]
    required_id = 'required-' + 'z' * 200
    observations.append(ToolObservation(principal_id='p', session_id='s', run_id='r',
        invocation_id='required', result_id=required_id, source_result_ids=['required-source'],
        tool_name='query', status='empty'))
    original = copy.deepcopy(observations)

    sample_evidence, _ = native._verification_evidence(observations[:1])
    evidence, known = native._verification_evidence(observations)
    repeated = native._verification_evidence(observations)

    assert len(json.dumps(evidence, ensure_ascii=False)) <= 20_000
    assert repeated == (evidence, known)
    assert set(known) == ({obs.result_id for obs in observations}
        | {source for obs in observations for source in obs.source_result_ids})
    assert observations == original
    multi = sample_evidence[0]
    assert multi['total_rows'] == 7 and multi['column_count'] == 3
    assert len(multi['preview_rows']) == 5
    assert [row['table_id'] for row in multi['preview_rows']] == ['a', 'a', 'a', 'b', 'b']
    assert len(multi['summary']) <= 240 and len(multi['args_summary']) <= 400
    assert all(len(value) <= 160 for row in multi['preview_rows']
        for value in row.values() if isinstance(value, str))
    assert required_id in known and 'required-source' in known
    assert not any(item['result_id'] == required_id for item in evidence)
    assert evidence and all('summary' not in item and 'args_summary' not in item for item in evidence)


def test_verification_evidence_drops_samples_before_reference_metadata():
    import harness.native_hermes as native
    observations = [ToolObservation(principal_id='p', session_id='s', run_id='r',
        invocation_id=f'i-{i}', result_id=f'result-{i}', tool_name='query',
        validated_arguments={'index': i}, status='success', summary='kept summary',
        columns=['value'], preview_rows=[{'value': 'x' * 1000} for _ in range(5)], total_rows=5)
        for i in range(30)]

    evidence, known = native._verification_evidence(observations)

    assert len(evidence) == len(observations)
    assert all('summary' in item and 'args_summary' in item for item in evidence)
    assert sum(len(item['preview_rows']) for item in evidence) < 30 * 5
    assert known == sorted(f'result-{i}' for i in range(30))


def test_verification_evidence_handles_empty_and_error_results():
    import harness.native_hermes as native
    assert native._verification_evidence([]) == ([], [])
    error = ToolObservation(principal_id='p', session_id='s', run_id='r', invocation_id='i',
        result_id='error', tool_name='query', status='error', preview_rows=[{'secret': 'row'}],
        total_rows=1, error={'message': 'not copied'})

    evidence, known = native._verification_evidence([error])

    assert known == ['error']
    assert evidence[0]['preview_rows'] == []
    assert 'error' not in evidence[0]

    oversized = ToolObservation(principal_id='p', session_id='s', run_id='r', invocation_id='i',
        result_id='oversized', tool_name='query', status='success',
        preview_rows=[{'value': 10 ** 500}], total_rows=1)
    evidence, _ = native._verification_evidence([oversized])
    assert isinstance(evidence[0]['preview_rows'][0]['value'], str)
    assert len(evidence[0]['preview_rows'][0]['value']) <= 160


def test_native_bridge_exposes_only_allowed_backend_tools():
    from harness.native_hermes import NativeBridge
    registry = ToolRegistry()
    for name, effect in [('read_result', 'read'), ('render_yield_table', 'artifact'), ('write', 'persistent_write')]:
        registry.add(ToolSpec(name, name, Contract, lambda *a: None, effect=effect))
    bridge = NativeBridge(registry, None)
    assert {x['function']['name'] for x in bridge.schemas()} == {'read_result', 'render_yield_table'}
    with pytest.raises(PermissionError):
        asyncio.run(bridge.invoke({'name': 'write', 'args': {}, 'id': 'call'}))


def test_native_bridge_uses_existing_executor_and_preserves_result_ids(monkeypatch):
    import harness.native_hermes as native
    observed = ToolObservation(principal_id='p', session_id='s', run_id='r',
        invocation_id='i', result_id='stored', tool_name='read', status='success',
        preview_rows=[{'value': 7}], total_rows=1)
    calls = []
    class Executor:
        def __init__(self, registry, context): pass
        async def execute(self, call):
            calls.append(call)
            return observed
    monkeypatch.setattr(native, 'ToolExecutor', Executor)
    registry = ToolRegistry()
    registry.add(ToolSpec('read_result', 'read', Contract, lambda *a: None))
    bridge = native.NativeBridge(registry, None)
    result = asyncio.run(bridge.invoke({'name': 'read_result', 'args': {}, 'id': 'native-id'}))
    assert result['result_id'] == 'stored'
    assert calls[0]['id'] == 'native-id'
    assert bridge.observations[0]['result_id'] == 'stored'


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


@pytest.mark.parametrize('kind', ['models', 'tokens'])
def test_recovered_native_run_cannot_exceed_consumed_budget(monkeypatch, kind):
    import harness.native_hermes as native
    from harness.config import Settings
    settings = Settings()
    limit = settings.model_limit if kind == 'models' else settings.token_limit
    async def forbidden(*args, **kwargs):
        pytest.fail('An exhausted run must not launch another engine')
    monkeypatch.setattr(native.asyncio, 'create_subprocess_exec', forbidden)
    context = SimpleNamespace(settings=settings, run={'usage': {kind: limit}})
    result = asyncio.run(native.run_native(context, ToolRegistry()))
    assert result['status'] == 'partial'
    assert result['stop_reason'] == kind + '_limit'


def test_native_controller_pins_engine_and_restores_history(monkeypatch):
    import uuid
    from harness.config import Settings
    from harness.control import RunController
    from harness.store import HarnessStore
    import harness.native_hermes as native

    async def scenario():
        store = HarnessStore(database='harness_test_' + uuid.uuid4().hex)
        controller = RunController(store, Settings(engine='hermes'))
        calls = []
        async def synthetic(context, *, history):
            calls.append(history)
            messages = history + [{'role': 'user', 'content': context.run['query']},
                {'role': 'assistant', 'content': 'synthetic answer'}]
            return {'status': 'completed', 'stop_reason': 'completed', 'answer': 'synthetic answer',
                'native_messages': messages, 'native_usage': {'total_tokens': 12, 'api_calls': 1},
                'native_engine': {'commit': native.UPSTREAM_COMMIT}, 'observations': [], 'result_ids': []}
        monkeypatch.setattr('harness.hermes_chat.run_chat', synthetic)
        try:
            await store.setup()
            first = await controller.start('test', 's', 'r1', 'synthetic one')
            await controller.tasks[first['run_id']]
            saved = await store.get_run('test', first['run_id'])
            assert saved['status'] == 'completed'
            assert saved['engine'] == 'hermes'
            assert saved['native_engine']['commit'] == native.UPSTREAM_COMMIT
            second = await controller.start('test', 's', 'r2', 'synthetic two')
            await controller.tasks[second['run_id']]
            assert calls == [[], saved['native_messages']]
        finally:
            await controller.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


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


@pytest.mark.parametrize('requested,expected', [
    (None, {'query_db', 'read_result'}),
    ([], {'read_result'}),
    (['extra'], {'extra', 'read_result'}),
])
def test_native_bridge_defaults_to_harness_capabilities(requested, expected):
    from harness.native_hermes import NativeBridge
    registry = ToolRegistry()
    for name in ('query_db', 'read_result', 'extra', 'execute_code', 'terminal',
                 'read_file', 'search_files', 'write_file', 'patch', 'delegate_task'):
        registry.add(ToolSpec(name, name, Contract, lambda *a: None))
    context = SimpleNamespace(settings=SimpleNamespace(native_allowed_tools=requested))
    bridge = NativeBridge(registry, context)
    assert bridge.allowed == expected
    assert {tool['function']['name'] for tool in bridge.schemas()} == expected


def test_native_bridge_rejects_unknown_explicit_capability():
    from harness.native_hermes import NativeBridge
    context = SimpleNamespace(settings=SimpleNamespace(native_allowed_tools=['missing']))
    with pytest.raises(ValueError, match='unknown tools: missing'):
        NativeBridge(ToolRegistry(), context)

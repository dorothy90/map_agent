import asyncio
from types import SimpleNamespace

import pytest

from harness.tools.registry import ToolRegistry, ToolSpec
from harness.types import Contract, ToolObservation


def test_native_bridge_exposes_only_allowed_backend_tools():
    from harness.native_hermes import NativeBridge
    registry = ToolRegistry()
    for name, effect in [('read', 'read'), ('plot', 'artifact'), ('write', 'persistent_write')]:
        registry.add(ToolSpec(name, name, Contract, lambda *a: None, effect=effect))
    bridge = NativeBridge(registry, None)
    assert {x['function']['name'] for x in bridge.schemas()} == {'read', 'plot'}
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
    registry.add(ToolSpec('read', 'read', Contract, lambda *a: None))
    bridge = native.NativeBridge(registry, None)
    result = asyncio.run(bridge.invoke({'name': 'read', 'args': {}, 'id': 'native-id'}))
    assert result['result_id'] == 'stored'
    assert calls[0]['id'] == 'native-id'
    assert bridge.observations[0]['result_id'] == 'stored'


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
    from langgraph.checkpoint.memory import InMemorySaver
    from harness.config import Settings
    from harness.control import RunController
    from harness.store import HarnessStore
    import harness.native_hermes as native

    async def scenario():
        store = HarnessStore(database='harness_test_' + uuid.uuid4().hex)
        controller = RunController(store, Settings(engine='hermes'), InMemorySaver())
        calls = []
        async def synthetic(context, registry, *, history):
            calls.append(history)
            messages = history + [{'role': 'user', 'content': context.run['query']},
                {'role': 'assistant', 'content': 'synthetic answer'}]
            return {'status': 'completed', 'stop_reason': 'completed', 'answer': 'synthetic answer',
                'native_messages': messages, 'native_usage': {'total_tokens': 12, 'api_calls': 1},
                'native_engine': {'commit': native.UPSTREAM_COMMIT}, 'observations': [], 'result_ids': []}
        monkeypatch.setattr(native, 'run_native', synthetic)
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


def test_bundled_source_matches_recorded_upstream_and_local_patches():
    import hashlib
    import json
    from pathlib import Path
    import harness.native_hermes as native
    source = Path(native.__file__).with_name('engine')
    manifest = json.loads((source / 'UPSTREAM.json').read_text())
    assert manifest['commit'] == native.UPSTREAM_COMMIT
    assert 'MIT License' in (source / 'LICENSE').read_text()
    for path, upstream_hash in manifest['files'].items():
        expected = manifest['local_files'].get(path, upstream_hash)
        assert hashlib.sha256((source / path).read_bytes()).hexdigest() == expected, path
    patch = (source / 'PATCHES.diff').read_text()
    assert all('+++ b/' + path in patch for path in manifest['local_files'])


def test_embedded_profile_disables_host_package_probe_without_losing_settings(monkeypatch, tmp_path):
    import yaml
    import harness.native_hermes as native
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    profile = native.prepare_profile('p')
    config = profile / 'config.yaml'
    assert yaml.safe_load(config.read_text())['agent']['environment_probe'] is False
    config.write_text('skills:\n  auto_load: [custom-skill]\nagent:\n  environment_probe: true\n  stall_guards: false\n')
    assert native.prepare_profile('p') == profile
    saved = yaml.safe_load(config.read_text())
    assert saved['skills']['auto_load'] == ['custom-skill']
    assert saved['agent'] == {'environment_probe': False, 'stall_guards': False}

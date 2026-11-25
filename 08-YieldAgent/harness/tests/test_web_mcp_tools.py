"""Employee MCP tools exercised through real stdio discovery and registry dispatch."""
import builtins
import io
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import psutil
import pytest
import yaml


@pytest.fixture
def mcp_chat(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'engine'))
    monkeypatch.setenv('HERMES_DISABLE_LAZY_INSTALLS', '1')
    home = tmp_path / 'Alice'
    home.mkdir()
    # Exercise direct Hermes schemas; the default deferred bridge is tested separately.
    (home / 'config.yaml').write_text('tools:\n  tool_search:\n    enabled: "off"\n')
    monkeypatch.setenv('HERMES_HOME', str(home))
    from hermes_cli.plugins_discovery import suppress_plugin_discovery
    with suppress_plugin_discovery():
        import model_tools
    from tools.registry import registry
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS
    from harness.web_connections import install_web_connections
    from harness.web_mcp_tools import attach_employee_mcp_toolsets, shutdown_employee_mcp_tools

    previous = registry.snapshot_registration('manage_connections')
    monkeypatch.setitem(INLINE_TOOL_EXECUTORS, 'manage_connections', INLINE_TOOL_EXECUTORS['manage_connections'])
    install_web_connections(registry)
    installed = registry.snapshot_registration('manage_connections')
    script = tmp_path / 'server.py'
    pids = tmp_path / 'server.pids'
    script.write_text('''import os
import sys
from mcp.server.fastmcp import FastMCP
with open(sys.argv[1], 'a') as out:
    out.write(str(os.getpid()) + '\\n')
server = FastMCP('employee-test')
@server.tool()
def ping() -> str:
    return 'pong:unique-employee-token'
server.run(transport='stdio')
''')

    def manage(action, config=None):
        args = dict(action=action)
        if action != 'status':
            args['connectors'] = [dict(name='employee_test', mcp=True)]
            if config is not None:
                args['connectors'][0]['config'] = config
        return json.loads(registry.dispatch('manage_connections', args))

    def add(**overrides):
        config = dict(command=sys.executable, args=[str(script), str(pids)])
        config.update(overrides)
        assert manage('add', config)['ok']

    def definitions(toolsets):
        return {item['function']['name'] for item in model_tools.get_tool_definitions(
            enabled_toolsets=toolsets, quiet_mode=True)}

    def assert_reaped():
        children = [int(pid) for pid in pids.read_text().splitlines()] if pids.exists() else []
        deadline = time.monotonic() + 5
        while any(psutil.pid_exists(pid) for pid in children) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not [pid for pid in children if psutil.pid_exists(pid)], 'MCP child leaked'

    try:
        yield SimpleNamespace(add=add, manage=manage, attach=attach_employee_mcp_toolsets,
                              shutdown=shutdown_employee_mcp_tools, definitions=definitions,
                              registry=registry, home=home, pids=pids, assert_reaped=assert_reaped)
    finally:
        shutdown_employee_mcp_tools()
        registry.restore_registration('manage_connections', installed, previous)
        assert_reaped()


TOOL = 'mcp__employee_test__ping'


def test_enabled_tool_is_visible_and_callable(mcp_chat):
    m = mcp_chat
    m.add()
    toolsets = ['web_connections']
    assert m.attach(toolsets) == ['mcp-employee_test']
    assert m.attach(toolsets) == []
    assert toolsets.count('mcp-employee_test') == 1
    assert TOOL in m.definitions(toolsets)
    assert 'pong:unique-employee-token' in m.registry.dispatch(TOOL, {})


def test_disabled_server_is_not_loaded(mcp_chat):
    m = mcp_chat
    m.add(enabled=False)
    toolsets = ['web_connections']
    assert m.attach(toolsets) == []
    assert TOOL not in m.definitions(toolsets)
    assert not m.pids.exists()


def test_default_tool_search_exposes_and_dispatches_mcp(mcp_chat):
    m = mcp_chat
    m.add()
    config_path = m.home / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['tools'].pop('tool_search')
    config_path.write_text(yaml.safe_dump(config))
    toolsets = ['web_connections']
    m.attach(toolsets)
    import model_tools
    from tools.tool_search import dispatch_tool_describe, dispatch_tool_search, resolve_underlying_call
    definitions = model_tools.get_tool_definitions(enabled_toolsets=toolsets, quiet_mode=True)
    search_schema = next(td['function'] for td in definitions if td['function']['name'] == 'tool_search')
    assert TOOL in search_schema['description']
    # AIAgent retains these pre-assembly definitions as its session's bridge scope.
    catalog = model_tools.get_tool_definitions(enabled_toolsets=toolsets, quiet_mode=True,
                                             skip_tool_search_assembly=True)
    search = dispatch_tool_search({'queries': ['employee_test ping']}, current_tool_defs=catalog)
    assert TOOL in search
    described = json.loads(dispatch_tool_describe({'names': [TOOL]}, current_tool_defs=catalog))
    assert TOOL in described['tools']
    name, args, error = resolve_underlying_call({'name': TOOL, 'arguments': {}},
                                              session_tool_names=[td['function']['name'] for td in catalog])
    assert error is None
    assert 'pong:unique-employee-token' in m.registry.dispatch(name, args)


def test_broken_server_keeps_chat_usable(mcp_chat):
    m = mcp_chat
    m.add(command='/nonexistent/employee-mcp-server')
    toolsets = ['web_connections']
    assert m.attach(toolsets) == []
    assert TOOL not in m.definitions(toolsets)
    assert 'manage_connections' in m.definitions(toolsets)
    assert m.manage('status')['ok']


def test_scheduled_run_does_not_load_server(mcp_chat):
    m = mcp_chat
    m.add()
    toolsets = ['web_connections']
    assert m.attach(toolsets, scheduled=True) == []
    assert TOOL not in m.definitions(toolsets)
    assert not m.pids.exists()


def test_employee_profiles_are_isolated(mcp_chat, tmp_path, monkeypatch):
    m = mcp_chat
    m.add()
    bob = tmp_path / 'Bob'
    bob.mkdir()
    # An unreadable-as-YAML second profile must not affect Alice's discovery.
    bob_config = bob / 'config.yaml'
    bob_config.write_text('mcp_servers: [invalid')
    before = bob_config.read_bytes()
    alice_sets = ['web_connections']
    config_reads = set()

    def track_config_reads(open_file):
        def read(file, mode='r', *args, **kwargs):
            if isinstance(file, (str, os.PathLike)) and 'r' in mode:
                path = Path(file)
                if path.name == 'config.yaml':
                    config_reads.add(path.resolve())
            return open_file(file, mode, *args, **kwargs)
        return read

    with monkeypatch.context() as reads:
        reads.setattr(builtins, 'open', track_config_reads(builtins.open))
        reads.setattr(io, 'open', track_config_reads(io.open))
        m.attach(alice_sets)
    assert config_reads == {(m.home / 'config.yaml').resolve()}
    assert TOOL in m.definitions(alice_sets)
    assert bob_config.read_bytes() == before
    m.shutdown()
    m.assert_reaped()
    bob_config.write_text('mcp_servers: {}\n')
    monkeypatch.setenv('HERMES_HOME', str(bob))
    bob_sets = ['web_connections']
    assert m.attach(bob_sets) == []
    assert TOOL not in m.definitions(bob_sets + ['mcp-employee_test'])


def test_shutdown_reaps_real_stdio_child(mcp_chat):
    m = mcp_chat
    m.add()
    m.attach(['web_connections'])
    assert m.pids.exists()
    assert psutil.pid_exists(int(m.pids.read_text().splitlines()[-1]))
    m.shutdown()
    m.assert_reaped()


def test_configuration_changes_apply_next_turn(mcp_chat):
    m = mcp_chat
    m.add()
    current = ['web_connections']
    m.attach(current)
    assert TOOL in m.definitions(current)
    assert m.manage('remove')['ok']
    assert TOOL in m.definitions(current)
    assert 'pong:unique-employee-token' in m.registry.dispatch(TOOL, {})
    m.shutdown()
    following = ['web_connections']
    assert m.attach(following) == []
    assert TOOL not in m.definitions(following + ['mcp-employee_test'])
    m.add()
    assert TOOL not in m.definitions(following)
    m.shutdown()
    next_turn = ['web_connections']
    m.attach(next_turn)
    assert TOOL in m.definitions(next_turn)
    assert 'pong:unique-employee-token' in m.registry.dispatch(TOOL, {})


def test_symlink_config_is_rejected(mcp_chat, tmp_path):
    m = mcp_chat
    m.add()
    config = m.home / 'config.yaml'
    target = tmp_path / 'other-config.yaml'
    config.rename(target)
    before = target.read_bytes()
    config.symlink_to(target)
    toolsets = ['web_connections']
    assert m.attach(toolsets) == []
    assert TOOL not in m.definitions(toolsets)
    assert not m.pids.exists()
    assert target.read_bytes() == before


@pytest.mark.parametrize('unsafe', ['hardlink', 'home_symlink', 'relative_home', 'missing_home', 'invalid_yaml'])
def test_unsafe_or_invalid_profile_loads_nothing(mcp_chat, tmp_path, monkeypatch, unsafe):
    m = mcp_chat
    m.add()
    config = m.home / 'config.yaml'
    if unsafe == 'hardlink':
        os.link(config, tmp_path / 'linked-config.yaml')
    elif unsafe == 'home_symlink':
        alias = tmp_path / 'alias'
        alias.symlink_to(m.home, target_is_directory=True)
        monkeypatch.setenv('HERMES_HOME', str(alias))
    elif unsafe == 'relative_home':
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv('HERMES_HOME', 'Alice')
    elif unsafe == 'missing_home':
        monkeypatch.delenv('HERMES_HOME')
    else:
        config.write_text('mcp_servers: [invalid')
    before = config.read_bytes()
    toolsets = ['web_connections']
    assert m.attach(toolsets) == []
    assert toolsets == ['web_connections']
    assert not m.pids.exists()
    assert config.read_bytes() == before


def test_mcp_dict_pin_refreshes_schema():
    from harness.hermes_chat_worker import refresh_web_tool_pin
    old = {'type': 'function', 'function': {'name': TOOL, 'description': 'old'}}
    new = {'type': 'function', 'function': {'name': TOOL, 'description': 'new',
           'parameters': {'type': 'object', 'properties': {'text': {'type': 'string'}}}}}
    updates = []
    db = SimpleNamespace(update_session_tool_names=lambda *args: updates.append(args))
    refresh_web_tool_pin(db, 'session', {'tool_names': json.dumps({'tools': [old]})}, [new], set())
    assert updates == [('session', {'tools': [new]})]


def test_history_preserves_only_public_mcp_results():
    from harness.tests.test_gateway_pr7b_unit import MemoryServices, call, client_for
    services = MemoryServices()
    receipt = {'local_mcp': True, 'ok': True, 'servers': []}
    args = {'text': 'hello'}
    cases = [
        ('manage_connections', {}, json.dumps(receipt)),
        ('manage_connections', {}, None),
        (TOOL, args, 'echo:hello'),
        ('tool_call', {'calls': [{'name': TOOL, 'arguments': args}]}, 'echo:hello'),
        ('tool_call', {'name': TOOL, 'arguments': args}, 'echo:hello'),
        ('query_db', {}, 'PRIVATE'), ('read_result', {}, 'PRIVATE'),
        ('render_yield_table', {}, 'PRIVATE'),
        ('manage_connections', {}, '{"local_mcp": false, "secret": "PRIVATE"}'),
    ]
    with client_for(services) as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        sid = call(ws, 'session.create')['result']['session_id']
        native, events = [], []
        for index, (name, arguments, content) in enumerate(cases):
            cid = str(index)
            native.extend([
                {'role': 'assistant', 'tool_calls': [{'id': cid, 'function': {
                    'name': name, 'arguments': json.dumps(arguments)}}]},
                {'role': 'tool', 'tool_call_id': cid, 'content': content}])
            events.append({'type': 'tool_finished', 'payload': {'tool_id': cid, 'name': name,
                'result': receipt if index < 2 else {'status': 'success'}}})
        services.runs.append({'principal_id': 'alice', 'session_id': sid, 'run_id': 'run',
            'created_at': 1, 'query': 'question', 'active': False, 'status': 'completed',
            'native_messages': native, 'events': events})
        rows = client.get(f'/gateway-m1/api/sessions/{sid}/messages').json()['messages']
        rows = [row for row in rows if row['role'] == 'tool']
        for row in rows[:2]:
            assert json.loads(row['content']) == receipt
        for row in rows[2:5]:
            assert row['name'] == TOOL
            assert row['args'] == args
            assert row['content'] == 'echo:hello'
            assert row['context'] == '{"text":"hello"}'
        assert 'PRIVATE' not in json.dumps(rows)


@pytest.mark.parametrize('name,args,expected', [
    ('manage_connections', {}, True), (TOOL, {}, True),
    ('tool_call', {'calls': [{'name': TOOL, 'arguments': {}}]}, True),
    ('tool_call', {'name': TOOL, 'arguments': {}}, True),
    ('tool_call', {'calls': [{'name': 'query_db'}]}, False),
    ('query_db', {}, False), ('execute_code', {}, False), ('clarify', {}, False),
    ('skills_list', {}, False), ('kanban_create', {}, False),
])
def test_worker_emits_only_mcp_display_tools(name, args, expected):
    from harness.hermes_chat_worker import is_mcp_display_tool
    assert is_mcp_display_tool(name, args) is expected


@pytest.mark.parametrize('completed', [True, False])
def test_chat_projects_mcp_ipc_into_live_events(monkeypatch, tmp_path, completed):
    import asyncio
    from unittest.mock import AsyncMock, Mock
    from harness import hermes_chat
    from harness.config import Settings
    from harness.gateway.events import project_run_event

    (tmp_path / 'config.yaml').write_text('{}')
    monkeypatch.setattr(hermes_chat, 'ROOT', tmp_path)
    monkeypatch.setattr(hermes_chat, 'prepare_profile', lambda principal: tmp_path)
    monkeypatch.setattr(hermes_chat, 'cancel_run_prompts', AsyncMock())
    process = SimpleNamespace(stdin=SimpleNamespace(write=Mock(), drain=AsyncMock(), close=Mock(),
                              is_closing=lambda: True), stdout=None, wait=AsyncMock(return_value=0), returncode=0)
    monkeypatch.setattr(hermes_chat.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    receipt = {'local_mcp': True, 'ok': True, 'servers': []}
    calls = [('connections', 'manage_connections', json.dumps(receipt)),
             ('echo', TOOL, 'echo:hello'), ('error', TOOL, '{"isError":true}'),
             ('large', TOOL, '한' * 65536),
             ('large-receipt', 'manage_connections', {'data': '😀' * 65536, 'local_mcp': True})]

    async def messages(*unused):
        for cid, name, result in calls:
            yield {'type': 'tool_start', 'invocation_id': cid, 'name': name, 'args': {'text': 'hello'}}
            yield {'type': 'tool_complete', 'invocation_id': cid, 'name': name,
                   'args': {'text': 'hello'}, 'result': result}
        if completed:
            yield {'type': 'result', 'result': {'status': 'completed'}}

    monkeypatch.setattr(hermes_chat, 'coalesced_worker_messages', messages)
    store = SimpleNamespace(db=SimpleNamespace(name='test', gateway_sessions=SimpleNamespace(find_one=AsyncMock(return_value=None))),
                            runs=SimpleNamespace(update_one=AsyncMock(), find_one=AsyncMock(return_value={})), event=AsyncMock())
    run = dict(run_id='run', epoch=1, principal_id='alice', session_id='session', created_at='2026-01-01T00:00:00+00:00')
    context = SimpleNamespace(settings=Settings(api_key='test', model='test'), run=run, store=store,
                              check=AsyncMock(), remaining_seconds=lambda: 30)
    if completed:
        assert asyncio.run(hermes_chat.run_chat(context))['status'] == 'completed'
    else:
        with pytest.raises(RuntimeError, match='without a completed response'):
            asyncio.run(hermes_chat.run_chat(context))
    recorded = store.event.call_args_list
    assert len(recorded) == 2 * len(calls)
    for index, (cid, name, result) in enumerate(calls):
        start, finish = recorded[index * 2:index * 2 + 2]
        assert start.args[1] == 'tool_started'
        assert finish.args[1] == 'tool_finished'
        assert start.kwargs == {'event_key': cid + ':started', 'epoch': 1}
        assert finish.kwargs == {'event_key': cid + ':finished', 'epoch': 1}
        assert start.args[2]['context'] == '{"text":"hello"}'
        payload = finish.args[2]
        assert payload['tool_id'] == payload['invocation_id'] == cid
        assert payload['name'] == name
        assert payload['duration_s'] >= 0
        assert payload['is_error'] is (cid == 'error')
        projected = project_run_event(run, {'type': 'tool_finished', 'payload': payload})
        assert projected['type'] == 'tool.complete'
        assert projected['payload']['result'] == payload['result']
        if cid == 'connections':
            assert payload['result'] == receipt
        elif cid == 'echo':
            assert payload['result'] == 'echo:hello'
        elif cid == 'large':
            assert len(payload['result'].encode('utf-8')) <= 64 * 1024
        elif cid == 'large-receipt':
            assert payload['result']['local_mcp'] is True
            assert len(json.dumps(payload['result'], ensure_ascii=False).encode('utf-8')) <= 64 * 1024


def test_session_rpc_history_unwraps_untrusted_mcp_wrapper():
    from harness.tests.test_gateway_pr7b_unit import MemoryServices, call, client_for
    wrapped = (
        '<untrusted_tool_result source="mcp__yfd_echo__echo">\n'
        'The following content was retrieved from an external source.\n\n'
        '{"result": "echo:clean"}\n'
        '</untrusted_tool_result>'
    )
    services = MemoryServices()
    with client_for(services) as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        sid = call(ws, 'session.create')['result']['session_id']
        services.runs.append({
            'principal_id': 'alice', 'session_id': sid, 'run_id': 'r-wrap',
            'created_at': 1, 'query': 'echo', 'active': False, 'status': 'completed',
            'events': [],
            'native_messages': [
                {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': 'c-wrap', 'type': 'function', 'function': {
                        'name': 'mcp__yfd_echo__echo', 'arguments': '{"text":"clean"}'}},
                ]},
                {'role': 'tool', 'tool_call_id': 'c-wrap', 'content': wrapped},
            ],
        })
        rows = client.get(f'/gateway-m1/api/sessions/{sid}/messages').json()['messages']
        tool_msg = next(m for m in rows if m['role'] == 'tool')
        assert tool_msg['content'] == '{"result": "echo:clean"}'

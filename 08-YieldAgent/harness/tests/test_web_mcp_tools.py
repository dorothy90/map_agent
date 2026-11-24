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

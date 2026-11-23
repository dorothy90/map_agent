import copy
import importlib.util
import json
from pathlib import Path
import sys
import pytest


def worker_module():
    before = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location('pin_refresh_worker', Path(__file__).parents[1] / 'native_worker.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = before


def tool(name, **properties):
    return {'type': 'function', 'function': {'name': name, 'description': name,
            'parameters': {'type': 'object', 'properties': properties}}}


class Store:
    def __init__(self, pin):
        self.pin = pin
        self.writes = []

    def get_session(self, session_id):
        assert session_id == 'same-session'
        return {'tool_names': json.dumps(self.pin), 'history': ['keep me']}

    def update_session_tool_names(self, session_id, pin):
        self.writes.append((session_id, copy.deepcopy(pin)))
        self.pin = pin


def test_refresh_replaces_only_same_name_backend_contract_preserving_pin():
    old = tool('render_yield_table', mode={'type': 'string'})
    from harness.tools.registry import domain_registry

    current = next(tool for tool in domain_registry().model_tools()
                   if tool['function']['name'] == 'render_yield_table')
    other = tool('terminal', command={'type': 'string'})
    saved = {'version': 'engine-revision', 'tools': [other, old], 'delegation_backend': {'keep': True}, 'extra': 3}
    store = Store(copy.deepcopy(saved))
    startup = {'session_id': 'same-session', 'tools': [current, tool('new_backend')]}

    assert worker_module().refresh_backend_tool_pin(store, startup)
    assert store.pin == {**saved, 'tools': [other, current]}
    assert not worker_module().refresh_backend_tool_pin(store, startup)
    assert len(store.writes) == 1
    assert saved['tools'][1] == old


def test_refresh_respects_effective_allowlist():
    old = tool('render_yield_table')
    store = Store({'version': 'engine-revision', 'tools': [old]})
    startup = {'session_id': 'same-session', 'allowed_tools': ['read_result'],
               'tools': [tool('render_yield_table', period={'type': 'string'})]}
    assert not worker_module().refresh_backend_tool_pin(store, startup)
    assert store.writes == []


@pytest.mark.parametrize('pin', [None, ['render_yield_table'], {}, {'tools': []},
                                      {'version': 'v', 'tools': ['render_yield_table']}])
def test_legacy_or_malformed_pin_is_not_rewritten(pin):
    store = Store(pin)
    assert not worker_module().refresh_backend_tool_pin(store, {'session_id': 'same-session', 'tools': [tool('render_yield_table')]})
    assert store.writes == []


def test_refresh_failure_does_not_continue_with_stale_contract():
    store = Store({'version': 'v', 'tools': [tool('render_yield_table')]})
    def fail(*args):
        raise RuntimeError('storage unavailable')
    store.update_session_tool_names = fail
    with pytest.raises(RuntimeError, match='storage unavailable'):
        worker_module().refresh_backend_tool_pin(store, {'session_id': 'same-session', 'tools': [tool('render_yield_table', period={})]})


def test_engine_restoration_uses_refreshed_same_version_schema():
    import subprocess
    code = '''
import copy, json, sys
from types import SimpleNamespace
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'harness'))
import native_worker
from tools.mcp_tool_agent import restore_agent_tool_prefix, tool_pin_version
from harness.tools.registry import domain_registry
old = {'type':'function','function':{'name':'render_yield_table','description':'old','parameters':{'type':'object','properties':{'mode':{'type':'string'},'columns':{'type':'array'}}}}}
new = next(tool for tool in domain_registry().model_tools()
           if tool['function']['name'] == 'render_yield_table')
removed = {'mode', 'columns', 'column_groups', 'column_formats', 'delta_row'}
assert not removed.intersection(new['function']['parameters']['properties'])
pin = {'version':tool_pin_version(), 'tools':[old]}
agent = SimpleNamespace(tools=[new], valid_tool_names={'render_yield_table'})
restore_agent_tool_prefix(agent, pin)
assert 'period' not in agent.tools[0]['function']['parameters']['properties']
assert {'mode', 'columns'} <= agent.tools[0]['function']['parameters']['properties'].keys()
class DB:
    def get_session(self, sid): return {'tool_names':json.dumps(pin)}
    def update_session_tool_names(self, sid, updated): pin.update(updated)
native_worker.refresh_backend_tool_pin(DB(), {'session_id':'same-session','tools':[new]})
agent = SimpleNamespace(tools=[new], valid_tool_names={'render_yield_table'})
restore_agent_tool_prefix(agent, pin)
assert agent.tools == [new]
assert 'period' in agent.tools[0]['function']['parameters']['properties']
assert not removed.intersection(agent.tools[0]['function']['parameters']['properties'])
print('same-version restore: stale before, current after')
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).parents[2],
                            capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'stale before, current after' in result.stdout


def test_web_resume_updates_only_visible_yield_contract_and_deferred_catalog():
    from harness.hermes_chat_worker import refresh_web_tool_pin

    old_memory = tool('memory', old={})
    search = tool('tool_search', current_catalog={})
    current_yield = tool('read_result', result_id={})
    saved = {
        'version': 'engine-revision', 'extra': 3,
        'tools': [old_memory, tool('tool_search', stale_catalog={}), tool('query_db'), tool('read_result')],
    }
    store = Store(saved)
    visible = [search, tool('tool_call'), current_yield, tool('memory', current={})]
    for _ in range(2):
        refresh_web_tool_pin(store, 'same-session', store.get_session('same-session'), visible, ['read_result'])
    assert store.writes == [('same-session', {**saved, 'tools': [old_memory, search, current_yield, tool('tool_call')]})]


@pytest.mark.parametrize('old, should_clear', [
    (['read_result'], True),
    (['memory'], True),
    (['memory', 'tool_search'], False),
])
def test_web_resume_handles_legacy_name_pins(old, should_clear):
    from harness.hermes_chat_worker import refresh_web_tool_pin

    store = Store(old)
    refresh_web_tool_pin(store, 'same-session', store.get_session('same-session'),
                         [tool('memory'), tool('tool_search')], ['read_result'])
    assert store.writes == ([('same-session', None)] if should_clear else [])

import copy
import json
import pytest


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

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


def model_tool_names():
    from harness.tools.registry import domain_registry

    return [tool["function"]["name"] for tool in domain_registry().model_tools()]


def test_domain_tool_registry_exposes_yield_handlers():
    from harness.tools.registry import domain_registry

    registry = domain_registry()
    assert model_tool_names() == [
        "read_result", "query_db", "list_tables", "load_wafer_maps", "render_yield_table",
    ]
    assert registry.tools["read_result"].handler.__module__ == "harness.tools.python_tools"
    assert registry.tools["query_db"].handler.__module__ == "harness.tools.query_db"
    assert registry.tools["query_db"].read_only is True
    assert registry.tools["query_db"].effect == "artifact"
    assert registry.tools["query_db"].eager is True
    assert registry.tools["list_tables"].handler.__module__ == "harness.tools.list_tables"
    assert registry.tools["list_tables"].read_only is True
    assert registry.tools["list_tables"].effect == "read"
    assert registry.tools["list_tables"].eager is True


def test_lot_history_is_a_read_only_native_domain_tool():
    from harness.tools.registry import domain_registry, native_domain_registry

    native = native_domain_registry()
    assert set(native.tools) - set(domain_registry().tools) == {"query_lot_history"}
    assert native.tools["query_lot_history"].read_only
    assert any(tool["function"]["name"] == "query_lot_history" for tool in native.model_tools())


def test_engine_session_search_reads_profile_state_db(tmp_path):
    engine = Path(__file__).parents[1] / "engine"
    profile = tmp_path / "profile"
    script = f"""
import json
import sys
from pathlib import Path
sys.path.insert(0, {str(engine)!r})
from hermes_state import SessionDB
from tools.session_search_tool import session_search
home = Path({str(profile)!r})
home.mkdir(parents=True, exist_ok=True)
db = SessionDB(db_path=home / 'state.db')
db.create_session('old-session', 'cli', model='test')
db.append_message('old-session', 'user', 'needle-pr65 historical question')
db.append_message('old-session', 'assistant', 'historical answer')
db.close()
print(session_search(query='needle-pr65'))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script], cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "HERMES_HOME": str(profile)},
        text=True, capture_output=True, check=True,
    )

    payload = json.loads(result.stdout.strip())
    assert payload["success"] is True
    assert payload["results"][0]["session_id"] == "old-session"
    assert payload["results"][0]["messages"][0]["content"] == "needle-pr65 historical question"
    assert (profile / "state.db").is_file()


def test_host_model_tools_payload_log_records_bound_names(tmp_path):
    from harness.executor import log_model_tools_payload

    output = tmp_path / "host-tools.jsonl"
    model = SimpleNamespace(kwargs={"tools": [
        {"type": "function", "function": {"name": "finish"}},
        {"type": "function", "name": "web_search"},
    ]})
    log_model_tools_payload(output, model, request_id="run:reasoning:1")

    assert json.loads(output.read_text()) == {
        "api_request_id": "run:reasoning:1", "tools": ["finish", "web_search"]
    }


def test_session_search_registry_marks_history_as_unconfirmed_scope(tmp_path):
    harness = Path(__file__).parents[1]
    profile = tmp_path / 'profile'
    script = f"""
import json
import sys
from pathlib import Path
sys.path[:0] = [{str(harness.parent)!r}, {str(harness / 'engine')!r}]
from hermes_state import SessionDB
from tools import session_search_tool
from tools.registry import registry
from harness.chat_tool_helpers import constrain_session_search
home = Path({str(profile)!r})
home.mkdir(parents=True, exist_ok=True)
db = SessionDB(db_path=home / 'state.db')
db.create_session('old-session', 'cli', model='test')
anchor = db.append_message('old-session', 'user', 'needle-scope historical question')
db.append_message('old-session', 'assistant', 'historical answer')
db.create_session('child-session', 'cli', model='test', parent_session_id='old-session')
child_anchor = db.append_message('child-session', 'user', 'child history')
db.close()
assert registry.get_entry('session_search') is not None
cases = [
    {{'query': 'needle-scope'}},
    {{}},
    {{'session_id': 'old-session'}},
    {{'session_id': 'old-session', 'around_message_id': anchor}},
    {{'session_id': 'old-session', 'around_message_id': child_anchor}},
    {{'query': 'nonexistentuniquetoken'}},
]
before = [json.loads(registry.dispatch('session_search', args)) for args in cases]
constrain_session_search(session_search_tool)
once = session_search_tool.session_search
constrain_session_search(session_search_tool)
assert session_search_tool.session_search is once, 'wrapping must be idempotent'
for args, original in zip(cases, before):
    result = json.loads(registry.dispatch('session_search', args))
    assert result['success'] is True, result
    marker = result.pop('scope_confirmation')
    assert marker == {{
        'source': 'session_search',
        'confirms_current_scope': False,
        'note': '검색 결과 자체는 현재 요청의 조건을 확정하지 않는다. 현재 대화에서 이미 사용자 확인된 조건은 유지한다. 다른 세션 또는 현재 대화에서 미확정인 조건은 후보이며, 조회·맵·분석 도구에 사용하기 전에 clarify로 확인한다.',
    }}
    assert result == original, (args, result, original)
assert before[0]['results'][0]['messages'][0]['content'] == 'needle-scope historical question'
assert before[2]['messages']
assert before[3]['messages']
assert before[4]['warning']
assert before[5]['count'] == 0 and before[5]['results'] == []
for args in (
    {{'session_id': 'missing-session'}},
    {{'session_id': 'old-session', 'around_message_id': 'invalid'}},
    {{'profile': 'other'}},
    {{'session_id': 'other/id'}},
):
    result = json.loads(registry.dispatch('session_search', args))
    assert result['success'] is False, result
    assert 'scope_confirmation' not in result
    if args.get('profile') or args.get('session_id') == 'other/id':
        assert 'cross-profile session search is disabled' in result['error']
for profile in (None, '', '   '):
    result = json.loads(registry.dispatch('session_search', {{'profile': profile}}))
    assert result['success'] is True
    assert result['scope_confirmation']['confirms_current_scope'] is False
print('verified')
"""
    result = subprocess.run(
        [sys.executable, '-I', '-B', '-c', script], cwd=tmp_path,
        env={'PATH': os.environ.get('PATH', ''), 'HERMES_HOME': str(profile)},
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'verified'


def test_session_search_preserves_non_json_and_non_success_results():
    from harness.chat_tool_helpers import constrain_session_search

    for result in ('not json', None, '[]', 'null', '{"success": false, "error": "unavailable"}',
                   '{"success": 1, "results": []}'):
        module = SimpleNamespace(session_search=lambda: result)
        constrain_session_search(module)
        actual = module.session_search()
        if result in ('not json', None):
            assert actual == result
        else:
            assert json.loads(actual) == json.loads(result)

"""Run the native agent/registry in its own interpreter, as the chat worker does."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_web_delegation_and_todos_are_scoped(tmp_path):
    (tmp_path / 'config.yaml').write_text(
        'delegation:\n  orchestrator_enabled: false\n  base_url: http://localhost:2/v1\n'
        '  model: different-model\n  command: unwanted-child-command\n'
    )
    result = subprocess.run(
        [sys.executable, '-I', '-B', __file__], cwd=tmp_path,
        env={'PATH': os.environ['PATH'], 'HOME': str(tmp_path), 'HERMES_HOME': str(tmp_path)},
        text=True, capture_output=True, timeout=45,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def check_delegation():
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS, InlineToolContext
    from agent.tool_executor import _unwrap_tool_search_call
    from harness.web_delegation import configure_web_delegation
    from hermes_state_registry import acquire, release_or_close
    from model_tools import _dispatch_bridge_tool, get_tool_definitions
    from run_agent import AIAgent
    from tools.registry import registry
    from tools.todo_tool import TodoStore
    from types import SimpleNamespace

    configure_web_delegation()
    for name, toolset in [('lookup_yield', 'yield'), ('read_shared', 'web_shared_files')]:
        schema = {'name': name, 'description': name, 'parameters': {'type': 'object', 'properties': {}}}
        registry.register(name=name, toolset=toolset, schema=schema, handler=lambda args: 'ok')
    db = acquire(Path(os.environ['HERMES_HOME']) / 'state.db')
    parent = AIAgent(model='test-model', provider='custom', base_url='http://localhost:1/v1', api_key='test',
                     quiet_mode=True, platform='yield_web', session_id='owner', session_db=db,
                     enabled_toolsets=['yield', 'web_shared_files', 'skills', 'memory', 'clarify', 'todo', 'delegation'])
    db.create_session('owner', source='yield_web')

    def child_turn(child, **kwargs):
        raw = get_tool_definitions(enabled_toolsets=child.enabled_toolsets, disabled_toolsets=child.disabled_toolsets,
                                   quiet_mode=True, skip_tool_search_assembly=True)
        assert {tool['function']['name'] for tool in raw} == {'lookup_yield', 'read_shared'}, raw
        result, target = _dispatch_bridge_tool('tool_call', {'calls': [
            {'name': 'terminal', 'arguments': {'command': 'pwd'}}]}, child.enabled_toolsets, child.disabled_toolsets)
        assert target is None and 'error' in json.loads(result), 'terminal was offered to the child'
        assert child.model == parent.model, child.model
        assert child.base_url.rstrip('/') == parent.base_url.rstrip('/'), child.base_url
        assert not child.acp_command, child.acp_command
        assert child.session_id != parent.session_id
        assert child._session_db.db_path == parent._session_db.db_path
        return {'completed': True, 'final_response': 'checked the shared data', 'messages': []}

    AIAgent.run_conversation = child_turn
    try:
        name, args, error = _unwrap_tool_search_call(parent, 'tool_call', {'calls': [
            {'name': 'todo_list', 'arguments': {'todos': [{'id': 'a', 'content': 'Check the map', 'status': 'pending'}]}}]})
        assert name == 'todo_list' and not error
        invoke = INLINE_TOOL_EXECUTORS[name]
        ctx = InlineToolContext(effective_task_id='task')
        assert json.loads(invoke(parent, args, ctx))['todos'][0]['id'] == 'a'
        assert json.loads(invoke(SimpleNamespace(_todo_store=TodoStore()), {}, ctx))['todos'] == []
        for args in [{'goal': 'Inspect the image', 'images': ['../other.png']},
                     {'tasks': json.dumps([{'goal': 'Inspect the image', 'images': ['../other.png']}])}]:
            assert 'vision_analyze' in json.loads(parent._dispatch_delegate_task(args))['error']
        result = json.loads(parent._dispatch_delegate_task({'tasks': [{'goal': 'Check the shared data'}]}))
        assert result['results'][0]['summary'] == 'checked the shared data', result
    finally:
        parent.close()
        release_or_close(db)


if __name__ == '__main__':
    harness = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(harness / 'engine'), str(harness.parent)]
    from hermes_cli.plugins_discovery import suppress_plugin_discovery
    with suppress_plugin_discovery():
        check_delegation()

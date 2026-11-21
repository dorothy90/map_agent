def test_loaded_skill_body_occurs_once_in_context():
    from langchain_core.messages import ToolMessage
    from harness.context import build_context
    state = {'goal': {}, 'loaded_skills': {'sample': {'name': 'sample', 'hash': 'h', 'content': 'UNIQUE BODY'}},
             'messages': [ToolMessage(content='{"name":"sample","hash":"h","content":"UNIQUE BODY"}', name='read_skill', tool_call_id='call')]}
    messages = build_context(state, 'base')
    assert str([m.content for m in messages]).count('UNIQUE BODY') == 1
    assert any('UNIQUE BODY' in m.content for m in messages if m.type == 'system')
    assert all('UNIQUE BODY' not in m.content for m in messages if m.type == 'tool')


def test_hermes_skill_survives_new_chat_and_is_scoped_to_user(monkeypatch, tmp_path):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys

    from harness import native_hermes

    monkeypatch.setattr(native_hermes, 'ROOT', tmp_path)
    engine = Path(native_hermes.__file__).with_name('engine')
    probe = """
import json, sys
sys.path.insert(0, sys.argv[1])
from model_tools import get_tool_definitions
from tools.registry import registry
from tools import skills_tool, skill_manager_tool

action, session = sys.argv[2:]
name = 'report-format'
body = '---\\nname: report-format\\ndescription: Use for report formatting.\\n---\\nEnd with a signed label.'
tools = get_tool_definitions(enabled_toolsets=['skills'], skip_tool_search_assembly=True, quiet_mode=True)
assert {row['function']['name'] for row in tools} == {'skills_list', 'skill_view', 'skill_manage'}
if action == 'create':
    args = {'operations': [{'action': 'create', 'name': name, 'content': body}]}
    result = registry.dispatch('skill_manage', args, task_id=session)
else:
    result = registry.dispatch('skill_view', {'name': name}, task_id=session)
print(result)
"""
    def call(user, session, action):
        profile = native_hermes.prepare_profile(user)
        proc = subprocess.run([sys.executable, '-I', '-B', '-c', probe, str(engine), action, session],
            env={'PATH': os.environ['PATH'], 'HERMES_HOME': str(profile)},
            cwd=tmp_path, check=True, capture_output=True, text=True, timeout=40)
        return json.loads(proc.stdout)

    assert call('alice', 'first', 'create')['success']
    assert 'End with a signed label.' in call('alice', 'second', 'read')['content']
    assert call('bob', 'third', 'read')['success'] is False

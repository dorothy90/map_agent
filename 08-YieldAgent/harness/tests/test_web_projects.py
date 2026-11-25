import json
from pathlib import Path

from harness.web_projects import install_web_projects


def test_pathless_native_projects_group_only_originating_employee_chat(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'engine'))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    from tools.registry import ToolRegistry

    assignments = {}
    def chat(employee, session, database='db'):
        key = employee, session, database
        def session_project(project):
            if project is not None:
                assignments[key] = project
            return assignments.get(key, {}).get('id')
        registry = ToolRegistry()
        install_web_projects(registry, tmp_path / employee, database, session_project)
        assert 'path' not in registry.get_schema('desktop_project')['parameters']['properties']
        return lambda action, **fields: json.loads(registry.dispatch('desktop_project', {'action': action, **fields}))

    first, second = chat('employee-1', 'one'), chat('employee-1', 'two')
    origin = first('create', name='4SA')
    assert 'id' in origin
    assert first('create', name='4SA')['id'] == origin['id']
    assert second('list')['active_id'] is None
    second('switch', name=origin['slug'])
    first('create', name='4SS')
    assert second('list')['active_id'] == origin['id']
    assert len(first('list')['projects']) == 2
    assert not second('create', name='external', path='/etc')['success']
    for other in [chat('employee-2', 'one'), chat('employee-1', 'one', 'other-db')]:
        assert not other('switch', name=origin['id'])['success']
        assert other('list') == {'active_id': None, 'projects': []}

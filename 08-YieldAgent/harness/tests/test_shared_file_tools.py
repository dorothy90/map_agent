"""Exercise the stock Hermes handlers through their real tool registry."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_shared_files_use_native_handlers_on_published_data_only(tmp_path):
    shared = tmp_path / 'shared'
    shared.mkdir()
    (shared / 'published.csv').write_text('sample,value\nwafer_A,73.6\n')
    (shared / 'notes').mkdir()
    (shared / 'notes/other.txt').write_text('wafer_B')
    (shared / '.env').write_text('wafer_private')
    outside = tmp_path / 'staff.txt'
    outside.write_text('wafer_private')
    (shared / 'alias').symlink_to(outside)
    (shared / 'outside').symlink_to(tmp_path, target_is_directory=True)
    (shared / 'linked_name').symlink_to(shared / 'published.csv')
    os.mkfifo(shared / 'pipe')
    script = """
        import errno, json, os, sys
        from pathlib import Path
        root = Path(sys.argv[1])
        from PIL import Image
        from tools import file_tools, vision_tools
        from tools.registry import registry
        from harness import shared_file_tools as policy
        from harness.hermes_chat_worker import bind_provider_secret
        from agent.secret_scope import reset_secret_scope
        from agent.auxiliary_client import set_runtime_main
        endpoint = 'https://openrouter.ai/api/v1'
        assert bind_provider_secret('openrouter', 'https://example.invalid', 'fixture') is None
        scope = bind_provider_secret('openrouter', endpoint, 'sk-or-fixture')
        assert scope is not None and 'OPENROUTER_API_KEY' not in os.environ
        set_runtime_main('openrouter', 'z-ai/glm-5.3-flash', base_url=endpoint, api_key='sk-or-fixture')
        assert vision_tools.check_vision_requirements()
        reset_secret_scope(scope)
        from concurrent.futures import ThreadPoolExecutor
        Image.new('RGB', (2, 2), 'blue').save(root / 'map.png')
        (root / 'vector.png').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        policy.install_shared_file_policy(registry, root)
        def call(name, **args):
            result = registry.dispatch(name, args, task_id='shared-policy-test')
            return json.loads(result) if isinstance(result, str) else result
        assert '2|wafer_A,73.6' in call('read_file', path='published.csv')['content']
        assert 'wafer_B' in call('read_file', path=str(root / 'notes/other.txt'))['content']
        with ThreadPoolExecutor(2) as pool:
            calls = [pool.submit(registry.dispatch, 'read_file', {'path': 'published.csv'},
                                 task_id=f'session-{i}') for i in range(2)]
            assert all('wafer_A' in item.result() for item in calls)
        for path in ('../staff.txt', str(root.parent / 'staff.txt'), 'outside/staff.txt',
                     'alias', 'linked_name', '.env', 'pipe', 'missing.csv'):
            assert 'error' in call('read_file', path=path), path
            assert 'error' in call('search_files', path=path, pattern='wafer'), path
        assert set(call('search_files', pattern='*.csv', target='files')['files']) == {'published.csv'}
        matches = call('search_files', pattern='wafer')['matches']
        assert {m['path'] for m in matches} == {'notes/other.txt', 'published.csv'}
        assert call('search_files', pattern='wafer_private')['total_count'] == 0
        for url in ('https://example.invalid/img.png', 'data:image/png;base64,YQ==',
                    'alias', 'outside/map.png', 'vector.png', str(root.parent / 'staff.txt')):
            assert 'error' in call('vision_analyze', image_url=url, question='Inspect'), url
        vision_tools._should_use_native_vision_fast_path = lambda: True
        from tools import vision_tools_history_budget
        vision_tools_history_budget.resolve_repeat_cap = lambda: 1
        image = call('vision_analyze', image_url='map.png', question='Inspect')
        assert image.get('_multimodal') and any(part['type'] == 'image_url' for part in image['content']), image
        assert image['meta']['image_url'] == 'map.png'
        assert 'error' in call('vision_analyze', image_url='map.png', question='Inspect again')
        def no_links(*args, **kwargs):
            raise OSError(errno.EXDEV, 'cross-device link')
        os.link = no_links
        assert call('search_files', path='notes', pattern='wafer_B')['matches'][0]['path'] == 'notes/other.txt'
        policy.MAX_COPY_BYTES = 3
        assert call('search_files', pattern='*.csv', target='files')['files'] == ['published.csv']
        assert 'error' in call('search_files', pattern='wafer')
        assert (root / 'published.csv').read_text() == 'sample,value\\nwafer_A,73.6\\n'
    """
    harness = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, '-c', textwrap.dedent(script), str(shared)],
        cwd=tmp_path, timeout=60, capture_output=True, text=True,
        env={'PATH': os.environ['PATH'], 'HOME': str(tmp_path), 'HERMES_HOME': str(tmp_path / 'profile'),
             'PYTHONPATH': os.pathsep.join(map(str, (harness.parent, harness / 'engine'))),
             'HERMES_DISABLE_LAZY_INSTALLS': '1', 'PYTHON_DOTENV_DISABLED': '1'},
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_employee_workspace_writes_use_hermes_and_leave_published_files_read_only(tmp_path):
    shared = tmp_path / 'shared'
    shared.mkdir()
    (shared / 'published.txt').write_text('published')
    harness = Path(__file__).resolve().parents[1]
    script = """
        import json, sys
        from pathlib import Path
        from tools import file_tools, vision_tools
        from tools.registry import registry
        from harness.personal_file_tools import install_personal_file_policy
        from harness.shared_file_tools import install_shared_file_policy
        shared, profile = map(Path, sys.argv[1:3])
        profile.mkdir(exist_ok=True)
        install_shared_file_policy(registry, shared)
        install_personal_file_policy(registry, profile, 'yielddata', shared)
        def call(name, **args):
            return json.loads(registry.dispatch(name, args, task_id='staff-chat'))
        target = '/workspace/notes/sample.txt'
        if sys.argv[3] == 'other':
            assert 'error' in call('read_file', path=target)
        else:
            assert 'error' not in call('write_file', path=target, content='draft'), 'write'
            assert 'draft' in call('read_file', path=target)['content']
            assert 'error' not in call('patch', path=target, old_string='draft', new_string='verified')
            assert 'verified' in call('read_file', path=target)['content']
            matches = call('search_files', path='/workspace', pattern='verified')['matches']
            assert [m['path'] for m in matches] == [target], matches
            one = '*** Begin Patch\\n*** Add File: /workspace/extra.txt\\n+example\\n*** End Patch'
            assert 'error' not in call('patch', mode='patch', patch=one)
            assert 'example' in call('read_file', path='/workspace/extra.txt')['content']
            assert 'error' in call('patch', mode='patch', patch=one.replace('/workspace/extra.txt', str(shared / 'new.txt')))
            for path in ['published.txt', str(shared / 'published.txt'), '/workspace/../outside', '/workspace/.git/config']:
                assert 'error' in call('write_file', path=path, content='no'), path
            assert (shared / 'published.txt').read_text() == 'published'
            assert not (shared / 'new.txt').exists()
            actual = next(profile.rglob('sample.txt'))
            actual.with_name('alias').symlink_to(shared / 'published.txt')
            assert 'error' in call('write_file', path='/workspace/notes/alias', content='no')
            from tools.delegate_tool_registry import _register_subagent
            _register_subagent({'subagent_id': 'delegated-test'})
            for path, permitted in [('published.txt', True), (target, False)]:
                result = registry.dispatch('read_file', {'path': path}, task_id='delegated-test')
                assert ('content' in json.loads(result)) == permitted
    """
    for principal in ('owner', 'other'):
        profile = tmp_path / principal
        result = subprocess.run(
            [sys.executable, '-c', textwrap.dedent(script), str(shared), str(profile), principal],
            cwd=tmp_path, timeout=60, capture_output=True, text=True,
            env={'PATH': os.environ['PATH'], 'HOME': str(profile), 'HERMES_HOME': str(profile),
                 'HERMES_WRITE_SAFE_ROOT': str(profile),
                 'PYTHONPATH': os.pathsep.join(map(str, (harness.parent, harness / 'engine'))),
                 'HERMES_DISABLE_LAZY_INSTALLS': '1', 'PYTHON_DOTENV_DISABLED': '1'},
        )
        assert result.returncode == 0, result.stdout + result.stderr

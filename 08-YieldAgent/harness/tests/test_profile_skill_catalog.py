import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
import yaml

from harness import native_hermes
from harness.router import router


def test_profile_cards_match_native_skills_after_toggle(tmp_path, monkeypatch):
    monkeypatch.setattr(native_hermes, 'ROOT', tmp_path)
    profile = native_hermes.prepare_profile('owner')
    skills = profile / 'skills'
    package = skills / 'analysis' / 'summary'
    package.mkdir(parents=True)
    (package / 'SKILL.md').write_text(
        '---\nname: summary\ndescription: Overview\nmetadata:\n  yield_ui:\n'
        '    artwork: trend\n    title: 요약 카드\n---\n# 요약\n본문\n')
    foreign = native_hermes.prepare_profile('another') / 'skills' / 'private'
    foreign.mkdir(parents=True)
    (foreign / 'SKILL.md').write_text('# Private')
    (skills / 'linked').symlink_to(foreign, target_is_directory=True)

    app = FastAPI()
    app.include_router(router)

    @app.middleware('http')
    async def identify(request, call_next):
        request.state.principal_id = 'owner'
        return await call_next(request)

    client = TestClient(app)
    url = '/harness/skills'
    card, = client.get(url, params={'principal_id': 'another'}).json()['skills']
    assert card == dict(name='summary', title='요약 카드', category='analysis',
                        description='Overview', artwork='trend', enabled=True, content='# 요약\n본문')
    assert client.put(url + '/private/enabled', json={'enabled': False}).status_code == 404
    assert client.put(url + '/summary/enabled', json={'enabled': False}).is_success
    assert client.get(url).json()['skills'][0]['enabled'] is False

    script = ('from tools.skills_tool import find_all_skills; '
              'assert "summary" not in [s["name"] for s in find_all_skills()]')
    subprocess.run([sys.executable, '-c', script], check=True, cwd=tmp_path,
                   env={**os.environ, 'HERMES_HOME': str(profile),
                        'PYTHONPATH': str(Path(native_hermes.__file__).with_name('engine'))})
    assert client.put(url + '/summary/enabled', json={'enabled': True}).is_success
    assert 'summary' not in yaml.safe_load((profile / 'config.yaml').read_text())['skills']['disabled']
    new = skills / 'new-package'
    new.mkdir()
    (new / 'SKILL.md').write_text('---\nname: new-package\ndescription: Fresh\n---\n# 새 스킬\n')
    cards = client.get(url).json()['skills']
    fresh, = [s for s in cards if s['name'] == 'new-package']
    assert fresh['title'] == '새 스킬' and fresh['artwork'] == 'generic'
    with ThreadPoolExecutor(2) as pool:
        updates = [pool.submit(client.put, f'{url}/{name}/enabled', json={'enabled': False})
                   for name in ('summary', 'new-package')]
        assert all(update.result().is_success for update in updates)
    assert yaml.safe_load((profile / 'config.yaml').read_text())['skills']['disabled'] == ['new-package', 'summary']


def test_skills_require_verified_profile():
    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).get('/harness/skills?principal_id=owner').status_code == 401

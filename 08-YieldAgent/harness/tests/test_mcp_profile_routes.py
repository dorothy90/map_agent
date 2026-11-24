"""Profile MCP REST operations, including real stdio discovery."""
import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import yaml

from harness import native_hermes
from harness.router import router

URL = '/harness/mcp/servers'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(native_hermes, 'ROOT', tmp_path)
    app = FastAPI()
    app.include_router(router)

    @app.middleware('http')
    async def identify(request, call_next):
        request.state.principal_id = request.headers.get('x-test-principal', 'owner')
        return await call_next(request)

    return TestClient(app)


def test_profile_mcp_lifecycle(client, tmp_path):
    assert client.get(URL).json() == {'servers': []}
    script = tmp_path / 'server.py'
    script.write_text('''import os
from mcp.server.fastmcp import FastMCP
assert os.environ['TEST_TOKEN'] == 'fake-value-for-redaction'
server = FastMCP('profile-test')
@server.tool()
def ping() -> str:
    """Return pong."""
    return 'pong'
server.run(transport='stdio')
''')
    body = dict(name='local-test', command=sys.executable, args=[str(script)], env={'TEST_TOKEN': 'fake-value-for-redaction'})
    added = client.post(URL, json=body)
    assert added.status_code == 200, added.text
    summary = added.json()
    assert summary['transport'] == 'stdio' and summary['enabled'] is True
    assert summary['env']['TEST_TOKEN'] != body['env']['TEST_TOKEN']
    assert client.get(URL).json()['servers'] == [summary]
    assert client.post(URL, json=body).status_code == 409
    path = URL + '/local-test'
    probe = client.post(path + '/test').json()
    assert probe['ok'], probe
    assert probe['tools'] == [{'name': 'mcp__local_test__ping', 'description': 'Return pong.'}]
    assert isinstance(probe['elapsed_ms'], int)
    config = native_hermes.profile_path('owner') / 'config.yaml'
    for enabled in (False, True):
        assert client.put(path + '/enabled', json={'enabled': enabled}).json()['enabled'] is enabled
        assert yaml.safe_load(config.read_text())['mcp_servers']['local-test']['enabled'] is enabled
    edit = dict(command=sys.executable, args=[str(script)], env=summary['env'])
    saved = yaml.safe_load(config.read_text())
    saved['mcp_servers']['local-test'].update(enabled=False, connect_timeout=12)
    config.write_text(yaml.safe_dump(saved))
    assert client.put(path, json=edit).status_code == 200
    saved = yaml.safe_load(config.read_text())['mcp_servers']['local-test']
    assert saved['enabled'] is False and saved['connect_timeout'] == 12
    assert yaml.safe_load(config.read_text())['mcp_servers']['local-test']['env'] == body['env']
    before = config.read_bytes()
    assert client.post(path + '/test', json=edit).json()['ok']
    draft = client.post(URL + '/unsaved/test', json={**edit, 'env': body['env']}).json()
    assert draft['ok'] and draft['tools'][0]['name'] == 'mcp__unsaved__ping'
    assert config.read_bytes() == before
    failed = client.post(path + '/test', json={**edit, 'command': '/nonexistent/mcp'} )
    assert failed.status_code == 200 and failed.json()['ok'] is False
    assert client.delete(path).json() == {'ok': True}
    for method, suffix, kwargs in [('get', '', {}), ('delete', '', {}), ('put', '/enabled', {'json': {'enabled': True}}), ('post', '/test', {})]:
        if method == 'get':
            assert client.get(URL).json() == {'servers': []}
        else:
            assert getattr(client, method)(path + suffix, **kwargs).status_code == 404


@pytest.mark.parametrize('body', [
    {'name': 'http', 'url': 'https://example.test'},
    {'name': 'http', 'command': 'python', 'transport': 'http'},
    {'name': 'http', 'command': 'python', 'oauth': {}},
    {'name': 'http', 'command': 'python', 'auth': 'none'},
    {'name': 'http', 'command': 'python', 'headers': {}},
    {'name': 'http', 'command': 'python', 'catalog': 'test'},
    {'name': '../bad', 'command': 'python'}, {'name': 'missing'},
    {'name': 'empty', 'command': '   '},
    {'name': 'bad', 'command': 'python', 'args': [1]},
    {'name': 'bad', 'command': 'python', 'env': {'BAD-KEY': 'value'}},
    {'name': 'bad', 'command': 'python', 'env': {'KEY': 1}},
    {'name': 'bad', 'command': 'python', 'enabled': 'false'},
    {'name': 'bad', 'command': 'sh', 'args': ['-c', 'curl https://example.invalid']},
])
def test_reject_invalid_stdio(client, body):
    response = client.post(URL, json=body)
    assert response.status_code == 400, response.text
    assert isinstance(response.json()['detail'], str)


def test_isolation_and_authentication(client):
    assert client.post(URL, json={'name': 'private', 'command': 'python'}).is_success
    headers = {'x-test-principal': 'another'}
    assert client.get(URL, headers=headers).json() == {'servers': []}
    assert client.put(URL + '/private/enabled', headers=headers, json={'enabled': True}).status_code == 404
    assert client.delete(URL + '/private', headers=headers).status_code == 404
    app = FastAPI()
    app.include_router(router)
    unauthenticated = TestClient(app)
    assert unauthenticated.get(URL).status_code == 401
    assert unauthenticated.post(URL, json={'name': 'x', 'command': 'python'}).status_code == 401
    assert client.put(URL + '/private/enabled', json={'enabled': 'false'}).status_code == 422


@pytest.mark.parametrize('unsafe', ['profile_symlink', 'config_symlink', 'config_hardlink'])
def test_unsafe_profile(client, tmp_path, unsafe):
    profile = native_hermes.prepare_profile('owner')
    config = profile / 'config.yaml'
    target = tmp_path / 'target'
    if unsafe == 'profile_symlink':
        profile.rename(target)
        profile.symlink_to(target, target_is_directory=True)
        protected = target / 'config.yaml'
    elif unsafe == 'config_symlink':
        config.rename(target)
        config.symlink_to(target)
        protected = target
    else:
        os.link(config, target)
        protected = target
    before = protected.read_bytes()
    assert client.get(URL).status_code == 503
    assert client.post(URL, json={'name': 'x', 'command': 'python'}).status_code == 503
    assert protected.read_bytes() == before


def test_existing_http_can_toggle_and_delete_but_not_edit(client):
    profile = native_hermes.prepare_profile('owner')
    config = profile / 'config.yaml'
    saved = yaml.safe_load(config.read_text())
    saved['mcp_servers'] = {'remote': {'url': 'http://127.0.0.1:1/mcp', 'connect_timeout': 1}}
    config.write_text(yaml.safe_dump(saved))
    path = URL + '/remote'
    assert client.put(path, json={'command': 'python'}).status_code == 400
    assert client.put(path + '/enabled', json={'enabled': False}).is_success
    probe = client.post(path + '/test')
    assert probe.status_code == 200 and probe.json()['ok'] is False
    assert client.delete(path).is_success

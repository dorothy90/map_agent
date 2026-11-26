import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from harness.dev.local_gateway_app import FixedLocalPrincipal, create_app


@pytest.fixture
def client():
    base_app = FastAPI()

    @base_app.post('/runs')
    async def runs(request: Request):
        return {'principal_id': request.state.principal_id}

    app = create_app(base_app=base_app, user='local-dev', services_factory=lambda ctrl: None)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize('headers', [
    {'origin': 'http://testserver'},
    {'host': 'localhost', 'origin': 'http://localhost'},
    {'host': '192.168.1.20:61402', 'origin': 'http://192.168.1.20:61402'},
    {},
])
def test_runs_accepts_any_host_and_origin(client, headers):
    response = client.post('/runs', headers=headers, json={})

    assert response.status_code == 200
    assert response.json() == {'principal_id': 'local-dev'}
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['referrer-policy'] == 'same-origin'


def test_cross_origin_requests(client):
    headers = {'origin': 'http://192.168.1.30:61413'}
    preflight = client.options('/runs', headers={**headers,
        'access-control-request-method': 'POST',
        'access-control-request-headers': 'content-type,x-client'})
    response = client.post('/runs', headers=headers, json={})

    assert preflight.status_code == response.status_code == 200
    assert 'POST' in preflight.headers['access-control-allow-methods']
    assert 'x-client' in preflight.headers['access-control-allow-headers']
    for result in (preflight, response):
        assert result.headers['access-control-allow-origin'] == '*'
        assert 'access-control-allow-credentials' not in result.headers
        assert result.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('origin', [None, 'http://testserver', 'http://192.168.1.30:61413'])
def test_fixed_principal_accepts_lan_peers_and_websocket_origins(origin):
    connection = SimpleNamespace(client=SimpleNamespace(host='192.168.1.30'),
        headers={'host': '192.168.1.20:61402', **({'origin': origin} if origin else {})},
        state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(principal_user='local-dev')))
    resolver = FixedLocalPrincipal()

    principal = asyncio.run(resolver.resolve(connection))

    assert principal.user_id == 'local-dev'
    assert connection.state.file_ticket_required is True
    assert asyncio.run(resolver.allows(connection, principal)) is True

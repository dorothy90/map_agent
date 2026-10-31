"""In-process ASGI only: never import agent_server or start its lifespan."""
import ast
import builtins
import json
import os
from pathlib import Path
import re
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from harness.gateway.auth import AuthenticationFailure, Principal, require_owner
from harness.gateway.routes import build_gateway_router
from harness.gateway.unsupported import http_rejection, rpc_rejection


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / 'harness/gateway/manifest.json').read_text())
ROWS = MANIFEST['routes'] + MANIFEST['external_routes']
HTTP_ROWS = [r for r in ROWS if r['method'] != 'WEBSOCKET']
WS_ROWS = [r for r in ROWS if r['method'] == 'WEBSOCKET']


class FixtureResolver:
    def __init__(self, user_id='alice', denied=False):
        self.principal = Principal(user_id)
        self.denied = denied
        self.seen = []

    async def resolve(self, connection):
        self.seen.append((connection.scope['type'], self.principal.user_id, dict(connection.path_params)))
        if self.denied:
            raise AuthenticationFailure(403)
        # Fixture ownership records are server-side; never trust request owner fields.
        owners = {'alice-session': 'alice', 'bob-session': 'bob', 'alice-profile': 'alice'}
        resource = connection.path_params.get('session_id') or connection.path_params.get('name')
        if resource in owners:
            require_owner(self.principal, owners[resource])
        return self.principal


class FixtureOrigin:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = 0

    async def allows(self, websocket, principal):
        self.calls += 1
        return self.allowed


class FixtureTicket:
    def __init__(self):
        self.used = False

    async def verify_and_consume(self, websocket, principal):
        if self.used:
            return False
        self.used = True
        return True


def client_for(resolver=None, **kwargs):
    app = FastAPI()
    app.include_router(build_gateway_router(resolver, **kwargs))
    return TestClient(app)


def concrete(path):
    return re.sub(r'\{[^}]+\}', 'fixture', path)


@pytest.mark.parametrize('row', HTTP_ROWS, ids=lambda row: row['name'])
def test_all_http_routes_authenticate_before_policy(row):
    with client_for() as client:
        response = client.request(row['method'], concrete(row['path']), content=b'invalid json')
    assert response.status_code == 401


@pytest.mark.parametrize('row', HTTP_ROWS, ids=lambda row: row['name'])
def test_all_http_routes_match_pr1_or_unconnected(row):
    with client_for(FixtureResolver()) as client:
        response = client.request(row['method'], concrete(row['path']))
    expected = http_rejection(MANIFEST, row['method'], row['path'])
    assert response.status_code == (503 if expected is None else expected[0])
    if row['method'] != 'HEAD':
        if expected:
            assert response.json() == expected[1]
        else:
            assert response.json()['error']['code'] == 'handler_not_connected'


def test_unknown_and_ownership_and_static_precedence():
    for user in ('alice', 'bob'):
        resolver = FixtureResolver(user)
        with client_for(resolver) as client:
            assert client.get('/not-in-contract').status_code == 404
            assert client.get(f'/api/sessions/{user}-session/messages').status_code == 503
            other = 'bob' if user == 'alice' else 'alice'
            assert client.get(f'/api/sessions/{other}-session/messages').status_code == 403
            response = client.get('/api/sessions/search')
            assert response.json()['error']['feature'] == '/api/sessions/search'
            response = client.get('/api/profiles/alice-profile/soul')
            assert response.status_code == (501 if user == 'alice' else 403)
        assert all(item[1] == user for item in resolver.seen)
    with client_for() as client:
        assert client.get('/not-in-contract').status_code == 404
    with client_for(FixtureResolver(denied=True)) as client:
        assert client.get('/api/media').status_code == 403


@pytest.mark.parametrize('row', WS_ROWS, ids=lambda row: row['name'])
@pytest.mark.parametrize('authenticated', [False, True])
def test_ws_denied_before_accept(row, authenticated):
    resolver = FixtureResolver() if authenticated else None
    with client_for(resolver) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(row['path']):
                pytest.fail('must not upgrade')
    assert exc.value.code == 1008


def test_ws_same_resolver_gates_frames_and_ticket_reuse():
    resolver, origin, ticket = FixtureResolver('bob'), FixtureOrigin(), FixtureTicket()
    with client_for(resolver, origin_policy=origin, ticket_verifier=ticket) as client:
        assert client.get('/api/health').status_code == 503
        with client.websocket_connect('/api/ws') as ws:
            for method in ('shell.exec', 'invented.rpc', 'prompt.submit'):
                ws.send_json({'jsonrpc': '2.0', 'id': 7, 'method': method})
                frame = ws.receive_json()
                expected = rpc_rejection(MANIFEST, method, 7)
                if expected:
                    assert frame == expected
                else:
                    assert frame['error']['code'] == -32005
                    assert frame['error']['message'] == 'handler_not_connected'
            ws.send_json({'jsonrpc': '2.0', 'method': 'shell.exec'})
            ws.send_json({'jsonrpc': '2.0', 'id': 9, 'method': 'invented.rpc'})
            assert ws.receive_json()['id'] == 9  # notifications have no response
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/api/ws'):
                pytest.fail('ticket must be consumed')
    assert {item[:2] for item in resolver.seen} == {('http', 'bob'), ('websocket', 'bob')}


def test_origin_denial_does_not_consume_ticket_and_auth_precedes_origin():
    origin, ticket = FixtureOrigin(False), FixtureTicket()
    for resolver in (None, FixtureResolver()):
        with client_for(resolver, origin_policy=origin, ticket_verifier=ticket) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect('/api/ws'):
                    pytest.fail('must not upgrade')
    assert origin.calls == 1
    assert not ticket.used
    with client_for(FixtureResolver(), origin_policy=FixtureOrigin()) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/api/ws'):
                pytest.fail('missing ticket verifier')


def test_unsupported_ws_rejects_even_with_all_gates_injected():
    for row in WS_ROWS:
        if row['path'] == '/api/ws':
            continue
        resolver = FixtureResolver()
        with client_for(resolver, origin_policy=FixtureOrigin(), ticket_verifier=FixtureTicket()) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(row['path']):
                    pytest.fail('unsupported WS')
        assert len(resolver.seen) == 1


def test_disabled_requests_do_not_open_files_or_start_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('disabled request performed I/O')

    with client_for(FixtureResolver(), origin_policy=FixtureOrigin(), ticket_verifier=FixtureTicket()) as client:
        with monkeypatch.context() as guarded:
            guarded.setattr(builtins, 'open', forbidden)
            guarded.setattr(Path, 'open', forbidden)
            guarded.setattr(subprocess, 'Popen', forbidden)
            for row in HTTP_ROWS:
                assert client.request(row['method'], concrete(row['path'])).status_code in (501, 503)
            with client.websocket_connect('/api/ws') as ws:
                ws.send_json({'jsonrpc': '2.0', 'id': 1, 'method': 'shell.exec'})
                assert ws.receive_json()['error']['code'] == -32004


def test_gateway_imports_have_no_execution_or_store_dependencies():
    allowed = {'dataclasses', 'typing', 'starlette.requests', 'starlette.websockets',
               'starlette.responses', 'fastapi', 'json', 'pathlib',
               'auth', 'policy', 'unsupported'}
    for filename in ('auth.py', 'routes.py'):
        tree = ast.parse((ROOT / 'harness/gateway' / filename).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name in allowed for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.module in allowed


def test_missing_principal_result_fails_closed():
    class MissingPrincipal:
        async def resolve(self, connection):
            return None

    with client_for(MissingPrincipal()) as client:
        assert client.get('/api/media').status_code == 401
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/api/ws'):
                pytest.fail('missing principal')


@pytest.mark.parametrize('mode,expected', [('legacy', -32601), ('authority', -32005)])
def test_ws_mode_is_server_selected_without_fallback(mode, expected):
    with client_for(FixtureResolver(), origin_policy=FixtureOrigin(),
                    ticket_verifier=FixtureTicket(), rpc_mode=mode) as client:
        with client.websocket_connect('/api/ws') as ws:
            ws.send_json({'jsonrpc': '2.0', 'id': 1, 'method': 'prompt.receipt',
                          'params': {'rpc_mode': 'authority'}})
            assert ws.receive_json()['error']['code'] == expected


def test_ws_invalid_input_does_not_dispatch_and_connection_survives():
    with client_for(FixtureResolver(), origin_policy=FixtureOrigin(),
                    ticket_verifier=FixtureTicket()) as client:
        with client.websocket_connect('/api/ws') as ws:
            ws.send_text('{')
            assert ws.receive_json()['error']['code'] == -32700
            for value in ([], {'id': 1}, {'jsonrpc': '2.0', 'id': {}, 'method': 'ping'}):
                ws.send_json(value)
                assert ws.receive_json()['error']['code'] == -32600
            ws.send_json({'jsonrpc': '2.0', 'id': None, 'method': 'unknown'})
            assert ws.receive_json() == rpc_rejection(MANIFEST, 'unknown', None)


@pytest.mark.parametrize('enabled', [False, True])
def test_agent_server_actual_mount_block_without_import_side_effects(monkeypatch, enabled):
    source = (ROOT / 'agent_server.py').read_text()
    tree = ast.parse(source)
    blocks = [node for node in tree.body if isinstance(node, ast.If)
              and 'GATEWAY_M1_ENABLED' in ast.unparse(node.test)]
    assert len(blocks) == 1
    health = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'health')
    app = FastAPI()
    # Execute the real health function/decorator and real opt-in block only.
    exec(compile(ast.Module(body=[health], type_ignores=[]), 'agent_server.py', 'exec'), {'app': app})
    existing_routes = tuple(app.routes)
    imports = []
    original_import = builtins.__import__

    def watched_import(name, *args, **kwargs):
        if name.startswith('harness.gateway'):
            imports.append(name)
        return original_import(name, *args, **kwargs)

    if enabled:
        monkeypatch.setenv('GATEWAY_M1_ENABLED', 'true')
    else:
        monkeypatch.delenv('GATEWAY_M1_ENABLED', raising=False)
    monkeypatch.setattr(builtins, '__import__', watched_import)
    exec(compile(ast.Module(body=blocks, type_ignores=[]), 'agent_server.py', 'exec'), {'app': app, 'os': os})
    assert tuple(app.routes[:len(existing_routes)]) == existing_routes
    assert bool(imports) is enabled
    with TestClient(app) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/gateway-m1/api/media').status_code == (401 if enabled else 404)
        assert client.get('/api/media').status_code == 404

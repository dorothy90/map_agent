"""REST/static RPC through in-process ASGI; no Mongo, sockets or models."""
import json
from pathlib import Path
import re

import pytest

from harness.gateway.auth import AuthenticationFailure
from harness.gateway.services import GatewayServices
from harness.gateway.publication import candidate_digest
from harness.gateway.unsupported import http_rejection, rpc_rejection
from harness.tests.test_gateway_pr7b_unit import MemoryServices, call, client_for


PREFIX = '/gateway-m1'
CONNECTED = {'/api/sessions', '/api/profiles/sessions', '/api/profiles/active',
             '/api/health', '/api/status'}
STATIC = ('config.get', 'model.options', 'profiles.list', 'tools.list', 'tools.show',
          'toolsets.list', 'i18n.catalog', 'i18n.languages', 'paste.collapse')
MANIFEST = json.loads((Path(__file__).parents[1] / 'gateway/manifest.json').read_text())


def seeded():
    services = MemoryServices()
    services.profile_id_for = lambda principal: 'profile-' + principal.user_id
    for owner in ('alice', 'bob'):
        services.sessions[owner, owner + '-session'] = {
            'id': owner + '-session', 'source': 'cli', 'started_at': 1.0,
            'title': owner + ' title', 'profile_name': 'profile-' + owner,
            'rows': [{'id': 1, 'role': 'user', 'content': owner + ' question'},
                     {'id': 2, 'role': 'assistant', 'content': 'PRIVATE-CANDIDATE'}],
            'model_config': {'api_key': 'sk-private'}, 'cwd': '/private/host',
            'system_prompt': 'PRIVATE-SYSTEM'}
    return services


@pytest.mark.parametrize('path', sorted(CONNECTED))
def test_connected_rest_shape(path):
    with client_for(seeded()) as client:
        response = client.get(PREFIX + path)
        assert response.status_code == 200, response.text
        body = response.json()
        if path.endswith('/sessions'):
            assert set(body) >= {'sessions', 'total', 'limit', 'offset', 'storage'}
            assert body['total'] == 1
            assert body['sessions'][0]['message_count'] == 1
        elif path.endswith('/active'):
            assert body == {'active': 'profile-alice', 'current': 'profile-alice'}
        elif path.endswith('/health'):
            assert body == {'ok': True, 'auth_required': True}
        else:
            assert body == {'auth_required': True}
        assert 'bob' not in response.text
        for hidden in ('PRIVATE-', 'sk-private', '/private/host', 'api_key', 'system_prompt'):
            assert hidden not in response.text


def test_rest_messages_reuses_public_projection_and_ownership():
    with client_for(seeded()) as client:
        body = client.get(PREFIX + '/api/sessions/alice-session/messages').json()
        assert body['messages'] == [{'role': 'user', 'content': 'alice question', 'id': 1}]
        assert body['pagination'] == {'limit': 500, 'offset': 0, 'order': 'latest', 'returned': 1}
        own = client.get(PREFIX + '/api/sessions/bob-session/messages', headers={'x-test-user': 'bob'})
        assert own.status_code == 200
        foreign = client.get(PREFIX + '/api/sessions/bob-session/messages')
        missing = client.get(PREFIX + '/api/sessions/missing/messages')
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json() == {'detail': 'Session not found'}


def test_static_i18n_original_shapes():
    with client_for(GatewayServices(None, None)) as client:
        with client.websocket_connect(PREFIX + '/api/ws') as ws:
            languages = call(ws, 'i18n.languages')['result']['languages']
            assert len(languages) == 17
            assert languages[0] == {'id': 'en', 'endonym': 'English', 'rtl': False, 'source': 'bundled'}
            for lang, expected in [('ko-KR', 'ko'), ('pt-BR', 'pt'), ('unrecognized', 'en')]:
                assert call(ws, 'i18n.catalog', {'lang': lang})['result'] == {
                    'lang': expected, 'surface': 'core', 'messages': {}}
            bad = call(ws, 'i18n.catalog', {'lang': 'en', 'surface': 'bad'})
            assert bad['error']['code'] == 4002
            assert bad['error']['message'] == 'surface must be one of core, tui, desktop'


def test_all_manifest_http_rows_and_no_services_compatibility():
    connected = CONNECTED | {'/api/sessions/{session_id}/messages'}
    for services in (None, seeded()):
        with client_for(services) as client:
            for row in MANIFEST['routes'] + MANIFEST['external_routes']:
                if row['method'] == 'WEBSOCKET':
                    continue
                path = row['path'].replace('{session_id}', 'alice-session')
                path = re.sub(r'\{[^}]+\}', 'example', path)
                response = client.request(row['method'], PREFIX + path)
                rejection = http_rejection(MANIFEST, row['method'], row['path'])
                if row['method'] == 'HEAD':
                    assert response.status_code == rejection[0]
                    assert response.content == b''
                    continue
                if rejection is not None:
                    assert (response.status_code, response.json()) == rejection, row
                elif services is not None and row['method'] == 'GET' and row['path'] in connected:
                    assert response.status_code == 200, (row, response.text)
                else:
                    assert response.status_code == 503, (row, response.text)
                    assert response.json()['error']['code'] == 'handler_not_connected'


@pytest.mark.parametrize('status', [401, 403])
def test_every_http_route_authenticates_before_service(status):
    class Deny:
        async def resolve(self, connection):
            raise AuthenticationFailure(status)

    services = seeded()
    with client_for(services, resolver=Deny()) as client:
        for row in MANIFEST['routes'] + MANIFEST['external_routes']:
            if row['method'] == 'WEBSOCKET':
                continue
            path = re.sub(r'\{[^}]+\}', 'example', row['path'])
            response = client.request(row['method'], PREFIX + path)
            assert response.status_code == status, row
            if row['method'] == 'HEAD':
                assert response.content == b''
                continue
            assert response.json() == {'error': {'code': 'unauthorized' if status == 401 else 'forbidden'}}
    assert not services.scopes


@pytest.mark.parametrize('mode', ['legacy', 'authority'])
@pytest.mark.parametrize('connected', [False, True])
def test_each_static_rpc_and_mode_stays_manifest_gated(mode, connected):
    services = seeded() if connected else None
    with client_for(services, mode) as client, client.websocket_connect(PREFIX + '/api/ws') as ws:
        for method in STATIC:
            response = call(ws, method, {'principal_id': 'bob', 'profile': 'host', 'profile_id': 'bob'})
            rejection = rpc_rejection(MANIFEST, method, 1, mode=mode)
            if rejection is not None:
                assert response == rejection
            elif connected and method.startswith('i18n.'):
                assert 'result' in response
                assert 'bob' not in json.dumps(response)
            else:
                assert response['error']['code'] == -32005
    if services is not None:
        assert not services.scopes


def test_bundled_i18n_all_surfaces_and_no_host_metadata():
    with client_for(GatewayServices(None, None)) as client, client.websocket_connect(PREFIX + '/api/ws') as ws:
        languages = call(ws, 'i18n.languages')['result']['languages']
        for row in languages:
            for surface in ('core', 'desktop', 'tui'):
                result = call(ws, 'i18n.catalog', {'lang': row['id'], 'surface': surface})['result']
                assert set(result) == {'lang', 'surface', 'messages'}
                assert result['lang'] == row['id'] and result['surface'] == surface
                assert all(isinstance(k, str) and isinstance(v, str) for k, v in result['messages'].items())
                if surface == 'tui' and row['id'] != 'en':
                    assert len(result['messages']) > 500
                else:
                    assert result['messages'] == {}
                encoded = json.dumps(result)
                assert not re.search(r'API_KEY|sk-[A-Za-z0-9]{12}|/Users/|/home/|/tmp/', encoded)


def test_verified_history_and_mixed_history_fail_closed():
    services = seeded()
    services.sessions['alice', 'alice-session']['rows'] = []
    run = {'principal_id': 'alice', 'session_id': 'alice-session', 'query': 'Question',
           'run_id': 'r', 'answer_text': 'PRIVATE-CANDIDATE', 'status': 'running', 'active': True}
    services.runs.append(run)
    with client_for(services) as client:
        path = PREFIX + '/api/sessions/alice-session/messages'
        assert client.get(path).json()['messages'] == [{'role': 'user', 'content': 'Question'}]
        run.update(answer_text='VERIFIED', status='completed', active=False,
                   observations=[{'tool_name': 'verify_answer', 'scope': {'verdict': 'pass'},
                                  'validated_arguments': candidate_digest('VERIFIED')}])
        body = client.get(path).json()
        assert body['messages'][-1] == {'role': 'assistant', 'content': 'VERIFIED'}
        assert 'PRIVATE-CANDIDATE' not in json.dumps(body)
        assert client.get(PREFIX + '/api/sessions?min_messages=2').json()['total'] == 1
        services.sessions['alice', 'alice-session']['rows'] = [
            {'id': 1, 'role': 'user', 'content': 'Old'}]
        for url in (path, PREFIX + '/api/sessions', PREFIX + '/api/profiles/sessions'):
            response = client.get(url)
            assert response.status_code == 503
            assert response.json()['error']['code'] == 'handler_not_connected'


def test_rest_and_ws_preserve_the_same_native_tool_projection():
    services = seeded()
    services.sessions['alice', 'alice-session']['rows'] = []
    answer = 'Verified answer'
    services.runs.append({'principal_id': 'alice', 'session_id': 'alice-session',
        'run_id': 'run', 'created_at': 1, 'query': 'Question', 'active': False,
        'status': 'completed', 'answer_text': answer, 'native_messages': [
            {'role': 'user', 'content': 'Question'},
            {'role': 'assistant', 'tool_calls': [
                {'id': 'call', 'function': {
                    'name': 'query_yield', 'arguments': '{"lot":"L-1"}'}},
                {'id': 'non-finite', 'function': {
                    'name': 'broken_tool', 'arguments': {'value': float('nan')}}}]},
            {'role': 'tool', 'tool_call_id': 'call', 'content': 'PRIVATE RESULT BODY'},
            {'role': 'tool', 'tool_call_id': 'non-finite', 'content': 'PRIVATE NAN RESULT'},
            {'role': 'assistant', 'content': 'PRIVATE NATIVE ANSWER'}],
        'observations': [{'tool_name': 'verify_answer', 'scope': {'verdict': 'pass'},
            'validated_arguments': candidate_digest(answer)}]})

    with client_for(services) as client:
        rest = client.get(PREFIX + '/api/sessions/alice-session/messages').json()['messages']
        with client.websocket_connect(PREFIX + '/api/ws') as ws:
            wire = call(ws, 'session.history', {'session_id': 'alice-session'})['result']['messages']

    assert rest == [
        {'role': 'user', 'content': 'Question'},
        {'role': 'tool', 'name': 'query_yield', 'context': '{"lot":"L-1"}',
            'tool_call_id': 'call', 'args': {'lot': 'L-1'}},
        {'role': 'tool', 'name': 'broken_tool', 'context': '',
            'tool_call_id': 'non-finite', 'args': {}},
        {'role': 'assistant', 'content': answer}]
    assert wire[1:3] == rest[1:3]
    assert 'PRIVATE' not in json.dumps(rest)


@pytest.mark.parametrize('query,status', [('limit=-1', 422), ('offset=-1', 422),
    ('limit=bad', 422), ('order=bad', 400), ('archived=bad', 400),
    ('full=true', 503), ('cwd_prefix=/private/host', 503)])
def test_list_validation_and_unavailable_selectors(query, status):
    with client_for(seeded()) as client:
        assert client.get(PREFIX + '/api/sessions?' + query).status_code == status


def test_messages_pagination_and_profile_selectors():
    services = seeded()
    services.sessions['alice', 'alice-session']['rows'] = [
        {'id': i, 'role': 'user', 'content': str(i)} for i in range(5)]
    with client_for(services) as client:
        path = PREFIX + '/api/sessions/alice-session/messages'
        for query, wanted in [('limit=2', ['0', '1']), ('limit=2&order=latest', ['3', '4']),
                              ('limit=2&order=latest&offset=1', ['2', '3']), ('limit=0', [])]:
            body = client.get(path + '?' + query).json()
            assert [r['content'] for r in body['messages']] == wanted
        for query, code in [('limit=-1', 422), ('offset=-1', 422), ('order=bad', 400)]:
            assert client.get(path + '?' + query).status_code == code
        for route in ('/api/sessions', '/api/profiles/sessions', '/api/profiles/active'):
            own = client.get(PREFIX + route)
            selected = client.get(PREFIX + route + '?profile=bob&principal_id=bob')
            assert own.json() == selected.json()


def test_store_failure_is_not_empty_success_and_does_not_leak():
    class Broken(GatewayServices):
        async def open(self, principal):
            raise RuntimeError('PRIVATE-PASSWORD /private/host sk-private')

    with client_for(Broken(None, None)) as client:
        for path in ('/api/sessions', '/api/sessions/a/messages', '/api/profiles/sessions'):
            response = client.get(PREFIX + path)
            assert response.status_code == 500
            assert response.json() == {'detail': 'Internal server error'}


def test_pinned_rows_survive_page_and_boolean_validation():
    services = seeded()
    services.sessions['alice', 'pinned'] = {
        'id': 'pinned', 'source': 'cli', 'started_at': 0, 'title': 'Pinned', 'pinned': True, 'rows': []}
    with client_for(services) as client:
        for path in ('/api/sessions', '/api/profiles/sessions'):
            body = client.get(PREFIX + path + '?limit=1').json()
            assert [row['id'] for row in body['sessions']] == ['alice-session', 'pinned']
            assert client.get(PREFIX + path + '?full=no').status_code == 200
            assert client.get(PREFIX + path + '?full=nonsense').status_code == 422
        path = PREFIX + '/api/sessions/alice-session/messages'
        for key in ('include_compacted', 'inline_images'):
            assert client.get(path + '?' + key + '=nonsense').status_code == 422


def test_profiles_archived_pins_are_paged_but_not_counted_as_live():
    services = seeded()
    services.sessions['alice', 'archived-pin'] = {
        'id': 'archived-pin', 'source': 'cli', 'started_at': 0, 'title': 'Archived pin',
        'pinned': True, 'archived': True, 'rows': []}
    with client_for(services) as client:
        body = client.get(PREFIX + '/api/profiles/sessions?limit=1').json()
        assert body['total'] == 1
        assert [row['id'] for row in body['sessions']] == ['alice-session', 'archived-pin']


def test_compacted_selector_keeps_shared_projection_and_ws_default():
    class CompactedServices(MemoryServices):
        async def open(self, principal):
            scope = await super().open(principal)
            read = scope.sessions.get_messages

            async def get_messages(sid, **kwargs):
                rows = await read(sid, **kwargs)
                return rows if kwargs.get('include_compacted') else [
                    row for row in rows if row.get('active', True)]

            scope.sessions.get_messages = get_messages
            return scope

    services = CompactedServices()
    services.sessions = seeded().sessions
    services.sessions['alice', 'alice-session']['rows'] = [
        {'id': 1, 'role': 'user', 'content': 'Compacted', 'active': False, 'compacted': True},
        {'id': 2, 'role': 'user', 'content': 'Current', 'active': True}]
    with client_for(services) as client:
        path = PREFIX + '/api/sessions/alice-session/messages'
        assert [row['content'] for row in client.get(path).json()['messages']] == ['Current']
        assert [row['content'] for row in client.get(path + '?include_compacted=true').json()['messages']] == [
            'Compacted', 'Current']
        with client.websocket_connect(PREFIX + '/api/ws') as ws:
            history = call(ws, 'session.history', {'session_id': 'alice-session'})['result']
            assert [row['text'] for row in history['messages']] == ['Compacted', 'Current']
        services.sessions['alice', 'alice-session']['rows'].pop()
        services.runs.append({'principal_id': 'alice', 'session_id': 'alice-session',
                              'query': 'Host query', 'active': True})
        assert client.get(path).status_code == 503

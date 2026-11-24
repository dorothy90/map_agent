"""In-process WebSocket tests only; no Mongo, ports, engine or model calls."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness.gateway.auth import Principal
from harness.gateway.routes import build_gateway_router


class Resolver:
    async def resolve(self, connection):
        return Principal(connection.headers.get('x-test-user', 'alice'))


class Allow:
    async def allows(self, websocket, principal):
        return True

    async def verify_and_consume(self, websocket, principal):
        return True


def client_for(services=None, mode='legacy', resolver=None):
    app = FastAPI()
    app.include_router(build_gateway_router(
        resolver or Resolver(), origin_policy=Allow(), ticket_verifier=Allow(),
        rpc_mode=mode, services=services), prefix='/gateway-m1')
    return TestClient(app)


def call(ws, method, params=None, rid=1):
    ws.send_json({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params or {}})
    return ws.receive_json()


def test_services_none_preserves_unconnected():
    with client_for() as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        assert call(ws, 'ping')['error']['code'] == -32005


def test_connected_ping_and_manifest_policy():
    from harness.gateway.services import GatewayServices
    with client_for(GatewayServices(None, None)) as client:
        with client.websocket_connect('/gateway-m1/api/ws') as ws:
            assert call(ws, 'ping')['result'] == {'pong': True}
            assert call(ws, 'gateway.capabilities')['result'] == {'per_session_exclusive_submit': False}
            assert call(ws, 'shell.exec')['error']['code'] == -32004
            assert call(ws, 'session.delete')['error']['code'] == -32004
            assert call(ws, 'not.real')['error']['code'] == -32601
            assert call(ws, 'config.get')['error']['code'] == -32005

import asyncio
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect

from harness.gateway.auth import DenyPrincipalResolver
from harness.gateway.connection import GatewayConnection
from harness.gateway.events import new_log, replay, retain, project_run_event
from harness.gateway.execution import RpcError
from harness.gateway.publication import candidate_digest
from harness.gateway.services import GatewayScope, GatewayServices


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, ordering):
        return self

    async def to_list(self, length):
        return deepcopy(self.rows)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query):
        return Cursor([r for r in self.rows if all(r.get(k) == v for k, v in query.items())])

    async def find_one(self, query):
        return next(iter((await self.find(query).to_list(None))), None)


class MemorySessions:
    def __init__(self, owner, shared):
        self.owner, self.shared = owner, shared

    async def create_session(self, sid, source, **kwargs):
        self.shared.setdefault((self.owner, sid), {'id': sid, 'source': source,
            'started_at': 1.0, 'title': '', 'profile_name': kwargs['profile_name']})

    async def get_session(self, sid):
        row = self.shared.get((self.owner, sid))
        return row if row and not row.get('deleted') else None

    async def get_session_title(self, sid):
        return (await self.get_session(sid))['title']

    async def set_session_title(self, sid, title):
        (await self.get_session(sid))['title'] = title
        return True

    async def set_session_deleted(self, sid):
        (await self.get_session(sid))['deleted'] = True

    async def set_session_archived(self, sid, archived=True):
        (await self.get_session(sid))['archived'] = archived

    async def set_session_hidden(self, sid, hidden=True):
        (await self.get_session(sid))['hidden'] = hidden

    async def resolve_resume_session_id(self, sid):
        return (await self.get_session(sid)).get('tip', sid)

    async def get_compression_lineage(self, sid):
        return [sid]

    async def get_messages(self, sid, **kwargs):
        return (await self.get_session(sid)).get('rows', [])

    async def list_sessions_rich(self, *, include_archived=False, archived_only=False,
                                 include_pinned=False, include_hidden=False, **kwargs):
        rows = []
        for (owner, _), row in self.shared.items():
            if owner != self.owner or row.get('deleted'):
                continue
            if row.get('hidden') and not (include_hidden or archived_only):
                continue
            archived = bool(row.get('archived'))
            archive_match = archived if archived_only else include_archived or not archived
            if archive_match or (include_pinned and row.get('pinned')):
                rows.append(row)
        return rows


class MemoryEvents:
    def __init__(self, owner, shared):
        self.owner, self.shared = owner, shared

    def log(self, sid):
        return self.shared.setdefault((self.owner, sid), new_log(self.owner, sid))

    async def watermark(self, sid):
        log = self.log(sid)
        return {'replay_epoch': log['epoch'], 'last_sequence': log['seq']}

    async def since(self, sid, epoch, after_seq=0):
        return replay(self.log(sid), epoch, after_seq)

    def append(self, sid, name='message.start', payload=None):
        retain(self.log(sid), {'type': name, 'session_id': sid, 'payload': payload or {}})


class MemoryPending:
    def __init__(self, owner, rows):
        self.owner, self.rows = owner, rows
        self.pending_prompts = Rows(rows)
        self.cancelled = []

    async def legacy_request(self, sid, generation, prompt_id):
        row = await self.pending_prompts.find_one({'principal_id': self.owner,
            'session_id': sid, 'prompt_id': prompt_id, 'status': 'pending'})
        if not row:
            return None
        return {'jsonrpc': '2.0', 'id': prompt_id, 'method': row['kind'],
                'params': {'session_id': sid, 'questions': [{'qid': 'q', 'question': 'Choose'}]}}

    async def cancel(self, prompt_id, reason):
        self.cancelled.append(prompt_id)
        for row in self.rows:
            if row['principal_id'] == self.owner and row['prompt_id'] == prompt_id:
                row['status'] = 'cancelled'


class MemoryExecution:
    def __init__(self, owner, services, sessions):
        self.principal_id, self.profile_id = owner, 'server-profile'
        self.services, self.sessions = services, sessions
        self.events = MemoryEvents(owner, services.logs)
        self.pending = MemoryPending(owner, services.prompts)
        self.store = SimpleNamespace(runs=Rows(services.runs))
        self.calls = []

    async def _session(self, sid, params=None):
        if not await self.sessions.get_session(sid):
            raise RpcError('not_found')

    async def _active(self, sid):
        return next((r for r in self.services.runs if r['principal_id'] == self.principal_id
                     and r['session_id'] == sid and r.get('active')), None)

    async def snapshot(self, sid):
        return {'active_run': await self._active(sid), 'prompts': await self._current_prompts(sid),
                **await self.events.watermark(sid)}

    async def handle(self, sid):
        return {'authority_epoch': 0, 'revision': 0, 'execution_generation': 0}

    async def _current_prompts(self, sid):
        return [r for r in self.services.prompts if r['principal_id'] == self.principal_id
                and r['session_id'] == sid and r['status'] == 'pending']

    async def _pending_row(self, sid, prompt_id, generation=None):
        return next((r for r in await self._current_prompts(sid) if r['prompt_id'] == prompt_id), None)

    async def _publish_pending(self):
        pass

    async def request_answer(self, sid, params):
        row = await self._pending_row(sid, params['id'])
        if row:
            row['status'] = 'resolved'
            row['result'] = params['result']
        return {'status': 'ok' if row else 'expired'}

    async def interrupt(self, sid, params, mode):
        await self._session(sid)
        return {'status': 'not_interrupted'}

    async def rpc(self, method, sid, params, **kwargs):
        self.calls.append((method, sid, deepcopy(params), kwargs))
        await self._session(sid)
        if self.services.failure:
            raise RuntimeError('secret database password must not appear')
        if method == 'prompt.submit':
            if kwargs['mode'] == 'legacy':
                return {'error': {'code': -32004, 'message': 'unsupported'}}
            if self.services.slow:
                await asyncio.sleep(0.15)
            self.events.append(sid)
            result = {'run_id': 'run', 'status': 'started'}
        elif method == 'request.answer':
            result = await self.request_answer(sid, params)
        elif method == 'session.events.since':
            if kwargs['mode'] == 'legacy':
                mark = await self.events.watermark(sid)
                result = await self.events.since(sid, mark['replay_epoch'], params.get('last_seen', 0))
                result['open_requests'] = [await self.pending.legacy_request(sid, 0, r['prompt_id'])
                                          for r in await self._current_prompts(sid)]
            else:
                result = await self.events.since(sid, params.get('replay_epoch'), params.get('last_sequence', 0))
        else:
            result = {'output': 'ok'}
        return {'result': result}


class MemoryServices(GatewayServices):
    poll_interval = 0.005
    max_requests = 32

    def __init__(self):
        super().__init__(None, None, poll_interval=0.005)
        self.sessions, self.logs, self.prompts, self.runs = {}, {}, [], []
        self.scopes = []
        self.failure = self.slow = False

    async def open(self, principal):
        sessions = MemorySessions(principal.user_id, self.sessions)
        scope = GatewayScope(sessions, MemoryExecution(principal.user_id, self, sessions))
        self.scopes.append(scope)
        return scope


def receive_id(ws, identity, events=None):
    for _ in range(100):
        frame = ws.receive_json()
        if frame.get('id') == identity and ('result' in frame or 'error' in frame):
            return frame
        if events is not None:
            events.append(frame)
    raise AssertionError('RPC response missing')


def send_call(ws, method, params, rid):
    ws.send_json({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params})


@pytest.mark.parametrize('mode', ['legacy', 'authority'])
def test_sessions_create_resume_title_list_detach_and_principal_boundary(mode):
    services = MemoryServices()
    with client_for(services, mode) as client:
        with client.websocket_connect('/gateway-m1/api/ws') as ws:
            created = call(ws, 'session.create', {'principal_id': 'bob', 'profile': 'host'})['result']
            sid = created['session_id']
            assert ('alice', sid) in services.sessions and ('bob', sid) not in services.sessions
            assert services.sessions['alice', sid]['profile_name'] == 'server-profile'
            assert created['messages'] == []
            resumed = call(ws, 'session.resume', {'session_id': sid})['result']
            if mode == 'authority':
                assert set(('subscription_id', 'prompts', 'pending', 'replay_epoch')) <= resumed.keys()
                assert call(ws, 'session.list', {'scope': 'stored'})['result']['sessions'][0]['id'] == sid
                assert call(ws, 'session.detach', {'session_id': sid, 'subscription_id': 'stale'})['result']['detached'] is False
                assert call(ws, 'session.detach', {'session_id': sid,
                    'subscription_id': resumed['subscription_id']})['result']['detached'] is True
            else:
                assert call(ws, 'session.title', {'session_id': sid, 'title': 'Name'})['result']['title'] == 'Name'
                assert call(ws, 'session.list')['result']['sessions'][0]['title'] == 'Name'
                assert call(ws, 'session.most_recent')['result']['session_id'] == sid
                assert call(ws, 'session.history', {'session_id': sid})['result'] == {'count': 0, 'messages': []}
                assert call(ws, 'session.close', {'session_id': sid})['result']['closed'] is True
        with client.websocket_connect('/gateway-m1/api/ws', headers={'x-test-user': 'bob'}) as ws:
            foreign = call(ws, 'session.resume', {'session_id': sid})['error']
            missing = call(ws, 'session.resume', {'session_id': 'missing'})['error']
            assert foreign == missing


def test_authority_mode_does_not_fall_back_to_legacy():
    with client_for(MemoryServices(), 'authority') as client:
        with client.websocket_connect('/gateway-m1/api/ws') as ws:
            for method in ('ping', 'client.capabilities', 'session.history', 'approval.pending', 'request.answer'):
                assert call(ws, method)['error']['code'] == -32601
            assert call(ws, 'session.stats', {'session_id': 's'})['error']['code'] == -32005


def test_connected_authentication_and_disabled_ws():
    with client_for(MemoryServices(), resolver=DenyPrincipalResolver()) as client:
        assert client.get('/gateway-m1/api/sessions').status_code == 401
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect('/gateway-m1/api/ws'):
                pass
        assert caught.value.code == 1008
    with client_for(MemoryServices()) as client:
        for path in ('/api/events', '/api/pty', '/api/pub'):
            with pytest.raises(WebSocketDisconnect) as caught:
                with client.websocket_connect('/gateway-m1' + path):
                    pass
            assert caught.value.code == 1008


def test_slow_submit_does_not_block_another_rpc_or_event_pump():
    services = MemoryServices()
    services.slow = True
    with client_for(services, 'authority') as client:
        with client.websocket_connect('/gateway-m1/api/ws') as ws:
            sid = call(ws, 'session.create')['result']['session_id']
            send_call(ws, 'prompt.submit', {'session_id': sid, 'text': 'hello', 'submission_id': 'x'}, 2)
            send_call(ws, 'session.info', {'session_id': sid}, 3)
            assert ws.receive_json()['id'] == 3
            frames = []
            assert receive_id(ws, 2, frames)['result']['run_id'] == 'run'
            if not frames:
                frames.append(ws.receive_json())
            assert frames[0]['params']['seq'] == 1
            send_call(ws, 'session.info', {'session_id': sid}, 4)
            assert ws.receive_json()['id'] == 4  # no duplicate pushed event


def test_exception_is_internal_error_without_secret_and_connection_survives():
    services = MemoryServices()
    with client_for(services) as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        sid = call(ws, 'session.create')['result']['session_id']
        services.failure = True
        response = call(ws, 'session.status', {'session_id': sid})
        assert response['error'] == {'code': -32603, 'message': 'Internal error'}
        assert 'password' not in json.dumps(response)
        assert call(ws, 'ping')['result']['pong'] is True


def test_capability_and_legacy_response_frames_are_connection_bound():
    services = MemoryServices()
    with client_for(services) as client:
        with client.websocket_connect('/gateway-m1/api/ws') as a:
            sid = call(a, 'session.create')['result']['session_id']
            services.prompts.append({'principal_id': 'alice', 'session_id': sid, 'prompt_id': 'durable',
                'status': 'pending', 'kind': 'clarify', 'execution_generation': 0})
            assert call(a, 'client.capabilities', {'server_requests': True})['result']['server_requests'] == ['approval', 'clarify']
            question = a.receive_json()
            assert question['method'] == 'clarify'
            with client.websocket_connect('/gateway-m1/api/ws') as b:
                b.send_json({'jsonrpc': '2.0', 'id': question['id'], 'result': {'answers': {'q': 'forged'}}})
                assert call(b, 'ping')['result']['pong']
                assert services.prompts[0]['status'] == 'pending'
            with client.websocket_connect('/gateway-m1/api/ws', headers={'x-test-user': 'bob'}) as b:
                b.send_json({'jsonrpc': '2.0', 'id': question['id'], 'result': {'answers': {'q': 'forged'}}})
                assert call(b, 'request.answer', {'id': 'durable', 'result': {}})['result']['status'] == 'expired'
                assert services.prompts[0]['status'] == 'pending'
            a.send_json({'jsonrpc': '2.0', 'id': question['id'], 'result': {'answers': {'q': 'yes'}}})
            assert call(a, 'ping')['result']['pong']
            assert services.prompts[0]['result'] == {'answers': {'q': 'yes'}}
            a.send_json({'jsonrpc': '2.0', 'id': question['id'], 'result': {'answers': {'q': 'again'}}})
            assert call(a, 'ping')['result']['pong']
            assert services.prompts[0]['result']['answers']['q'] == 'yes'


@pytest.mark.parametrize('as_error', [True, False])
def test_disconnect_or_error_cancels_open_legacy_request(as_error):
    services = MemoryServices()
    with client_for(services) as client:
        with client.websocket_connect('/gateway-m1/api/ws') as ws:
            sid = call(ws, 'session.create')['result']['session_id']
            call(ws, 'client.capabilities', {'server_requests': True})
            services.prompts.append({'principal_id': 'alice', 'session_id': sid, 'prompt_id': 'durable',
                'status': 'pending', 'kind': 'approval', 'execution_generation': 0})
            question = ws.receive_json()
            if as_error:
                ws.send_json({'jsonrpc': '2.0', 'id': question['id'], 'error': {'code': -32601, 'message': 'unsupported'}})
                assert call(ws, 'ping')['result']['pong']
        assert services.prompts[0]['status'] == 'cancelled'


class Sink:
    def __init__(self):
        self.frames = []

    async def send_json(self, frame):
        self.frames.append(frame)


MANIFEST = json.loads((Path(__file__).parents[1] / 'gateway/manifest.json').read_text())


@pytest.mark.parametrize('gap_kind', ['truncated', 'epoch', 'hole'])
def test_pump_independent_ordering_gap_and_cleanup(gap_kind):
    async def scenario():
        services = MemoryServices()
        a = GatewayConnection(Sink(), Principal('alice'), services, MANIFEST, 'authority')
        b = GatewayConnection(Sink(), Principal('alice'), services, MANIFEST, 'authority')
        scope = await a.context()
        await scope.sessions.create_session('s', 'gui', profile_name='default')
        for peer in (a, b):
            await peer.subscribe('s')
            peer.subscriptions['s'].ready = True
        scope.execution.events.append('s')
        scope.execution.events.append('s')
        await a.pump_once()
        await a.pump_once()
        await b.pump_once()
        assert [f['params']['seq'] for f in a.ws.frames] == [1, 2]
        assert b.ws.frames == a.ws.frames
        log = scope.execution.events.log('s')
        if gap_kind == 'epoch':
            log['epoch'] = 'new'
        elif gap_kind == 'truncated':
            log['evicted_through'] = 3
            log['seq'] = 3
        else:
            log['seq'] = 3  # a missing retained frame followed by seq=4
            scope.execution.events.append('s')
        await a.pump_once()
        assert a.ws.frames[-1]['params']['type'] == 'session.replay_gap'
        assert 'seq' not in a.ws.frames[-1]['params']
        count = len(a.ws.frames)
        await a.detach('s')
        scope.execution.events.append('s')
        await a.pump_once()
        assert len(a.ws.frames) == count and not a.subscriptions
    asyncio.run(scenario())


def test_reconnect_replay_and_snapshot_pending_restore():
    services = MemoryServices()
    with client_for(services, 'authority') as client:
        with client.websocket_connect('/gateway-m1/api/ws') as ws:
            created = call(ws, 'session.create')['result']
            sid = created['session_id']
            call(ws, 'prompt.submit', {'session_id': sid, 'text': 'hello', 'submission_id': 'x'})
        services.prompts.append({'principal_id': 'alice', 'session_id': sid, 'prompt_id': 'p',
            'status': 'pending', 'kind': 'clarify', 'execution_generation': 0})
        with client.websocket_connect('/gateway-m1/api/ws') as ws:
            replayed = call(ws, 'session.events.since', {'session_id': sid,
                'replay_epoch': created['replay_epoch'], 'last_sequence': 0})['result']
            assert [f['seq'] for f in replayed['events']] == [1]
            resumed = call(ws, 'session.resume', {'session_id': sid})['result']
            assert resumed['prompts'][0]['prompt_id'] == 'p'
            assert resumed['last_sequence'] == 1


def test_history_and_push_never_project_unverified_body():
    services = MemoryServices()
    with client_for(services) as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        sid = call(ws, 'session.create')['result']['session_id']
        services.sessions['alice', sid]['rows'] = [{'id': 1, 'role': 'assistant', 'content': 'SECRET-CANDIDATE'}]
        assert call(ws, 'session.history', {'session_id': sid})['result']['messages'] == []
        run = {'principal_id': 'alice', 'session_id': sid, 'query': 'hello',
               'active': True, 'status': 'running', 'answer_text': 'SECRET-CANDIDATE'}
        services.runs.append(run)
        assert 'SECRET-CANDIDATE' not in json.dumps(call(ws, 'session.history', {'session_id': sid}))
        for event_type in ('message.delta', 'message.interim', 'message.complete'):
            assert project_run_event(run, {'type': event_type, 'payload': {'text': 'SECRET-CANDIDATE'}}) is None


def test_history_projects_native_tools_in_order_without_cumulative_duplicates():
    services = MemoryServices()
    with client_for(services) as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        sid = call(ws, 'session.create')['result']['session_id']
        long_args = {'query': '수율 ' * 30, 'limit': 5}
        first_history = [
            {'role': 'user', 'content': 'first question'},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'call-1', 'function': {'name': 'search_yield',
                    'arguments': json.dumps(long_args, ensure_ascii=False)}},
                {'id': 'call-2', 'function': {'name': 'read_result'}}]},
            {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'PRIVATE FULL RESULT ONE'},
            {'role': 'tool', 'tool_call_id': 'call-2', 'content': 'PRIVATE FULL RESULT TWO'},
            {'role': 'assistant', 'content': 'PRIVATE NATIVE ANSWER ONE'}]
        second_history = first_history + [
            {'role': 'user', 'content': 'second question'},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'call-3', 'function': {'name': 'query_lot', 'arguments': '{bad json'}}]},
            {'role': 'tool', 'tool_call_id': 'call-3', 'content': 'PRIVATE FULL RESULT THREE'},
            {'role': 'assistant', 'content': 'PRIVATE NATIVE ANSWER TWO'}]
        for number, (query, answer, history) in enumerate((
                ('first question', 'verified one', first_history),
                ('second question', 'verified two', second_history))):
            services.runs.append({'principal_id': 'alice', 'session_id': sid,
                'run_id': f'run-{number}', 'created_at': number, 'query': query,
                'active': False, 'status': 'completed', 'answer_text': answer,
                'native_messages': history,
                'observations': [{'tool_name': 'verify_answer', 'scope': {'verdict': 'pass'},
                    'validated_arguments': candidate_digest(answer)}]})

        messages = call(ws, 'session.history', {'session_id': sid})['result']['messages']
        assert [row['role'] for row in messages] == [
            'user', 'tool', 'tool', 'assistant', 'user', 'tool', 'assistant']
        assert [row.get('text') for row in messages if row['role'] != 'tool'] == [
            'first question', 'verified one', 'second question', 'verified two']
        assert messages[1]['name'] == 'search_yield'
        assert messages[1]['tool_call_id'] == 'call-1'
        assert messages[1]['args'] == long_args
        assert 0 < len(messages[1]['context']) <= 80
        assert messages[2] == {'role': 'tool', 'name': 'read_result', 'context': '',
            'tool_call_id': 'call-2', 'args': {}}
        assert messages[5] == {'role': 'tool', 'name': 'query_lot', 'context': '',
            'tool_call_id': 'call-3', 'args': {}}
        encoded = json.dumps(messages)
        assert 'PRIVATE FULL RESULT' not in encoded
        assert 'PRIVATE NATIVE ANSWER' not in encoded


def test_history_uses_same_verified_artifact_text_as_live_completion():
    services = MemoryServices()
    with client_for(services) as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        sid = call(ws, 'session.create')['result']['session_id']
        answer = 'verified answer'
        run = {'principal_id': 'alice', 'session_id': sid, 'run_id': 'run-artifacts',
            'created_at': 1, 'query': 'show artifacts', 'active': False,
            'status': 'completed', 'answer_text': answer, 'observations': [
                {'tool_name': 'run_python', 'artifact_refs': [
                    {'artifact_id': 'map.png', 'title': 'Wafer map', 'mime': 'image/png'},
                    {'artifact_id': 'report.html', 'title': 'HTML report', 'mime': 'text/html'},
                    {'artifact_id': 'analysis.py', 'title': '분석 코드', 'mime': 'text/plain'},
                ]},
                {'tool_name': 'verify_answer', 'scope': {'verdict': 'pass'},
                    'validated_arguments': candidate_digest(answer)},
            ]}
        services.runs.append(run)

        history_text = call(ws, 'session.history', {'session_id': sid})['result']['messages'][-1]['text']
        live_text = project_run_event(run, {'type': 'run_finished', 'payload': {}})['payload']['text']
        assert history_text == live_text
        assert '![Wafer map](/harness/artifacts/map.png?' in history_text
        assert '[HTML report](/harness/artifacts/report.html?' in history_text
        assert '분석 코드' not in history_text


def test_history_and_info_restore_completed_run_usage_and_duration():
    services = MemoryServices()
    with client_for(services, 'authority') as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        sid = call(ws, 'session.create')['result']['session_id']
        answer = 'verified answer'
        services.runs.append({
            'principal_id': 'alice', 'session_id': sid, 'run_id': 'run-usage',
            'created_at': 1, 'query': 'summarize yield', 'active': False,
            'status': 'completed', 'answer_text': answer,
            'native_usage': {'input_tokens': 98714, 'output_tokens': 6599,
                             'total_tokens': 105313, 'api_calls': 6},
            'usage': {'active_seconds': 46.057},
            'observations': [
                {'tool_name': 'verify_answer', 'scope': {'verdict': 'pass'},
                 'validated_arguments': candidate_digest(answer)},
            ],
        })
        resumed = call(ws, 'session.resume', {'session_id': sid})['result']
        assistant = resumed['messages'][-1]
        assert assistant['text'] == answer
        assert assistant['interim'] is False
        assert assistant['usage'] == {'input': 98714, 'output': 6599, 'total': 105313, 'calls': 6}
        assert assistant['duration_s'] == 46
        assert resumed['info']['usage'] == assistant['usage']
        info = call(ws, 'session.info', {'session_id': sid})['result']
        assert info['usage'] == assistant['usage']
        rest = client.get('/gateway-m1/api/sessions/' + sid + '/messages?include_compacted=true').json()
        rest_assistant = [row for row in rest['messages'] if row['role'] == 'assistant'][-1]
        assert rest_assistant['content'] == answer
        assert rest_assistant['interim'] is False
        assert rest_assistant['usage'] == assistant['usage']
        assert rest_assistant['duration_s'] == 46


def test_session_info_omits_usage_when_counters_are_incomplete():
    services = MemoryServices()
    with client_for(services, 'authority') as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        sid = call(ws, 'session.create')['result']['session_id']
        services.runs.append({'principal_id': 'alice', 'session_id': sid, 'query': 'hello',
            'active': False, 'status': 'completed'})
        response = call(ws, 'session.info', {'session_id': sid})
        assert 'result' in response
        assert 'usage' not in response['result']


def test_history_keeps_native_tools_when_final_answer_is_unverified():
    services = MemoryServices()
    with client_for(services) as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        sid = call(ws, 'session.create')['result']['session_id']
        services.runs.append({'principal_id': 'alice', 'session_id': sid,
            'run_id': 'running', 'created_at': 1, 'query': 'question', 'active': True,
            'status': 'running', 'answer_text': 'PRIVATE DRAFT', 'native_messages': [
                {'role': 'user', 'content': 'question'},
                {'role': 'assistant', 'tool_calls': [
                    {'id': 'missing-call', 'function': {'name': 'fallback_tool', 'arguments': None}}]},
                {'role': 'tool', 'tool_call_id': 'missing-call', 'content': 'PRIVATE RESULT'}]})

        messages = call(ws, 'session.history', {'session_id': sid})['result']['messages']
        assert messages == [
            {'role': 'user', 'text': 'question'},
            {'role': 'tool', 'name': 'fallback_tool', 'context': '',
                'tool_call_id': 'missing-call', 'args': {}}]
        assert 'PRIVATE' not in json.dumps(messages)


def test_history_restores_only_published_tool_commentary_in_order():
    services = MemoryServices()
    with client_for(services) as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        sid = call(ws, 'session.create')['result']['session_id']
        public = 'Reading the stored rows.'
        native = [{'role': 'user', 'content': 'question'}]
        for number, text in enumerate(('Unpublished tool draft', public, f'\n{public} ', public)):
            call_id = f'call-{number}'
            native.extend([
                {'role': 'assistant', 'content': text, 'reasoning': 'private reasoning',
                 'tool_calls': [{'id': call_id, 'function': {'name': 'read_result'}}]},
                {'role': 'tool', 'tool_call_id': call_id, 'content': 'private raw rows'}])
        native.append({'role': 'assistant', 'content': 'Unsettled answer'})
        services.runs.append({'principal_id': 'alice', 'session_id': sid, 'run_id': 'run',
            'created_at': 1, 'query': 'question', 'active': False, 'status': 'completed',
            'answer_text': 'Published answer', 'public_interims': [public, 'Unsettled answer', f' {public}\n'],
            'native_messages': native, 'observations': [{'tool_name': 'verify_answer',
                'scope': {'verdict': 'pass'}, 'validated_arguments': candidate_digest('Published answer')}]})

        rows = call(ws, 'session.history', {'session_id': sid})['result']['messages']
        assert [r.get('text', r.get('tool_call_id')) for r in rows] == [
            'question', 'call-0', public, 'call-1', public, 'call-2', 'call-3', 'Published answer']
        assert rows[2]['interim'] is rows[4]['interim'] is True
        assert 'private' not in json.dumps(rows)
        restored = client.get(f'/gateway-m1/api/sessions/{sid}/messages').json()['messages']
        assert restored[2] == {'role': 'assistant', 'content': public, 'interim': True}


def test_history_preserves_public_tool_completion_status_without_result_data():
    services = MemoryServices()
    with client_for(services) as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        sid = call(ws, 'session.create')['result']['session_id']
        native = [{'role': 'user', 'content': 'question'}]
        events = []
        for call_id, state in [('one', 'error'), ('two', 'partial'), ('three', 'success')]:
            native.extend([
                {'role': 'assistant', 'tool_calls': [{'id': call_id, 'function': {'name': 'render_yield_table'}}]},
                {'role': 'tool', 'tool_call_id': call_id, 'content': 'private result'}])
            events.append({'type': 'tool_finished', 'payload': {'tool_id': call_id, 'name': 'render_yield_table',
                'result': {'status': state, 'rows': ['private row']}, 'summary': 'private detail'}})
        services.runs.append({'principal_id': 'alice', 'session_id': sid, 'run_id': 'run',
            'created_at': 1, 'query': 'question', 'active': False, 'status': 'completed',
            'events': events, 'native_messages': native})
        history = client.get(f'/gateway-m1/api/sessions/{sid}/messages').json()['messages']
        assert [(row['tool_call_id'], json.loads(row['content'])) for row in history if row['role'] == 'tool'] == [
            ('one', {'status': 'error'}), ('two', {'status': 'partial'}), ('three', {'status': 'success'})]
        assert 'private' not in json.dumps(history)


def test_history_projects_ordered_gateway_tool_rows_without_result_bodies():
    services = MemoryServices()
    with client_for(services) as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        sid = call(ws, 'session.create')['result']['session_id']
        services.sessions['alice', sid]['rows'] = [
            {'id': 1, 'role': 'user', 'content': 'stored question'},
            {'id': 2, 'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'stored-call', 'function': {
                    'name': 'stored_tool', 'arguments': '{"value":7}'}}]},
            {'id': 3, 'role': 'tool', 'tool_call_id': 'stored-call',
                'tool_name': 'stored_tool', 'content': 'PRIVATE STORED RESULT'}]

        messages = call(ws, 'session.history', {'session_id': sid})['result']['messages']
        assert messages == [
            {'role': 'user', 'text': 'stored question', 'row_id': 1},
            {'role': 'tool', 'name': 'stored_tool', 'context': '{"value":7}',
                'tool_call_id': 'stored-call', 'args': {'value': 7}}]
        assert 'PRIVATE' not in json.dumps(messages)


def test_history_scopes_cumulative_native_tools_by_goal_request():
    services = MemoryServices()
    with client_for(services) as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        sid = call(ws, 'session.create')['result']['session_id']
        first = [
            {'role': 'user', 'content': 'original'},
            {'role': 'assistant', 'tool_calls': [{'id': 'old', 'function': {
                'name': 'old_tool', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'old', 'content': 'old result'}]
        services.runs.extend([
            {'principal_id': 'alice', 'session_id': sid, 'run_id': 'one', 'created_at': 1,
                'query': 'original', 'active': False, 'status': 'partial', 'native_messages': first},
            {'principal_id': 'alice', 'session_id': sid, 'run_id': 'two', 'created_at': 2,
                'query': 'latest edit', 'goal_request': 'original\nlatest edit', 'active': True,
                'status': 'running', 'native_messages': first + [
                    {'role': 'user', 'content': 'original\nlatest edit'},
                    {'role': 'assistant', 'tool_calls': [{'id': 'new', 'function': {
                        'name': 'new_tool', 'arguments': '{}'}}]},
                    {'role': 'tool', 'tool_call_id': 'new', 'content': 'new result'}]}])

        messages = call(ws, 'session.history', {'session_id': sid})['result']['messages']
        assert [row.get('tool_call_id') for row in messages if row['role'] == 'tool'] == ['old', 'new']
        assert [row['text'] for row in messages if row['role'] == 'user'] == ['original', 'latest edit']


def test_history_tolerates_malformed_stored_tool_metadata():
    services = MemoryServices()
    with client_for(services) as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        sid = call(ws, 'session.create')['result']['session_id']
        services.sessions['alice', sid]['rows'] = [
            {'id': 1, 'role': 'user', 'content': 'question'},
            {'id': 2, 'role': 'assistant', 'tool_calls': 7},
            {'id': 3, 'role': 'assistant', 'tool_calls': [
                {'id': ['not', 'hashable'], 'function': {'name': 'ignored', 'arguments': {}}},
                {'id': 'bad-args', 'function': {
                    'name': 'stored_tool', 'arguments': {'when': datetime(2026, 1, 1)}}}]},
            {'id': 4, 'role': 'tool', 'tool_call_id': 'bad-args', 'content': 'PRIVATE RESULT'}]

        messages = call(ws, 'session.history', {'session_id': sid})['result']['messages']
        assert messages[-1] == {'role': 'tool', 'name': 'stored_tool', 'context': '',
            'tool_call_id': 'bad-args', 'args': {}}


@pytest.mark.parametrize('bad', [[], True, 'params'])
def test_bad_params_and_invalid_frames_do_not_kill_connection(bad):
    with client_for(MemoryServices()) as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        send_call(ws, 'ping', bad, 1)
        assert ws.receive_json()['error']['code'] == 4000
        ws.send_json({'jsonrpc': '2.0', 'method': 'ping', 'id': []})
        assert ws.receive_json()['error']['code'] == -32600
        ws.send_text('{')
        assert ws.receive_json()['error']['code'] == -32700
        assert call(ws, 'ping')['result']['pong']


def test_liveness_reply_cannot_arm_an_unfinished_snapshot_subscription():
    async def scenario():
        services = MemoryServices()
        peer = GatewayConnection(Sink(), Principal('alice'), services, MANIFEST, 'legacy')
        scope = await peer.context()
        await scope.sessions.create_session('s', 'gui', profile_name='default')
        subscribed, finish = asyncio.Event(), asyncio.Event()

        async def unfinished_snapshot():
            await peer.subscribe('s')
            subscribed.set()
            await finish.wait()

        task = asyncio.create_task(unfinished_snapshot())
        await subscribed.wait()
        await peer.process({'id': 1, 'method': 'ping', 'params': {}})
        assert not peer.subscriptions['s'].ready
        finish.set()
        await task
    asyncio.run(scenario())


def test_one_viewer_disconnect_does_not_cancel_another_viewers_prompt():
    async def scenario():
        services = MemoryServices()
        a = GatewayConnection(Sink(), Principal('alice'), services, MANIFEST, 'legacy')
        b = GatewayConnection(Sink(), Principal('alice'), services, MANIFEST, 'legacy')
        scope = await a.context()
        await scope.sessions.create_session('s', 'gui', profile_name='default')
        services.prompts.append({'principal_id': 'alice', 'session_id': 's', 'prompt_id': 'p',
            'status': 'pending', 'kind': 'clarify', 'execution_generation': 0})
        for peer in (a, b):
            peer.server_requests = True
            await peer.subscribe('s')
            assert await peer.open_requests('s')
        assert set(a.requests).isdisjoint(b.requests)
        await a.release_requests()
        assert services.prompts[0]['status'] == 'pending'
        await b.release_requests()
        assert services.prompts[0]['status'] == 'cancelled'
    asyncio.run(scenario())


def test_detach_cannot_be_overtaken_by_legacy_pending_read():
    async def scenario():
        services = MemoryServices()
        peer = GatewayConnection(Sink(), Principal('alice'), services, MANIFEST, 'legacy')
        scope = await peer.context()
        await scope.sessions.create_session('s', 'gui', profile_name='default')
        services.prompts.append({'principal_id': 'alice', 'session_id': 's', 'prompt_id': 'p',
            'status': 'pending', 'kind': 'clarify', 'execution_generation': 0})
        peer.server_requests = True
        await peer.subscribe('s')
        peer.subscriptions['s'].ready = True
        entered, release = asyncio.Event(), asyncio.Event()
        original = scope.execution._current_prompts

        async def delayed(sid):
            entered.set()
            await release.wait()
            return await original(sid)

        scope.execution._current_prompts = delayed
        pumping = asyncio.create_task(peer.pump_once())
        await entered.wait()

        async def detach():
            await peer.detach('s')
            return len(peer.ws.frames)

        detaching = asyncio.create_task(detach())
        await asyncio.sleep(0)
        release.set()
        at_detach = await detaching
        await pumping
        assert len(peer.ws.frames) == at_detach
        assert not peer.requests and not services._legacy_readers
    asyncio.run(scenario())


def test_failed_snapshot_then_retry_can_deliver_events(monkeypatch):
    from harness.gateway.session_rpc import SessionRPC

    async def scenario():
        services = MemoryServices()
        peer = GatewayConnection(Sink(), Principal('alice'), services, MANIFEST, 'authority')
        scope = await peer.context()
        await scope.sessions.create_session('s', 'gui', profile_name='default')
        original = SessionRPC.info

        async def broken(self, sid):
            raise RuntimeError('temporary DB failure')

        monkeypatch.setattr(SessionRPC, 'info', broken)
        await asyncio.create_task(peer.process({'id': 1, 'method': 'session.resume', 'params': {'session_id': 's'}}))
        assert peer.ws.frames[-1]['error']['code'] == -32603
        monkeypatch.setattr(SessionRPC, 'info', original)
        await asyncio.create_task(peer.process({'id': 2, 'method': 'session.resume', 'params': {'session_id': 's'}}))
        assert 'result' in peer.ws.frames[-1]
        scope.execution.events.append('s')
        await peer.pump_once()
        assert peer.ws.frames[-1].get('method') == 'event'
    asyncio.run(scenario())


def test_usage_reports_only_observed_counters_and_refuses_incomplete_usage():
    services = MemoryServices()
    with client_for(services) as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        sid = call(ws, 'session.create')['result']['session_id']
        assert call(ws, 'session.usage', {'session_id': sid})['result'] == {'input': 0, 'output': 0, 'total': 0, 'calls': 0}
        services.runs.append({'principal_id': 'alice', 'session_id': sid, 'query': 'hello', 'active': False,
            'status': 'completed', 'native_usage': {'input_tokens': 12, 'output_tokens': 3, 'total_tokens': 15, 'api_calls': 2}})
        assert call(ws, 'session.usage', {'session_id': sid})['result'] == {'input': 12, 'output': 3, 'total': 15, 'calls': 2}
        services.runs[0]['active'] = True
        assert call(ws, 'session.usage', {'session_id': sid})['error']['code'] == -32005
        services.runs[0]['active'] = False
        observed = services.runs[0]['native_usage']
        for key in observed:
            for invalid in (None, -1, True, '1', 1.5):
                services.runs[0]['native_usage'] = {**observed, key: invalid}
                assert call(ws, 'session.usage', {'session_id': sid})['error']['code'] == -32005
            services.runs[0]['native_usage'] = {k: v for k, v in observed.items() if k != key}
            assert call(ws, 'session.usage', {'session_id': sid})['error']['code'] == -32005
        del services.runs[0]['native_usage']
        assert call(ws, 'session.usage', {'session_id': sid})['error']['code'] == -32005


def test_pump_retries_admission_lock_contention_without_closing_peer():
    async def scenario():
        services = MemoryServices()
        peer = GatewayConnection(Sink(), Principal('alice'), services, MANIFEST, 'legacy')
        retried = asyncio.Event()
        calls = 0

        async def busy_once():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RpcError('session_busy')
            retried.set()

        peer.pump_once = busy_once
        task = asyncio.create_task(peer.pump())
        try:
            await asyncio.wait_for(retried.wait(), 0.5)
            assert calls >= 2 and not peer.closed
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_history_reopens_clarify_result_before_final_answer():
    services = MemoryServices()
    with client_for(services) as client, client.websocket_connect('/gateway-m1/api/ws') as ws:
        sid = call(ws, 'session.create')['result']['session_id']
        result = {'responses': [{'question': 'Criterion?', 'choices_offered': None,
                  'status': 'answered', 'user_response': '80%'}], 'outcome': 'submitted'}
        event = {'type': 'tool_finished', 'payload': {'name': 'clarify', 'tool_id': 'prompt',
                 'args': {'questions': [{'question': 'Criterion?', 'choices': [], 'multi_select': False}]},
                 'result': result}}
        services.runs.append({'principal_id': 'alice', 'session_id': sid, 'run_id': 'r',
            'created_at': 1, 'query': 'question', 'status': 'completed', 'active': False,
            'native_messages': [
                {'role': 'user', 'content': 'question'},
                {'role': 'assistant', 'tool_calls': [{'id': 't1', 'function': {'name': 'query_db', 'arguments': '{}'}}]},
                {'role': 'tool', 'tool_call_id': 't1', 'name': 'query_db'},
            ], 'events': [event, event], 'answer_text': 'final',
            'observations': [{'tool_name': 'verify_answer', 'scope': {'verdict': 'pass'},
                              'validated_arguments': candidate_digest('final')}]})
        messages = call(ws, 'session.history', {'session_id': sid})['result']['messages']
        assert [m['role'] for m in messages] == ['user', 'tool', 'tool', 'assistant']
        assert messages[1]['tool_call_id'] == 'prompt'
        assert messages[1]['name'] == 'clarify'
        assert json.loads(messages[1]['content']) == result
        assert messages[2]['name'] == 'query_db'
        assert call(ws, 'session.resume', {'session_id': sid})['result']['messages'] == messages


def test_authority_title_get_set():
    with client_for(MemoryServices(), 'authority') as client:
        with client.websocket_connect('/gateway-m1/api/ws') as ws:
            sid = call(ws, 'session.create')['result']['session_id']
            assert call(ws, 'session.title', {'session_id': sid, 'title': 'Name'})['result'] == {'title': 'Name', 'pending': False}
            assert call(ws, 'session.title', {'session_id': sid})['result'] == {'title': 'Name', 'session_key': sid}


@pytest.mark.parametrize('method,params,expected', [
    ('session.delete', {}, {'deleted': 'saved'}),
    ('session.archive', {}, {'archived': True, 'session_key': 'saved'}),
    ('session.archive', {'session_key': 'saved'}, {'archived': True, 'session_key': 'saved'}),
    ('session.set_hidden', {'hidden': True}, {'hidden': True, 'session_key': 'saved'}),
])
def test_authority_owner_soft_flags(method, params, expected):
    services = MemoryServices()
    row = {'id': 'saved', 'source': 'cli', 'started_at': 1.0, 'title': 'Keep',
           'rows': [{'id': 1, 'role': 'user', 'content': 'Keep message'}]}
    services.sessions['alice', 'saved'] = row
    args = params if 'session_key' in params else {'session_id': 'saved', **params}
    with client_for(services, 'authority') as client:
        with client.websocket_connect('/gateway-m1/api/ws', headers={'x-test-user': 'bob'}) as ws:
            foreign = call(ws, method, args)['error']
            missing = call(ws, method, {**params, 'session_id': 'missing', **({'session_key': 'missing'} if 'session_key' in params else {})})['error']
            assert foreign == missing
            assert foreign['message'] == 'not_found'
            assert not any(row.get(flag) for flag in ('deleted', 'archived', 'hidden'))
        with client.websocket_connect('/gateway-m1/api/ws') as ws:
            assert call(ws, method, args)['result'] == expected
            assert call(ws, 'session.list', {'scope': 'stored'})['result']['sessions'] == []
            assert row['title'] == 'Keep' and row['rows'][0]['content'] == 'Keep message'
            if method != 'session.delete':
                field = 'hidden' if method == 'session.set_hidden' else 'archived'
                assert call(ws, method, {'session_id': 'saved', field: False})['result'][field] is False
                assert len(call(ws, 'session.list', {'scope': 'stored'})['result']['sessions']) == 1


@pytest.mark.parametrize('method,params', [
    ('session.delete', {}), ('session.delete', {'session_id': 'saved', 'extra': True}),
    ('session.archive', {'session_id': 'saved', 'archived': 'true'}),
    ('session.archive', {'session_id': 'saved', 'session_key': 'other'}),
    ('session.set_hidden', {'session_id': 'saved'}),
    ('session.set_hidden', {'session_id': 'saved', 'hidden': 1}),
])
def test_authority_soft_flags_validate_params(method, params):
    with client_for(MemoryServices(), 'authority') as client:
        with client.websocket_connect('/gateway-m1/api/ws') as ws:
            assert call(ws, method, params)['error']['code'] == 4001

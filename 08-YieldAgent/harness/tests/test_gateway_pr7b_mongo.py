"""Real localhost Mongo + in-process WebSocket integration. AUTHOR MUST NOT RUN.

UUID database, finally drop. Motor is constructed inside TestClient's app lifespan
and every DB action runs through its portal on that same loop. No actual port is
bound, no model or worker launch occurs. Only Mongo uses localhost networking.
"""
from contextlib import asynccontextmanager, contextmanager
import json
from types import SimpleNamespace
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness.control import RunController
from harness.gateway.auth import Principal
from harness.gateway.events import SessionEvents
from harness.gateway.execution import Execution
from harness.gateway.publication import candidate_digest
from harness.gateway.routes import build_gateway_router
from harness.gateway.services import GatewayServices
from harness.store import HarnessStore


class OfflineController(RunController):
    def __init__(self, store):
        super().__init__(store, SimpleNamespace(engine='hermes'))
        self.launches = []

    async def launch(self, run_id):
        run = await self.store.acquire(run_id, self.owner)
        if run:
            self.launches.append(run_id)


class Resolver:
    async def resolve(self, connection):
        return Principal(connection.headers.get('x-test-user', 'alice'))


class Allow:
    async def allows(self, websocket, principal):
        return True

    async def verify_and_consume(self, websocket, principal):
        return True


class LifespanServices:
    async def serve_ws(self, websocket, principal, manifest, mode):
        await websocket.app.state.gateway.serve_ws(websocket, principal, manifest, mode)


@contextmanager
def application():
    @asynccontextmanager
    async def lifespan(app):
        store = HarnessStore(uri='mongodb://localhost:27017',
                             database='harness_pr7b_test_' + uuid.uuid4().hex)
        try:
            controller = OfflineController(store)
            app.state.store, app.state.controller = store, controller
            app.state.gateway = GatewayServices(store, controller, poll_interval=0.01)
            yield
        finally:
            try:
                await store.client.drop_database(store.db.name)
            finally:
                store.client.close()

    app = FastAPI(lifespan=lifespan)
    for mode, prefix in (('authority', '/gateway-m1'), ('legacy', '/legacy')):
        app.include_router(build_gateway_router(Resolver(), origin_policy=Allow(),
            ticket_verifier=Allow(), rpc_mode=mode, services=LifespanServices()), prefix=prefix)
    with TestClient(app) as client:
        yield client, app.state


def send(ws, method, params=None, rid=1):
    ws.send_json({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params or {}})


def until(ws, predicate, seen=None):
    for _ in range(2000):
        frame = ws.receive_json()
        if seen is not None:
            seen.append(frame)
        if predicate(frame):
            return frame
    raise AssertionError('Expected frame did not arrive')


def rpc(ws, method, params=None, rid=1, seen=None):
    send(ws, method, params, rid)
    return until(ws, lambda f: f.get('id') == rid and ('result' in f or 'error' in f), seen)


def result(ws, method, params=None, rid=1, seen=None):
    reply = rpc(ws, method, params, rid, seen)
    assert 'result' in reply, reply
    return reply['result']


async def add_raw_events(state, rid):
    for kind in ('message.start', 'message.delta', 'message.interim', 'message.complete', 'agent.print'):
        await state.store.event(rid, kind, {'text': 'UNVERIFIED-CANDIDATE'})


async def finish_verified(state, receipt):
    await state.store.runs.update_one({'run_id': receipt['run_id']}, {'$set': {
        'answer_text': 'VERIFIED-FINAL', 'observations': [{
            'tool_name': 'verify_answer', 'scope': {'verdict': 'pass'},
            'validated_arguments': candidate_digest('VERIFIED-FINAL')}]}})
    await state.store.finish(receipt['run_id'], receipt['execution_generation'],
                             'completed', 'VERIFIED-FINAL', 'hermes_returned')


async def register_prompt(state, sid, receipt, kind, prompt_id):
    execution = Execution(state.controller, 'alice', profile_id='default')
    if kind == 'clarify':
        await execution.pending.register_clarify(sid, receipt['execution_generation'],
            {'clarify_id': prompt_id, 'qid': 'q', 'question': 'Choose one?', 'choices': ['yes', 'no']},
            run_id=receipt['run_id'])
    else:
        await execution.pending.register(sid, receipt['execution_generation'],
            {'request_id': prompt_id, 'tool_name': 'synthetic', 'command': 'Synthetic action'},
            run_id=receipt['run_id'], tool_allowed=True, effect='artifact')
    await execution._publish_pending()


def test_ws_submit_push_ack_loss_reconnect_gap_and_publication(monkeypatch):
    with application() as (client, state):
        observed = []
        with client.websocket_connect('/gateway-m1/api/ws') as ws:
            created = result(ws, 'session.create')
            sid = created['session_id']
            params = {'session_id': sid, 'submission_id': 'lost-ack', 'text': 'Synthetic question'}
            admitted = result(ws, 'prompt.submit', params, seen=observed)
            # Resend identical input even if the first receipt was lost in transit.
            assert result(ws, 'prompt.submit', params, rid=2, seen=observed) == admitted
            assert state.controller.launches == [admitted['run_id']]
            client.portal.call(add_raw_events, state, admitted['run_id'])
            before = result(ws, 'session.events.since', {'session_id': sid,
                'replay_epoch': created['replay_epoch']}, rid=3, seen=observed)
            assert 'UNVERIFIED-CANDIDATE' not in json.dumps(before)
            client.portal.call(finish_verified, state, admitted)
            until(ws, lambda f: f.get('params', {}).get('type') == 'message.complete', observed)
            events = [f['params'] for f in observed if f.get('method') == 'event' and 'seq' in f['params']]
            seq = [f['seq'] for f in events]
            assert seq == list(range(1, len(seq) + 1))
            assert 'UNVERIFIED-CANDIDATE' not in json.dumps(observed)
            assert sum(f['type'] == 'message.complete' for f in events) == 1
        with client.websocket_connect('/gateway-m1/api/ws') as ws:
            # Fresh connection and fresh Execution instances read durable receipt/replay.
            retried = result(ws, 'prompt.submit', params)
            for key in ('run_id', 'admission_id', 'ref', 'sequence', 'authority_epoch',
                        'execution_generation'):
                assert retried[key] == admitted[key]
            assert retried['status'] == 'terminal'
            assert retried['outcome'] == 'completed'
            async def stored_submission_runs():
                return await state.store.runs.find({
                    'principal_id': 'alice', 'session_id': sid,
                    'request_id': params['submission_id']}).to_list(None)
            stored = client.portal.call(stored_submission_runs)
            assert [run['run_id'] for run in stored] == [admitted['run_id']]
            assert state.controller.launches == [admitted['run_id']]
            replayed = result(ws, 'session.events.since', {'session_id': sid,
                'replay_epoch': created['replay_epoch'], 'last_sequence': 0}, rid=4)
            assert [f['seq'] for f in replayed['events']] == seq
            restored = result(ws, 'session.resume', {'session_id': sid}, rid=5)
            assert restored['messages'][-1] == {'role': 'assistant', 'text': 'VERIFIED-FINAL'}
            assert state.controller.launches == [admitted['run_id']]

        # Force actual bounded-ring eviction, then recover by snapshot. The
        # temporary small ring avoids 500+ DB round trips; production stays 512.
        from harness.gateway import events as events_module
        monkeypatch.setattr(events_module, '_REPLAY_BUFFER_MAX', 3)
        async def overflow():
            execution = Execution(state.controller, 'alice', profile_id='default')
            next_run = await execution.submit(sid, {'session_id': sid, 'submission_id': 'next', 'text': 'Next'})
            for _ in range(6):
                await state.store.event(next_run['run_id'], 'message.start', {})
            await execution.events.ingest(sid)
        client.portal.call(overflow)
        with client.websocket_connect('/gateway-m1/api/ws') as ws:
            gap = result(ws, 'session.events.since', {'session_id': sid,
                'replay_epoch': created['replay_epoch'], 'last_sequence': 0})
            assert gap['snapshot_required'] and gap['truncated'] and gap['events'] == []
            snap = result(ws, 'session.resume', {'session_id': sid}, rid=2)
            assert snap['last_sequence'] == gap['latest_seq']
            async def rotate():
                return await SessionEvents(state.store, 'alice').rebuild(sid)
            mark = client.portal.call(rotate)
            notification = until(ws, lambda f: f.get('params', {}).get('type') == 'session.replay_gap')
            assert notification['params']['payload']['replay_epoch'] == mark['replay_epoch']


def test_concurrent_submits_single_execution_and_two_principals():
    with application() as (client, state):
        with client.websocket_connect('/gateway-m1/api/ws') as a:
            sid = result(a, 'session.create')['session_id']
            params = {'session_id': sid, 'submission_id': 'same', 'text': 'Synthetic'}
            for rid in range(10, 26):
                send(a, 'prompt.submit', params, rid)
            replies = {}
            while len(replies) < 16:
                frame = a.receive_json()
                if frame.get('id') in range(10, 26):
                    assert 'result' in frame, frame
                    replies[frame['id']] = frame['result']
            assert all(v == replies[10] for v in replies.values())
            assert len(state.controller.launches) == 1
            conflict = rpc(a, 'prompt.submit', {**params, 'submission_id': 'other'}, rid=30)
            assert conflict['error']['message'] == 'session_busy'
            with client.websocket_connect('/gateway-m1/api/ws', headers={'x-test-user': 'bob'}) as b:
                assert rpc(b, 'session.resume', {'session_id': sid})['error']['message'] == 'not_found'
                own = result(b, 'session.create', {'principal_id': 'alice', 'profile_id': 'host'}, rid=2)
                assert own['info']['profile_id'] == 'default'
                foreign = rpc(b, 'prompt.receipt', {'session_id': own['session_id'],
                    'admission_id': replies[10]['admission_id']}, rid=3)
                assert foreign['error']['message'] == 'not_found'
                assert result(b, 'prompt.submit', {**params, 'session_id': own['session_id']}, rid=4)['run_id'] != replies[10]['run_id']


def test_authority_pending_resume_and_generation_fenced_responses():
    with application() as (client, state):
        with client.websocket_connect('/gateway-m1/api/ws') as ws:
            sid = result(ws, 'session.create')['session_id']
            receipt = result(ws, 'prompt.submit', {'session_id': sid, 'submission_id': 'x', 'text': 'Synthetic'}, rid=2)
            for kind in ('clarify', 'approval'):
                client.portal.call(register_prompt, state, sid, receipt, kind, kind + '-p')
            snap = result(ws, 'session.resume', {'session_id': sid}, rid=3)
            assert {p['kind'] for p in snap['prompts']} == {'approval', 'clarify'}
        # Authority disconnect leaves durable prompts restorable.
        with client.websocket_connect('/gateway-m1/api/ws') as ws:
            snap = result(ws, 'session.resume', {'session_id': sid})
            assert len(snap['prompts']) == 2
            for index, kind in enumerate(('clarify', 'approval'), 10):
                params = {'session_id': sid, 'execution_generation': receipt['execution_generation'],
                          'prompt_id': kind + '-p', **({'answer': 'PRIVATE-ANSWER'} if kind == 'clarify' else {'choice': 'once'})}
                assert result(ws, kind + '.respond', params, rid=index)['status'] == 'resolved'
            replayed = result(ws, 'session.events.since', {'session_id': sid,
                'replay_epoch': snap['replay_epoch']}, rid=20)
            completed = [e['payload'] for e in replayed['events'] if e['type'] == 'tool.complete']
            assert len(completed) == 1
            assert completed[0]['name'] == 'clarify'
            assert completed[0]['result']['responses'][0]['user_response'] == 'PRIVATE-ANSWER'
            settled = [e for e in replayed['events'] if e['type'] != 'tool.complete']
            assert 'PRIVATE-ANSWER' not in json.dumps(settled)
            assert {'clarify.request', 'approval.request', 'clarify.settled', 'approval.settled', 'tool.complete'} <= {e['type'] for e in replayed['events']}


def test_legacy_wire_response_cross_connection_and_disconnect_cleanup():
    with application() as (client, state):
        with client.websocket_connect('/legacy/api/ws') as a:
            result(a, 'client.capabilities', {'server_requests': True})
            sid = result(a, 'session.create', rid=2)['session_id']
            async def submit_for_legacy_viewer():
                execution = Execution(state.controller, 'alice', profile_id='default')
                return await execution.submit(sid, {'session_id': sid, 'submission_id': 'x', 'text': 'Synthetic'})
            receipt = client.portal.call(submit_for_legacy_viewer)
            assert rpc(a, 'prompt.submit', {'session_id': sid, 'text': 'Synthetic'}, rid=3)['error']['code'] == -32004
            for index, kind in enumerate(('clarify', 'approval'), 10):
                client.portal.call(register_prompt, state, sid, receipt, kind, kind + '-p')
                question = until(a, lambda f: f.get('method') == kind)
                with client.websocket_connect('/legacy/api/ws') as b:
                    b.send_json({'jsonrpc': '2.0', 'id': question['id'], 'result': {'choice': 'once'}})
                    result(b, 'ping')
                    async def actual_status():
                        row = await Execution(state.controller, 'alice', profile_id='default').pending.pending_prompts.find_one(
                            {'principal_id': 'alice', 'prompt_id': kind + '-p'})
                        return row['status']
                    assert client.portal.call(actual_status) == 'pending'
                answer = {'answers': {'q': 'PRIVATE'}} if kind == 'clarify' else {'choice': 'once'}
                a.send_json({'jsonrpc': '2.0', 'id': question['id'], 'result': answer})
                result(a, 'ping', rid=index)
                assert client.portal.call(actual_status) == 'resolved'
            client.portal.call(register_prompt, state, sid, receipt, 'clarify', 'disconnect-p')
            until(a, lambda f: f.get('method') == 'clarify')
        async def disconnected():
            pending = Execution(state.controller, 'alice', profile_id='default').pending
            row = await pending.pending_prompts.find_one({'principal_id': 'alice', 'prompt_id': 'disconnect-p'})
            return row['status']
        assert client.portal.call(disconnected) == 'cancelled'
        assert not state.gateway._legacy_readers


def test_two_windows_independent_stream_sequence():
    with application() as (client, state):
        with client.websocket_connect('/gateway-m1/api/ws') as a:
            created = result(a, 'session.create')
            sid = created['session_id']
            with client.websocket_connect('/gateway-m1/api/ws') as b:
                result(b, 'session.resume', {'session_id': sid})
                seen_a, seen_b = [], []
                receipt = result(a, 'prompt.submit', {'session_id': sid, 'submission_id': 'x', 'text': 'Synthetic'}, rid=2, seen=seen_a)
                client.portal.call(add_raw_events, state, receipt['run_id'])
                client.portal.call(finish_verified, state, receipt)
                for ws, seen in ((a, seen_a), (b, seen_b)):
                    until(ws, lambda f: f.get('params', {}).get('type') == 'message.complete', seen)
                streams = [[f['params'] for f in seen if f.get('method') == 'event' and 'seq' in f['params']]
                           for seen in (seen_a, seen_b)]
                assert streams[0] == streams[1]
                seq = [f['seq'] for f in streams[0]]
                assert seq == list(range(1, len(seq) + 1))

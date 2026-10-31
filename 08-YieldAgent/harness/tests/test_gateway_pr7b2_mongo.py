"""MANUAL ONLY: real localhost Mongo; do not run in Codex.

UUID database, app-loop Motor, finally drop. WS/REST are in-process, and launch
is fake: no engine, model call or real listening port. Reuses PR-7b fixtures.
"""
from contextlib import asynccontextmanager, contextmanager
import json
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness.gateway.routes import build_gateway_router
from harness.gateway.services import GatewayServices
from harness.store import HarnessStore
from harness.tests.test_gateway_pr7b_mongo import (
    Allow, OfflineController, Resolver, finish_verified, result,
)


class LifespanServices:
    async def serve_ws(self, websocket, principal, manifest, mode):
        await websocket.app.state.gateway.serve_ws(websocket, principal, manifest, mode)

    async def serve_http(self, incoming, principal, row):
        return await incoming.app.state.gateway.serve_http(incoming, principal, row)


@contextmanager
def application():
    @asynccontextmanager
    async def lifespan(app):
        store = HarnessStore(uri='mongodb://localhost:27017',
                             database='harness_pr7b2_test_' + uuid.uuid4().hex)
        try:
            controller = OfflineController(store)
            app.state.store, app.state.controller = store, controller
            app.state.gateway = GatewayServices(store, controller, poll_interval=0.01,
                profile_id_for=lambda principal: 'profile-' + principal.user_id)
            yield
        finally:
            try:
                await store.client.drop_database(store.db.name)
            finally:
                store.client.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(build_gateway_router(Resolver(), origin_policy=Allow(),
        ticket_verifier=Allow(), rpc_mode='authority', services=LifespanServices()), prefix='/gateway-m1')
    with TestClient(app) as client:
        yield client, app.state


def test_two_principals_ws_runs_rest_verified_history():
    with application() as (client, state):
        sessions = {}
        for owner in ('alice', 'bob'):
            headers = {'x-test-user': owner}
            with client.websocket_connect('/gateway-m1/api/ws', headers=headers) as ws:
                sid = result(ws, 'session.create', {'title': owner + ' title'})['session_id']
                sessions[owner] = sid
                receipt = result(ws, 'prompt.submit', {
                    'session_id': sid, 'submission_id': 'submission', 'text': owner + ' question'}, rid=2)
                async def unverified():
                    await state.store.runs.update_one({'run_id': receipt['run_id']},
                        {'$set': {'answer_text': 'UNVERIFIED-CANDIDATE'}})
                    await state.store.event(receipt['run_id'], 'message.delta', {'text': 'UNVERIFIED-CANDIDATE'})
                client.portal.call(unverified)
                path = '/gateway-m1/api/sessions/' + sid + '/messages'
                before = client.get(path, headers=headers)
                assert before.status_code == 200
                assert before.json()['messages'] == [{'role': 'user', 'content': owner + ' question'}]
                assert 'UNVERIFIED-CANDIDATE' not in before.text
                client.portal.call(finish_verified, state, receipt)
                after = client.get(path, headers=headers)
                assert after.status_code == 200
                assert after.json()['messages'] == [
                    {'role': 'user', 'content': owner + ' question'},
                    {'role': 'assistant', 'content': 'VERIFIED-FINAL'}]
                assert 'UNVERIFIED-CANDIDATE' not in after.text

        for owner, other in (('alice', 'bob'), ('bob', 'alice')):
            headers = {'x-test-user': owner}
            for path in ('/api/sessions', '/api/profiles/sessions'):
                response = client.get('/gateway-m1' + path + '?profile=' + other, headers=headers)
                assert response.status_code == 200
                body = response.json()
                assert body['total'] == 1
                assert [row['id'] for row in body['sessions']] == [sessions[owner]]
                assert body['sessions'][0]['message_count'] == 2
                assert body['sessions'][0]['profile'] == 'profile-' + owner
                assert sessions[other] not in json.dumps(body)
            foreign = client.get('/gateway-m1/api/sessions/' + sessions[other] + '/messages', headers=headers)
            missing = client.get('/gateway-m1/api/sessions/missing/messages', headers=headers)
            assert foreign.status_code == missing.status_code == 404
            assert foreign.json() == missing.json() == {'detail': 'Session not found'}
        assert len(state.controller.launches) == 2

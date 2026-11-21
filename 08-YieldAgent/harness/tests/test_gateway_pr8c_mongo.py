"""MANUAL ONLY: real localhost Mongo; do not run in Codex.

UUID database, app-loop Motor, finally drop. WS/REST are in-process, and launch
is fake: no engine, model call or real listening port. Reuses PR-7b fixtures.
"""
from contextlib import asynccontextmanager, contextmanager
import json
import uuid

import pytest

from harness.gateway.auth import Principal
from harness.tests.test_gateway_pr7b_mongo import rpc

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
                             database='harness_pr8c_test_' + uuid.uuid4().hex)
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
    for mode, prefix in (('authority', '/gateway-m1'), ('legacy', '/legacy')):
        app.include_router(build_gateway_router(Resolver(), origin_policy=Allow(),
            ticket_verifier=Allow(), rpc_mode=mode, services=LifespanServices()), prefix=prefix)
    with TestClient(app) as client:
        yield client, app.state



@pytest.mark.parametrize('mode,prefix', [('authority', '/gateway-m1'), ('legacy', '/legacy')])
@pytest.mark.parametrize('deferred', [False, True])
def test_resume_finalized_history_and_principal_scope(mode, prefix, deferred):
    with application() as (client, state):
        alice = {'x-test-user': 'alice'}
        with client.websocket_connect('/gateway-m1/api/ws', headers=alice) as owner:
            sid = result(owner, 'session.create')['session_id']
            receipt = result(owner, 'prompt.submit', {'session_id': sid,
                'submission_id': 'pr8c', 'text': 'stored question'})
            client.portal.call(finish_verified, state, receipt)

        async def finalize():
            scope = await state.gateway.open(Principal('alice'))
            await scope.sessions.end_session(sid, 'completed')
            return await scope.sessions.get_session(sid)
        before = client.portal.call(finalize)
        assert before['ended_at'] is not None
        with client.websocket_connect(prefix + '/api/ws', headers=alice) as ws:
            params = {'session_id': sid, 'source': 'desktop', 'cols': 96,
                      'defer_history': deferred, 'omit_messages': deferred,
                      'inline_images': False, 'profile': 'bob'}
            snapshot = result(ws, 'session.resume', params)
            assert snapshot['running'] is False
            assert snapshot['message_count'] == 2
            assert snapshot['info']['profile_name'] == 'profile-alice'
            if deferred:
                assert snapshot['messages'] == []
                assert snapshot['hydrating'] is False
                assert snapshot['status'] == 'idle'
            else:
                assert len(snapshot['messages']) == 2
                assert 'hydrating' not in snapshot
            if mode == 'authority':
                assert snapshot['last_sequence'] > 0
                assert snapshot['replay_epoch']
            history = client.get(prefix + '/api/sessions/' + sid + '/messages', headers=alice)
            assert history.status_code == 200
            assert history.json()['messages'] == [
                {'role': 'user', 'content': 'stored question'},
                {'role': 'assistant', 'content': 'VERIFIED-FINAL', 'interim': False}]

        async def row_after():
            scope = await state.gateway.open(Principal('alice'))
            return await scope.sessions.get_session(sid)
        assert client.portal.call(row_after) == before
        with client.websocket_connect(prefix + '/api/ws', headers={'x-test-user': 'bob'}) as bob:
            foreign = rpc(bob, 'session.resume', params)['error']
            missing = rpc(bob, 'session.resume', {**params, 'session_id': 'missing'})['error']
            assert foreign == missing
            assert foreign['message'] == 'not_found'
        assert len(state.controller.launches) == 1

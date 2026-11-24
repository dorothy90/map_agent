"""Gateway history checks use a temporary Mongo database and never launch a worker."""
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('flag', [None, 'false', 'true'])
def test_standalone_app_import_has_no_gateway_routes_in_fresh_process(flag):
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join(filter(None, (str(ROOT), env.get('PYTHONPATH'))))
    env.pop('GATEWAY_M1_ENABLED', None)
    if flag is not None:
        env['GATEWAY_M1_ENABLED'] = flag
    code = """
import sys
from harness.app import app
assert 'harness.gateway.routes' not in sys.modules
assert all(not r.path.startswith(('/gateway-m1', '/api/ws', '/api/sessions')) for r in app.routes)
assert any(r.path == '/runs' for r in app.routes)
"""
    subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, check=True,
                   capture_output=True, text=True, timeout=30)


def test_gateway_history_is_readable_through_retained_router():
    import httpx
    from pymongo.errors import ConnectionFailure
    from harness.control import RunController
    from harness.gateway.auth import Principal
    from harness.gateway.routes import build_gateway_router
    from harness.gateway.services import GatewayServices
    from harness.store import HarnessStore

    mongo_uri = os.getenv('HARNESS_MONGO_URI')
    if not mongo_uri and sys.platform != 'darwin':
        pytest.skip('HARNESS_MONGO_URI not configured')
    mongo_uri = mongo_uri or 'mongodb://localhost:27017'

    class OfflineController(RunController):
        async def launch(self, run_id):
            await self.store.acquire(run_id, self.owner)

    class Resolver:
        async def resolve(self, connection):
            return Principal('alice')

    class Allow:
        async def allows(self, websocket, principal):
            return True

        async def verify_and_consume(self, websocket, principal):
            return True

    def gateway_submission(database):
        # TestClient owns its loop; never share its Motor client with scenario().
        @asynccontextmanager
        async def lifespan(app):
            writer = HarnessStore(uri=mongo_uri, database=database)
            try:
                controller = OfflineController(writer, SimpleNamespace(engine='hermes', principal='alice'))
                services = GatewayServices(writer, controller)
                scope = await services.open(Principal('alice'))
                await scope.sessions.create_session('gateway', 'gui')
                app.include_router(build_gateway_router(Resolver(), services=services,
                    origin_policy=Allow(), ticket_verifier=Allow(), rpc_mode='authority'), prefix='/gateway-m1')
                yield
            finally:
                writer.client.close()

        with TestClient(FastAPI(lifespan=lifespan)) as client:
            with client.websocket_connect('/gateway-m1/api/ws') as ws:
                ws.send_json({'jsonrpc': '2.0', 'id': 1, 'method': 'prompt.submit', 'params': {
                    'session_id': 'gateway', 'submission_id': 'new', 'text': 'gateway query'}})
                for _ in range(100):
                    reply = ws.receive_json()
                    if reply.get('id') == 1:
                        assert 'result' in reply, reply
                        return reply['result']
                pytest.fail('gateway did not acknowledge submission')

    async def scenario():
        store = HarnessStore(uri=mongo_uri, database='harness_pr10_test_' + uuid.uuid4().hex)
        connected = False
        try:
            try:
                await store.client.admin.command('ping')
            except ConnectionFailure:
                if sys.platform == 'darwin':
                    pytest.fail('Start the local test MongoDB or configure HARNESS_MONGO_URI')
                pytest.skip('Mongo unavailable; no server is started by this test')
            connected = True
            await store.setup()
            controller = OfflineController(store, SimpleNamespace(engine='hermes', principal='alice'))
            services = GatewayServices(store, controller)
            scope = await services.open(Principal('alice'))
            receipt = await asyncio.to_thread(gateway_submission, store.db.name)
            run_id = receipt['run_id']
            run = await store.get_run('alice', run_id)
            assert run['session_id'] == 'gateway' and run['epoch'] == 1
            await store.event(run_id, 'progress', {'message': 'persisted'})
            await store.finish(run_id, run['epoch'], 'cancelled', 'stopped', 'user_cancelled')
            # Persist an actual additive gateway field as a rollback fixture.
            # This does not pretend to exercise unknown-recovery itself.
            await store.runs.update_one({'_id': run_id}, {
                '$set': {'gateway_unknown_resolution': run['epoch']}})
            before = await store.get_run('alice', run_id)

            # A fresh host exposes the retained run/history API over the same DB.
            from harness.router import router
            app = FastAPI()
            app.include_router(router)
            app.state.harness = OfflineController(store, controller.settings)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.get('/runs/' + run_id)
                public_keys = {'run_id', 'session_id', 'query', 'status', 'answer', 'stop_reason',
                               'goal_revision', 'usage', 'question', 'runtime_version', 'observations'}
                assert response.status_code == 200
                assert response.json() == {key: before.get(key) for key in public_keys}
                assert (await client.get('/gateway-m1/api/sessions')).status_code == 404
                history = (await client.get('/harness/sessions/gateway')).json()
                assert history == {'session_id': 'gateway', 'runs': [response.json()], 'next_cursor': None}
                stream = await client.get(f'/runs/{run_id}/events')
                assert stream.headers['content-type'].startswith('text/event-stream')
                assert '"run_finished"' in stream.text
                cursor = before['events'][0]['sequence']
                replay = await client.get(f'/runs/{run_id}/events', params={'after': cursor})
                ids = [int(line[4:]) for line in replay.text.splitlines() if line.startswith('id: ')]
                assert ids == [e['sequence'] for e in before['events'] if e['sequence'] > cursor]
                assert (await client.get(f'/runs/{run_id}/events', params={'after': ids[-1]})).text == ''
                legacy = await client.post('/runs', json={'session_id': 'legacy', 'request_id': 'old', 'query': 'old query'})
                assert legacy.status_code == 200 and set(legacy.json()) == public_keys
                old = await store.get_run('alice', legacy.json()['run_id'])
                await store.finish(old['run_id'], old['epoch'], 'cancelled', 'stopped', 'user_cancelled')

            # The list is keyed by gateway session rows, not all harness_runs.
            gateway = FastAPI()
            gateway.include_router(build_gateway_router(Resolver(), services=services), prefix='/gateway-m1')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url='http://test') as client:
                listing = await client.get('/gateway-m1/api/sessions')
                assert listing.status_code == 200
                assert {row['id'] for row in listing.json()['sessions']} == {'gateway'}
                await scope.sessions.create_session('legacy', 'gui')
                listing = await client.get('/gateway-m1/api/sessions')
                assert listing.status_code == 200
                assert {row['id'] for row in listing.json()['sessions']} == {'gateway', 'legacy'}
                messages = await client.get('/gateway-m1/api/sessions/legacy/messages')
                assert messages.json()['messages'] == [{'role': 'user', 'content': 'old query'}]
                await scope.sessions.append_message('legacy', 'user', 'separate transcript')
                # Existing fail-closed policy: mixed stores cannot infer ordering.
                for path in ('/gateway-m1/api/sessions', '/gateway-m1/api/sessions/legacy/messages'):
                    mixed = await client.get(path)
                    assert mixed.status_code == 503
                    assert mixed.json()['error']['code'] == 'handler_not_connected'
            assert await store.get_run('alice', run_id) == before
        finally:
            try:
                if connected:
                    await store.client.drop_database(store.db.name)
            finally:
                store.client.close()
    asyncio.run(scenario())

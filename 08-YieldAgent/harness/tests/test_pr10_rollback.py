"""PR-10 characterization; Mongo tests are opt-in and never launch a worker.

Author validation: py_compile only. HARNESS_MONGO_URI enables a UUID temporary
DB; unavailable Mongo skips before setup, and every connected DB is dropped.
"""
import ast
import asyncio
import builtins
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


def mounted_app():
    """Execute only the actual legacy mount, health and gateway opt-in block."""
    from harness.router import router
    tree = ast.parse((ROOT / 'agent_server.py').read_text())
    selected = [node for node in tree.body if
                (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                 and ast.unparse(node.value.func) == 'app.include_router'
                 and node.value.args and ast.unparse(node.value.args[0]) == 'harness_router')
                or (isinstance(node, ast.If) and 'GATEWAY_M1_ENABLED' in ast.unparse(node.test))
                or (isinstance(node, ast.FunctionDef) and node.name == 'health')]
    assert len(selected) == 3
    app = FastAPI()
    exec(compile(ast.Module(body=selected, type_ignores=[]), 'agent_server.py', 'exec'),
         {'app': app, 'harness_router': router, 'os': os})
    return app


def route_set(routes):
    return {(method, route.path) for route in routes for method in getattr(route, 'methods', ())}


@pytest.mark.parametrize('disabled', [None, 'false'])
def test_actual_mount_preserves_complete_legacy_route_snapshot(monkeypatch, disabled):
    from harness.router import router
    original_import = builtins.__import__
    imports = []

    def watched(name, *args, **kwargs):
        if name.startswith('harness.gateway'):
            imports.append(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', watched)
    if disabled is None:
        monkeypatch.delenv('GATEWAY_M1_ENABLED', raising=False)
    else:
        monkeypatch.setenv('GATEWAY_M1_ENABLED', disabled)
    off = mounted_app()
    assert imports == []
    with TestClient(off) as client:
        assert client.get('/health').status_code == 200
        for path in ('/gateway-m1/api/health', '/gateway-m1/api/sessions',
                     '/gateway-m1/api/ws', '/api/ws', '/api/sessions'):
            assert client.get(path).status_code == 404
    monkeypatch.setenv('GATEWAY_M1_ENABLED', 'true')
    on = mounted_app()
    assert imports == ['harness.gateway.routes']
    expected = route_set(router.routes)
    assert expected
    # Compare all method/path pairs, not a handpicked route count.
    off_snapshot = route_set(r for r in off.routes if r.endpoint in {x.endpoint for x in router.routes})
    on_snapshot = route_set(r for r in on.routes if r.endpoint in {x.endpoint for x in router.routes})
    assert off_snapshot == on_snapshot == expected


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


@pytest.mark.skipif(not os.getenv('HARNESS_MONGO_URI'), reason='HARNESS_MONGO_URI not configured')
def test_gateway_and_legacy_history_survive_flag_off_restart(monkeypatch):
    import httpx
    from pymongo.errors import ConnectionFailure
    from harness.control import RunController
    from harness.gateway.auth import Principal
    from harness.gateway.routes import build_gateway_router
    from harness.gateway.services import GatewayServices
    from harness.store import HarnessStore

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
            writer = HarnessStore(uri=os.environ['HARNESS_MONGO_URI'], database=database)
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
        store = HarnessStore(uri=os.environ['HARNESS_MONGO_URI'],
                             database='harness_pr10_test_' + uuid.uuid4().hex)
        connected = False
        try:
            try:
                await store.client.admin.command('ping')
            except ConnectionFailure:
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

            # New app/controller over the same DB characterizes a flag-OFF restart.
            monkeypatch.setenv('GATEWAY_M1_ENABLED', 'false')
            app = mounted_app()
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

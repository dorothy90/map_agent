"""Opt-in PR8 fixture. Never import this module during ordinary unit tests.

Uses real Mongo and gateway routes, but never starts an engine or model. Identity,
origin and ticket policies here are deliberately test-only, not production auth.
"""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import uuid

if __name__ == '__main__' and not os.environ.get('HARNESS_MONGO_URI'):
    raise SystemExit('HARNESS_MONGO_URI is required for opt-in integration tests')

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi import FastAPI, HTTPException, Request
from harness.control import RunController
from harness.gateway.auth import AuthenticationFailure, Principal
from harness.gateway.events import SessionEvents
from harness.gateway.routes import build_gateway_router
from harness.gateway.services import GatewayServices
from harness.store import HarnessStore


class OfflineController(RunController):
    def __init__(self, store):
        super().__init__(store, SimpleNamespace(engine='hermes'))
        self.launches = []

    async def launch(self, run_id):
        if await self.store.acquire(run_id, self.owner):
            self.launches.append(run_id)


class Resolver:
    async def resolve(self, connection):
        user = connection.query_params.get('user')
        if user not in ('alice', 'bob'):
            raise AuthenticationFailure(401)
        return Principal(user)


class FixturePolicy:
    async def allows(self, websocket, principal):
        # Native Node WebSocket has no custom Origin option. Absent Origin is
        # accepted only by this loopback fixture; any supplied foreign Origin fails.
        return websocket.headers.get('origin') in (None, websocket.app.state.origin)

    async def verify_and_consume(self, websocket, principal):
        # Unique tickets per dial, scoped to the test user; no production format.
        ticket = websocket.query_params.get('ticket', '')
        if not ticket.startswith(principal.user_id + '-') or ticket in websocket.app.state.tickets:
            return False
        websocket.app.state.tickets.add(ticket)
        return True


class Services:
    async def serve_ws(self, ws, principal, manifest, mode):
        ws.app.state.sockets.add(ws)
        try:
            await ws.app.state.gateway.serve_ws(ws, principal, manifest, mode)
        finally:
            ws.app.state.sockets.discard(ws)

    async def serve_http(self, request, principal, row):
        return await request.app.state.gateway.serve_http(request, principal, row)


class ObserveWire:
    def __init__(self, app, state):
        self.app, self.state = app, state

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and not scope['path'].startswith('/__test/'):
            self.state['http'].append(scope['path'])
            self.state['trace'].append({'http': scope['path']})
        pending = {}

        async def incoming():
            message = await receive()
            if message['type'] == 'websocket.receive' and message.get('text'):
                try:
                    frame = json.loads(message['text'])
                    if 'method' in frame:
                        self.state['rpc'].append(frame['method'])
                        self.state['trace'].append({'rpc': frame['method']})
                        pending[frame.get('id')] = frame['method']
                except (ValueError, TypeError):
                    pass
            return message

        async def outgoing(message):
            if message['type'] == 'websocket.send' and message.get('text'):
                frame = json.loads(message['text'])
                if (self.state['lose_ack'] and pending.get(frame.get('id')) == 'prompt.submit'
                        and 'result' in frame):
                    self.state['lose_ack'] = False
                    await send({'type': 'websocket.close', 'code': 1012})
                    return
            await send(message)
        await self.app(scope, incoming, outgoing)


def make_app(uri, database):
    wire = {'rpc': [], 'http': [], 'lose_ack': False, 'trace': []}

    @asynccontextmanager
    async def lifespan(app):
        store = HarnessStore(uri=uri, database=database)
        app.state.store = store
        app.state.controller = OfflineController(store)
        app.state.gateway = GatewayServices(store, app.state.controller, poll_interval=0.02)
        app.state.sockets, app.state.tickets = set(), set()
        app.state.origin = os.environ['PR8_FIXTURE_ORIGIN']
        try:
            await store.setup()
            yield
        finally:
            try:
                await store.client.drop_database(database)
            finally:
                store.client.close()

    app = FastAPI(lifespan=lifespan)
    for mode, prefix in (('authority', '/gateway-m1'), ('legacy', '/gateway-legacy')):
        app.include_router(build_gateway_router(Resolver(), origin_policy=FixturePolicy(),
            ticket_verifier=FixturePolicy(), rpc_mode=mode, services=Services()), prefix=prefix)

    async def local(request):
        if request.client is None or request.client.host not in ('127.0.0.1', '::1'):
            raise HTTPException(403)

    @app.get('/__test/state')
    async def state(request: Request):
        await local(request)
        calls = await app.state.store.calls.distinct('tool_name')
        return {**wire, 'launches': app.state.controller.launches,
                'blocked': [name for name in calls if name not in {'read_result', 'skills_list', 'skill_view', 'skill_manage'}]}

    @app.post('/__test/control')
    async def control(request: Request):
        await local(request)
        body = await request.json()
        action = body.get('action')
        if action == 'ack_loss':
            wire['lose_ack'] = True
        elif action == 'drop':
            for ws in list(app.state.sockets):
                await ws.close(code=1012)
        elif action == 'replay_limit':
            from harness.gateway import events
            limit = body.get('limit')
            if type(limit) is not int or not 1 <= limit <= 512:
                raise HTTPException(400)
            events._REPLAY_BUFFER_MAX = limit
        elif action in ('publish', 'finish', 'epoch'):
            user, sid = body.get('user'), body.get('session_id')
            if user not in ('alice', 'bob'):
                raise HTTPException(400)
            events = SessionEvents(app.state.store, user)
            await events._owned(sid)
            if action == 'epoch':
                return await events.rebuild(sid)
            receipt = body['receipt']
            run = await app.state.store.runs.find_one({'run_id': receipt['run_id'],
                'principal_id': user, 'session_id': sid})
            if run is None:
                raise HTTPException(404)
            if action == 'publish':
                for _ in range(body.get('count', 1)):
                    await app.state.store.event(run['run_id'], 'message.start', {})
            else:
                answer = body.get('answer', 'VERIFIED-FINAL')
                await app.state.store.runs.update_one({'run_id': run['run_id']}, {'$set': {
                    'answer_text': answer, 'native_engine': {'entrypoint': 'stock_chat'}}})
                await app.state.store.finish(run['run_id'], receipt['execution_generation'],
                    'completed', answer, 'hermes_returned')
            return await events.ingest(sid)
        else:
            raise HTTPException(400)
        return {'ok': True}

    return ObserveWire(app, wire)


async def main(app_factory=make_app):
    uri = os.environ.get('HARNESS_MONGO_URI')
    if not uri:
        raise SystemExit('HARNESS_MONGO_URI is required for opt-in integration tests')
    import uvicorn
    database = 'zz_pr8_' + uuid.uuid4().hex
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    os.environ['PR8_FIXTURE_ORIGIN'] = f'http://127.0.0.1:{port}'
    server = uvicorn.Server(uvicorn.Config(app_factory(uri, database), log_level='critical', access_log=False))
    loop = asyncio.get_running_loop()
    def stdin_ready():
        if not os.read(sys.stdin.fileno(), 4096):
            server.should_exit = True
            loop.remove_reader(sys.stdin.fileno())
    loop.add_reader(sys.stdin.fileno(), stdin_ready)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            if task.done():
                await task
                raise RuntimeError('Fixture startup failed')
            await asyncio.sleep(0.02)
        print(json.dumps({'port': port, 'db': database}), file=sys.__stdout__, flush=True)
        await task
    finally:
        server.should_exit = True
        loop.remove_reader(sys.stdin.fileno())
        sock.close()


if __name__ == '__main__':
    # Deliberately do not print driver errors (which can contain connection URIs).
    try:
        asyncio.run(main())
    except Exception:
        print('PR8 integration fixture failed', file=sys.stderr)
        raise SystemExit(1)

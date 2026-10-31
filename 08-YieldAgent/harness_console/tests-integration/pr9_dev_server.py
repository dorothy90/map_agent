"""Manual PR9b loopback fixture. No engine/model; never imported by unit tests."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi import FastAPI, HTTPException
from harness.gateway.auth import AuthenticationFailure, Principal
from harness.gateway.publication import candidate_digest
from harness.gateway.routes import build_gateway_router
from harness.gateway.services import GatewayServices
from harness.gateway.session_store import MongoSessionStore
from harness.router import router as result_router
from harness.store import HarnessStore
from pr8_gateway_server import OfflineController


class Resolver:
    async def resolve(self, connection):
        if connection.client is None or connection.client.host not in ('127.0.0.1', '::1'):
            raise AuthenticationFailure(403)
        user = connection.query_params.get('user')
        if user not in ('alice', 'bob'):
            raise AuthenticationFailure(401)
        return Principal(user)


class DevPolicy:
    async def allows(self, websocket, principal):
        return websocket.headers.get('origin') == os.environ.get('PR9_VITE_ORIGIN', 'http://127.0.0.1:5175')

    async def verify_and_consume(self, websocket, principal):
        # Fake identity only; no production ticket/auth claim.
        return websocket.client.host in ('127.0.0.1', '::1')


async def seed(store, gateway):
    for user in ('alice', 'bob'):
        sessions = MongoSessionStore(store, user)
        await sessions.setup()
        for suffix, title in [('chat', '일반 대화'), ('result', '표와 검증 결과'), ('pending', '검증과 질문 대기'), ('approval', '승인 대기')]:
            sid = f'{user}-{suffix}'
            await sessions.create_session(sid, 'cli', profile_name='default')
            await sessions.set_session_title(sid, title)
            if suffix == 'chat':
                await sessions.append_message(sid, 'user', title)
                await sessions.append_message(sid, 'assistant', '저장된 일반 대화입니다.')
                continue
            # Host-run history comes only from query and verified answer evidence;
            # visible user rows mixed with runs make SessionRPC.history fail closed.
            run = await sessions.start_run(sid, uuid.uuid4().hex, title)
            run = await store.acquire(run['run_id'], 'pr9-seed')
            if suffix == 'approval':
                scope = await gateway.open(Principal(user))
                await scope.execution.pending.register(sid, run['epoch'], {
                    'request_id': 'pr9-approval', 'tool_name': 'write_report',
                    'command': 'write_report --name weekly',
                    'description': '주간 보고서 아티팩트 생성',
                }, run_id=run['run_id'], timeout_s=3600, tool_allowed=True, effect='artifact')
                continue
            if suffix == 'pending':
                # public_list_row counts history without rejecting active runs.
                # history includes this query; verified_answer returns None while
                # active, so no mixed message rows means no NotConnected here.
                scope = await gateway.open(Principal(user))
                await scope.execution.pending.register_clarify(sid, run['epoch'], {
                    'clarify_id': 'pr9-question', 'question': '조회 기간을 선택해주세요.',
                    'choices': ['최근 1주', '최근 1개월'], 'multi_select': False,
                }, run_id=run['run_id'], timeout_s=3600)
                continue
            answer = '검증된 결과입니다.\n\n| 제품 | 수율 |\n|---|---|\n| 4SS | 98.5 |'
            observation = await store.put_result(user, sid, {
                'run_id': run['run_id'], 'invocation_id': 'seed-table', 'tool_name': 'yield_query',
                'summary': '화면 확인용 데이터', 'status': 'success',
            }, [{'제품': '4SS', '수율': 98.5}], [{'title': '결과 미리보기', 'mime': 'text/html',
                'data': '<!doctype html><html><body><h1>검증된 결과</h1><p>4SS 수율 98.5%</p></body></html>'}])
            review = {'invocation_id': 'seed-review', 'tool_name': 'verify_answer', 'status': 'success',
                      'result_id': observation.result_id, 'summary': '저장된 결과와 답변 일치',
                      'scope': {'verdict': 'pass'}, 'validated_arguments': candidate_digest(answer)}
            await store.runs.update_one({'_id': run['run_id']}, {'$set': {
                'answer_text': answer, 'observations': [observation.model_dump(mode='json'), review]}})
            await store.finish(run['run_id'], run['epoch'], 'completed', answer, 'hermes_returned')
            # A second, failed revision has no candidate body anywhere in the fixture.
            failed = await sessions.start_run(sid, uuid.uuid4().hex, '실패 상태 예시')
            failed = await store.acquire(failed['run_id'], 'pr9-seed')
            await store.runs.update_one({'_id': failed['run_id']}, {'$set': {'observations': [{
                'invocation_id': 'failed-review', 'tool_name': 'verify_answer', 'status': 'error',
                'result_id': '', 'summary': '검증 근거 부족', 'scope': {'verdict': 'fail'}}]}})
            await store.finish(failed['run_id'], failed['epoch'], 'failed', '', 'verification_fail')


def make_app(uri, database, *, lifespan_factory=None):
    @asynccontextmanager
    async def lifespan(app):
        store = HarnessStore(uri=uri, database=database)
        app.state.harness = OfflineController(store)
        app.state.harness.settings = SimpleNamespace(principal='unused')
        gateway = GatewayServices(store, app.state.harness)
        app.state.gateway = gateway
        try:
            await store.setup()
            await seed(store, gateway)
            yield
        finally:
            try:
                await store.client.drop_database(database)
            finally:
                store.client.close()
    app = FastAPI(lifespan=lifespan if lifespan_factory is None else lifespan_factory(uri, database, seed))
    resolver = Resolver()

    @app.middleware('http')
    async def identity(request, call_next):
        if lifespan_factory is not None and request.url.path.startswith('/__dev/'):
            if request.client is None or request.client.host not in ('127.0.0.1', '::1'):
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail': 'Loopback only'}, status_code=403)
            return await call_next(request)
        try:
            principal = await resolver.resolve(request)
        except AuthenticationFailure:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail': 'Development identity required'}, status_code=401)
        request.state.principal_id = principal.user_id
        return await call_next(request)

    class Services:
        async def serve_ws(self, ws, principal, manifest, mode):
            return await ws.app.state.gateway.serve_ws(ws, principal, manifest, mode)
        async def serve_http(self, request, principal, row):
            return await request.app.state.gateway.serve_http(request, principal, row)

    app.include_router(build_gateway_router(resolver, origin_policy=DevPolicy(), ticket_verifier=DevPolicy(), rpc_mode='authority', services=Services()))
    app.include_router(build_gateway_router(resolver, origin_policy=DevPolicy(), ticket_verifier=DevPolicy(), rpc_mode='authority', services=Services()), prefix='/gateway-m1')
    app.include_router(result_router)
    return app


async def main():
    uri = os.environ.get('HARNESS_MONGO_URI')
    if not uri:
        raise SystemExit('HARNESS_MONGO_URI is required')
    import uvicorn
    database = 'zz_pr9_' + uuid.uuid4().hex
    sock = socket.socket()
    sock.bind(('127.0.0.1', int(os.environ.get('PR9_PORT', '8001'))))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(make_app(uri, database), log_level='critical', access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            if task.done():
                await task
                raise RuntimeError('Development fixture startup failed')
            await asyncio.sleep(0.02)
        print(json.dumps({'port': sock.getsockname()[1], 'db': database}), flush=True)
        await task
    finally:
        server.should_exit = True
        await task
        sock.close()


if __name__ == '__main__':
    asyncio.run(main())

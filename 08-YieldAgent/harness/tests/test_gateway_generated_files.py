"""Exercise web download tickets and immutable output refs against real localhost GridFS."""
import asyncio
import hashlib
import uuid
from urllib.parse import quote, urlencode

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from harness.gateway.auth import AuthenticationFailure, Principal
from harness.gateway.generated_files import DownloadTickets
from harness.gateway.routes import build_gateway_router
from harness.gateway.services import GatewayServices
from harness.gateway.session_store import MongoSessionStore
from harness.store import HarnessStore


class TestLogin:
    async def resolve(self, request):
        owner = request.headers.get('x-test-user')
        if owner is None:
            raise AuthenticationFailure(401)
        request.state.file_ticket_required = True
        return Principal(owner)


DOWNLOAD = '/gateway-m1/api/fs/download'
TICKET = '/gateway-m1/api/files/ticket'


async def check_stream(app, query, source):
    received = hashlib.sha256()
    chunks = []

    async def send(frame):
        if frame['type'] == 'http.response.start':
            assert frame['status'] == 200
        elif block := frame.get('body'):
            received.update(block)
            chunks.append(len(block))

    async def receive():
        await asyncio.Future()

    await app({'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
               'http_version': '1.1', 'method': 'GET', 'scheme': 'http', 'path': DOWNLOAD,
               'query_string': urlencode(query).encode(), 'headers': [(b'x-test-user', b'owner')],
               'server': ('test', 80), 'client': ('test', 0)}, receive, send)
    assert received.digest() == hashlib.sha256(source).digest()
    assert len(chunks) > 1
    assert max(chunks) <= 255 * 1024


def test_ticket_downloads_registered_files():
    async def scenario():
        store = HarnessStore(database='harness_download_test_' + uuid.uuid4().hex)
        try:
            await store.setup()
            sessions = MongoSessionStore(store, 'owner')
            await sessions.setup()
            await sessions.create_session('chat', 'gui')
            await sessions.create_session('other', 'gui')
            app = FastAPI()
            app.include_router(build_gateway_router(TestLogin(), services=GatewayServices(store, None)),
                               prefix='/gateway-m1')
            data = b''.join(hashlib.sha256(str(i).encode()).digest() * 4096 for i in range(160))
            cases = [
                ('맵.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                ('보고서 (1)%.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'),
                ('brief.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
                ('report.html', 'text/html'), ('legacy.csv', 'text/csv'), ('raw.unrecognized', None),
            ]
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                headers = {'x-test-user': 'owner'}
                for field in ('path', 'session_id', 'profile', 'ticket'):
                    malformed = {'route': 'download', 'path': '/outputs/run/result/a', 'session_id': 'chat', field: []}
                    assert (await client.post(TICKET, headers=headers, json=malformed)).status_code == 422
                for name, mime in cases:
                    body = data if mime is None else b'generated file content'
                    result_id = uuid.uuid4().hex
                    path = f'/outputs/run/{result_id}/{quote(name, safe="")}'
                    key = await store.blobs.upload_from_stream(name, body)
                    ref = {'artifact_id': str(key), 'title': name, 'output_path': path}
                    if mime:
                        ref['mime'] = mime
                    await store.results.insert_one({'principal_id': 'owner', 'session_id': 'chat',
                        'result_id': result_id, 'observation': {'run_id': 'run', 'invocation_id': 'generate',
                        'result_id': result_id, 'principal_id': 'owner', 'session_id': 'chat',
                        'tool_name': 'publish_csv' if name.endswith('.csv') else 'run_python',
                        'artifact_refs': [ref, {'artifact_id': str(key), 'title': 'private.txt'}]}})
                    query = {'path': path, 'session_id': 'chat', 'profile': 'default'}
                    head = await client.head(DOWNLOAD, headers=headers, params=query)
                    assert head.status_code == 200
                    assert head.headers['x-hermes-file-ticket'] == 'required'
                    assert head.headers['content-type'].split(';')[0] == (mime or 'application/octet-stream')
                    assert head.headers['content-disposition'] == "attachment; filename*=UTF-8''" + quote(name)
                    assert int(head.headers['content-length']) == len(body)
                    assert (await client.get(DOWNLOAD, headers=headers, params=query)).status_code == 403
                    mint = await client.post(TICKET, headers=headers, json={'route': 'download', **query})
                    assert mint.status_code == 200
                    signed = query | mint.json()
                    wrong_owner = await client.get(DOWNLOAD, headers={'x-test-user': 'other'}, params=signed)
                    assert wrong_owner.status_code == 403
                    if mime is None:
                        await check_stream(app, signed, body)
                    else:
                        downloaded = await client.get(DOWNLOAD, headers=headers, params=signed)
                        assert downloaded.status_code == 200 and downloaded.content == body
                        assert downloaded.headers['content-disposition'] == head.headers['content-disposition']
                    wrong = signed | {'session_id': 'other'}
                    assert (await client.get(DOWNLOAD, headers=headers, params=wrong)).status_code == 403
                    for _ in range(2):
                        assert (await client.head(DOWNLOAD, headers=headers, params=signed)).status_code == 200
                    assert (await client.head(DOWNLOAD, headers=headers, params=signed)).status_code == 403

                assert (await client.head(DOWNLOAD, params=query)).status_code == 401
                for user, change in [('other', {}), ('owner', {'session_id': 'other'}),
                                     ('owner', {'profile': 'other'}), ('owner', {'path': path.rsplit('/', 1)[0] + '/private.txt'})]:
                    response = await client.post(TICKET, headers={'x-test-user': user},
                        json={'route': 'download', **(query | change)})
                    assert response.status_code == 404

                mint = await client.post(TICKET, headers=headers, json={'route': 'download', **query})
                await sessions.delete_session('chat')
                assert (await client.get(DOWNLOAD, headers=headers, params=query | mint.json())).status_code == 404
        finally:
            try:
                await store.client.drop_database(store.db.name)
            finally:
                store.client.close()

    asyncio.run(scenario())


def test_download_ticket_expires(monkeypatch):
    clock = 10
    monkeypatch.setattr('harness.gateway.generated_files.time.monotonic', lambda: clock)
    tickets = DownloadTickets()
    ticket = tickets.mint('scope')
    clock += 60
    with pytest.raises(HTTPException) as exc:
        tickets.consume(ticket, 'scope')
    assert exc.value.status_code == 403

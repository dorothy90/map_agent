import asyncio
import io
import uuid
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from harness.router import router
from harness.store import HarnessStore


def test_only_owned_matrix_panel_ranges_are_readable():
    async def exercise():
        store = HarnessStore(database='matrix_delivery_' + uuid.uuid4().hex)
        try:
            await store.setup()
            panels = io.BytesIO(b'firstsecond')
            artifacts = [{'mime': 'text/html', 'data': 'matrix', 'matrix_panels': panels},
                         {'mime': 'text/html', 'data': 'legacy'}]
            call = {'run_id': 'r', 'invocation_id': 'c', 'tool_name': 'load_wafer_maps'}
            saved = await store.put_result('a', 'chat', call, [], artifacts)
            assert panels.closed
            assert len(saved.model_view()['artifact_refs']) == 2
            app = FastAPI()
            app.include_router(router)
            settings = SimpleNamespace(principal='a')
            app.state.harness = SimpleNamespace(store=store, settings=settings)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                def url(i=0):
                    return '/harness/artifacts/' + saved.artifact_refs[i]['artifact_id'] + '/matrix-panels'
                params = dict(session_id='chat', offset=5, length=6)
                response = await client.get(url(), params=params)
                assert response.status_code == 200 and response.content == b'second'
                assert (await client.get(url(), params=dict(params, length=7))).status_code == 416
                assert (await client.get(url(1), params=params)).status_code == 404
                assert (await client.get(url(), params=dict(params, session_id='else'))).status_code == 404
                settings.principal = 'other'
                assert (await client.get(url(), params=params)).status_code == 404
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(exercise())

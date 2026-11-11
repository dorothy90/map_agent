"""Synthetic artifacts through localhost GridFS, HTTP, replay, and history."""
import asyncio
import json
from types import SimpleNamespace
import uuid

import httpx
from fastapi import FastAPI, Request

from harness.gateway.events import SessionEvents, project_run_event
from harness.gateway.publication import candidate_digest
from harness.gateway.session_rpc import SessionRPC
from harness.gateway.session_store import MongoSessionStore
from harness.router import compatibility_chat, router
from harness.store import HarnessStore


def test_selected_artifacts_survive_gridfs_http_replay_history_and_legacy_sse():
    async def scenario():
        store = HarnessStore(uri='mongodb://127.0.0.1:27017',
            database='harness_artifact_delivery_test_' + uuid.uuid4().hex)
        session_id = 'maps & reports 한글'
        try:
            await store.setup()
            sessions = MongoSessionStore(store, 'owner')
            await sessions.setup()
            await sessions.create_session(session_id, 'gui')
            run = await store.start_run('owner', session_id, 'new', 'Return selected maps', engine='hermes')
            run = await store.acquire(run['run_id'], 'test-worker')
            files = [
                {'title': f'Map [{index}]', 'mime': 'image/png', 'data': b'fixture-png-' + str(index).encode()}
                for index in range(7)
            ] + [
                {'title': 'HTML report', 'mime': 'text/html', 'data': '<h1>Selected report</h1>'},
                {'title': 'Deck', 'mime': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                 'data': b'fixture-pptx'},
            ]
            observation = await store.put_result('owner', session_id,
                {'tool_name': 'render_maps', 'run_id': run['run_id'], 'invocation_id': 'maps'}, [], files)
            projected = observation.model_view()['artifact_refs']
            assert len(projected) == 9
            answer = '\n\n'.join(['Selected final outputs.', *(ref['markdown'] for ref in projected[5:])])
            review = {'tool_name': 'verify_answer', 'scope': {'verdict': 'pass'},
                'validated_arguments': candidate_digest(answer)}
            await store.runs.update_one({'_id': run['run_id']}, {'$set': {
                'answer_text': answer, 'observations': [observation.model_dump(mode='json'), review]}})
            for ref in observation.artifact_refs:
                await store.event(run['run_id'], 'artifact', {'artifact_id': ref['artifact_id']}, epoch=run['epoch'])
            await store.finish(run['run_id'], run['epoch'], 'completed', answer, 'hermes_returned')
            current = await store.get_run('owner', run['run_id'])
            live = project_run_event(current, current['events'][-1])
            assert live['payload']['text'] == answer
            assert all(ref['artifact_id'] not in answer for ref in projected[:5])

            replay = SessionEvents(store, 'owner')
            watermark = await replay.watermark(session_id)
            frames = await replay.since(session_id, watermark['replay_epoch'], 0)
            final_frames = [frame for frame in frames['events'] if frame['type'] == 'message.complete']
            assert [frame['payload']['text'] for frame in final_frames] == [answer]
            scope = SimpleNamespace(sessions=sessions,
                execution=SimpleNamespace(store=store, principal_id='owner'))
            history = await SessionRPC(scope, SimpleNamespace(mode='canonical')).history(session_id)
            assert [item['text'] for item in history if item['role'] == 'assistant'] == [answer]

            app = FastAPI()
            app.state.harness = SimpleNamespace(store=store, settings=SimpleNamespace(principal='owner'))
            app.include_router(router)

            @app.get('/fixture/legacy-stream')
            async def legacy_stream(request: Request):
                return await compatibility_chat(SimpleNamespace(
                    after_sequence=0, run_id=run['run_id'], resume_value=None,
                    session_id=session_id, query='Return selected maps'), request)

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
                for ref, source in zip(projected, files):
                    get = await client.get(ref['href'])
                    head = await client.head(ref['href'])
                    assert get.status_code == head.status_code == 200
                    expected = source['data'].encode() if isinstance(source['data'], str) else source['data']
                    assert get.content == expected
                    assert head.content == b''
                    assert get.headers['content-type'].startswith(ref['mime'])
                    assert get.headers['x-content-type-options'] == 'nosniff'
                    if ref['mime'] == 'text/html':
                        assert get.headers['content-disposition'] == 'inline'
                        assert "sandbox allow-scripts; frame-ancestors 'self'" in get.headers['content-security-policy']
                    else:
                        assert get.headers['content-disposition'] == 'attachment'
                wrong_session = await client.get('/harness/artifacts/' + projected[0]['artifact_id'],
                    params={'session_id': 'foreign'})
                assert wrong_session.status_code == 404
                app.state.harness.settings.principal = 'foreign'
                assert (await client.get(projected[0]['href'])).status_code == 404
                app.state.harness.settings.principal = 'owner'
                stream = await client.get('/fixture/legacy-stream')
                payloads = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith('data: ')]
                artifacts = [item for item in payloads if item['type'] == 'artifact']
                assert {item['artifact_id'] for item in artifacts} == {ref['artifact_id'] for ref in projected}
                assert [item['content'] for item in payloads if item['type'] == 'message'] == [answer]

            from harness.native_verification import inspect_answer_artifacts
            await store.results.update_one({'result_id': observation.result_id},
                {'$unset': {'observation.artifact_refs.0.result_id': ''}})
            historic_ref, _ = await store.artifact('owner', session_id, projected[0]['artifact_id'])
            assert historic_ref['result_id'] == observation.result_id
            inspected = await inspect_answer_artifacts(store, 'owner', session_id, projected[0]['markdown'])
            assert inspected['verdict'] == 'pass'
            assert inspected['selected_result_ids'] == [observation.result_id]

            await store.runs.update_one({'_id': run['run_id']}, {'$unset': {'artifact_delivery': ''}})
            legacy = await store.get_run('owner', run['run_id'])
            legacy_text = project_run_event(legacy, legacy['events'][-1])['payload']['text']
            assert all(ref['artifact_id'] in legacy_text for ref in projected if ref['mime'] in ('image/png', 'text/html'))
            legacy_history = await SessionRPC(scope, SimpleNamespace(mode='canonical')).history(session_id)
            assert [item['text'] for item in legacy_history if item['role'] == 'assistant'] == [legacy_text]
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())

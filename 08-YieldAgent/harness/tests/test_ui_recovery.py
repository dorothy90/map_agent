import asyncio
import uuid
from types import SimpleNamespace

import httpx
from fastapi import FastAPI, Request

from harness.config import Settings
from harness.router import router, compatibility_chat
from harness.store import HarnessStore
from models import ChatRequest


def test_snapshot_latest_pagination_and_question_history():
    from agent_server import get_session_history

    async def scenario():
        store = HarnessStore(database='harness_test_' + uuid.uuid4().hex)
        app = FastAPI()
        app.state.harness = SimpleNamespace(store=store, settings=Settings(enabled=True))
        app.include_router(router)
        app.add_api_route('/session/{session_id}/history', get_session_history, methods=['GET'])
        @app.post('/chat/stream')
        async def chat(body: ChatRequest, request: Request):
            return await compatibility_chat(body, request)
        try:
            await store.setup()
            for i in range(101):
                run = await store.start_run('local', 'session', f'r{i}', f'query{i}')
                run = await store.acquire(run['run_id'], 'test')
                if i < 100:
                    await store.finish(run['run_id'], run['epoch'], 'completed', f'answer{i}', 'verified')
            await store.event(run['run_id'], 'input_required', {'message': 'Which period?', 'interrupt_id': 'q'})
            await store.runs.update_one({'_id': run['run_id']}, {'$set': {'user_inputs': [{'question': {'interrupt_id': 'q'}, 'value': {'period': '4 weeks'}}]}})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                page = (await client.get('/harness/sessions/session?limit=10')).json()
                assert page['runs'][0]['run_id'] == run['run_id']
                assert len(page['runs']) == 10 and page['next_cursor']
                second = (await client.get('/harness/sessions/session', params={'limit': 10, 'cursor': page['next_cursor']})).json()
                assert not {r['run_id'] for r in page['runs']} & {r['run_id'] for r in second['runs']}
                snapshot = (await client.get('/session/session/history')).json()
                assert snapshot['latest_run']['run_id'] == run['run_id']
                assert snapshot['through_sequence'] == 0
                # Complete after snapshot: replay must still retrieve the final answer.
                await store.finish(run['run_id'], run['epoch'], 'completed', 'last answer', 'verified')
                replay = await client.post('/chat/stream', json={'query': '', 'session_id': 'session', 'run_id': snapshot['latest_run']['run_id'], 'after_sequence': snapshot['through_sequence']})
                assert 'last answer' in replay.text
                history = (await client.get('/session/session/history')).json()
                contents = [t['content'] for t in history['turns']]
                assert contents[-3:] == ['Which period?', 'period: 4 weeks', 'last answer']
                assert history['through_sequence'] > 0
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_table_selection_and_artifact_order_match_stream_and_history():
    import json
    from agent_server import get_session_history
    from harness.types import ResultTable

    async def scenario():
        store = HarnessStore(database='harness_test_' + uuid.uuid4().hex)
        app = FastAPI()
        app.state.harness = SimpleNamespace(store=store, settings=Settings(enabled=True))
        app.include_router(router)
        app.add_api_route('/session/{session_id}/history', get_session_history, methods=['GET'])
        @app.post('/chat/stream')
        async def chat(body: ChatRequest, request: Request):
            return await compatibility_chat(body, request)
        try:
            await store.setup()
            run = await store.start_run('local', 'session', 'req', 'tables')
            run = await store.acquire(run['run_id'], 'test')
            obs = await store.put_result('local', 'session', {'run_id': run['run_id'], 'invocation_id': 'i', 'tool_name': 'query'}, [],
                [{'mime': 'text/markdown', 'title': 'Second artifact', 'data': 'second'}, {'mime': 'text/markdown', 'title': 'First artifact', 'data': 'first'}],
                tables=[ResultTable(table_id='weekly', title='Weekly', rows=[{'week': 1}], complete=True), ResultTable(table_id='wafer', title='Wafer', rows=[{'wafer': 7}], complete=True)])
            await store.event(run['run_id'], 'tool_started', {'name': 'query', 'invocation_id': 'i', 'state': 'running', 'parent_invocation_id': 'parent', 'elapsed_seconds': 0})
            await store.event(run['run_id'], 'tool_finished', {'name': 'query', 'invocation_id': 'i', 'result_id': obs.result_id, 'summary': 'done', 'state': 'partial', 'parent_invocation_id': 'parent', 'elapsed_seconds': 1.5})
            for artifact in obs.artifact_refs:
                await store.event(run['run_id'], 'artifact', artifact)
            await store.finish(run['run_id'], run['epoch'], 'completed', 'answer', 'verified')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                assert (await client.get(f'/harness/results/{obs.result_id}?session_id=session')).status_code == 422
                selected = await client.get(f'/harness/results/{obs.result_id}?session_id=session&table_id=wafer')
                assert selected.json()['rows'] == [{'wafer': 7}]
                replay = await client.post('/chat/stream', json={'session_id': 'session', 'run_id': run['run_id'], 'query': ''})
                events = [json.loads(line[6:]) for line in replay.text.splitlines() if line.startswith('data: ')]
                statuses = [event for event in events if event['type'] == 'status']
                assert [event['state'] for event in statuses] == ['running', 'partial']
                assert statuses[-1]['invocation_id'] == 'i' and statuses[-1]['parent_invocation_id'] == 'parent' and statuses[-1]['elapsed'] == 1.5
                streamed = [event['artifact_id'] for event in events if event['type'] == 'artifact']
                history = (await client.get('/session/session/history')).json()
                restored = history['turns'][-1]['artifacts']
                assert [item['artifact_id'] for item in restored] == streamed
                assert [item['title'] for item in restored] == ['Weekly', 'Wafer', 'Second artifact', 'First artifact']
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_accepted_input_and_waiting_cancel_are_durable_over_http():
    import json
    from langgraph.checkpoint.memory import InMemorySaver
    from harness.control import RunController
    from agent_server import get_session_history

    async def scenario():
        store = HarnessStore(database='harness_test_' + uuid.uuid4().hex)
        service = RunController(store, Settings(enabled=True), InMemorySaver())
        # Keep this persistence/transport test independent of a model provider.
        async def no_launch(run_id):
            return None
        service.launch = no_launch
        app = FastAPI()
        app.state.harness = service
        app.include_router(router)
        app.add_api_route('/session/{session_id}/history', get_session_history, methods=['GET'])
        @app.post('/chat/stream')
        async def chat(body: ChatRequest, request: Request):
            return await compatibility_chat(body, request)
        try:
            await store.setup()
            run = await store.start_run('local', 'session', 'req', 'original')
            run = await store.acquire(run['run_id'], 'test')
            question = {'interrupt_id': 'q', 'message': '기간 질문', 'fields': []}
            await store.runs.update_one({'_id': run['run_id']}, {'$set': {'question': question}})
            await store.finish(run['run_id'], run['epoch'], 'waiting_user', '기간 질문', 'input_required', detail=question)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                payload = {'kind': 'input', 'request_id': 'answer', 'goal_revision': 1, 'interrupt_id': 'q', 'value': '4 weeks'}
                assert (await client.post(f'/runs/{run["run_id"]}/input', json=payload)).status_code == 200
                assert (await client.post(f'/runs/{run["run_id"]}/input', json=payload)).status_code == 200
                stored = await store.get_run('local', run['run_id'])
                assert len([event for event in stored['events'] if event['type'] == 'input_received']) == 1
                # Return to another genuine wait, then cancel through the public API.
                resumed = await store.acquire(run['run_id'], 'test')
                next_question = {'interrupt_id': 'q2', 'message': '제품 질문', 'fields': []}
                await store.runs.update_one({'_id': run['run_id']}, {'$set': {'question': next_question}})
                await store.finish(run['run_id'], resumed['epoch'], 'waiting_user', '제품 질문', 'input_required', detail=next_question)
                cancelled = await client.post(f'/runs/{run["run_id"]}/cancel')
                assert cancelled.json()['status'] == 'cancelled'
                history = (await client.get('/session/session/history')).json()
                assert [turn['content'] for turn in history['turns']][1:4] == ['기간 질문', '4 weeks', '제품 질문']
                replay = await client.post('/chat/stream', json={'query': '', 'session_id': 'session', 'run_id': run['run_id']})
                events = [json.loads(line[6:]) for line in replay.text.splitlines() if line.startswith('data: ')]
                assert [event['content'] for event in events if event['type'] == 'user_input'] == ['4 weeks']
                assert events[-1]['status'] == 'cancelled'
        finally:
            await service.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_session_router_recognizes_both_harness_versions_and_keeps_legacy():
    from harness.router import uses_harness

    async def scenario():
        store = HarnessStore(database='harness_test_' + uuid.uuid4().hex)
        app = FastAPI()
        app.state.harness = SimpleNamespace(store=store, settings=Settings(enabled=True))
        @app.get('/route/{session_id}')
        async def route(session_id: str, request: Request):
            return {'harness': await uses_harness(request, session_id)}
        try:
            await store.setup()
            for version in ['harness/v1', 'harness/v2', 'legacy', 'harness/v99']:
                await store.db.harness_sessions.insert_one({'principal_id': 'local', 'session_id': version, 'runtime': version})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                for version, expected in [('harness/v1', True), ('harness/v2', True), ('legacy', False), ('harness/v99', False)]:
                    # Direct request avoids interpreting the version slash as a path.
                    request = Request({'type': 'http', 'app': app, 'headers': []})
                    assert await uses_harness(request, version) is expected
                assert (await client.get('/route/new')).json()['harness']
                record = await store.db.harness_sessions.find_one({'session_id': 'new'})
                assert record['runtime'] == 'harness/v2'
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())

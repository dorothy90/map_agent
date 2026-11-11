import asyncio
import uuid
from types import SimpleNamespace

import httpx
from fastapi import FastAPI, Request

from harness.config import Settings
from harness.router import router, compatibility_chat
from harness.store import HarnessStore
from models import ChatRequest


def test_latest_pagination_and_run_replay():

    async def scenario():
        store = HarnessStore(database='harness_test_' + uuid.uuid4().hex)
        app = FastAPI()
        app.state.harness = SimpleNamespace(store=store, settings=Settings(enabled=True))
        app.include_router(router)
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
                snapshot = (await client.get(f"/runs/{run['run_id']}")).json()
                assert snapshot['run_id'] == run['run_id']
                # Complete after snapshot: replay must still retrieve the final answer.
                await store.finish(run['run_id'], run['epoch'], 'completed', 'last answer', 'verified')
                replay = await client.post('/chat/stream', json={'query': '', 'session_id': 'session', 'run_id': snapshot['run_id'], 'after_sequence': 0})
                assert 'last answer' in replay.text
                completed = (await client.get(f"/runs/{run['run_id']}")).json()
                assert completed['answer'] == 'last answer'
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_table_selection_and_artifact_stream_order():
    import json
    from harness.types import ResultTable

    async def scenario():
        store = HarnessStore(database='harness_test_' + uuid.uuid4().hex)
        app = FastAPI()
        app.state.harness = SimpleNamespace(store=store, settings=Settings(enabled=True))
        app.include_router(router)
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
                assert [event['title'] for event in events if event['type'] == 'artifact'] == ['Weekly', 'Wafer', 'Second artifact', 'First artifact']
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_accepted_input_and_waiting_cancel_are_durable_over_http():
    import json
    from harness.control import RunController

    async def scenario():
        store = HarnessStore(database='harness_test_' + uuid.uuid4().hex)
        service = RunController(store, Settings(enabled=True))
        # Keep this persistence/transport test independent of a model provider.
        async def no_launch(run_id):
            return None
        service.launch = no_launch
        app = FastAPI()
        app.state.harness = service
        app.include_router(router)
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
                replay = await client.post('/chat/stream', json={'query': '', 'session_id': 'session', 'run_id': run['run_id']})
                events = [json.loads(line[6:]) for line in replay.text.splitlines() if line.startswith('data: ')]
                assert [event['content'] for event in events if event['type'] == 'user_input'] == ['4 weeks']
                assert events[-1]['status'] == 'cancelled'
        finally:
            await service.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())

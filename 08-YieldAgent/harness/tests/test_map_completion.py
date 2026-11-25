import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from harness.gateway.events import SessionEvents
from harness.gateway.map_completion import publish_map_completion
from harness.gateway.session_rpc import SessionRPC
from harness.gateway.session_store import MongoSessionStore
from harness.store import HarnessStore


def test_map_completion_follows_settled_turn_and_replays_once_to_original_owner():
    async def scenario():
        store = HarnessStore(database='map_notice_test_' + uuid4().hex)
        try:
            await store.setup()
            owner, sid = 'alice', 'original'
            sessions = MongoSessionStore(store, owner)
            await sessions.setup()
            await sessions.create_session(sid, 'gui')
            await sessions.create_session('other', 'gui')
            await MongoSessionStore(store, 'bob').create_session('b', 'gui')
            run = await store.start_run(owner, sid, 'r1', 'Draw maps', engine='hermes')
            rid = run['run_id']
            result = await store.put_result(owner, sid, dict(run_id=rid, tool_name='load_wafer_maps',
                invocation_id='i', status='partial', scope={'map_aggregate': {'source_exhausted': True,
                'measurement_count': 4, 'included_measurement_count': 3}}), [],
                [{'mime': 'text/html', 'title': 'Map', 'data': '<main>Map</main>'}])
            job = dict(_id='j', principal_id=owner, session_id=sid, run_id=rid, invocation_id='i',
                       result_id=result.result_id, process_id='proc')
            notice = dict(type='completion', session_key=sid, task_id='j', owner_task_id='j', session_id='proc',
                          completion_reason='exited', exit_code=0, command='load_wafer_maps')
            job.update(status='completed', delivery='background', notice=notice)
            await store.db.map_jobs.insert_one(job)
            for key, changed in [('exit_code', 1), ('session_key', 'other'), ('task_id', 'wrong'), ('session_id', 'foreign')]:
                with pytest.raises(PermissionError):
                    await publish_map_completion(store, job, {**notice, key: changed})
            with pytest.raises(PermissionError):
                await publish_map_completion(store, {**job, 'principal_id': 'bob'}, notice)
            await store.runs.update_one({'_id': rid}, {'$set': {'active': False, 'status': 'completed',
                'answer_text': 'Accepted', 'native_engine': {'entrypoint': 'stock_chat'}}})
            other = await store.start_run(owner, sid, 'r2', 'Another question', engine='hermes')
            with pytest.raises(BlockingIOError):
                await publish_map_completion(store, job, notice)
            await store.runs.update_one({'_id': other['run_id']}, {'$set': {'active': False, 'status': 'completed',
                'answer_text': 'Second answer', 'native_engine': {'entrypoint': 'stock_chat'}}})
            delivered = await publish_map_completion(store, job, notice)
            assert delivered == await publish_map_completion(store, job, notice)
            assert '대상 측정 4건 중 3건' in delivered['text']
            assert result.artifact_refs[0]['artifact_id'] in delivered['text']
            for principal, session, expected in [(owner, sid, True), (owner, 'other', False), ('bob', 'b', False)]:
                rpc = SessionRPC(SimpleNamespace(sessions=MongoSessionStore(store, principal),
                    execution=SimpleNamespace(store=store, principal_id=principal)), SimpleNamespace(mode='legacy'))
                rows = await rpc.history(session)
                if expected:
                    assert [row['text'] for row in rows] == ['Draw maps', 'Accepted', 'Another question',
                                                               'Second answer', delivered['text']]
                else:
                    assert rows == []
                events = SessionEvents(store, principal)
                epoch = (await events.watermark(session))['replay_epoch']
                frames = (await events.since(session, epoch))['events']
                assert [frame['type'] for frame in frames] == (['message.start', 'message.complete'] if expected else [])
                if expected:
                    assert frames[-1]['payload']['text'] == delivered['text']
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_exhausted_launch_failure_delivers_only_after_verified_background_receipt():
    from time import time
    from harness.gateway.map_completion import publish_map_job_failure
    from harness.map_jobs import MAX_ATTEMPTS

    async def scenario():
        store = HarnessStore(database='map_notice_test_' + uuid4().hex)
        try:
            await store.setup()
            sessions = MongoSessionStore(store, 'alice')
            await sessions.setup()
            await sessions.create_session('s', 'gui')
            run = await store.start_run('alice', 's', 'r1', 'Map', engine='hermes')
            rid = run['run_id']
            job = dict(_id='dead-job', principal_id='alice', session_id='s', run_id=rid, invocation_id='call',
                       status='failed', delivery='background', lease_until=time() - 1, attempts=MAX_ATTEMPTS)
            jobs = store.db.map_jobs
            await jobs.insert_one(job)
            with pytest.raises(BlockingIOError):
                await publish_map_job_failure(store, job)
            await store.runs.update_one({'_id': rid}, {'$set': {'active': False, 'status': 'completed'}})
            with pytest.raises(PermissionError):
                await publish_map_job_failure(store, job)
            result = await store.put_result('alice', 's', dict(run_id=rid, invocation_id='call', tool_name='load_wafer_maps',
                scope={'background_map': {'job_id': job['_id'], 'status': 'processing'}}), [], [])
            await store.calls.insert_one(dict(run_id=rid, invocation_id='call', tool_name='load_wafer_maps',
                                             status='succeeded', result_id=result.result_id))
            for key, wrong in [('attempts', MAX_ATTEMPTS - 1), ('lease_until', time() + 30), ('delivery', 'inline')]:
                await jobs.update_one({'_id': job['_id']}, {'$set': {key: wrong}})
                with pytest.raises(PermissionError):
                    await publish_map_job_failure(store, job)
                await jobs.update_one({'_id': job['_id']}, {'$set': {key: job[key]}})
            with pytest.raises(PermissionError):
                await publish_map_job_failure(store, {**job, 'session_id': 'other'})
            posted = await publish_map_job_failure(store, job)
            assert '집계를 완료하지 못했습니다' in posted['text'] and '/harness/' not in posted['text']
            assert await publish_map_job_failure(store, job) == posted
            assert 'notice' not in await jobs.find_one({'_id': job['_id']})
            events = SessionEvents(store, 'alice')
            epoch = (await events.watermark('s'))['replay_epoch']
            frames = (await events.since('s', epoch))['events']
            assert [f['type'] for f in frames] == ['message.start', 'message.complete']
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())

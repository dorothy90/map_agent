"""Deliver a map worker's Hermes completion to the chat that submitted it."""
import time

from ..artifact_delivery import artifact_links
from ..store import Conflict
from ..types import now


async def _completion_body(store, job, notice):
    failed = notice.get('exit_code') != 0 or notice.get('completion_reason') != 'exited'
    if failed:
        return '웨이퍼 맵 집계를 완료하지 못했습니다.'
    result = await store.observation(job['principal_id'], job['session_id'], job['result_id'])
    if (result.tool_name != 'load_wafer_maps' or result.run_id != job['run_id']
            or result.invocation_id != job['invocation_id']):
        raise PermissionError('Map result does not belong to the originating call')
    if result.status in ('error', 'cancelled'):
        return '웨이퍼 맵 집계가 중지되었습니다.' if result.status == 'cancelled' else '웨이퍼 맵 집계를 완료하지 못했습니다.'
    facts = result.scope.get('map_aggregate') or {}
    refs = [ref for ref in result.artifact_refs if ref.get('mime') == 'text/html' or
            (ref.get('mime') or '').startswith('image/')]
    if result.status == 'empty' or (not refs and facts.get('included_measurement_count') == 0):
        return '조회 조건에 맞는 웨이퍼 맵이 없습니다.'
    if facts.get('source_exhausted') is not True or not refs:
        return '웨이퍼 맵 집계를 완료하지 못했습니다.'
    links = artifact_links(result.session_id, result.result_id, refs)
    summary = (f"웨이퍼 맵 집계가 끝났습니다. 대상 측정 {facts['measurement_count']:,}건 중 "
               f"{facts['included_measurement_count']:,}건을 포함했습니다.")
    return '\n\n'.join([summary, *(link['markdown'] for link in links)])


async def publish_map_completion(store, job, notice):
    owner, sid, rid = job['principal_id'], job['session_id'], job['run_id']
    job_id = str(job['_id'])
    expected = dict(type='completion', session_id=job['process_id'], session_key=sid, task_id=job_id,
                    owner_task_id=job_id, command='load_wafer_maps')
    if any(notice.get(k) != v for k, v in expected.items()) or type(notice.get('exit_code')) is not int:
        raise PermissionError('Mismatched Hermes map completion')
    scope = dict(principal_id=owner, session_id=sid)
    source = await store.db.map_jobs.find_one({**scope, '_id': job['_id'], 'run_id': rid,
                                               'invocation_id': job['invocation_id'], 'process_id': job['process_id']})
    if (not source or source.get('status') != 'completed' or source.get('delivery') not in ('background', 'delivered')
            or source.get('notice') != notice or source.get('result_id') != job.get('result_id')):
        raise PermissionError('Unbound map job')
    return await _post_notice(store, source, await _completion_body(store, source, notice))


async def publish_map_job_failure(store, job):
    from ..map_jobs import MAX_ATTEMPTS

    bound = {key: job[key] for key in ('_id', 'principal_id', 'session_id', 'run_id', 'invocation_id')}
    source = await store.db.map_jobs.find_one({**bound, 'status': 'failed', 'delivery': {'$in': ['background', 'delivered']},
        'attempts': {'$gte': MAX_ATTEMPTS}, 'lease_until': {'$lt': time.time()}, 'notice': {'$exists': False}})
    if not source:
        raise PermissionError('No exhausted background job')
    accepted = await store.calls.find_one({'run_id': job['run_id'], 'invocation_id': job['invocation_id'],
                                          'tool_name': 'load_wafer_maps', 'status': 'succeeded'}, {'result_id': 1})
    if not accepted:
        if await store.runs.find_one({'_id': job['run_id'], 'principal_id': job['principal_id'],
                                      'session_id': job['session_id'], 'active': True}, {'_id': 1}):
            raise BlockingIOError('Background receipt is pending')
        raise PermissionError('No accepted map invocation')
    receipt = await store.observation(job['principal_id'], job['session_id'], accepted['result_id'])
    if (receipt.run_id != job['run_id'] or receipt.invocation_id != job['invocation_id']
            or receipt.tool_name != 'load_wafer_maps' or receipt.scope.get('background_map', {}).get('job_id') != job['_id']):
        raise PermissionError('Background receipt does not match the job')
    return await _post_notice(store, source, '웨이퍼 맵 집계를 완료하지 못했습니다. 집계 작업을 다시 시도했지만 결과를 받지 못했습니다.')


async def _post_notice(store, job, text):
    owner, sid, rid = job['principal_id'], job['session_id'], job['run_id']
    scope = dict(principal_id=owner, session_id=sid)
    job_id = str(job['_id'])
    event_id = 'map-complete:' + job_id
    try:
        async with store.session_lock(owner, sid):
            visible_chat = {'principal_id': owner, 'id': sid, 'deleted': {'$ne': True}}
            if not await store.db.gateway_sessions.find_one(visible_chat, {'_id': 1}):
                raise LookupError('Originating chat unavailable')
            prior = await store.runs.find_one({**scope, 'events.event_id': event_id}, {'events.$': 1})
            if prior:
                return prior['events'][0]['payload']
            origin = await store.runs.find_one({**scope, '_id': rid}, {'status': 1})
            if not origin or origin['status'] in ('cancelled', 'cancelling'):
                raise PermissionError('Originating request unavailable')
            if await store.runs.find_one({**scope, 'active': True}, {'_id': 1}):
                raise BlockingIOError('Chat has an active turn')
            payload = dict(job_id=job_id, originating_run_id=rid, delivered_at=now(), text=text)
            # Lock shared with prompt admission: both frames follow the last settled
            # reply, even if another turn ran while the map was computing.
            last = await store.runs.find_one(scope, {'run_id': 1, 'events': {'$slice': -1}},
                                             sort=[('created_at', -1), ('_id', -1)])
            sequence = last['events'][-1]['sequence'] if last['events'] else 0
            frames = [
                {'type': 'map_notice_start', 'event_id': event_id + ':start', 'payload': {}, 'sequence': sequence + 1},
                {'type': 'map_notice_complete', 'event_id': event_id, 'payload': payload, 'sequence': sequence + 2},
            ]
            result = await store.runs.update_one({**scope, '_id': last['_id'], 'active': False,
                'events.event_id': {'$ne': event_id}}, {'$push': {'events': {'$each': frames}}})
            if not result.modified_count:
                raise BlockingIOError('Chat admission changed')
            return payload
    except Conflict as exc:
        raise BlockingIOError('Chat admission is busy') from exc

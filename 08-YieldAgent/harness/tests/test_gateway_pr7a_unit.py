"""Offline PR-7a checks: no Mongo, sockets, workers or model calls."""
import asyncio
import importlib.util


def test_pr7a_library_exists():
    assert importlib.util.find_spec('harness.gateway.events') is not None
    assert importlib.util.find_spec('harness.gateway.execution') is not None


def test_ring_frame_watermarks_and_epoch():
    from harness.gateway.events import new_log, retain, replay, event_frame, replay_gap
    log = new_log('a', 's')
    epoch = log['epoch']
    for n in range(514):
        retain(log, {'type': 'message.start', 'session_id': 's', 'payload': {'sequence': 1}})
    assert log['seq'] == 514
    assert len(log['ring']) == 512
    assert replay(log, epoch, 0)['snapshot_required'] is True
    result = replay(log, epoch, 2)
    assert result['count'] == 512 and not result['truncated']
    assert result == replay(log, epoch, 2)
    assert result['events'][0]['seq'] == 3
    assert result['events'][0]['payload']['sequence'] == 1
    assert event_frame(result['events'][0])['method'] == 'event'
    assert replay(log, 'old', 514)['events'] == []
    assert replay(log, epoch, 515)['truncated']
    assert replay_gap('s', epoch, 514) == {'jsonrpc': '2.0', 'method': 'event', 'params': {
        'type': 'session.replay_gap', 'session_id': 's', 'payload': {'replay_epoch': epoch, 'latest_seq': 514}}}


def test_projection_never_replays_drafts():
    from harness.gateway.events import project_run_event
    run = {'run_id': 'r', 'status': 'running', 'active': True}
    for kind in ('message.delta', 'message.interim', 'message.complete', 'commentary'):
        assert project_run_event(run, {'type': kind, 'payload': {'content': 'DRAFT'}}) is None
    for kind in ('reasoning', 'thinking', 'agent.print', 'message.start'):
        result = project_run_event(run, {'type': kind, 'payload': {'content': 'DRAFT'}})
        assert 'DRAFT' not in str(result)


def test_projection_maps_internal_tool_events_to_hermes_lifecycle_payloads():
    from harness.gateway.events import project_run_event

    run = {'run_id': 'r', 'status': 'running', 'active': True}
    args = {'lot': 'L-1'}
    start = project_run_event(run, {'type': 'tool_started', 'payload': {
        'name': 'query_db', 'invocation_id': 'call-1', 'args': args,
        'context': '{"lot":"L-1"}', 'state': 'running', 'elapsed_seconds': 0}})
    assert start == {'type': 'tool.start', 'payload': {
        'tool_id': 'call-1', 'name': 'query_db', 'args': args,
        'context': '{"lot":"L-1"}'}}

    result = {'status': 'error', 'summary': 'query failed', 'error': {'code': 'QueryError'}}
    complete = project_run_event(run, {'type': 'tool_finished', 'payload': {
        'name': 'query_db', 'invocation_id': 'call-1', 'args': args,
        'result': result, 'summary': 'query failed', 'status': 'error',
        'elapsed_seconds': 1.25, 'result_id': 'stored-result'}})
    assert complete == {'type': 'tool.complete', 'payload': {
        'tool_id': 'call-1', 'name': 'query_db', 'args': args,
        'result': result, 'summary': 'query failed', 'duration_s': 1.25}}


def test_projection_preserves_canonical_tool_payloads_and_progress_message():
    from harness.gateway.events import project_run_event

    run = {'run_id': 'r', 'status': 'running', 'active': True}
    complete_payload = {'tool_id': 'call-1', 'name': 'query_db',
        'args': {'lot': 'L-1'}, 'result': {'status': 'success'},
        'summary': 'done', 'duration_s': .5}
    assert project_run_event(run, {'type': 'tool.complete', 'payload': complete_payload}) == {
        'type': 'tool.complete', 'payload': complete_payload}
    progress_payload = {'tool_id': 'call-1', 'name': 'query_db',
        'progress': 2.0, 'total': 4.0, 'percent': 50, 'message': '2/4 lots'}
    assert project_run_event(run, {'type': 'tool.progress', 'payload': progress_payload}) == {
        'type': 'tool.progress', 'payload': progress_payload}


def test_receipt_states_and_validation():
    from harness.gateway.execution import receipt_value, submit_values, RpcError
    import pytest
    run = {'run_id': 'r', 'epoch': 1, 'active': True, 'status': 'running'}
    row = {'admission_id': 'a', 'sequence': 3, 'authority_epoch': 1}
    value = receipt_value(row, run, 'profile', 's')
    assert value['status'] == 'started' and value['run_id'] == 'r'
    run.update(active=False, status='cancelled')
    assert receipt_value(row, run, 'profile', 's')['outcome'] == 'interrupted'
    assert submit_values({'session_id': 's', 'text': 'hello', 'submission_id': 'id'}) == ('id', 'hello')
    with pytest.raises(RpcError) as info:
        submit_values({'session_id': 's', 'text': 'hello'})
    assert info.value.error == {'code': 4001, 'message': 'invalid_params', 'data': {'reason': 'invalid_params'}}

# A small Mongo operation fake. Real-Mongo tests exercise server atomicity separately.
from copy import deepcopy
from types import SimpleNamespace
import time
import uuid

from pymongo.errors import DuplicateKeyError

_MISSING = object()


def _value(row, key):
    for part in key.split('.'):
        if not isinstance(row, dict) or part not in row:
            return _MISSING
        row = row[part]
    return row


def _matches(row, query):
    for key, expected in query.items():
        if key == '$or':
            if not any(_matches(row, q) for q in expected):
                return False
            continue
        value = _value(row, key)
        if isinstance(expected, dict):
            for op, arg in expected.items():
                if op == '$exists' and (value is not _MISSING) != arg:
                    return False
                if op == '$ne' and value == arg:
                    return False
                if op == '$lt' and (value is _MISSING or not value < arg):
                    return False
                if op == '$gt' and (value is _MISSING or not value > arg):
                    return False
                if op == '$in' and value not in arg:
                    return False
                if op == '$type' and not (isinstance(value, (int, float)) if arg == 'number' else isinstance(value, str)):
                    return False
        elif isinstance(value, list):
            if expected not in value:
                return False
        elif value is _MISSING:
            if expected is not None:
                return False
        elif value != expected:
            return False
    return True


class Cursor:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

    def sort(self, keys, direction=None):
        for key, order in reversed([(keys, direction)] if isinstance(keys, str) else keys):
            self.rows.sort(key=lambda r: r.get(key, ''), reverse=order < 0)
        return self

    async def to_list(self, length=None):
        return deepcopy(self.rows if length is None else self.rows[:length])

    def __aiter__(self):
        self.iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return deepcopy(next(self.iterator))
        except StopIteration:
            raise StopAsyncIteration


class Collection:
    def __init__(self):
        self.rows, self.indexes = [], []

    async def create_index(self, keys, unique=False, **kwargs):
        if unique:
            item = ([keys] if isinstance(keys, str) else [k for k, _ in keys], kwargs.get('partialFilterExpression', {}))
            if item not in self.indexes:
                self.indexes.append(item)

    def _unique(self, row, exclude=None):
        for keys, partial in self.indexes:
            if _matches(row, partial):
                for other in self.rows:
                    if other is not exclude and _matches(other, partial) and all(other.get(k) == row.get(k) for k in keys):
                        raise DuplicateKeyError('duplicate')

    async def insert_one(self, row):
        await asyncio.sleep(0)
        row = deepcopy(row)
        row.setdefault('_id', uuid.uuid4().hex)
        self._unique(row)
        self.rows.append(row)
        return SimpleNamespace(inserted_id=row['_id'])

    def find(self, query, projection=None):
        return Cursor([r for r in self.rows if _matches(r, query)])

    async def find_one(self, query, projection=None, sort=None):
        await asyncio.sleep(0)
        rows = self.find(query)
        if sort:
            rows.sort(sort)
        return deepcopy(rows.rows[0]) if rows.rows else None

    async def count_documents(self, query):
        return len(self.find(query).rows)

    async def replace_one(self, query, row, upsert=False):
        await asyncio.sleep(0)
        for idx, old in enumerate(self.rows):
            if _matches(old, query):
                self._unique(row, exclude=old)
                self.rows[idx] = deepcopy(row)
                return SimpleNamespace(modified_count=1, matched_count=1)
        if upsert:
            await self.insert_one(row)
        return SimpleNamespace(modified_count=0, matched_count=0)

    @staticmethod
    def _set(row, key, value, increment=False):
        parts = key.split('.')
        for part in parts[:-1]:
            row = row.setdefault(part, {})
        row[parts[-1]] = row.get(parts[-1], 0) + value if increment else deepcopy(value)

    async def find_one_and_update(self, query, update, upsert=False, return_document=False):
        await asyncio.sleep(0)
        old = next((r for r in self.rows if _matches(r, query)), None)
        if old is None and not upsert:
            return None
        row = deepcopy(old) if old is not None else {k: v for k, v in query.items() if not k.startswith('$') and not isinstance(v, dict)}
        row.setdefault('_id', uuid.uuid4().hex)
        for op, values in update.items():
            if op == '$setOnInsert' and old is not None:
                continue
            for key, value in values.items():
                if op == '$unset':
                    row.pop(key, None)
                else:
                    self._set(row, key, value, op == '$inc')
        self._unique(row, exclude=old)
        before = deepcopy(old)
        if old is None:
            self.rows.append(row)
        else:
            old.clear()
            old.update(row)
        return deepcopy(row) if return_document else before

    async def update_one(self, query, update, upsert=False):
        result = await self.find_one_and_update(query, update, upsert=upsert, return_document=True)
        return SimpleNamespace(modified_count=int(result is not None), matched_count=int(result is not None))


class Database:
    def __init__(self):
        self.collections = {}

    def __getattr__(self, key):
        return self.collections.setdefault(key, Collection())


async def fixture():
    from harness.store import HarnessStore
    from harness.control import RunController
    from harness.gateway.execution import Execution
    from harness.gateway.pending_controls import PendingControls
    store = HarnessStore.__new__(HarnessStore)
    store.db = Database()
    store.runs, store.calls, store.results = store.db.harness_runs, store.db.harness_invocations, store.db.harness_results
    # These writes are not part of the fake query engine's Mongo pipeline subset.
    async def event(run_id, kind, payload, *, event_key=None, epoch=None):
        row = next(r for r in store.runs.rows if r['run_id'] == run_id)
        if row['status'] not in ('running', 'cancelling') or (epoch is not None and row['epoch'] != epoch):
            return None
        if event_key and any(e.get('event_key') == event_key for e in row['events']):
            return None
        row['events'].append({'run_id': run_id, 'type': kind, 'payload': deepcopy(payload),
            'sequence': len(row['events']) + 1, 'event_id': uuid.uuid4().hex, 'event_key': event_key})
        return deepcopy(row)
    async def finish(run_id, epoch, status, answer, reason):
        row = next(r for r in store.runs.rows if r['run_id'] == run_id)
        row.update(status=status, active=False, answer=answer, stop_reason=reason)
        return deepcopy(row)
    store.event, store.finish = event, finish

    class OfflineController(RunController):
        async def launch(self, run_id):
            run = await self.store.acquire(run_id, self.owner)
            if run:
                self.launches.append(run_id)
    controller = OfflineController(store, SimpleNamespace(engine='hermes'))
    controller.launches = []
    await store.setup()
    await PendingControls(store, 'a').setup()
    for principal in ('a', 'b'):
        await store.db.gateway_sessions.insert_one({'principal_id': principal, 'id': 's', 'runtime_revision': 0})
    return store, controller, Execution(controller, 'a', profile_id='profile'), Execution(controller, 'b', profile_id='profile')


def test_ack_loss_concurrent_submit_receipt_and_principal_isolation():
    async def scenario():
        from harness.gateway.execution import Execution
        store, controller, a, b = await fixture()
        params = {'session_id': 's', 'submission_id': 'id', 'text': 'hello'}
        replies = await asyncio.gather(*(a.rpc('prompt.submit', 's', params) for _ in range(8)))
        assert all('result' in r for r in replies), replies
        assert len({r['result']['run_id'] for r in replies}) == 1
        assert len(controller.launches) == 1
        resumed = Execution(controller, 'a', profile_id='profile')
        assert await resumed.submit('s', params) == replies[0]['result']
        assert (await a.rpc('prompt.submit', 's', {**params, 'text': 'different'}))['error']['message'] == 'admission_conflict'
        assert (await a.rpc('prompt.submit', 's', {**params, 'submission_id': 'next'}))['error']['message'] == 'session_busy'
        assert (await b.rpc('prompt.receipt', 's', {'session_id': 's', 'admission_id': replies[0]['result']['admission_id']}))['error']['message'] == 'not_found'
        assert (await b.submit('s', params))['run_id'] != replies[0]['result']['run_id']
    asyncio.run(scenario())


def test_submit_conflict_run_appears_between_identity_and_active_reads():
    async def scenario():
        from harness.store import Conflict
        store, controller, a, _ = await fixture()
        start = controller.start
        active = a._active
        calls = 0

        async def contended(*args):
            raise Conflict('Session update in progress; retry this request')

        async def delayed_active(session_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                return None  # Lock is held, but no run has been inserted yet.
            if calls == 2:
                await start('a', 's', 'id', 'hello')
            return await active(session_id)

        controller.start = contended
        a._active = delayed_active
        params = {'session_id': 's', 'submission_id': 'id', 'text': 'hello'}
        reply = await a.rpc('prompt.submit', 's', params)
        assert 'result' in reply, reply
        assert reply['result'] == await a.submit('s', params)
        assert len(controller.launches) == len(store.runs.rows) == 1
    asyncio.run(scenario())


def test_delayed_admission_same_id_64_and_distinct_ids_16():
    async def scenario(count, same_id):
        store, controller, a, _ = await fixture()
        start_run = store.start_run

        async def delayed_start(*args, **kwargs):
            await asyncio.sleep(.05)  # Hold the real controller session lock.
            return await start_run(*args, **kwargs)

        store.start_run = delayed_start
        replies = await asyncio.gather(*(a.rpc('prompt.submit', 's', {
            'session_id': 's', 'submission_id': 'id' if same_id else str(n),
            'text': 'hello'}) for n in range(count)))
        successes = [r['result'] for r in replies if 'result' in r]
        assert len(successes) == (count if same_id else 1), replies
        assert all(r == successes[0] for r in successes)
        assert all(r['error']['message'] == 'session_busy' and r['error']['code'] == 4001
                   for r in replies if 'error' in r)
        assert len(controller.launches) == len(store.runs.rows) == 1
    asyncio.run(scenario(64, True))
    asyncio.run(scenario(16, False))


def test_submit_lock_retry_is_bounded(monkeypatch):
    async def scenario():
        from harness.store import Conflict
        _, controller, a, _ = await fixture()
        attempts = 0
        sleep = asyncio.sleep

        async def contended(*args):
            nonlocal attempts
            attempts += 1
            raise Conflict('Session update in progress; retry this request')

        async def fast_sleep(delay):
            await sleep(0)

        controller.start = contended
        monkeypatch.setattr(asyncio, 'sleep', fast_sleep)
        reply = await a.rpc('prompt.submit', 's', {
            'session_id': 's', 'submission_id': 'id', 'text': 'hello'})
        assert reply['error'] == {'code': 5001, 'message': 'storage_unavailable',
                                  'data': {'reason': 'storage_unavailable'}}
        assert attempts == 100
        assert controller.launches == []
    asyncio.run(scenario())


def test_pull_projection_restart_dedupe_and_run_boundaries():
    async def scenario():
        from harness.gateway.events import SessionEvents
        from harness.gateway.publication import candidate_digest
        store, controller, a, b = await fixture()
        first = await a.submit('s', {'session_id': 's', 'submission_id': '1', 'text': 'hello'})
        run = next(r for r in store.runs.rows if r['run_id'] == first['run_id'])
        for kind in ('message.start', 'message.delta', 'message.complete', 'thinking'):
            await store.event(run['run_id'], kind, {'text': 'DRAFT'})
        epoch = (await a.events.watermark('s'))['replay_epoch']
        await asyncio.gather(*(a.events.ingest('s') for _ in range(5)))
        replayed = await a.events.since('s', epoch)
        assert replayed['count'] == 2 and 'DRAFT' not in str(replayed)
        await store.event(run['run_id'], 'run_finished', {'answer': 'DRAFT'})
        run.update(active=False, status='completed', answer_text='FINAL', observations=[{
            'tool_name': 'verify_answer', 'scope': {'verdict': 'pass'}, 'validated_arguments': candidate_digest('FINAL')}])
        await a.events.ingest('s')
        second = await a.submit('s', {'session_id': 's', 'submission_id': '2', 'text': 'next'})
        await store.event(second['run_id'], 'message.start', {})
        restarted = SessionEvents(store, 'a')
        result = await restarted.since('s', epoch)
        assert [e['seq'] for e in result['events']] == [1, 2, 3, 4]
        assert result['events'][2]['payload']['text'] == 'FINAL'
        assert result['events'][3]['payload']['sequence'] == 1
        assert (await b.events.since('s', (await b.events.watermark('s'))['replay_epoch']))['events'] == []
        await restarted.rebuild('s')
        assert (await restarted.since('s', epoch, 4))['snapshot_required']
    asyncio.run(scenario())


def test_unknown_resolution_never_submits_again_and_cancel_started_is_noop():
    async def scenario():
        store, controller, a, _ = await fixture()
        receipt = await a.submit('s', {'session_id': 's', 'submission_id': 'id', 'text': 'hello'})
        params = {'session_id': 's', 'admission_id': receipt['admission_id']}
        assert (await a.cancel('s', params))['status'] == 'started'
        row = store.runs.rows[0]
        row['lease_until'] = 0
        await a.recover_unknown('s')
        assert (await a.receipt('s', params))['status'] == 'unknown'
        result = await a.resolve_unknown('s', {**params, 'execution_generation': 1})
        assert result['status'] == 'terminal' and result['outcome'] == 'interrupted'
        assert await a.resolve_unknown('s', {**params, 'execution_generation': 1}) == result
        assert len(controller.launches) == 1 and len(store.runs.rows) == 1
    asyncio.run(scenario())


def test_pending_adapter_wires_duplicates_and_answer_redaction():
    async def scenario():
        store, controller, a, b = await fixture()
        run = await a.submit('s', {'session_id': 's', 'submission_id': 'id', 'text': 'hello'})
        await a.pending.register_clarify('s', 1, {'clarify_id': 'question', 'questions': [
            {'qid': 'q1', 'question': 'First'}, {'qid': 'q2', 'question': 'Second'}]}, run_id=run['run_id'])
        assert await a.clarify_lock('s', {'request_id': 'question', 'question_id': 'q1', 'answer': 'OLD'}) == {'status': 'ok', 'remaining': ['q2']}
        assert await a.clarify_lock('s', {'request_id': 'question', 'question_id': 'q1', 'answer': 'PRIVATE'}) == {'status': 'ok', 'remaining': ['q2']}
        assert await b.request_answer('s', {'id': 'question', 'result': {'answers': {'q2': 'FOREIGN'}}}) == {'status': 'expired'}
        assert await a.request_answer('s', {'id': 'question', 'result': {'answers': {'q2': 'PRIVATE2'}}}) == {'status': 'ok'}
        assert await a.pending.wait('question', 0) == {'answers': {'q1': 'PRIVATE', 'q2': 'PRIVATE2'}, 'outcome': 'submitted'}
        await a.pending.register_clarify('s', 1, {'clarify_id': 'single', 'question': 'Question', 'qid': 'q'}, run_id=run['run_id'])
        params = {'session_id': 's', 'execution_generation': 1, 'prompt_id': 'single', 'answer': 'PRIVATE'}
        assert (await a.rpc('clarify.respond', 's', params))['result']['status'] == 'resolved'
        assert (await a.rpc('clarify.respond', 's', params))['result']['status'] == 'already_resolved'
        await a.pending.register('s', 1, {'request_id': 'approve', 'tool_name': 'synthetic', 'command': 'action'},
            run_id=run['run_id'], tool_allowed=True, effect='artifact')
        assert (await a.approval_pending('s', {'session_id': 's'}))['approvals'][0]['request_id'] == 'approve'
        params = {'session_id': 's', 'execution_generation': 1, 'prompt_id': 'approve', 'choice': 'once'}
        assert (await a.rpc('approval.respond', 's', params))['result']['status'] == 'resolved'
        epoch = (await a.events.watermark('s'))['replay_epoch']
        replayed = (await a.events.since('s', epoch))['events']
        completed = [e['payload'] for e in replayed if e['type'] == 'tool.complete']
        assert {e['tool_id'] for e in completed} == {'question:q1', 'question:q2', 'single'}
        assert all(len(e['result']['responses']) == 1 for e in completed)
        assert 'PRIVATE2' in str(completed)
        assert 'PRIVATE' not in str(await a.pending.list_audit())
        assert 'PRIVATE' not in str(store.runs.rows[0].get('native_messages'))
    asyncio.run(scenario())


def test_invalid_replay_parameters_are_typed_errors():
    async def scenario():
        _, _, a, _ = await fixture()
        for extra in ({'last_sequence': -1}, {'last_sequence': True}, {'replay_epoch': 123}):
            frame = await a.rpc('session.events.since', 's', {'session_id': 's', **extra})
            assert frame['error']['message'] == 'invalid_params'
    asyncio.run(scenario())


def test_delayed_interrupt_cannot_cancel_next_run_reusing_epoch():
    async def scenario():
        store, _, a, _ = await fixture()
        first = await a.submit('s', {'session_id': 's', 'submission_id': '1', 'text': 'one'})
        await store.finish(first['run_id'], 1, 'completed', '', '')
        second = await a.submit('s', {'session_id': 's', 'submission_id': '2', 'text': 'two'})
        reply = await a.rpc('session.interrupt', 's', {'session_id': 's', 'execution_generation': 1})
        assert reply['error']['message'] == 'stale_generation'
        assert reply['error']['data'] == {'reason': 'stale_generation', 'execution_generation': second['execution_generation']}
        assert (await store.get_run('a', second['run_id']))['active']
    asyncio.run(scenario())


def test_first_turn_interrupt_refusal_names_current_generation():
    async def scenario():
        store, _, a, _ = await fixture()
        receipt = await a.submit('s', {'session_id': 's', 'submission_id': 'first', 'text': 'hello'})
        reply = await a.rpc('session.interrupt', 's', {'session_id': 's', 'execution_generation': 0})
        assert reply['error'] == {'code': 4001, 'message': 'stale_generation', 'data': {
            'reason': 'stale_generation', 'execution_generation': receipt['execution_generation']}}
        assert type(reply['error']['data']['execution_generation']) is int
        assert (await store.get_run('a', receipt['run_id']))['active']
        reply = await a.rpc('session.interrupt', 's', {
            'session_id': 's', 'execution_generation': reply['error']['data']['execution_generation']})
        assert 'result' in reply
        assert (await store.get_run('a', receipt['run_id']))['status'] == 'cancelled'
    asyncio.run(scenario())


def test_stale_cancel_and_unknown_resolution_name_fresh_generation():
    from harness.gateway.execution import RpcError
    import pytest

    async def scenario():
        store, _, a, _ = await fixture()
        receipt = await a.submit('s', {'session_id': 's', 'submission_id': 'first', 'text': 'hello'})
        stale = await store.get_run('a', receipt['run_id'])
        await store.runs.update_one({'run_id': receipt['run_id']}, {'$set': {'epoch': 2}})
        with pytest.raises(RpcError) as info:
            await a._cancel_run(stale)
        assert info.value.error['data'] == {'reason': 'stale_generation', 'execution_generation': 2}
        reply = await a.rpc('prompt.resolve_unknown', 's', {'session_id': 's',
            'admission_id': receipt['admission_id'], 'execution_generation': 1})
        assert reply['error']['data'] == {'reason': 'stale_generation', 'execution_generation': 2}
        reply = await a.rpc('session.interrupt', 's', {'session_id': 's', 'execution_generation': 2},
            expected_run_id='another-run')
        assert reply['error']['data'] == {'reason': 'stale_generation', 'execution_generation': 2}
        assert (await store.get_run('a', receipt['run_id']))['active']
    asyncio.run(scenario())


def test_run_generation_survives_frozen_ring_and_both_since_paths():
    import json
    from harness.gateway.events import event_frame

    async def scenario():
        store, _, a, _ = await fixture()
        receipt = await a.submit('s', {'session_id': 's', 'submission_id': 'first', 'text': 'hello'})
        generation = receipt['execution_generation']
        kinds = ('run_started', 'progress', 'tool_started', 'tool_finished', 'approval.request',
                 'clarify.request', 'run_finished')
        projected_kinds = ('run_started', 'progress', 'tool.start', 'tool.complete', 'approval.request',
                           'clarify.request', 'run_finished')
        for kind in kinds:
            payload = {'execution_generation': generation} if kind.endswith('.request') else {
                'tool_started': {'name': 'query_db', 'invocation_id': 'call-1',
                    'args': {'lot': 'L-1'}, 'context': '{"lot":"L-1"}'},
                'tool_finished': {'name': 'query_db', 'invocation_id': 'call-1',
                    'args': {'lot': 'L-1'}, 'result': {'status': 'success'},
                    'summary': 'done', 'elapsed_seconds': .5},
            }.get(kind, {})
            await store.event(receipt['run_id'], kind, payload)
        epoch = (await a.events.watermark('s'))['replay_epoch']
        params = {'session_id': 's', 'replay_epoch': epoch, 'last_sequence': 0}
        first = (await a.rpc('session.events.since', 's', params))['result']
        frames = first['events']
        assert [frame['type'] for frame in frames] == list(projected_kinds)
        assert [frame['payload']['tool_id'] for frame in frames[2:4]] == ['call-1', 'call-1']
        assert [frame['seq'] for frame in frames] == list(range(1, len(kinds) + 1))
        assert all(frame['execution_generation'] == generation for frame in frames)
        for frame in frames:
            assert type(frame['execution_generation']) is int
            assert event_frame(frame)['params'] == frame
            if 'execution_generation' in frame['payload']:
                assert frame['payload']['execution_generation'] == frame['execution_generation']
        log = await store.db.gateway_event_logs.find_one({'principal_id': 'a', 'session_id': 's'})
        assert log['bytes'] == sum(item['size'] for item in log['ring'])
        for item, frame in zip(log['ring'], frames):
            assert item['size'] == len(item['frozen'])
            assert json.loads(item['frozen']) == {k: v for k, v in frame.items() if k != 'seq'}
        assert (await a.rpc('session.events.since', 's', params))['result'] == first
        tail = (await a.rpc('session.events.since', 's', {**params, 'last_sequence': 2}))['result']
        assert tail['events'] == frames[2:] and not tail['snapshot_required']
        legacy = (await a.rpc('session.events.since', 's', {'session_id': 's', 'last_seen': 2},
            mode='legacy'))['result']
        assert legacy['events'] == frames[2:] and not legacy['truncated']
        gap = await a.events.since('s', 'obsolete', 2)
        assert gap['events'] == [] and gap['snapshot_required']
    asyncio.run(scenario())


def test_snapshot_does_not_cancel_new_run_prompt_from_stale_active_read():
    async def scenario():
        store, _, a, _ = await fixture()
        first = await a.submit('s', {'session_id': 's', 'submission_id': '1', 'text': 'one'})
        stale = await store.get_run('a', first['run_id'])
        await store.finish(first['run_id'], 1, 'completed', '', '')
        second = await a.submit('s', {'session_id': 's', 'submission_id': '2', 'text': 'two'})
        await a.pending.register_clarify('s', 1, {'clarify_id': 'new', 'question': 'New?'}, run_id=second['run_id'])
        async def stale_read(sid):
            return stale
        a._active = stale_read
        await a._current_prompts('s')
        row = await a.pending.pending_prompts.find_one({'principal_id': 'a', 'prompt_id': 'new'})
        assert row['status'] == 'pending'
    asyncio.run(scenario())


def test_bytes_oversize_forget_global_eviction_and_frozen_copies():
    from harness.gateway import events
    log = events.new_log('a', 's')
    epoch = log['epoch']
    payload = {'type': 'message.start', 'session_id': 's', 'payload': {'value': 'original'}}
    events.retain(log, payload)
    payload['payload']['value'] = 'mutated'
    assert events.replay(log, epoch, 0)['events'][0]['payload']['value'] == 'original'
    events.retain(log, {'type': 'message.start', 'session_id': 's', 'payload': {'text': 'x' * (4 * 1024 * 1024)}})
    assert log['seq'] == 2 and len(log['ring']) == 1
    assert events.replay(log, epoch, 1)['truncated']
    events.drop_ring(log)
    log['forgotten'] = True
    events.retain(log, payload)
    assert log['seq'] == 3 and log['ring'] == []

    async def scenario():
        store, _, a, _ = await fixture()
        for index in range(65):
            row = events.new_log('a', f's{index}')
            events.retain(row, {'type': 'message.start', 'session_id': f's{index}', 'payload': {}})
            row['opened_at'] = index + 1
            await store.db.gateway_event_logs.insert_one(row)
        await a.events._trim_global()
        oldest = await store.db.gateway_event_logs.find_one({'session_id': 's0'})
        assert oldest['ring'] == [] and oldest['seq'] == 1 and oldest['evicted_through'] == 1
        assert await store.db.gateway_event_logs.count_documents({'opened_at': {'$ne': None}}) == 64
    asyncio.run(scenario())


def test_same_event_id_is_not_republished_with_another_run_sequence():
    async def scenario():
        store, _, a, _ = await fixture()
        result = await a.submit('s', {'session_id': 's', 'submission_id': 'id', 'text': 'hello'})
        run = next(r for r in store.runs.rows if r['run_id'] == result['run_id'])
        await store.event(run['run_id'], 'message.start', {})
        run['events'].append({**deepcopy(run['events'][0]), 'sequence': 2})
        epoch = (await a.events.watermark('s'))['replay_epoch']
        assert (await a.events.since('s', epoch))['count'] == 1
    asyncio.run(scenario())


def test_created_run_is_recovered_on_same_id_submit_without_new_run():
    async def scenario():
        store, controller, a, _ = await fixture()
        await store.start_run('a', 's', 'lost', 'hello', engine='hermes')
        result = await a.submit('s', {'session_id': 's', 'submission_id': 'lost', 'text': 'hello'})
        assert result['status'] == 'started'
        assert len(controller.launches) == 1 and len(store.runs.rows) == 1
    asyncio.run(scenario())


def test_projection_requires_matching_last_verification_and_terminal_run():
    from harness.gateway.events import verified_answer, verified_answer_with_artifacts, project_run_event
    from harness.gateway.publication import candidate_digest
    run = {'status': 'completed', 'active': False, 'answer_text': 'FINAL', 'observations': [{
        'tool_name': 'verify_answer', 'scope': {'verdict': 'pass'}, 'validated_arguments': candidate_digest('FINAL')}]}
    assert verified_answer(run) == 'FINAL'
    assert verified_answer({**run, 'active': True}) is None
    assert verified_answer({**run, 'answer_text': 'UNVERIFIED'}) is None
    for status in ('failed', 'partial', 'cancelled'):
        assert verified_answer({**run, 'status': status}) is None
    for verdict in ('fail', 'unknown'):
        changed = deepcopy(run)
        changed['observations'][-1]['scope']['verdict'] = verdict
        assert verified_answer(changed) is None
    for kind in ('reasoning.delta', 'reasoning.available', 'thinking.delta', 'agent.print'):
        event = project_run_event(run, {'type': kind, 'payload': {'text': 'UNVERIFIED'}})
        assert event is not None and 'UNVERIFIED' not in str(event)

    run.update(session_id='session / 1', observations=[
        {'tool_name': 'run_python', 'artifact_refs': [
            {'artifact_id': 'map / one', 'title': 'Wafer [map]', 'mime': 'image/png'},
            {'artifact_id': 'report one', 'title': 'HTML report', 'mime': 'text/html'},
            {'artifact_id': 'analysis.py', 'title': '분석 코드', 'mime': 'text/plain'},
        ]},
        {'tool_name': 'verify_answer', 'scope': {'verdict': 'pass'},
            'validated_arguments': candidate_digest('FINAL')},
    ])
    expected = ('FINAL\n\n![Wafer \\[map\\]](/harness/artifacts/map%20%2F%20one?session_id=session+%2F+1)'
                '\n\n[HTML report](/harness/artifacts/report%20one?session_id=session+%2F+1)')
    assert verified_answer_with_artifacts(run) == expected
    assert project_run_event(run, {'type': 'run_finished', 'payload': {}}) == {
        'type': 'message.complete', 'payload': {'text': expected}}
    assert '분석 코드' not in expected


def test_finished_projection_reports_observed_native_usage():
    from harness.gateway.events import project_run_event
    from harness.gateway.publication import candidate_digest

    run = {'status': 'completed', 'active': False, 'answer_text': 'FINAL', 'observations': [{
        'tool_name': 'verify_answer', 'scope': {'verdict': 'pass'},
        'validated_arguments': candidate_digest('FINAL')}],
        'native_usage': {'api_calls': 8, 'input_tokens': 109417,
                         'output_tokens': 2115, 'total_tokens': 111532}}
    event = {'type': 'run_finished', 'payload': {'usage': {'input': 999}}}
    assert project_run_event(run, event) == {'type': 'message.complete', 'payload': {
        'text': 'FINAL', 'usage': {'input': 109417, 'output': 2115, 'total': 111532, 'calls': 8}}}
    assert project_run_event({**run, 'active': True}, event)['type'] == 'run_finished'
    assert project_run_event({**run, 'answer_text': 'UNVERIFIED'}, event)['type'] == 'run_finished'

    observed = run['native_usage']
    for key in observed:
        for invalid in (None, -1, True, '1', 1.5):
            run['native_usage'] = {**observed, key: invalid}
            assert project_run_event(run, event)['payload'] == {'text': 'FINAL'}
        run['native_usage'] = {k: v for k, v in observed.items() if k != key}
        assert project_run_event(run, event)['payload'] == {'text': 'FINAL'}
    for missing in (None, {}):
        run['native_usage'] = missing
        assert project_run_event(run, event)['payload'] == {'text': 'FINAL'}
    del run['native_usage']
    assert project_run_event(run, event)['payload'] == {'text': 'FINAL'}
    run['native_usage'] = dict.fromkeys(observed, 0)
    assert project_run_event(run, event)['payload']['usage'] == {
        'input': 0, 'output': 0, 'total': 0, 'calls': 0}


def test_finished_native_usage_survives_session_event_replay():
    from harness.gateway.publication import candidate_digest

    async def scenario():
        store, _, a, _ = await fixture()
        receipt = await a.submit('s', {'session_id': 's', 'submission_id': 'usage', 'text': 'hello'})
        run = next(r for r in store.runs.rows if r['run_id'] == receipt['run_id'])
        await store.event(run['run_id'], 'run_finished', {})
        run.update(status='completed', active=False, answer_text='FINAL', observations=[{
            'tool_name': 'verify_answer', 'scope': {'verdict': 'pass'},
            'validated_arguments': candidate_digest('FINAL')}],
            native_usage={'api_calls': 8, 'input_tokens': 109417,
                          'output_tokens': 2115, 'total_tokens': 111532})
        epoch = (await a.events.watermark('s'))['replay_epoch']
        first = await a.events.since('s', epoch)
        completed = next(e for e in first['events'] if e['type'] == 'message.complete')
        assert completed['payload']['text'] == 'FINAL'
        assert completed['payload']['usage'] == {
            'input': 109417, 'output': 2115, 'total': 111532, 'calls': 8}
        assert completed['payload']['run_id'] == run['run_id']
        assert (await a.events.since('s', epoch))['events'] == first['events']
        assert (await a.events.since_legacy('s'))['events'] == first['events']

    asyncio.run(scenario())


def test_legacy_replay_keeps_legacy_keys_and_restores_open_requests():
    async def scenario():
        _, _, a, _ = await fixture()
        run = await a.submit('s', {'session_id': 's', 'submission_id': 'id', 'text': 'hello'})
        await a.pending.register_clarify('s', 1, {'clarify_id': 'question', 'question': 'Question?'}, run_id=run['run_id'])
        frame = await a.rpc('session.events.since', 's', {'session_id': 's', 'last_seen': 0, 'replay_epoch': 'ignored'}, mode='legacy')
        result = frame['result']
        assert set(result) == {'events', 'latest_seq', 'truncated', 'count', 'epoch', 'open_requests'}
        assert result['open_requests'][0]['id'] == 'question'
        assert result['open_requests'][0]['method'] == 'clarify'
    asyncio.run(scenario())


def test_resolve_unknown_concurrent_retries_are_idempotent():
    async def scenario():
        store, _, a, _ = await fixture()
        result = await a.submit('s', {'session_id': 's', 'submission_id': 'id', 'text': 'hello'})
        store.runs.rows[0].update(status='unknown', lease_until=0)
        params = {'session_id': 's', 'admission_id': result['admission_id'], 'execution_generation': 1}
        replies = await asyncio.gather(*(a.rpc('prompt.resolve_unknown', 's', params) for _ in range(5)))
        assert all('result' in r and r['result']['outcome'] == 'interrupted' for r in replies), replies
    asyncio.run(scenario())


def test_partial_published_answer_completes_live_and_history_projection():
    from harness.gateway.events import verified_answer, verified_answer_with_artifacts, project_run_event
    answer = '[weekly table](/harness/artifacts/table)\n\nVerification notice'
    run = {'status': 'partial', 'active': False, 'answer_text': answer,
        'native_usage': {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12, 'api_calls': 1}}
    event = {'type': 'run_finished', 'payload': {}}
    assert verified_answer(run) is None
    assert verified_answer_with_artifacts(run) == answer
    assert project_run_event(run, event) == {'type': 'message.complete', 'payload': {
        'text': answer, 'usage': {'input': 10, 'output': 2, 'total': 12, 'calls': 1}}}
    for changes in ({'active': True}, {'status': 'failed'}, {'status': 'cancelled'},
                    {'answer_text': ''}, {'answer_text': '   '}, {'answer_text': None}):
        assert verified_answer_with_artifacts({**run, **changes}) is None
        assert project_run_event({**run, **changes}, event)['type'] == 'run_finished'

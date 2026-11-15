"""PR-6b real localhost Mongo integration. AUTHOR MUST NOT EXECUTE THIS FILE.

UUID temporary database, finally drop; fake worker streams, no model or tools
with real side effects. Run explicitly outside the restricted Codex session.
"""
import asyncio
from contextlib import asynccontextmanager
import json
import time
from types import SimpleNamespace
import uuid

import pytest
from pydantic import BaseModel

from harness.config import Settings
from harness.executor import ExecutionContext
from harness.gateway.pending_controls import PendingControls
from harness.native_hermes import NativeBridge, NativePrompts, pending_snapshot, cancel_run_prompts
from harness.store import HarnessStore
from harness.tools.registry import ToolRegistry, ToolSpec
from harness.tests.test_native_pr6 import FakeWorker


@asynccontextmanager
async def database(*, enabled=True):
    store = HarnessStore(database='harness_pr6_test_' + uuid.uuid4().hex)
    try:
        run = {'_id': 'r', 'run_id': 'r', 'principal_id': 'p', 'session_id': 's', 'epoch': 3,
               'goal_revision': 7, 'query': 'Synthetic query', 'status': 'running', 'active': True,
               'lease_until': time.time() + 60, 'events': [], 'usage': {}}
        await store.runs.insert_one(run)
        context = ExecutionContext(store, Settings(native_prompts=enabled), run)
        yield context
    finally:
        await store.client.drop_database(store.db.name)
        store.client.close()


async def eventual(get, predicate=bool):
    async with asyncio.timeout(5):
        while True:
            result = await get()
            if predicate(result): return result
            await asyncio.sleep(.01)


async def row(context):
    return await context.store.runs.find_one({'_id': 'r'})


async def events(context):
    return (await row(context))['events']


async def adapter(context):
    pending = PendingControls(context.store, 'p')
    await pending.setup()
    return NativePrompts(context, pending)


async def live(context):
    return await pending_snapshot(context.store, 'p', 's', 3)


async def respond(context, answer='PRIVATE_ANSWER', *, principal='p', kind='clarify', choice='once'):
    prompts = await eventual(lambda: live(context))
    other = PendingControls(context.store, principal)
    return await other.respond('s', 3, prompts[0]['prompt_id'],
                               {'answer': answer} if kind == 'clarify' else {'choice': choice}, kind)


def bind_worker(monkeypatch, tmp_path, worker):
    import harness.native_hermes as native
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    monkeypatch.setattr(native.asyncio, 'create_subprocess_exec', worker.spawn)
    return native


def test_host_ipc_clarify_reconnect_isolation_privacy(monkeypatch, tmp_path):
    async def scenario():
        async with database() as context:
            worker = FakeWorker()
            native = bind_worker(monkeypatch, tmp_path, worker)
            task = asyncio.create_task(native.run_native(context, ToolRegistry()))
            try:
                prompts = await eventual(lambda: live(context))
                await eventual(lambda: row(context), lambda r: bool(r.get('pending_input')))
                assert (await row(context))['status'] == 'running'
                stranger = PendingControls(context.store, 'other')
                assert await stranger.snapshot('s', 3) == ()
                assert (await respond(context, principal='other'))['status'] == 'already_resolved'
                # A new instance simulates reconnect, including legacy replay.
                reconnected = PendingControls(context.store, 'p')
                assert await reconnected.snapshot('s', 3) == prompts
                legacy = await reconnected.legacy_request('s', 3, prompts[0]['prompt_id'])
                assert legacy['method'] == 'clarify'
                assert (await respond(context))['status'] == 'resolved'
                assert (await reconnected.respond('s', 3, prompts[0]['prompt_id'], {'answer': 'duplicate'}, 'clarify'))['status'] == 'already_resolved'
                await asyncio.wait_for(task, 5)
                assert worker.replies[0]['result'] == {'answers': {'q': 'PRIVATE_ANSWER'}, 'outcome': 'submitted'}
                assert worker.startup['native_prompts'] is True
                names = [e['type'] for e in await events(context)]
                assert names == ['clarify.request', 'yield.input_required', 'clarify.settled']
                payload = (await events(context))[1]['payload']
                assert payload['interrupt_id'] == payload['request_id'] == prompts[0]['prompt_id']
                assert payload['generation'] == 3 and payload['goal_revision'] == 7
                assert payload['fields'] == [] and payload['response_method'] == 'clarify.respond'
                assert 'pending_input' not in await row(context)
                assert 'PRIVATE_ANSWER' not in json.dumps(await row(context), default=str)
                assert 'PRIVATE_ANSWER' not in json.dumps(await reconnected.list_audit())
                assert await context.store.db.pending_prompts.count_documents({'status': 'pending'}) == 0
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_host_short_timeout(monkeypatch, tmp_path):
    async def scenario():
        async with database() as context:
            context.remaining_seconds = lambda *a: 1.02
            worker = FakeWorker()
            native = bind_worker(monkeypatch, tmp_path, worker)
            await asyncio.wait_for(native.run_native(context, ToolRegistry()), 5)
            assert worker.replies[0]['result'] == {
                'answers': {}, 'outcome': 'timed_out',
                'notice': '사용자가 답하지 않았습니다. 백분위수나 고정 임계값을 포함한 기준을 지어내지 말고, 사용자 기준이 여전히 필요하다고 알린 뒤 해당 턴을 종료하세요.',
            }
            pending = await context.store.db.pending_prompts.find_one({})
            assert pending['timeout_s'] == pytest.approx(.02)
            assert pending['status'] == 'timed_out'
            assert pending['result'] == {'answers': {}, 'outcome': 'timed_out'}
            assert [e['type'] for e in await events(context)] == ['clarify.request', 'yield.input_required', 'clarify.settled']
    asyncio.run(scenario())


@pytest.mark.parametrize('cause', ['interrupt', 'cancelling', 'worker_exit', 'worker_error'])
def test_host_teardown_cancels_pending(monkeypatch, tmp_path, cause):
    async def scenario():
        async with database() as context:
            worker = FakeWorker()
            native = bind_worker(monkeypatch, tmp_path, worker)
            task = asyncio.create_task(native.run_native(context, ToolRegistry()))
            try:
                await eventual(lambda: row(context), lambda r: bool(r.get('pending_input')))
                if cause == 'cancelling':
                    await context.store.runs.update_one({'_id': 'r'}, {'$set': {'status': 'cancelling'}})
                    task.cancel()
                elif cause == 'interrupt': task.cancel()
                elif cause == 'worker_exit': worker.messages.put_nowait(None)
                else: worker.messages.put_nowait({'type': 'error', 'message': 'synthetic failure'})
                outcome = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
                assert isinstance(outcome[0], BaseException)
                pending = await context.store.db.pending_prompts.find_one({})
                assert pending['status'] == 'cancelled'
                assert 'pending_input' not in await row(context)
                assert [e['type'] for e in await events(context)].count('clarify.settled') == 1
                assert worker.replies[0]['result'] == {'answers': {}, 'outcome': 'cancelled'}
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize('request_name,expected', [('sudo', {'value': ''}), ('secret', {'value': ''}),
    ('unknown', {'error': {'code': -32601, 'message': 'Method not found'}})])
def test_host_declines_and_audits_without_body(monkeypatch, tmp_path, request_name, expected):
    async def scenario():
        async with database() as context:
            worker = FakeWorker(request_name, {'value': 'PRIVATE_SECRET'})
            native = bind_worker(monkeypatch, tmp_path, worker)
            await asyncio.wait_for(native.run_native(context, ToolRegistry()), 5)
            assert worker.replies[0]['result'] == expected
            audit = await PendingControls(context.store, 'p').list_audit()
            assert len(audit) == 1 and audit[0]['event'] == 'declined_server_request'
            assert 'PRIVATE_SECRET' not in json.dumps(audit)
            assert await events(context) == []
    asyncio.run(scenario())


def test_flag_false_has_no_pending_collections(monkeypatch, tmp_path):
    async def scenario():
        async with database(enabled=False) as context:
            native = bind_worker(monkeypatch, tmp_path, FakeWorker())
            await asyncio.wait_for(native.run_native(context, ToolRegistry()), 5)
            assert set(await context.store.db.list_collection_names()).isdisjoint(
                {'pending_prompts', 'approval_rules', 'approval_audit'})
            assert await events(context) == []
    asyncio.run(scenario())


def synthetic_bridge(context, prompts):
    class Args(BaseModel): pass
    registry = ToolRegistry()
    registry.add(ToolSpec('synthetic_effect', 'Synthetic artifact', Args, None, effect='artifact'))
    bridge = NativeBridge(registry, context, approval_tools={'synthetic_effect'}, pending=prompts)
    executed = []
    class Observation:
        def model_dump(self, **kw): return {}
        def model_view(self): return {'status': 'success'}
    async def execute(call):
        executed.append(call)
        return Observation()
    bridge.executor = SimpleNamespace(execute=execute)
    return bridge, executed


@pytest.mark.parametrize('choice', ['once', 'deny', 'always', 'session'])
def test_synthetic_approval_gate_and_revocation(choice):
    async def scenario():
        async with database() as context:
            prompts = await adapter(context)
            bridge, executed = synthetic_bridge(context, prompts)
            call = {'id': 'synthetic', 'name': 'synthetic_effect', 'args': {}}
            task = asyncio.create_task(bridge.invoke(call))
            try:
                await eventual(lambda: row(context), lambda r: bool(r.get('pending_input')))
                await respond(context, kind='approval', choice=choice)
                result = await asyncio.wait_for(task, 2)
                assert result['status'] == ('denied' if choice == 'deny' else 'success')
                assert len(executed) == (0 if choice == 'deny' else 1)
                assert [e['type'] for e in await events(context)] == ['approval.request', 'yield.input_required', 'approval.settled']
                assert all('choice' not in e['payload'] for e in await events(context))
                if choice in ('always', 'session'):
                    await asyncio.wait_for(bridge.invoke(call), 2)
                    assert len(executed) == 2
                    assert [e['type'] for e in await events(context)].count('approval.request') == 1
                if choice == 'always':
                    rule = (await prompts.pending.list_rules())[0]
                    assert await prompts.pending.revoke_rule(rule['rule_id'])
                    task = asyncio.create_task(bridge.invoke(call))
                    await eventual(lambda: live(context))
                    await respond(context, kind='approval', choice='deny')
                    assert (await asyncio.wait_for(task, 2))['status'] == 'denied'
                    assert len(executed) == 2
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await prompts.cancel('test_end')
    asyncio.run(scenario())


@pytest.mark.parametrize('cause', ['timeout', 'cancel'])
def test_approval_fail_closed(cause):
    async def scenario():
        async with database() as context:
            if cause == 'timeout': context.remaining_seconds = lambda *a: 1.02
            prompts = await adapter(context)
            bridge, executed = synthetic_bridge(context, prompts)
            task = asyncio.create_task(bridge.invoke({'id': 'x', 'name': 'synthetic_effect', 'args': {}}))
            try:
                if cause == 'cancel':
                    await eventual(lambda: row(context), lambda r: bool(r.get('pending_input')))
                    await prompts.cancel('interrupt')
                assert (await asyncio.wait_for(task, 2))['status'] == 'denied'
                assert executed == []
                if cause == 'cancel': assert 'approval.cancelled' in [e['type'] for e in await events(context)]
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_generation_fencing_and_recovery_hook():
    async def scenario():
        async with database() as context:
            prompts = await adapter(context)
            task = asyncio.create_task(prompts.clarify({'questions': [{'qid': 'q', 'question': 'Question'}]}))
            try:
                await eventual(lambda: row(context), lambda r: bool(r.get('pending_input')))
                before = await events(context)
                await context.store.runs.update_one({'_id': 'r'}, {'$set': {'epoch': 4, 'pending_input': {'new': {}}}})
                await cancel_run_prompts(context.store, 'other', 'r', 'recovery')
                assert await context.store.db.pending_prompts.count_documents({'status': 'pending'}) == 1
                recovered = await cancel_run_prompts(context.store, 'p', 'r', 'recovery')
                assert recovered[0]['type'] == 'clarify.settled'
                await asyncio.gather(task, return_exceptions=True)
                assert await events(context) == before  # Old epoch cannot write into successor.
                assert (await row(context))['pending_input'] == {'new': {}}
                assert await pending_snapshot(context.store, 'p', 's', 4) == ()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_cross_instance_cancel_helper_preserves_approval_cancelled():
    async def scenario():
        async with database() as context:
            prompts = await adapter(context)
            bridge, executed = synthetic_bridge(context, prompts)
            task = asyncio.create_task(bridge.invoke({'id': 'x', 'name': 'synthetic_effect', 'args': {}}))
            try:
                await eventual(lambda: row(context), lambda r: bool(r.get('pending_input')))
                await cancel_run_prompts(context.store, 'p', 'r', 'recovery')
                assert (await asyncio.wait_for(task, 2))['status'] == 'denied'
                assert executed == []
                records = await events(context)
                assert [e['type'] for e in records] == ['approval.request', 'yield.input_required',
                                                       'approval.settled', 'approval.cancelled']
                assert records[-1]['payload']['reason'] == 'recovery'
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_concurrent_questions_partial_response_and_cleanup():
    async def scenario():
        async with database() as context:
            prompts = await adapter(context)
            questions = [{'qid': 'one', 'question': 'One'}, {'qid': 'two', 'question': 'Two'}]
            task = asyncio.create_task(prompts.clarify({'questions': questions}))
            try:
                cards = await eventual(lambda: live(context), lambda cards: len(cards) == 2)
                await eventual(lambda: row(context), lambda r: len(r.get('pending_input', {})) == 2)
                other = PendingControls(context.store, 'p')
                await other.respond('s', 3, cards[0]['prompt_id'], {'answer': 'PRIVATE_LOCKED'}, 'clarify')
                await eventual(lambda: row(context), lambda r: len(r.get('pending_input', {})) == 1)
                await other.respond('s', 3, cards[1]['prompt_id'], {'answer': ''}, 'clarify')
                assert await asyncio.wait_for(task, 2) == {'answers': {'one': 'PRIVATE_LOCKED', 'two': None}, 'outcome': 'submitted'}
                assert 'PRIVATE_LOCKED' not in json.dumps(await row(context), default=str)
                assert [e['type'] for e in await events(context)].count('clarify.settled') == 2
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_host_rejects_prompt_during_verification(monkeypatch, tmp_path):
    async def scenario():
        async with database() as context:
            class ReviewingWorker(FakeWorker):
                def write(self, data):
                    value = self.json.loads(data)
                    if value.get('call_id') == 'call-review':
                        self.messages.put_nowait({'type': 'call', 'method': 'server_request',
                            'call_id': 'call-prompt', 'request': 'clarify',
                            'params': {'questions': [{'qid': 'q', 'question': 'MUST_NOT_PUBLISH'}]}})
                    else:
                        super().write(data)
            worker = ReviewingWorker()
            worker.messages.get_nowait()
            worker.messages.put_nowait({'type': 'call', 'method': 'verification_start',
                'call_id': 'call-review', 'review_id': 'review', 'attempt': 1, 'candidate': 'PRIVATE_CANDIDATE'})
            native = bind_worker(monkeypatch, tmp_path, worker)
            await asyncio.wait_for(native.run_native(context, ToolRegistry()), 5)
            assert worker.replies[0]['result'] == {'answers': {}, 'outcome': 'cancelled'}
            assert await context.store.db.pending_prompts.count_documents({}) == 0
            assert 'PRIVATE_CANDIDATE' not in json.dumps(await events(context))
            assert 'MUST_NOT_PUBLISH' not in json.dumps(await events(context))
    asyncio.run(scenario())


def test_normal_result_cancels_an_outstanding_prompt(monkeypatch, tmp_path):
    async def scenario():
        async with database() as context:
            worker = FakeWorker()
            native = bind_worker(monkeypatch, tmp_path, worker)
            task = asyncio.create_task(native.run_native(context, ToolRegistry()))
            try:
                await eventual(lambda: row(context), lambda r: bool(r.get('pending_input')))
                worker.messages.put_nowait({'type': 'result', 'result': {
                    'completed': False, 'partial': True, 'turn_exit_reason': 'synthetic_end', 'final_response': ''}})
                assert (await asyncio.wait_for(task, 5))['stop_reason'] == 'synthetic_end'
                assert await context.store.db.pending_prompts.count_documents({'status': 'pending'}) == 0
                assert (await context.store.db.pending_prompts.find_one({}))['status'] == 'cancelled'
                assert 'pending_input' not in await row(context)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())

"""Real localhost Mongo integration. AUTHOR MUST NOT RUN THIS FILE.

No skips, Docker, model, sockets other than Mongo, or real tools. UUID database
is dropped in finally. RunController.start/cancel and HarnessStore are real;
only worker launch is replaced with the actual lease acquisition + a counter.
"""
import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
import uuid

from harness.control import RunController
from harness.gateway.events import SessionEvents
from harness.gateway.execution import Execution
from harness.gateway.pending_controls import PendingControls
from harness.gateway.publication import candidate_digest
from harness.gateway.session_store import MongoSessionStore
from harness.store import HarnessStore


class OfflineController(RunController):
    def __init__(self, store):
        super().__init__(store, SimpleNamespace(engine='hermes'))
        self.launches = []

    async def launch(self, run_id):
        run = await self.store.acquire(run_id, self.owner)
        if run:
            self.launches.append(run_id)


@asynccontextmanager
async def database():
    store = HarnessStore(uri='mongodb://localhost:27017', database='harness_pr7a_test_' + uuid.uuid4().hex)
    try:
        await store.setup()
        for principal in ('a', 'b'):
            sessions = MongoSessionStore(store, principal)
            await sessions.setup()
            await sessions.create_session('s', 'gui')
        await PendingControls(store, 'a').setup()
        controller = OfflineController(store)
        yield store, controller, Execution(controller, 'a', profile_id='profile'), Execution(controller, 'b', profile_id='profile')
    finally:
        await store.client.drop_database(store.db.name)
        store.client.close()


def test_same_id_64_concurrent_submits_share_receipt_and_execute_once():
    async def scenario():
        async with database() as (store, controller, a, _):
            params = {'session_id': 's', 'submission_id': 'id', 'text': 'Synthetic'}
            replies = await asyncio.gather(*(a.rpc('prompt.submit', 's', params) for _ in range(64)))
            assert all('result' in reply for reply in replies), replies
            admitted = replies[0]['result']
            assert all(reply['result'] == admitted for reply in replies)
            assert controller.launches == [admitted['run_id']]
            assert await store.runs.count_documents({'principal_id': 'a', 'session_id': 's'}) == 1
            assert await store.db.gateway_receipts.count_documents({'principal_id': 'a', 'session_id': 's'}) == 1
    asyncio.run(scenario())


def test_distinct_ids_16_concurrent_submits_admit_and_execute_exactly_once():
    async def scenario():
        async with database() as (store, controller, a, _):
            replies = await asyncio.gather(*(a.rpc('prompt.submit', 's', {
                'session_id': 's', 'submission_id': str(n), 'text': 'Synthetic'}) for n in range(16)))
            admitted = [reply['result'] for reply in replies if 'result' in reply]
            refused = [reply['error'] for reply in replies if 'error' in reply]
            assert len(admitted) == 1 and len(refused) == 15, replies
            assert all(error['code'] == 4001 and error['message'] == 'session_busy' for error in refused)
            assert controller.launches == [admitted[0]['run_id']]
            assert await store.runs.count_documents({'principal_id': 'a', 'session_id': 's'}) == 1
    asyncio.run(scenario())


def test_same_session_two_principals_isolate_each_rpc():
    async def scenario():
        async with database() as (store, controller, a, b):
            # Same session and submission identity, different owned queries.
            receipts = []
            for adapter, text in ((a, 'Owned by a'), (b, 'Owned by b')):
                reply = await adapter.rpc('prompt.submit', 's', {
                    'session_id': 's', 'submission_id': 'shared', 'text': text})
                assert 'result' in reply, reply
                receipts.append(reply['result'])
            assert receipts[0]['run_id'] != receipts[1]['run_id']
            assert len(controller.launches) == 2
            for owner, foreign, receipt in ((a, b, receipts[0]), (b, a, receipts[1])):
                params = {'session_id': 's', 'admission_id': receipt['admission_id']}
                assert (await owner.rpc('prompt.receipt', 's', params))['result'] == receipt
                assert (await foreign.rpc('prompt.receipt', 's', params))['error']['message'] == 'not_found'
                await store.event(receipt['run_id'], 'message.start', {})

            for owner, receipt in ((a, receipts[0]), (b, receipts[1])):
                epoch = (await owner.events.watermark('s'))['replay_epoch']
                reply = await owner.rpc('session.events.since', 's', {
                    'session_id': 's', 'last_sequence': 0, 'replay_epoch': epoch})
                assert 'result' in reply, reply
                events = reply['result']['events']
                assert events and {event['payload']['run_id'] for event in events} == {receipt['run_id']}

            for owner, foreign, receipt in ((a, b, receipts[0]), (b, a, receipts[1])):
                clarify_id = 'question-' + owner.principal_id
                approval_id = 'approval-' + owner.principal_id
                await owner.pending.register_clarify('s', 1, {
                    'clarify_id': clarify_id, 'question': 'Question?', 'qid': 'q'}, run_id=receipt['run_id'])
                await owner.pending.register('s', 1, {
                    'request_id': approval_id, 'tool_name': 'synthetic', 'command': 'Synthetic action'},
                    run_id=receipt['run_id'], tool_allowed=True, effect='artifact')
                for method, prompt_id, answer in (
                        ('clarify.respond', clarify_id, {'answer': 'PRIVATE'}),
                        ('approval.respond', approval_id, {'choice': 'once'})):
                    params = {'session_id': 's', 'execution_generation': 1, 'prompt_id': prompt_id, **answer}
                    assert (await foreign.rpc(method, 's', params))['result']['status'] == 'already_resolved'
                    row = await owner.pending.pending_prompts.find_one({
                        'principal_id': owner.principal_id, 'session_id': 's', 'prompt_id': prompt_id})
                    assert row['status'] == 'pending'
                    assert (await owner.rpc(method, 's', params))['result']['status'] == 'resolved'
    asyncio.run(scenario())


def test_lost_ack_restart_and_receipt_repair():
    async def scenario():
        async with database() as (store, controller, a, b):
            params = {'session_id': 's', 'submission_id': 'id', 'text': 'Synthetic prompt'}
            admitted = await a.submit('s', params)
            # First ACK is discarded; a fresh process adapter uses the same identity.
            resumed = Execution(OfflineController(store), 'a', profile_id='profile')
            assert await resumed.submit('s', params) == admitted
            assert (await resumed.rpc('prompt.submit', 's', {**params, 'text': 'Changed'}))['error']['message'] == 'admission_conflict'
            others = await asyncio.gather(*(resumed.rpc('prompt.submit', 's', {**params, 'submission_id': f'other-{n}'}) for n in range(16)))
            assert all(r['error']['message'] == 'session_busy' for r in others)
            assert (await b.rpc('prompt.receipt', 's', {'session_id': 's', 'admission_id': admitted['admission_id']}))['error']['message'] == 'not_found'
            assert (await b.submit('s', params))['run_id'] != admitted['run_id']
            # Simulate the exact run-created / receipt-not-yet-written crash window
            # using another owned session and real controller.start.
            await MongoSessionStore(store, 'a').create_session('crash', 'gui')
            raw = await controller.start('a', 'crash', 'lost', 'Synthetic prompt')
            repaired = await resumed.receipt('crash', {'session_id': 'crash', 'admission_id': raw['run_id']})
            assert repaired['run_id'] == raw['run_id']
            assert await store.runs.count_documents({'principal_id': 'a', 'session_id': 'crash'}) == 1
    asyncio.run(scenario())


def test_event_ingestion_sequence_gap_truncation_epoch_and_verified_publication():
    async def scenario():
        async with database() as (store, controller, a, b):
            receipt = await a.submit('s', {'session_id': 's', 'submission_id': '1', 'text': 'Synthetic'})
            rid = receipt['run_id']
            epoch = (await a.events.watermark('s'))['replay_epoch']
            for kind in ('message.start', 'message.delta', 'message.interim', 'message.complete', 'reasoning', 'agent.print'):
                await store.event(rid, kind, {'text': 'UNVERIFIED BODY'})
            await asyncio.gather(*(SessionEvents(store, 'a').ingest('s') for _ in range(8)))
            before = await a.events.since('s', epoch)
            assert before['count'] == 3 and 'UNVERIFIED BODY' not in str(before)
            await store.runs.update_one({'run_id': rid}, {'$set': {'answer_text': 'VERIFIED FINAL', 'observations': [{
                'tool_name': 'verify_answer', 'scope': {'verdict': 'pass'},
                'validated_arguments': candidate_digest('VERIFIED FINAL')}]}})
            await store.finish(rid, 1, 'completed', 'VERIFIED FINAL', 'hermes_returned')
            next_receipt = await a.submit('s', {'session_id': 's', 'submission_id': '2', 'text': 'Next'})
            await store.event(next_receipt['run_id'], 'message.start', {})
            restored = SessionEvents(store, 'a')
            replayed = await restored.since('s', epoch)
            assert [e['seq'] for e in replayed['events']] == list(range(1, 6))
            assert replayed['events'][-1]['payload']['sequence'] == 1
            assert len([e for e in replayed['events'] if e['type'] == 'message.complete']) == 1
            # A client missed frames 3..5: same since request from two windows.
            left, right = await asyncio.gather(restored.since('s', epoch, 2), a.events.since('s', epoch, 2))
            assert left == right and [e['seq'] for e in left['events']] == [3, 4, 5]
            assert 'UNVERIFIED BODY' not in json.dumps(await a.receipt('s', {
                'session_id': 's', 'admission_id': rid, 'include_result': True}))
            assert (await b.events.since('s', (await b.events.watermark('s'))['replay_epoch']))['count'] == 0
            for _ in range(514):
                await store.event(next_receipt['run_id'], 'message.start', {})
            truncated = await restored.since('s', epoch, 0)
            assert truncated['truncated'] and truncated['events'] == []
            tail = await restored.since('s', epoch, truncated['latest_seq'] - 512)
            assert tail['count'] == 512
            rotated = await restored.rebuild('s')
            assert rotated['last_sequence'] == truncated['latest_seq']
            assert (await a.events.since('s', epoch, rotated['last_sequence']))['snapshot_required']
    asyncio.run(scenario())


def test_pending_recovery_two_principals_wires_generation_and_no_answer_events():
    async def scenario():
        async with database() as (store, controller, a, b):
            receipt = await a.submit('s', {'session_id': 's', 'submission_id': 'a', 'text': 'Synthetic'})
            await b.submit('s', {'session_id': 's', 'submission_id': 'b', 'text': 'Synthetic'})
            await a.pending.register_clarify('s', 1, {'clarify_id': 'batch', 'questions': [
                {'qid': 'one', 'question': 'One?'}, {'qid': 'two', 'question': 'Two?'}]}, run_id=receipt['run_id'])
            await a._publish_pending()
            second = Execution(OfflineController(store), 'a', profile_id='profile')
            assert len((await second.snapshot('s'))['prompts']) == 2
            assert (await b.snapshot('s'))['prompts'] == []
            params = {'session_id': 's', 'execution_generation': 1, 'prompt_id': 'batch:q1', 'answer': 'PRIVATE'}
            assert (await b.respond('s', params, kind='clarify'))['status'] == 'already_resolved'
            assert (await second.respond('s', {**params, 'execution_generation': 99}, kind='clarify'))['status'] == 'already_resolved'
            assert await second.clarify_lock('s', {'request_id': 'batch', 'question_id': 'one', 'answer': 'PRIVATE'}) == {'status': 'ok', 'remaining': ['two']}
            assert await second.request_answer('s', {'id': 'batch', 'result': {'answers': {'two': 'PRIVATE2'}}}) == {'status': 'ok'}
            assert await a.pending.wait('batch', 0) == {'answers': {'one': 'PRIVATE', 'two': 'PRIVATE2'}, 'outcome': 'submitted'}
            await a.pending.register('s', 1, {'request_id': 'approval', 'tool_name': 'synthetic', 'command': 'Synthetic action'},
                run_id=receipt['run_id'], tool_allowed=True, effect='artifact')
            await a._publish_pending()
            approvals = await second.approval_pending('s', {'session_id': 's'})
            assert approvals['approvals'][0]['request_id'] == 'approval'
            params = {'session_id': 's', 'execution_generation': 1, 'prompt_id': 'approval', 'choice': 'once'}
            assert (await second.respond('s', params, kind='approval'))['status'] == 'resolved'
            assert (await second.respond('s', params, kind='approval'))['status'] == 'already_resolved'
            epoch = (await second.events.watermark('s'))['replay_epoch']
            assert 'PRIVATE' not in str(await second.events.since('s', epoch))
            assert 'PRIVATE' not in str((await store.get_run('a', receipt['run_id']))['events'])
            # An expired owner becomes unknown, then explicit acknowledgement
            # retires that exact generation. No ID minting / automatic resubmit.
            await store.runs.update_one({'run_id': receipt['run_id']}, {'$set': {'lease_until': 0}})
            await second.recover_unknown('s')
            resolution = await second.resolve_unknown('s', {'session_id': 's', 'admission_id': receipt['admission_id'], 'execution_generation': 1})
            assert resolution['outcome'] == 'interrupted'
            assert await store.runs.count_documents({'principal_id': 'a'}) == 1
    asyncio.run(scenario())

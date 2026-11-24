import asyncio
from contextlib import asynccontextmanager
import json
import time
import uuid


from harness.config import Settings
from harness.executor import ExecutionContext
from harness.gateway.pending_controls import PendingControls
from harness.native_hermes import NativePrompts, pending_snapshot, cancel_run_prompts
from harness.store import HarnessStore


@asynccontextmanager
async def database():
    store = HarnessStore(database='harness_pr6_test_' + uuid.uuid4().hex)
    try:
        run = {'_id': 'r', 'run_id': 'r', 'principal_id': 'p', 'session_id': 's', 'epoch': 3,
               'goal_revision': 7, 'query': 'Synthetic query', 'status': 'running', 'active': True,
               'lease_until': time.time() + 60, 'events': [], 'usage': {}}
        await store.runs.insert_one(run)
        context = ExecutionContext(store, Settings(), run)
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
                assert 'PRIVATE_LOCKED' not in json.dumps((await row(context)).get('native_messages'))
                completed = [e['payload'] for e in await events(context) if e['type'] == 'tool_finished']
                assert {e['tool_id'] for e in completed} == {c['prompt_id'] for c in cards}
                assert len(completed) == 2
                assert [e['result']['responses'][0]['status'] for e in completed] == ['answered', 'skipped']
                assert [e['type'] for e in await events(context)].count('clarify.settled') == 2
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())

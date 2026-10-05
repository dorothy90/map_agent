import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest
from langchain_core.messages import AIMessage

from harness.config import Settings
from harness.executor import ExecutionContext
from harness.store import HarnessStore, LeaseLost


class MemoryModel:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.calls = []
        self.schema = None

    def bind_tools(self, tools, **kwargs):
        self.schema = tools[0].__name__
        return self

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return AIMessage(content='', tool_calls=[{'id': str(len(self.calls)), 'name': self.schema, 'args': next(self.answers)}], usage_metadata={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15})


@asynccontextmanager
async def execution(**settings):
    store = HarnessStore(database='harness_memory_' + uuid.uuid4().hex)
    try:
        await store.setup()
        run = await store.start_run('alice', 's', 'req', 'Analyze')
        run = await store.acquire(run['run_id'], 'test')
        yield store, ExecutionContext(store, Settings(**settings), run)
    finally:
        await store.client.drop_database(store.db.name)
        store.client.close()


def test_profile_principal_scope_feedback_only_and_idempotent_budgeted_update():
    from harness.memory import load_profile, update_memory

    async def scenario():
        async with execution() as (store, ctx):
            await store.db.user_profiles.insert_many([{'_id': 'alice', 'profile': '- concise'}, {'_id': 'bob', 'profile': 'PRIVATE'}])
            assert await load_profile(ctx) == '- concise'
            await store.runs.update_one({'_id': ctx.run['run_id']}, {'$set': {'user_inputs': [
                {'request_id': 'slot', 'question': {'fields': [{'slot': 'product'}]}, 'value': {'product': '4SS'}},
                {'request_id': 'feedback', 'question': {'message': 'How should results be presented?', 'fields': []}, 'value': 'Prefer concise tables'}]}})
            model = MemoryModel([{'updated_profile': '- concise tables', 'qualitative_only': True, 'supported_feedback_ids': ['feedback']}])
            result = {'status': 'completed', 'answer': 'verified answer', 'observations': []}
            await update_memory(ctx, model, result)
            await update_memory(ctx, model, result)
            assert len(model.calls) == 1
            assert '4SS' not in str(model.calls) and 'PRIVATE' not in str(model.calls)
            assert await load_profile(ctx) == '- concise tables'
            saved = await store.get_run('alice', ctx.run['run_id'])
            assert saved['usage']['models'] == 1
            assert [event['payload']['purpose'] for event in saved['events'] if event['type'] == 'model_usage'] == ['memory']
            assert (await store.db.user_profiles.find_one({'_id': 'bob'}))['profile'] == 'PRIVATE'
    asyncio.run(scenario())


@pytest.mark.parametrize('status', ['failed', 'partial', 'cancelled', 'waiting_user'])
def test_unsuccessful_answers_are_not_promoted(status):
    from harness.memory import update_memory

    async def scenario():
        async with execution() as (store, ctx):
            model = MemoryModel([])
            await update_memory(ctx, model, {'status': status, 'answer': 'unverified', 'memory_feedback': [{'user_answer': 'remember this'}]})
            assert model.calls == []
            assert await store.db.user_profiles.count_documents({}) == 0
    asyncio.run(scenario())


def test_task_specific_memory_proposal_is_rejected():
    from harness.memory import update_memory

    async def scenario():
        async with execution() as (store, ctx):
            await store.runs.update_one({'_id': ctx.run['run_id']}, {'$set': {'user_inputs': [{'request_id': 'f', 'question': {'fields': []}, 'value': 'Show product 4SS this week'}]}})
            model = MemoryModel([{'updated_profile': '4SS this week', 'qualitative_only': False, 'supported_feedback_ids': ['f']}])
            await update_memory(ctx, model, {'status': 'completed', 'answer': 'done'})
            assert await store.db.user_profiles.count_documents({}) == 0
    asyncio.run(scenario())


def test_memory_respects_remaining_budget_and_cancellation():
    from harness.memory import update_memory

    async def scenario():
        async with execution(model_limit=3) as (store, ctx):
            await store.runs.update_one({'_id': ctx.run['run_id']}, {'$set': {'usage.models': 3, 'user_inputs': [{'request_id': 'f', 'question': {'fields': []}, 'value': 'concise please'}]}})
            model = MemoryModel([])
            await update_memory(ctx, model, {'status': 'completed', 'answer': 'done'})
            assert model.calls == []
            assert await store.db.user_profiles.count_documents({}) == 0
        async with execution() as (store, ctx):
            await store.runs.update_one({'_id': ctx.run['run_id']}, {'$set': {'status': 'cancelling'}})
            with pytest.raises(LeaseLost):
                await update_memory(ctx, MemoryModel([]), {'status': 'completed', 'answer': 'done'})
    asyncio.run(scenario())


def test_successful_source_bound_wiki_uses_metered_call_and_serial_writer(tmp_path, monkeypatch):
    from harness.memory import update_memory
    from wiki_queue import wiki_queue
    import wiki_store

    async def scenario():
        for name, value in {'_VAULT': tmp_path, '_EPISODES': tmp_path / 'episodes', '_CONCEPTS': tmp_path / 'concepts', '_ALIASES': tmp_path / 'aliases', '_SUPER_CONCEPTS': tmp_path / 'super_concepts', '_LOG': tmp_path / 'log.md', '_INDEX': tmp_path / 'index.md'}.items():
            monkeypatch.setattr(wiki_store, name, value)
        async with execution() as (store, ctx):
            obs = await store.put_result('alice', 's', {'run_id': ctx.run['run_id'], 'invocation_id': 'search', 'tool_name': 'search_fail_history',
                'validated_arguments': {'query': 'failure', 'product': 'P', 'fail_type': 'F', 'cause_oper': 'O'}, 'scope': {'mode': 'bm25'}},
                [{'doc_id': 'doc1', 'cause': 'verified source cause', 'action': 'verified source action'}], [])
            model = MemoryModel([{'episode_summary': 'source summary', 'episode_body_md': '## 원인\nverified source cause [doc1]', 'alias_pairs': []}])
            await wiki_queue.start()
            try:
                outcome = await update_memory(ctx, model, {'status': 'completed', 'answer': 'verified', 'observations': [obs.model_dump()]})
                assert outcome['wiki'] == 'updated:1', outcome
                assert len(model.calls) == 1
                assert len(list((tmp_path / 'episodes').glob('*.md'))) == 1
                assert len(list((tmp_path / 'concepts').glob('*.md'))) == 1
                saved = await store.get_run('alice', ctx.run['run_id'])
                assert saved['usage']['models'] == 1
                assert [event['payload']['purpose'] for event in saved['events'] if event['type'] == 'model_usage'] == ['memory']
                await update_memory(ctx, model, {'status': 'completed', 'answer': 'verified', 'observations': [obs.model_dump()]})
                assert len(model.calls) == 1
            finally:
                await wiki_queue.stop()
    asyncio.run(scenario())


def test_cancellation_during_profile_model_call_does_not_write_profile():
    from harness.memory import update_memory

    async def scenario():
        async with execution() as (store, ctx):
            await store.runs.update_one({'_id': ctx.run['run_id']}, {'$set': {'user_inputs': [{'request_id': 'f', 'question': {'fields': []}, 'value': 'concise please'}]}})
            started, cancelled = asyncio.Event(), asyncio.Event()
            class BlockingModel(MemoryModel):
                async def ainvoke(self, messages):
                    started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()
            task = asyncio.create_task(update_memory(ctx, BlockingModel([]), {'status': 'completed', 'answer': 'done'}))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cancelled.is_set()
            assert await store.db.user_profiles.count_documents({}) == 0
            saved = await store.get_run('alice', ctx.run['run_id'])
            usage = [event['payload'] for event in saved['events'] if event['type'] == 'model_usage']
            assert usage[-1]['purpose'] == 'memory' and usage[-1]['status'] == 'cancelled'
    asyncio.run(scenario())


def test_existing_wiki_synthesis_threshold_uses_the_same_run_budget(tmp_path, monkeypatch):
    import json
    from harness.memory import update_memory
    from wiki_queue import wiki_queue
    import wiki_store

    class WikiModel(MemoryModel):
        async def ainvoke(self, messages):
            payload = json.loads(messages[-1].content)
            self.calls.append(messages)
            if self.schema == 'SummarizeOut':
                args = {'episode_summary': 'source summary', 'episode_body_md': payload['raw_results'][0]['cause'], 'alias_pairs': []}
            else:
                sources = payload['episodes']
                args = {'body_markdown': '\n'.join(f"source [ep:{source['id'].removeprefix('episode:')}]" for source in sources),
                    'confidence': 0.6, 'citations': [{'episode_id': source['id']} for source in sources], 'notes': ''}
            return AIMessage(content='', tool_calls=[{'id': str(len(self.calls)), 'name': self.schema, 'args': args}], usage_metadata={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15})

    async def scenario():
        for name, value in {'_VAULT': tmp_path, '_EPISODES': tmp_path / 'episodes', '_CONCEPTS': tmp_path / 'concepts', '_ALIASES': tmp_path / 'aliases', '_SUPER_CONCEPTS': tmp_path / 'super_concepts', '_LOG': tmp_path / 'log.md', '_INDEX': tmp_path / 'index.md'}.items():
            monkeypatch.setattr(wiki_store, name, value)
        async with execution() as (store, first_ctx):
            model = WikiModel([])
            await wiki_queue.start()
            contexts = []
            try:
                for i in range(2):
                    if i == 0:
                        ctx = first_ctx
                    else:
                        run = await store.start_run('alice', 's', 'req2', 'Another verified search')
                        run = await store.acquire(run['run_id'], 'test')
                        ctx = ExecutionContext(store, Settings(), run)
                    contexts.append(ctx)
                    obs = await store.put_result('alice', 's', {'run_id': ctx.run['run_id'], 'invocation_id': 'search', 'tool_name': 'search_fail_history',
                        'validated_arguments': {'query': f'case {i}', 'product': 'P', 'fail_type': 'F', 'cause_oper': 'O'}, 'scope': {'mode': 'bm25'}},
                        [{'doc_id': f'doc{i}', 'cause': f'source cause {i}', 'action': f'source action {i}'}], [])
                    result = {'status': 'completed', 'answer': 'verified', 'observations': [obs.model_dump()]}
                    outcome = await update_memory(ctx, model, result)
                    assert outcome['wiki'] == 'updated:1', outcome
                    await store.finish(ctx.run['run_id'], ctx.run['epoch'], 'completed', 'verified', 'verified')
                assert len(model.calls) == 3
                last = await store.get_run('alice', contexts[-1].run['run_id'])
                assert last['usage']['models'] == 2
                purposes = [event['payload']['purpose'] for event in last['events'] if event['type'] == 'model_usage']
                assert purposes == ['memory', 'memory']
                assert len(list((tmp_path / 'episodes').glob('*.md'))) == 2
                concept = wiki_store._read(next((tmp_path / 'concepts').glob('*.md')))
                assert len(concept.metadata['citations']) == 2
            finally:
                await wiki_queue.stop()
    asyncio.run(scenario())


def test_cancelling_awaited_wiki_job_cancels_its_worker_model(monkeypatch):
    from wiki_queue import WikiQueue
    import wiki_summarizer
    from types import SimpleNamespace

    async def scenario():
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def check():
            return {}
        async def summary(*args):
            return {'episode': {}}
        async def blocking_persist(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        monkeypatch.setattr(wiki_summarizer, 'summarize_harness', summary)
        queue = WikiQueue()
        monkeypatch.setattr(queue, '_persist_harness', blocking_persist)
        ctx = SimpleNamespace(check=check, remaining_seconds=lambda: 30)
        await queue.start()
        try:
            task = asyncio.create_task(queue.ingest_harness(ctx, object(), {'raw_results': [{}]}))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(cancelled.wait(), 2)
            await asyncio.wait_for(queue._persist_q.join(), 2)
            assert all(not worker.done() for worker in queue._workers)
        finally:
            await queue.stop()
    asyncio.run(scenario())

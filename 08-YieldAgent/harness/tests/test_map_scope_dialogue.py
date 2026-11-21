import asyncio
from types import SimpleNamespace
from uuid import uuid4

from harness.hermes_chat_worker import attach_map_memory_refs, map_scope_dialogue
from harness.store import HarnessStore


def test_memory_refs_only_expose_entries_visible_in_enabled_native_snapshot():
    memory = SimpleNamespace(memory_entries=['visible', 'sensitive blocked text'],
        format_for_system_prompt=lambda _: 'header\ntitle\nrule\nvisible\n§\n[BLOCKED]',
        _parse_entries=lambda raw: raw.split('\n§\n'))
    agent = SimpleNamespace(_memory_store=memory, _memory_enabled=False, ephemeral_system_prompt='base')
    ctx = SimpleNamespace(map_memory_entries={})
    attach_map_memory_refs(agent, ctx)
    assert not ctx.map_memory_entries
    agent._memory_enabled = True
    attach_map_memory_refs(agent, ctx)
    assert list(ctx.map_memory_entries.values()) == ['visible']
    assert list(ctx.map_memory_entries)[0] in agent.ephemeral_system_prompt
    assert 'sensitive' not in agent.ephemeral_system_prompt


def test_scope_dialogue_keeps_submitted_answers_in_their_turn():
    async def scenario():
        store = HarnessStore(database='test_map_dialogue_' + uuid4().hex)
        owner = {'principal_id': 'alice', 'session_id': 'chat'}
        try:
            await store.runs.insert_many([
                {**owner, 'run_id': 'first', 'active': False, 'created_at': '01', 'query': 'make maps',
                 'answer_text': 'Which period?', 'native_messages': [{'role': 'tool', 'content': 'private data'}]},
                {**owner, 'run_id': 'next', 'created_at': '02', 'query': 'last four weeks'},
            ])
            prompt = {**owner, 'run_id': 'first', 'kind': 'clarify', 'questions': [{'qid': 'q0', 'question': 'Product?'}],
                      'status': 'resolved', 'result': {'outcome': 'submitted', 'answers': {'q0': '4SA'}}}
            await store.db.pending_prompts.insert_many([
                dict(prompt), {**prompt, 'principal_id': 'bob', 'result': {'outcome': 'submitted', 'answers': {'q0': 'secret'}}},
                {**prompt, 'run_id': 'next', 'result': {'outcome': 'timed_out', 'answers': {'q0': 'unconfirmed'}}},
            ])
            current = await store.runs.find_one({'run_id': 'next'})
            history = await map_scope_dialogue(store, current)
            assert [m['content'] for m in history] == ['make maps', 'Product?', '4SA', 'Which period?', 'last four weeks']
            assert [m['content'] for m in history if m.get('current_turn')] == ['last four weeks']
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())

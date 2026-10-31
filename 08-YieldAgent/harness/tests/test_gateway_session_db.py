"""Real localhost Mongo integration tests. No mocks, skip, xfail or fallback DB.

Run separately from test_gateway_session_contract.py when Mongo is reachable.
Every scenario owns a UUID database and drops it in finally, matching test_store.
"""
import asyncio
import sqlite3
import uuid
from contextlib import asynccontextmanager

import pytest

from harness.gateway.session_db import SessionDB
from harness.gateway.session_store import MongoSessionStore
from harness.store import Conflict, HarnessStore


@pytest.fixture(autouse=True)
def forbid_sqlite(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('SQLite must never be opened by the Mongo session adapter')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)


@asynccontextmanager
async def database():
    harness = HarnessStore(database='harness_gateway_session_test_' + uuid.uuid4().hex)
    try:
        a, b = MongoSessionStore(harness, 'a'), MongoSessionStore(harness, 'b')
        await a.setup()
        yield a, b, harness
    finally:
        await harness.client.drop_database(harness.db.name)
        harness.client.close()


def test_create_get_list_original_filters_and_sort():
    async def scenario():
        async with database() as (a, b, harness):
            assert await a.create_session('old', 'unknown') == 'old'
            await a.create_session('old', 'cli', model='first', cwd='/work/project')
            await a.create_session('old', 'api', model='ignored')
            assert (await a.get_session('old'))['source'] == 'cli'
            assert (await a.get_session('old'))['model'] == 'first'
            await a.create_session('new', 'api', cwd='/work/project/sub')
            await a.create_session('sibling', 'api', cwd='/work/project-two')
            await a.create_session('child', 'api', parent_session_id='new')
            await a.create_session('branch', 'api', parent_session_id='new', model_config={'_branched_from': 'new'})
            assert (await a.get_session('child'))['cwd'] == '/work/project/sub'
            rows = await a.list_sessions_rich(source='api', cwd_prefix='/work/project', project_compression_tips=False)
            assert [r['id'] for r in rows] == ['branch', 'new']
            assert all('preview' in r and 'last_active' in r and 'unread' in r for r in rows)
            assert [r['id'] for r in await a.list_sessions_rich(sources=['cli'], include_children=True)] == ['old']
            assert await a.list_sessions_rich(source='cli', exclude_sources=['cli']) == []
            assert await a.list_sessions_rich(min_message_count=1) == []
            await a.append_message('old', 'user', 'hello')
            assert [r['id'] for r in await a.list_sessions_rich(min_message_count=1)] == ['old']
            assert (await a.list_sessions_rich(order_by_last_active=True))[0]['id'] == 'old'
            assert (await a.list_sessions_rich(source='cli'))[0]['preview'] == 'hello'
            assert await b.list_sessions_rich() == []
    asyncio.run(scenario())


def test_list_pagination_and_hidden_archived_pins():
    async def scenario():
        async with database() as (a, _, harness):
            for i in range(5):
                await a.create_session(str(i), 'cli')
            ids = lambda rows: [r['id'] for r in rows]
            assert ids(await a.list_sessions_rich(limit=2, offset=1)) == ['3', '2']
            assert await a.list_sessions_rich(limit=0) == []
            assert await a.list_sessions_rich(offset=9) == []
            assert len(await a.list_sessions_rich(limit=-1, offset=-1)) == 5
            # Seed flags whose public mutators deliberately belong to a later PR.
            await a.sessions.update_one(a._session('4'), {'$set': {'hidden': 1, 'archived': 1}})
            await a.sessions.update_one(a._session('0'), {'$set': {'pinned': 1, 'archived': 1}})
            assert ids(await a.list_sessions_rich()) == ['3', '2', '1']
            assert ids(await a.list_sessions_rich(archived_only=True)) == ['4', '0']
            assert ids(await a.list_sessions_rich(limit=1, include_pinned=True)) == ['3', '0']
    asyncio.run(scenario())


def test_message_pagination_latest_order_and_boundaries():
    async def scenario():
        async with database() as (a, _, harness):
            await a.create_session('s', 'cli')
            ids = [await a.append_message('s', 'user', str(i), timestamp=100 - i) for i in range(6)]
            content = lambda rows: [r['content'] for r in rows]
            assert content(await a.get_messages('s', limit=2, offset=1)) == ['1', '2']
            assert content(await a.get_messages('s', limit=2, offset=1, latest=True)) == ['3', '4']
            assert content(await a.get_messages('s', after_id=ids[3])) == ['4', '5']
            assert content(await a.get_messages('s', offset=4)) == ['4', '5']
            assert content(await a.get_messages('s', limit=-1, offset=-1)) == list(map(str, range(6)))
            assert await a.get_messages('s', limit=0) == []
            assert await a.get_messages('s', offset=100, latest=True) == []
            for options in ({'latest': True}, {'offset': 1}, {'include_compacted': True}, {'include_ancestors': True}):
                with pytest.raises(ValueError):
                    await a.get_messages('s', after_id=ids[0], **options)
            rows = await a.messages.find(a._scope(session_id='s')).sort('seq', 1).to_list(None)
            assert [r['seq'] for r in rows] == list(range(1, 7))
            assert [r['id'] for r in rows] == ids
    asyncio.run(scenario())


async def compressed_chain(a):
    await a.create_session('root', 'cli')
    await a.append_message('root', 'user', 'root message', timestamp=10)
    await a.end_session('root', 'compression')
    await a.create_session('middle', 'cli', parent_session_id='root')
    await a.append_message('middle', 'assistant', 'middle message', timestamp=9)
    await a.end_session('middle', 'compression')
    await a.create_session('tip', 'cli', parent_session_id='middle')
    await a.append_message('tip', 'user', 'tip message', timestamp=8)


def test_resume_redirects_to_compression_child_and_ignores_forks():
    async def scenario():
        async with database() as (a, _, harness):
            await compressed_chain(a)
            for marker in ('_branched_from', '_delegate_from', '_reset_from'):
                await a.create_session(marker, 'cli', parent_session_id='tip', model_config={marker: 'tip'})
                await a.append_message(marker, 'user', 'must not hijack')
            await a.create_session('tool-child', 'tool', parent_session_id='tip')
            assert await a.resolve_resume_session_id('root') == 'tip'
            assert await a.resolve_resume_session_id('missing') == 'missing'
            assert await a.get_compression_chain('root') == ['root', 'middle', 'tip']
            assert await a.get_compression_tip('root') == 'tip'
            assert await a.get_compression_lineage('middle') == ['root', 'middle', 'tip']
            assert await a.resolve_session_id('midd') == 'middle'
            rows = await a.list_sessions_rich(source='cli')
            projected = next(r for r in rows if r.get('_lineage_root_id') == 'root')
            assert projected['id'] == 'tip'
            assert projected['_lineage_ids'] == ['root', 'middle', 'tip']
    asyncio.run(scenario())


def test_compression_ancestors_compacted_and_rewind_rows():
    async def scenario():
        async with database() as (a, _, harness):
            await compressed_chain(a)
            assert [r['content'] for r in await a.get_messages('tip', include_ancestors=True)] == ['root message', 'middle message', 'tip message']
            assert [r['content'] for r in await a.get_messages('tip')] == ['tip message']
            await a.messages.update_one(a._scope(session_id='root'), {'$set': {'active': 0, 'compacted': 1}})
            await a.messages.update_one(a._scope(session_id='middle'), {'$set': {'active': 0, 'compacted': 0}})
            assert [r['content'] for r in await a.get_messages('tip', include_ancestors=True)] == ['tip message']
            display = await a.get_messages('tip', include_ancestors=True, include_compacted=True)
            assert [r['content'] for r in display] == ['root message', 'tip message']
            assert len(await a.get_messages('tip', include_ancestors=True, include_inactive=True)) == 3
            assert (await a.get_messages('tip', include_ancestors=True, include_compacted=True, limit=1, latest=True))[0]['content'] == 'tip message'
            await a.create_session('branch', 'cli', parent_session_id='root', model_config={'_branched_from': 'root'})
            await a.append_message('branch', 'user', 'branch only')
            assert len(await a.get_messages('branch', include_ancestors=True)) == 1
    asyncio.run(scenario())


def test_compacted_protected_tail_is_deduplicated_before_paging():
    async def scenario():
        async with database() as (a, _, harness):
            await a.create_session('s', 'cli')
            old = await a.append_message('s', 'user', 'same', timestamp=10)
            await a.append_message('s', 'assistant', 'next', timestamp=11)
            new = await a.append_message('s', 'user', 'same', timestamp=10)
            await a.messages.update_one(a._scope(id=old), {'$set': {'active': 0, 'compacted': 1}})
            assert [r['id'] for r in await a.get_messages('s', include_compacted=True, limit=1)] == [new]
            assert [r['content'] for r in await a.get_messages('s', include_compacted=True, offset=1)] == ['next']
    asyncio.run(scenario())


def test_principal_isolation_missing_and_same_id_independence():
    async def scenario():
        async with database() as (a, b, harness):
            await compressed_chain(a)
            for sid in ('root', 'middle', 'tip', 'absent'):
                assert await b.get_session(sid) is None
                assert await b.get_messages(sid, include_ancestors=True, include_inactive=True) == []
                assert await b.resolve_session_id(sid) is None
                assert await b.resolve_resume_session_id(sid) == sid
                assert await b.delete_session(sid) is False
                assert await b.set_session_title(sid, 'no') is False
                await b.end_session(sid, 'closed')
                with pytest.raises(LookupError, match='Session not found'):
                    await b.append_message(sid, 'user', 'no')
            with pytest.raises(LookupError, match='Session not found'):
                await b.create_session('foreign-child', 'cli', parent_session_id='root')
            await b.create_session('tip', 'cli')
            await b.append_message('tip', 'user', 'b only')
            assert [m['content'] for m in await b.get_messages('tip', include_ancestors=True)] == ['b only']
            assert await b.delete_session('tip') is True
            assert await a.get_session('tip') is not None
            assert [r['id'] for r in await b.list_sessions_rich()] == []
            # Even a malformed stored link cannot bridge principal scopes.
            await b.create_session('other', 'cli')
            await b.sessions.update_one(b._session('other'), {'$set': {'parent_session_id': 'root'}})
            assert await b.get_messages('other', include_ancestors=True) == []
    asyncio.run(scenario())


def test_concurrent_submit_gets_exactly_one_active_run_per_principal():
    async def scenario():
        async with database() as (a, b, harness):
            for store in (a, b):
                await store.create_session('same', 'cli')
            result = await asyncio.gather(*(a.start_run('same', f'req-{i}', 'query') for i in range(12)), return_exceptions=True)
            winners = [r for r in result if isinstance(r, dict)]
            assert len(winners) == 1
            assert all(isinstance(r, (dict, Conflict)) for r in result)
            other = await b.start_run('same', 'req-0', 'query')
            assert other['run_id'] != winners[0]['run_id']
            assert await harness.runs.count_documents({'principal_id': 'a', 'session_id': 'same', 'active': True}) == 1
            acquired = await asyncio.gather(*(harness.acquire(winners[0]['run_id'], f'worker-{i}') for i in range(8)))
            assert sum(r is not None for r in acquired) == 1
            receipt = await a.start_run('same', winners[0]['request_id'], 'query')
            assert receipt['run_id'] == winners[0]['run_id']
    asyncio.run(scenario())


def test_titles_end_delete_and_tombstone():
    async def scenario():
        async with database() as (a, b, harness):
            await a.create_session('s', 'cli')
            assert await a.set_session_title('s', '  Hello\nworld\u200b ') is True
            assert await a.get_session_title('s') == 'Hello world'
            assert await a.get_session_title_source('s') == 'user'
            await a.create_session('other', 'cli')
            with pytest.raises(ValueError):
                await a.set_session_title('other', 'Hello world')
            await b.create_session('s', 'cli')
            assert await b.set_session_title('s', 'Hello world')
            with pytest.raises(ValueError):
                await a.set_session_title('s', 'a' * 101)
            await a.end_session('s', 'compression')
            await a.end_session('s', 'shutdown')
            assert (await a.get_session('s'))['end_reason'] == 'compression'
            await a.create_session('child', 'cli', parent_session_id='s')
            assert await a.set_session_title('child', 'Hello world')
            assert await a.get_session_title('s') is None
            assert await a.delete_session('s', expected_delete_ids=['wrong']) is False
            assert await a.delete_session('s') is True
            assert (await a.get_session('child'))['parent_session_id'] is None
            with pytest.raises(LookupError):
                await a.create_session('s', 'cli')
            assert await b.get_session('s') is not None
    asyncio.run(scenario())


def test_delete_compression_chain_and_active_guard():
    async def scenario():
        async with database() as (a, _, harness):
            await compressed_chain(a)
            await a.start_run('tip', 'r', 'query')
            with pytest.raises(Conflict):
                await a.delete_session('root', include_compression_chain=True, exclude_active_write_guards=True)
            assert await a.get_session('root') is not None
            assert await a.delete_session('tip', include_compression_chain=True)
            for sid in ('root', 'middle', 'tip'):
                assert await a.get_session(sid) is None
                assert await a.get_messages(sid, include_inactive=True) == []
    asyncio.run(scenario())


def test_sqlite_forbidden_during_all_sync_adapter_operations():
    async def scenario():
        async with database() as (a, _, harness):
            facade = SessionDB(a, loop=asyncio.get_running_loop())
            def original_caller():
                facade.ensure_session('s')
                facade.create_session('s', 'cli')
                facade.append_message('s', 'user', 'hello')
                assert facade.get_session('s')['message_count'] == 1
                assert facade.session_count() == 1
                assert facade.message_count('s') == 1
                assert facade.get_session_list_row('s')['id'] == 's'
                assert facade.resolve_session_id('s') == 's'
                assert facade.list_sessions_rich()[0]['id'] == 's'
                assert facade.get_messages('s')[0]['content'] == 'hello'
                assert facade.set_session_title('s', 'title')
                assert facade.get_session_title('s') == 'title'
                assert facade.get_session_title_source('s') == 'user'
                facade.end_session('s', 'compression')
                facade.create_session('child', 'cli', parent_session_id='s')
                facade.append_message('child', 'assistant', 'next')
                assert facade.resolve_resume_session_id('s') == 'child'
                assert facade.get_compression_chain('s') == ['s', 'child']
                assert facade.get_compression_tip('s') == 'child'
                assert facade.get_compression_lineage('child') == ['s', 'child']
                assert len(facade.get_messages('child', include_ancestors=True)) == 2
                assert facade.delete_session('s', include_compression_chain=True)
                facade.close()
            await asyncio.to_thread(original_caller)
    asyncio.run(scenario())


def test_profile_namespace_inheritance_and_listing_sibling_activity():
    async def scenario():
        async with database() as (a, _, harness):
            await a.create_session('root', 'cli', profile_name='main', session_key='agent:main:chat')
            await a.end_session('root', 'compression')
            await a.create_session('preferred', 'cli', parent_session_id='root')
            await a.end_session('preferred', 'compression')
            await a.create_session('sibling', 'cli', parent_session_id='root', session_key='agent:other:chat')
            assert (await a.get_session('preferred'))['profile_name'] == 'main'
            assert (await a.get_session('sibling'))['profile_name'] is None
            await a.append_message('sibling', 'user', 'latest sibling activity')
            await a.end_session('sibling', 'closed')
            assert await a.get_compression_tip('root') == 'preferred'
            rows = await a.list_sessions_rich(order_by_last_active=True, id_query='sibling')
            assert len(rows) == 1 and rows[0]['id'] == 'preferred'
            assert rows[0]['_lineage_root_id'] == 'root'
            assert await a.session_count(exclude_children=True) == 1
    asyncio.run(scenario())


def test_append_payload_roundtrip_and_counts():
    async def scenario():
        async with database() as (a, _, harness):
            await a.create_session('s', 'cli')
            calls = [{'id': 'call-1', 'function': {'name': 'query', 'arguments': '{}'}}]
            message_id = await a.append_message('s', 'assistant', [{'type': 'text', 'text': 'hello'}],
                tool_calls=calls, reasoning_details=[{'text': 'reason'}], display_metadata='{"tag": 1}',
                observed=True, _compressed_summary=True, message_uid='stable', timestamp=1)
            row = (await a.get_messages('s'))[0]
            assert row['id'] == message_id and row['message_uid'] == 'stable'
            assert row['content'] == [{'type': 'text', 'text': 'hello'}]
            assert row['tool_calls'] == calls
            assert row['display_metadata'] == {'tag': 1}
            assert row['reasoning_details'] == '[{"text": "reason"}]'
            assert row['_compressed_summary'] is True and row['observed'] == 1
            assert row['tool_call_uids'] is not None
            assert (await a.get_session('s'))['tool_call_count'] == 1
            assert await a.message_count() == 1
    asyncio.run(scenario())

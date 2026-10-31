"""Real localhost Mongo only. PR-6a author explicitly must NOT execute this file.

UUID database + finally drop, matching test_gateway_session_db.py. No fake DB,
Docker, skip, model calls, worker or transport. Tools below are synthetic only.
"""
import asyncio
from contextlib import asynccontextmanager
import json
import uuid

import pytest

from harness.gateway.pending_controls import PendingControls
from harness.gateway.pending_wire import PendingControlError
from harness.store import HarnessStore


@asynccontextmanager
async def database():
    harness = HarnessStore(database='harness_pending_test_' + uuid.uuid4().hex)
    try:
        a, b = PendingControls(harness, 'a'), PendingControls(harness, 'b')
        await a.setup()
        yield a, b, harness
    finally:
        await harness.client.drop_database(harness.db.name)
        harness.client.close()


async def clarify(a, identity='p', **kwargs):
    return await a.register_clarify('s', 1, {'clarify_id': identity, 'qid': 'q',
        'question': 'Synthetic question', 'choices': None, 'multi_select': False}, **kwargs)


async def approval(a, identity='p', session='s', reason='Synthetic effect', **kwargs):
    return await a.register(session, 1, {'request_id': identity, 'tool_name': 'synthetic_effect',
        'command': 'Synthetic action', 'description': reason},
        tool_allowed=True, effect='persistent_write', **kwargs)


def test_clarify_round_trip_reconnect_cross_instance_and_isolation():
    async def scenario():
        async with database() as (a, b, harness):
            await clarify(a)
            waiting = asyncio.create_task(a.wait('p'))
            second = PendingControls(harness, 'a')
            assert await second.snapshot('s', 1) == await a.snapshot('s', 1)
            assert await b.snapshot('s', 1) == ()
            assert await b.respond('s', 1, 'p', {'answer': 'foreign'}, 'clarify') == {
                'status': 'already_resolved', 'prompt_id': 'p'}
            assert (await second.respond('s', 1, 'p', {'answer': 'PRIVATE ANSWER'}, 'clarify'))['status'] == 'resolved'
            assert await asyncio.wait_for(waiting, 1) == {'answers': {'q': 'PRIVATE ANSWER'}, 'outcome': 'submitted'}
            assert await a.snapshot('s', 1) == ()
            assert 'PRIVATE ANSWER' not in json.dumps(await a.list_audit())
            assert await b.list_audit() == []
            with pytest.raises(LookupError):
                await b.wait('p', 0)
    asyncio.run(scenario())


@pytest.mark.parametrize('choice', ['once', 'deny', 'session', 'always'])
def test_approval_choices_and_scope(choice):
    async def scenario():
        async with database() as (a, b, harness):
            await approval(a)
            assert (await a.respond('s', 1, 'p', {'choice': choice}))['status'] == 'resolved'
            assert await a.wait('p') == {'choice': choice}
            await approval(a, 'next')
            live = await a.snapshot('s', 1)
            if choice in ('once', 'deny'):
                assert len(live) == 1
            else:
                assert live == ()
                assert await a.wait('next') == {'choice': choice}
            await approval(a, 'other-session', session='other')
            assert bool(await a.snapshot('other', 1)) is (choice != 'always')
            assert await b.list_rules() == []
            assert await b.list_audit() == []
            if choice == 'session':
                assert await a.list_rules() == []
                a.clear_session('s')
                await approval(a, 'after-clear')
                assert len(await a.snapshot('s', 1)) == 1
            if choice == 'always':
                rule = (await a.list_rules())[0]
                assert await b.revoke_rule(rule['rule_id']) is False
                assert await a.revoke_rule(rule['rule_id']) is True
                await approval(a, 'after-revoke')
                assert len(await a.snapshot('s', 1)) == 1
                events = [row['event'] for row in await a.list_audit()]
                assert 'rule_created' in events and 'rule_revoked' in events
                assert 'auto_allowed_by_rule' in events
    asyncio.run(scenario())


def test_rules_are_tool_condition_principal_scoped_and_smart_denial_wins():
    async def scenario():
        async with database() as (a, b, harness):
            await approval(a)
            await a.respond('s', 1, 'p', {'choice': 'always'})
            await approval(a, 'different', reason='Different reason')
            await approval(b, 'p')
            assert len(await a.snapshot('s', 1)) == 1
            assert len(await b.snapshot('s', 1)) == 1
            await a.register('s', 1, {'request_id': 'smart', 'tool_name': 'synthetic_effect',
                'description': 'Synthetic effect', 'smart_denied': True}, tool_allowed=True, effect='artifact')
            assert len(await a.snapshot('s', 1)) == 2
            with pytest.raises(PendingControlError):
                await a.respond('s', 1, 'smart', {'choice': 'always'})
    asyncio.run(scenario())


@pytest.mark.parametrize('kind', ['clarify', 'approval'])
def test_timeout_cancel_and_generation(kind):
    async def scenario():
        async with database() as (a, b, harness):
            register = clarify if kind == 'clarify' else approval
            await register(a, timeout_s=.01)
            result = await a.wait('p', .1)
            assert result == ({'choice': 'deny'} if kind == 'approval' else {'answers': {}, 'outcome': 'timed_out'})
            assert 'timed_out' in [row['event'] for row in await a.list_audit()]
            assert (await a.respond('s', 1, 'p', {'choice': 'once'}))['status'] == 'already_resolved'
            await register(a, 'cancel', run_id='run')
            waiting = asyncio.create_task(a.wait('cancel'))
            events = await a.cancel(run_id='run', reason='interrupt')
            assert any(e['type'] == kind + '.settled' for e in events)
            if kind == 'approval':
                assert any(e['type'] == 'approval.cancelled' for e in events)
            assert await asyncio.wait_for(waiting, .5) == ({'choice': 'deny'} if kind == 'approval' else {
                'answers': {}, 'outcome': 'cancelled'})
            row = await harness.db.pending_prompts.find_one({'principal_id': 'a', 'prompt_id': 'cancel'})
            assert row['status'] == 'cancelled'
            await register(a, 'stale')
            assert await a.snapshot('s', 0) == ()  # Old viewer cannot cancel a newer generation.
            assert len(await a.snapshot('s', 1)) == 1
            assert await a.snapshot('s', 2) == ()
            assert (await a.respond('s', 1, 'stale', {}, kind))['status'] == 'already_resolved'
    asyncio.run(scenario())


def test_twenty_responders_exactly_one_winner_and_cross_wire_race():
    async def scenario():
        async with database() as (a, b, harness):
            await approval(a)
            instances = [PendingControls(harness, 'a') for _ in range(20)]
            replies = await asyncio.gather(*(instance.respond('s', 1, 'p', {'choice': 'always'}) for instance in instances))
            assert sum(r['status'] == 'resolved' for r in replies) == 1
            assert len(await a.list_rules()) == 1
            assert len([r for r in await a.list_audit() if r['event'] == 'response']) == 1
            await clarify(a, 'c')
            replies = await asyncio.gather(a.respond('s', 1, 'c', {'answer': 'authority'}, 'clarify'),
                instances[0].respond_legacy('s', 1, 'c', {'answers': {'q': 'legacy'}}, 'clarify'))
            assert sum(r['status'] == 'resolved' for r in replies) == 1
            assert (await a.wait('c'))['answers']['q'] in ('authority', 'legacy')
    asyncio.run(scenario())


def test_multi_question_locks_replay_legacy_and_timeout_keep_answers():
    async def scenario():
        async with database() as (a, b, harness):
            questions = [{'qid': 'one', 'question': 'One'}, {'qid': 'two', 'question': 'Two'}]
            await a.register_clarify('s', 1, {'clarify_id': 'batch', 'questions': questions})
            prompts = await a.snapshot('s', 1)
            first, second = [p['prompt_id'] for p in prompts]
            assert (await a.respond('s', 1, first, {'answer': 'yes'}, 'clarify'))['status'] == 'resolved'
            assert (await a.respond('s', 1, first, {'answer': 'duplicate'}, 'clarify'))['status'] == 'already_resolved'
            new = PendingControls(harness, 'a')
            assert [p['prompt_id'] for p in await new.snapshot('s', 1)] == [second]
            replay = await new.legacy_request('s', 1, 'batch')
            assert replay['params']['answers'] == {'one': 'yes'}
            await new.respond_legacy('s', 1, 'batch', {'answers': {'two': None}}, 'clarify')
            assert await a.wait('batch') == {'answers': {'one': 'yes', 'two': None}, 'outcome': 'submitted'}
            await a.register_clarify('s', 1, {'clarify_id': 'timeout', 'questions': questions})
            first = (await a.snapshot('s', 1))[0]['prompt_id']
            await a.respond('s', 1, first, {'answer': 'locked'}, 'clarify')
            assert await a.wait('timeout', 0) == {'answers': {'one': 'locked'}, 'outcome': 'timed_out'}
    asyncio.run(scenario())


def test_restrictions_decline_audit_and_same_id_cannot_be_replaced():
    async def scenario():
        async with database() as (a, b, harness):
            for name in ('sudo', 'secret'):
                assert await a.decline_server_request(name, {'value': 'PRIVATE SECRET'}) == {'value': ''}
            assert 'PRIVATE SECRET' not in json.dumps(await a.list_audit())
            for tool in ('terminal', 'shell', 'file', 'browser', 'sudo', 'secret'):
                with pytest.raises(PermissionError):
                    await a.register('s', 1, {'tool_name': tool, 'request_id': tool},
                                     tool_allowed=True, effect='persistent_write')
            with pytest.raises(PermissionError):
                await a.register('s', 1, {'tool_name': 'synthetic_effect', 'request_id': 'denied'})
            await clarify(a)
            from pymongo.errors import DuplicateKeyError
            with pytest.raises(DuplicateKeyError):
                await clarify(a)
            assert len(await a.snapshot('s', 1)) == 1
    asyncio.run(scenario())


def test_concurrent_always_has_one_active_condition_rule_and_no_auto_prompt():
    async def scenario():
        async with database() as (a, b, harness):
            await approval(a, 'one')
            await approval(a, 'two')
            await asyncio.gather(a.respond('s', 1, 'one', {'choice': 'always'}),
                                 a.respond('s', 1, 'two', {'choice': 'always'}))
            rules = await a.list_rules()
            assert len(rules) == 1
            a.drain_events()
            await approval(a, 'auto')
            assert not any(e['type'] == 'approval.request' for e in a.drain_events())
            assert await a.wait('auto') == {'choice': 'always'}
            await a.revoke_rule(rules[0]['rule_id'])
            await approval(a, 'ask-again')
            assert len(await a.snapshot('s', 1)) == 1
    asyncio.run(scenario())


def test_recovery_of_old_decision_cannot_undo_revocation_but_new_consent_can():
    async def scenario():
        async with database() as (a, b, harness):
            await approval(a, 'old')
            await a.respond('s', 1, 'old', {'choice': 'always'})
            old = await harness.db.pending_prompts.find_one({'principal_id': 'a', 'prompt_id': 'old'})
            rule = (await a.list_rules())[0]
            await a.revoke_rule(rule['rule_id'])
            # Replay the idempotent post-CAS finalizer as a restarted waiter would.
            await a._finalize(old)
            assert await a.list_rules() == []
            await approval(a, 'new')
            await a.respond('s', 1, 'new', {'choice': 'always'})
            assert len(await a.list_rules()) == 1
            assert await a.revoke_rule(rule['rule_id']) is True
            assert len([r for r in await a.list_audit() if r['event'] == 'rule_revoked']) == 2
    asyncio.run(scenario())

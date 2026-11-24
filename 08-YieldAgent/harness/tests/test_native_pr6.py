"""PR-6b pure tests: no Mongo, sockets, engine startup or model calls."""
import asyncio
from types import SimpleNamespace

import pytest



def test_generation_timeout_and_history_projection():
    from harness.native_hermes import prompt_generation, prompt_history
    from harness.gateway.pending_wire import effective_timeout
    assert prompt_generation({'epoch': 4, 'goal_revision': 9}) == 4
    assert effective_timeout(300, 8) == 7
    assert effective_timeout(300, .5) == 0
    history = [{'role': 'assistant', 'tool_calls': [
        {'id': 'c', 'function': {'name': 'clarify', 'arguments': 'private question'}},
        {'id': 'r', 'function': {'name': 'read', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'c', 'content': 'private answer'},
        {'role': 'tool', 'tool_call_id': 'r', 'content': 'evidence'}]
    assert 'private' not in str(prompt_history(history))
    assert 'evidence' in str(prompt_history(history))
    assert 'private' in str(history)


def test_settlement_events_can_finish_while_cancelling_but_remain_epoch_fenced():
    from harness.store import HarnessStore
    calls = []
    class Runs:
        async def find_one_and_update(self, query, update, **kw): calls.append(query)
    async def scenario():
        store = SimpleNamespace(runs=Runs())
        for kind in ('clarify.settled', 'approval.settled', 'approval.cancelled', 'clarify.request', 'progress'):
            await HarnessStore.event(store, 'r', kind, {}, epoch=3)
        for query in calls[:3]:
            assert query == {'_id': 'r', 'status': {'$in': ['running', 'cancelling']}, 'active': True, 'epoch': 3}
        assert all(q['status'] == 'running' for q in calls[3:])
        await HarnessStore.event(store, 'r', 'tool_finished', {'name': 'clarify'}, epoch=3)
        assert calls[-1]['status'] == {'$in': ['running', 'cancelling']}
        assert calls[-1]['epoch'] == 3
        await HarnessStore.event(store, 'r', 'tool_finished', {'name': 'other'}, epoch=3)
        assert calls[-1]['status'] == 'running'
    asyncio.run(scenario())


@pytest.mark.parametrize('outcome,answers', [
    ('timed_out', {}), ('submitted', {'q': 'below 80%'}), ('cancelled', {}),
])
def test_host_clarify_timeout_notice_does_not_mutate_pending_result(monkeypatch, outcome, answers):
    from unittest.mock import AsyncMock
    from harness.native_hermes import NativePrompts

    async def scenario():
        stored_result = {'answers': answers, 'outcome': outcome}
        pending = SimpleNamespace(
            principal_id='p',
            register_clarify=AsyncMock(return_value='prompt'),
            pending_prompts=SimpleNamespace(find_one=AsyncMock(return_value={})),
        )
        context = SimpleNamespace(
            run={'run_id': 'r', 'session_id': 's', 'epoch': 3},
            check=AsyncMock(), remaining_seconds=lambda: 600,
        )
        prompts = NativePrompts(context, pending)
        waiter = AsyncMock(return_value=stored_result)
        monkeypatch.setattr(prompts, 'wait', waiter)
        result = await prompts.clarify({'questions': [{'qid': 'q', 'question': 'Criterion?'}]})
        waiter.assert_awaited_once_with('prompt', 300)
        assert pending.register_clarify.call_args.kwargs['timeout_s'] == 300
        assert stored_result == {'answers': answers, 'outcome': outcome}
        if outcome == 'timed_out':
            assert result == {
                'answers': {}, 'outcome': 'timed_out',
                'notice': '사용자가 답하지 않았습니다. 백분위수나 고정 임계값을 포함한 기준을 지어내지 말고, 사용자 기준이 여전히 필요하다고 알린 뒤 해당 턴을 종료하세요.',
            }
        else:
            assert result == stored_result

    asyncio.run(scenario())


@pytest.mark.parametrize('outcome', ['submitted', 'timed_out', 'cancelled'])
@pytest.mark.parametrize('publisher', ['native', 'stored'])
def test_clarify_completion_matches_parked_card(outcome, publisher):
    from unittest.mock import AsyncMock
    from harness.tests.test_gateway_pr7a_unit import fixture
    from harness.gateway.pending_controls import PendingControls
    from harness.gateway.events import project_run_event
    from harness.native_hermes import NativePrompts, publish_stored_prompt_events

    async def scenario():
        store, _, execution, _ = await fixture()
        reply = await execution.submit('s', {'session_id': 's', 'submission_id': 'id', 'text': 'hello'})
        run = await store.runs.find_one({'run_id': reply['run_id']})
        pending = PendingControls(store, 'a')
        questions = [{'qid': 'q', 'question': 'Criterion?', 'choices': ['A (Recommended)', 'B']}]
        identity = await pending.register_clarify('s', run['epoch'],
            {'clarify_id': 'prompt', 'questions': questions}, run_id=run['run_id'])
        prompts = NativePrompts(SimpleNamespace(run=run, store=store), pending)
        prompts.rows[identity] = await pending.pending_prompts.find_one({'prompt_id': identity})
        await prompts.publish()
        if outcome == 'cancelled':
            await pending.cancel(identity, reason='interrupt')
        else:
            await pending.respond_legacy('s', run['epoch'], identity, {
                'answers': {'q': 'A (Recommended)'} if outcome == 'submitted' else {},
                'outcome': outcome}, 'clarify')
        if publisher == 'stored':
            await publish_stored_prompt_events(store, pending)
        await prompts.publish()
        await prompts.publish()
        saved = await store.runs.find_one({'run_id': run['run_id']})
        completed = [e for e in saved['events'] if e['type'] == 'tool_finished']
        assert len(completed) == 1
        payload = completed[0]['payload']
        assert payload['name'] == 'clarify'
        assert payload['tool_id'] == payload['invocation_id'] == identity
        assert payload['args']['questions'][0]['question'] == 'Criterion?'
        assert payload['status'] == 'success'
        result = payload['result']
        assert result['outcome'] == outcome
        assert result['responses'] == [{'question': 'Criterion?', 'choices_offered': ['A', 'B'],
            'status': 'answered' if outcome == 'submitted' else 'unanswered',
            'user_response': 'A' if outcome == 'submitted' else None}]
        if outcome == 'timed_out':
            assert result['notice'].startswith('사용자가 답하지 않았습니다.')
        projected = project_run_event(saved, completed[0])
        assert projected['type'] == 'tool.complete'
        assert projected['payload']['result'] == result
        assert projected['payload']['is_error'] is False
        assert 'native_messages' not in saved
    asyncio.run(scenario())


def test_clarify_result_cleans_multiselect_and_distinguishes_empty_from_missing():
    from harness.gateway.pending_wire import clarify_tool_result
    questions = [{'qid': str(i), 'question': str(i), 'choices': None, 'multi_select': i == 0}
                 for i in range(4)]
    result = clarify_tool_result(questions, {'0': '["A (Recommended)", "B"]', '1': '', '2': None}, 'submitted')
    assert [r['status'] for r in result['responses']] == ['answered', 'skipped', 'skipped', 'unanswered']
    assert result['responses'][0]['user_response'] == ['A', 'B']


@pytest.mark.parametrize('outcome', ['timed_out', 'cancelled'])
def test_batch_partial_settlement_and_reconnect_completion(outcome):
    from harness.tests.test_gateway_pr7a_unit import fixture
    from harness.gateway.pending_controls import PendingControls
    from harness.native_hermes import publish_stored_prompt_events, pending_snapshot, cancel_run_prompts

    async def scenario():
        store, _, execution, _ = await fixture()
        reply = await execution.submit('s', {'session_id': 's', 'submission_id': 'id', 'text': 'hello'})
        run = await store.runs.find_one({'run_id': reply['run_id']})
        pending = PendingControls(store, 'a')
        await pending.register_clarify('s', run['epoch'], {'clarify_id': 'batch', 'questions': [
            {'qid': 'a', 'question': 'A'}, {'qid': 'b', 'question': 'B'}]}, run_id=run['run_id'])
        await publish_stored_prompt_events(store, pending)
        await pending.respond('s', run['epoch'], 'batch:q1', {'answer': 'chosen'}, 'clarify')
        await publish_stored_prompt_events(store, pending)
        if outcome == 'timed_out':
            await pending.pending_prompts.update_one({'prompt_id': 'batch'}, {'$set': {'expires_at': 0}})
            assert await pending_snapshot(store, 'a', 's', run['epoch']) == ()
        else:
            await store.runs.update_one({'run_id': run['run_id']}, {'$set': {'status': 'cancelling'}})
            await cancel_run_prompts(store, 'a', run['run_id'], 'interrupt')
        await pending_snapshot(store, 'a', 's', run['epoch'])
        saved = await store.runs.find_one({'run_id': run['run_id']})
        completed = [e['payload'] for e in saved['events'] if e['type'] == 'tool_finished']
        assert [p['tool_id'] for p in completed] == ['batch:q1', 'batch:q2']
        assert completed[0]['result']['outcome'] == 'submitted'
        assert completed[0]['result']['responses'][0]['user_response'] == 'chosen'
        assert completed[1]['result']['outcome'] == outcome
        assert completed[1]['result']['responses'] == [{'question': 'B', 'choices_offered': None,
            'status': 'unanswered', 'user_response': None}]
        assert all(len(p['result']['responses']) == 1 for p in completed)
    asyncio.run(scenario())

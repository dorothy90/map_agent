import ast
import asyncio
from pathlib import Path

from harness.gateway.events import SessionEvents, project_run_event
from harness.gateway.publication import candidate_digest
from harness.tests.test_gateway_pr7a_unit import fixture


def test_worker_callbacks():
    tree = ast.parse(Path(__file__).parents[1].joinpath('native_worker.py').read_text())
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == 'AIAgent')
    callbacks = {k.arg: k.value for k in call.keywords}
    emitted = []
    for name, text, kwargs in [
        ('stream_delta_callback', 'chunk', {}),
        ('interim_assistant_callback', 'whole', {'already_streamed': True}),
        ('interim_assistant_callback', 'fallback', {}),
    ]:
        callback = eval(compile(ast.Expression(callbacks[name]), '<callback>', 'eval'), {'emit': emitted.append})
        callback(text, **kwargs)
    assert emitted == [
        {'type': 'commentary', 'content': 'chunk', 'delta': True},
        {'type': 'commentary', 'content': 'whole', 'already_streamed': True},
        {'type': 'commentary', 'content': 'fallback', 'already_streamed': False}]


def test_live_replay_then_verified_completion():
    async def scenario():
        store, _, execution, _ = await fixture()
        receipt = await execution.submit('s', {'session_id': 's', 'submission_id': '1', 'text': 'hello'})
        run = next(r for r in store.runs.rows if r['run_id'] == receipt['run_id'])
        events = SessionEvents(store, 'a')
        epoch = (await events.watermark('s'))['replay_epoch']
        await events.publish_live('s', 'message.delta', {'text': 'draft'})
        await events.publish_live('s', 'message.interim', {'text': 'draft', 'already_streamed': True})
        live = await events.since('s', epoch)
        assert live['events'][0]['payload']['text'] == 'draft'
        assert live['events'][0]['type'] == 'message.delta'
        last = live['latest_seq']
        assert run.get('events', []) == []
        for kind in ('message.delta', 'message.interim', 'message.complete', 'commentary'):
            assert project_run_event(run, {'type': kind, 'payload': {'text': 'draft'}}) is None
        assert project_run_event(run, {'type': 'run_finished'})['type'] == 'run_finished'
        await store.event(run['run_id'], 'run_finished', {'text': 'unverified'})
        run.update(active=False, status='completed', answer_text='verified', observations=[{
            'tool_name': 'verify_answer', 'scope': {'verdict': 'pass'},
            'validated_arguments': candidate_digest('verified')}])
        settled = await events.since('s', epoch, last)
        assert not settled['truncated']
        assert settled['events'][0]['seq'] == last + 1
        assert settled['events'][0]['payload']['text'] == 'verified'
        assert settled['events'][0]['type'] == 'message.complete'
        reopened = await events.since('s', epoch, 0)
        assert reopened['truncated'] and not reopened['events']
        assert 'draft' not in str(await events._read('s'))
    asyncio.run(scenario())


def test_host_coalesces_and_flushes_before_boundaries_and_idle():
    from harness.native_hermes import coalesced_worker_messages, live_commentary
    from harness.native_ipc import encode

    async def scenario():
        reader = asyncio.StreamReader()
        stream = coalesced_worker_messages(reader, lambda: 2)
        for text in ('a', 'b'):
            reader.feed_data(encode({'type': 'commentary', 'content': text, 'delta': True}).encode())
        first = await asyncio.wait_for(anext(stream), .5)
        assert live_commentary(first) == ('message.delta', {'text': 'ab'})
        for boundary in [
            {'type': 'commentary', 'content': 'full', 'already_streamed': True},
            {'type': 'step', 'tokens': 1, 'models': 1},
            {'type': 'call', 'call_id': 'call-test', 'method': 'tool_call', 'name': 'test', 'args': {}},
            {'type': 'result', 'result': {}},
            {'type': 'error', 'message': 'error'},
        ]:
            reader.feed_data(encode({'type': 'commentary', 'content': 'c', 'delta': True}).encode())
            reader.feed_data(encode(boundary).encode())
            assert (await anext(stream))['content'] == 'c'
            assert await anext(stream) == boundary
        reader.feed_eof()
        assert [m async for m in stream] == []
    assert live_commentary({'type': 'commentary', 'content': 'verification'}) is None
    assert live_commentary({'type': 'commentary', 'content': 'full', 'already_streamed': False}) == (
        'message.interim', {'text': 'full', 'already_streamed': False})
    asyncio.run(scenario())

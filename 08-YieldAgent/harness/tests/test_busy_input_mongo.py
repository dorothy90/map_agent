"""Gateway busy input behavior using isolated localhost MongoDB."""
import asyncio
from types import SimpleNamespace

from harness.gateway.auth import Principal
from harness.gateway.connection import GatewayConnection, Subscription

from harness.gateway.execution import Execution
from harness.tests.test_gateway_pr7a_mongo import database
from harness.tests.test_gateway_pr7b_unit import MANIFEST, Sink


def test_busy_mode_is_persisted_per_principal():
    async def scenario():
        async with database() as (_, controller, a, b):
            params = {'key': 'busy', 'session_id': 's'}
            assert (await a.rpc('config.get', None, params))['result'] == {'value': 'steer'}
            assert (await a.rpc('config.set', None, {**params, 'value': 'queue'}))['result'] == {'value': 'queue'}
            reopened = Execution(controller, 'a', profile_id='profile')
            assert (await reopened.rpc('config.get', None, params))['result'] == {'value': 'queue'}
            assert (await b.rpc('config.get', None, params))['result'] == {'value': 'steer'}
            for bad in [{'key': 'busy', 'value': 'bad'}, {'key': 'model', 'value': 'queue'}]:
                assert 'error' in await a.rpc('config.set', None, bad)
            assert 'error' in await a.rpc('config.get', None, {'key': 'model'})
            assert (await reopened.rpc('config.get', None, params))['result'] == {'value': 'queue'}
    asyncio.run(scenario())


def test_steer_only_changes_the_targeted_active_run():
    async def scenario():
        async with database() as (store, _, a, b):
            correction = {'session_id': 's', 'text': '$keep this text'}
            idle = await a.rpc('session.steer', 's', correction)
            assert idle['error']['data']['reason'] == 'execution_not_ready'
            admitted = await a.submit('s', {'session_id': 's', 'submission_id': 'id', 'text': 'original'})
            run_id = admitted['run_id']
            generation = admitted['execution_generation']
            for adapter in [a, b]:
                refused = await adapter.rpc('session.steer', 's',
                    {**correction, 'execution_generation': generation + 1}, expected_run_id=run_id)
                assert refused['error']['data']['reason'] in {'execution_not_ready', 'stale_generation'}
            assert not (await store.get_run('a', run_id)).get('steer_inbox')
            for text in ['', '  ', 'x' * 8001]:
                refused = await a.rpc('session.steer', 's', {**correction, 'text': text})
                assert refused['error']['data']['reason'] == 'invalid_params'
            accepted = await a.rpc('session.steer', 's', {**correction, 'execution_generation': generation},
                expected_run_id=run_id)
            assert accepted['result'] == {'status': 'queued'}
            mark = await a.events.watermark('s')
            events = (await a.events.since('s', mark['replay_epoch'], 0))['events']
            assert [event['payload']['text'] for event in events if event['type'] == 'steer.accepted'] == [
                correction['text']]
            inbox = (await store.get_run('a', run_id))['steer_inbox']
            assert len(inbox) == 1 and inbox[0]['text'] == correction['text']
            await store.runs.update_one({'_id': run_id}, {'$set': {'status': 'waiting_user'}})
            refused = await a.rpc('session.steer', 's', correction, mode='legacy')
            assert refused['error']['data']['reason'] == 'execution_not_ready'
            assert (await store.get_run('a', run_id))['steer_inbox'] == inbox
    asyncio.run(scenario())


def test_queued_flag_uses_normal_exclusive_admission():
    async def scenario():
        async with database() as (store, controller, a, _):
            params = {'session_id': 's', 'submission_id': 'first', 'text': 'queued draft', 'queued': True}
            admitted = (await a.rpc('prompt.submit', 's', params))['result']
            assert admitted['status'] == 'started'
            refused = await a.rpc('prompt.submit', 's', {**params, 'submission_id': 'next'})
            assert refused['error']['data']['reason'] == 'session_busy'
            assert controller.launches == [admitted['run_id']]
            assert await store.runs.count_documents({'principal_id': 'a', 'session_id': 's'}) == 1
    asyncio.run(scenario())



def test_live_carryover_event_allows_steer_on_successor():
    async def scenario():
        async with database() as (store, _, a, _):
            sid = 's'
            first = await a.submit(sid, {'session_id': sid, 'submission_id': 'first', 'text': 'original'})
            peer = GatewayConnection(Sink(), Principal('a'), None, MANIFEST, 'authority')
            peer.scope = SimpleNamespace(execution=a)
            peer.run_bindings[sid] = first['run_id']
            mark = await a.events.watermark(sid)
            peer.subscriptions[sid] = Subscription('sub', mark['replay_epoch'], 0, ready=True)
            await store.finish(first['run_id'], first['execution_generation'], 'completed', 'answer', None)
            next_run = await a.submit(sid, {'session_id': sid, 'submission_id': 'carryover', 'text': 'carried steer'})
            await store.runs.update_one({'_id': next_run['run_id']},
                {'$set': {'steer_carryover_from': first['run_id']}})
            await store.event(next_run['run_id'], 'run_started', {})
            await peer.pump_once()
            assert any(frame['params']['payload'].get('carryover_text') == 'carried steer'
                       for frame in peer.ws.frames)
            await peer.process({'id': 1, 'method': 'session.steer', 'params': {'session_id': sid,
                'text': 'new steer', 'execution_generation': next_run['execution_generation']}})
            assert peer.ws.frames[-1]['result'] == {'status': 'queued'}
    asyncio.run(scenario())

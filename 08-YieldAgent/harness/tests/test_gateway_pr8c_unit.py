"""PR-8c: real dispatch over in-memory stores, no Mongo or sockets.

H/methods_session.py:885-914,958-978; contracts/canonical_sessions.py:69.
"""
import asyncio
from copy import deepcopy

import pytest

from harness.gateway.auth import Principal
from harness.gateway.connection import GatewayConnection
from harness.gateway.session_rpc import SessionRPC
from harness.tests.test_gateway_pr7b_unit import MANIFEST, MemoryServices, Sink


async def peer_for(mode='authority'):
    services = MemoryServices()
    peer = GatewayConnection(Sink(), Principal('alice'), services, MANIFEST, mode)
    scope = await peer.context()
    await scope.sessions.create_session('s', 'cli', profile_name='server-profile')
    services.sessions['alice', 's'].update(ended_at=123, end_reason='completed',
        rows=[{'id': 1, 'role': 'user', 'content': 'stored question'}])
    return services, peer, scope


async def resume(peer, **params):
    await peer.process({'id': 1, 'method': 'session.resume', 'params': {'session_id': 's', **params}})
    return peer.ws.frames[-1]


@pytest.mark.parametrize('mode', ['legacy', 'authority'])
@pytest.mark.parametrize('omit', [False, True])
@pytest.mark.parametrize('inline', [False, True])
def test_deferred_snapshot_and_readonly_mount(mode, omit, inline):
    async def scenario():
        services, peer, scope = await peer_for(mode)
        before = deepcopy(services.sessions)
        reply = (await resume(peer, source='desktop', defer_history=True,
            omit_messages=omit, cols=96, inline_images=inline, profile='foreign'))['result']
        assert reply['messages'] == []
        assert reply['message_count'] == 1
        assert reply['hydrating'] is False
        assert reply['running'] is False
        assert reply['status'] == 'idle'
        assert reply['info']['profile_name'] == 'server-profile'
        assert services.sessions == before
        assert 'messages_omitted' not in reply
        if mode == 'authority':
            assert reply['last_sequence'] == 0
            assert {'authority_epoch', 'replay_epoch', 'subscription_id', 'pending', 'prompts'} <= reply.keys()
        else:
            assert reply['open_requests'] == []
        assert await SessionRPC(scope, peer).history('s') == [{'role': 'user', 'text': 'stored question', 'row_id': 1}]
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['legacy', 'authority'])
@pytest.mark.parametrize('omit', [False, True])
def test_false_or_absent_preserves_snapshot(mode, omit):
    async def scenario():
        _, peer, _ = await peer_for(mode)
        absent = (await resume(peer, omit_messages=omit))['result']
        explicit = (await resume(peer, defer_history=False, omit_messages=omit))['result']
        for value in (absent, explicit):
            value.pop('subscription_id', None)
            assert 'hydrating' not in value
            assert 'status' not in value
        assert absent == explicit
        assert absent['messages'] == ([] if omit else [{'role': 'user', 'text': 'stored question', 'row_id': 1}])
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['legacy', 'authority'])
@pytest.mark.parametrize('key,value', [('eager_build', True), ('lazy', True), ('workspace', '/tmp'),
    ('editor', {'name': 'editor'}), ('api_key', 'untrusted'), ('close_on_disconnect', True),
    ('display_projection', True), ('title', 'title'), ('latest', 'cli')])
def test_unsupported_resume_effects_still_fail_closed(mode, key, value):
    async def scenario():
        _, peer, _ = await peer_for(mode)
        error = (await resume(peer, source='desktop', defer_history=True, **{key: value}))['error']
        assert error['code'] == -32005
        assert error['message'] == 'handler_not_connected'
        assert error['data']['feature'] == 'session.resume'
        assert not peer.subscriptions
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['legacy', 'authority'])
def test_foreign_and_missing_indistinguishable(mode):
    async def scenario():
        services, _, _ = await peer_for(mode)
        bob = GatewayConnection(Sink(), Principal('bob'), services, MANIFEST, mode)
        foreign = await resume(bob, defer_history=True, source='desktop', profile='alice')
        missing = await resume(bob, session_id='missing', defer_history=True, source='desktop')
        assert foreign['error'] == missing['error']
        assert foreign['error']['message'] == 'not_found'
        assert not bob.subscriptions
    asyncio.run(scenario())


@pytest.mark.parametrize('source', ['desktop', 'telegram', 'cli', '', None])
def test_source_is_surface_hint_not_persisted_identity(source):
    async def scenario():
        services, peer, _ = await peer_for()
        assert 'result' in await resume(peer, source=source, defer_history=True)
        assert services.sessions['alice', 's']['source'] == 'cli'
    asyncio.run(scenario())


def test_replay_boundary_precedes_history_and_live_state_is_preserved(monkeypatch):
    async def scenario():
        services, peer, scope = await peer_for()
        services.runs.append({'principal_id': 'alice', 'session_id': 's', 'run_id': 'r',
                             'active': True, 'query': 'live question'})
        services.sessions['alice', 's']['rows'] = []
        original = SessionRPC.history
        async def history(self, sid, **kwargs):
            scope.execution.events.append(sid)
            return await original(self, sid, **kwargs)
        monkeypatch.setattr(SessionRPC, 'history', history)
        reply = (await resume(peer, defer_history=True))['result']
        assert reply['last_sequence'] == 0
        assert reply['running'] is True
        assert reply['status'] == 'working'
        assert reply['message_count'] == 1
        await peer.pump_once()
        assert peer.ws.frames[-1]['method'] == 'event'
        assert peer.ws.frames[-1]['params']['seq'] == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['legacy', 'authority'])
@pytest.mark.parametrize('params', [{'defer_history': 'true'}, {'defer_history': None}, {'source': 42}])
def test_invalid_contract_types_do_not_subscribe(mode, params):
    async def scenario():
        _, peer, _ = await peer_for(mode)
        error = (await resume(peer, **params))['error']
        assert error['code'] == (4000 if mode == 'legacy' else 4001)
        assert not peer.subscriptions
    asyncio.run(scenario())


def test_deferred_retry_after_history_failure_has_no_stale_subscription(monkeypatch):
    async def scenario():
        _, peer, _ = await peer_for()
        original = SessionRPC.history
        async def broken(self, sid):
            raise RuntimeError('transient read failure')
        monkeypatch.setattr(SessionRPC, 'history', broken)
        assert (await resume(peer, defer_history=True))['error']['code'] == -32603
        assert not peer.subscriptions
        monkeypatch.setattr(SessionRPC, 'history', original)
        for _ in range(2):
            reply = (await resume(peer, defer_history=True))['result']
            assert reply['message_count'] == 1
            assert reply['messages'] == []
            assert len(peer.subscriptions) == 1
    asyncio.run(scenario())


def test_manifest_modes_and_console_projection_share_source():
    import hashlib
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    source = root / 'harness/gateway/manifest.json'
    row = next(row for row in MANIFEST['rpc'] if row['name'] == 'session.resume')
    projected = json.loads((root / 'harness_console/src/platform/yield-manifest.generated.json').read_text())
    assert projected['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    for mode, params in [('legacy', 'SessionResumeParams'), ('authority', 'CanonicalResumeParams')]:
        assert row['modes'][mode]['classification'] == 'implemented'
        assert row['modes'][mode]['params'] == params
        assert projected['rpc']['session.resume']['modes'][mode]['classification'] == 'implemented'


@pytest.mark.parametrize('mode', ['legacy', 'authority'])
def test_deferred_pending_prompt_reports_waiting(mode):
    async def scenario():
        services, peer, _ = await peer_for(mode)
        services.prompts.append({'principal_id': 'alice', 'session_id': 's', 'prompt_id': 'p',
            'status': 'pending', 'kind': 'clarify', 'execution_generation': 0})
        reply = (await resume(peer, defer_history=True))['result']
        assert reply['status'] == 'waiting'
        assert reply['hydrating'] is False
    asyncio.run(scenario())

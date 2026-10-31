"""Golden frames and auth-before-policy behavior; no transport dependencies."""
import copy
import json
from pathlib import Path

import pytest


@pytest.fixture
def manifest():
    return json.loads((Path(__file__).resolve().parents[1] / 'gateway/manifest.json').read_text())


@pytest.mark.parametrize('request_id', ['request-17', 42, None])
def test_rpc_unsupported_frame(manifest, request_id):
    from harness.gateway.unsupported import rpc_rejection
    assert rpc_rejection(manifest, 'shell.exec', request_id) == {
        'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32004, 'message': 'unsupported',
        'data': {'reason': 'disabled_by_policy', 'feature': 'shell.exec', 'upstream': '5894e750'}}}


def test_unknown_stub_and_mode_boundary(manifest):
    from harness.gateway.unsupported import rpc_rejection
    assert rpc_rejection(manifest, 'invented.rpc', 1) == {
        'jsonrpc': '2.0', 'id': 1, 'error': {'code': -32601, 'message': 'Method not found'}}
    assert rpc_rejection(manifest, 'mcp.servers.add', 1)['error']['data']['reason'] == 'deferred_milestone_3'
    assert rpc_rejection(manifest, 'prompt.receipt', 1, mode='legacy')['error']['code'] == -32601
    assert rpc_rejection(manifest, 'prompt.receipt', 1, mode='authority') is None
    with pytest.raises(ValueError):
        rpc_rejection(manifest, 'ping', 1, mode='invented')


def test_http_unknown_and_policy(manifest):
    from harness.gateway.unsupported import http_rejection
    assert http_rejection(manifest, 'GET', '/api/media') == (501, {'error': {
        'code': 'unsupported', 'reason': 'disabled_by_policy', 'feature': '/api/media', 'upstream': '5894e750'}})
    assert http_rejection(manifest, 'GET', '/invented')[0] == 404
    assert http_rejection(manifest, 'GET', '/api/sessions') is None
    assert http_rejection(manifest, 'GET', '/api/mcp/servers')[1]['error']['reason'] == 'deferred_milestone_3'


@pytest.mark.parametrize('name', ['shell.exec', 'invented.rpc', 'ping'])
@pytest.mark.parametrize('code', [401, 403])
def test_auth_and_ownership_have_priority(manifest, name, code):
    from harness.gateway.unsupported import http_rejection, rpc_rejection
    rpc_error = {'code': -32001, 'message': 'unauthorized' if code == 401 else 'forbidden'}
    assert rpc_rejection(manifest, name, 7, auth_error=rpc_error)['error'] == rpc_error
    http_error = (code, {'error': {'code': 'unauthorized' if code == 401 else 'forbidden'}})
    for path in ['/api/media', '/invented', '/api/sessions']:
        assert http_rejection(manifest, 'GET', path, auth_error=http_error) == http_error


def test_server_request_declines_without_4404(manifest):
    from harness.gateway.unsupported import server_request_rejection
    for row in manifest['server_requests']:
        frame = server_request_rejection(manifest, row['name'], 'server-1')
        if row['classification'] == 'implemented':
            assert frame is None
        else:
            assert frame == {'jsonrpc': '2.0', 'id': 'server-1',
                             'error': {'code': -32601, 'message': 'Method not found'}}


def test_capabilities_are_separate_and_honest(manifest):
    from harness.gateway.capabilities import RPC_METHOD, yield_capabilities
    original = copy.deepcopy(manifest)
    result = yield_capabilities(manifest, mode='authority')
    assert RPC_METHOD == 'yield.capabilities'
    assert result['schema_version'] == 1 and result['upstream'] == '5894e750'
    assert result['rpc_mode'] == 'authority'
    assert result['runtime_connected'] is False
    assert 'per_session_exclusive_submit' not in result
    assert 'yield.capabilities' not in {r['name'] for r in manifest['rpc']}
    assert 'gateway.capabilities' not in result['methods']  # legacy-only, no fallback
    assert result['methods']['prompt.submit']['status'] == 'unsupported'
    assert result['methods']['prompt.submit']['reason'] == 'handler_not_connected'
    assert result['methods']['shell.exec']['status'] == 'unsupported'
    assert len(set().union(*(set(v) for v in result['classification'].values()))) == 48
    assert len(result['events']) == 86 and len(result['server_requests']) == 16
    assert len(result['routes']) == 290
    result['events'][0]['name'] = 'changed'
    assert manifest == original


def test_every_rpc_mode_uses_its_policy(manifest):
    from harness.gateway.unsupported import rpc_rejection
    for row in manifest['rpc']:
        for mode, entry in row['modes'].items():
            frame = rpc_rejection(manifest, row['name'], 'fixture', mode=mode)
            if entry['classification'] == 'implemented':
                assert frame is None
            else:
                assert frame['error']['code'] == -32004
                assert frame['error']['data']['reason'] == entry['reason']
                assert frame['error']['data']['feature'] == row['name']


def test_auth_frames_are_detached_and_ws_is_not_http(manifest):
    from harness.gateway.unsupported import http_rejection, rpc_rejection
    auth = {'code': -32001, 'message': 'forbidden', 'data': {'scope': 'session'}}
    frame = rpc_rejection(manifest, 'shell.exec', 1, auth_error=auth)
    frame['error']['data']['scope'] = 'changed'
    assert auth['data']['scope'] == 'session'
    with pytest.raises(ValueError, match='WebSocket'):
        http_rejection(manifest, 'WEBSOCKET', '/api/console')


@pytest.mark.parametrize('name,mode,reason', [
    ('worker.adopt', 'authority', 'external_worker_control_not_needed'),
    ('worker.persist', 'authority', 'external_worker_control_not_needed'),
    ('worker.register', 'authority', 'external_worker_control_not_needed'),
    ('session.save', 'legacy', 'local_file_save_disabled'),
    ('session.export', 'authority', 'server_download_deferred'),
    ('session.delete', 'legacy', 'deferred_milestone'),
])
def test_revised_rpc_rejection_reasons(manifest, name, mode, reason):
    from harness.gateway.unsupported import rpc_rejection
    assert rpc_rejection(manifest, name, 'policy-1', mode=mode) == {
        'jsonrpc': '2.0', 'id': 'policy-1',
        'error': {'code': -32004, 'message': 'unsupported',
                  'data': {'reason': reason, 'feature': name, 'upstream': '5894e750'}}}


@pytest.mark.parametrize('method,path,reason', [
    ('POST', '/api/sessions/import', 'cross_user_session_management'),
    ('POST', '/api/sessions/owner-backfill', 'cross_user_session_management'),
    ('POST', '/api/sessions/prune', 'cross_user_session_management'),
    ('POST', '/api/sessions/bulk-delete', 'cross_user_session_management'),
    ('POST', '/api/memory/reset', 'global_memory_reset_before_user_isolation'),
    ('GET', '/api/sessions/{session_id}/export', 'server_download_deferred'),
    ('DELETE', '/api/sessions/{session_id}', 'deferred_milestone'),
])
def test_revised_http_rejection_reasons(manifest, method, path, reason):
    from harness.gateway.unsupported import http_rejection
    assert http_rejection(manifest, method, path) == (501, {'error': {
        'code': 'unsupported', 'reason': reason, 'feature': path, 'upstream': '5894e750'}})


def test_host_identity_policy_still_permits_dispatch(manifest):
    from harness.gateway.unsupported import http_rejection
    assert http_rejection(manifest, 'GET', '/api/host/identity') is None

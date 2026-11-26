"""Offline PR-1 inventory checks; never import or execute upstream Python."""
import json
from collections import Counter
from pathlib import Path
import sys

import pytest

from harness.tests.local_test_resources import hermes_checkout

ROOT = Path(__file__).resolve().parents[1] / 'gateway'


@pytest.fixture
def manifest():
    return json.loads((ROOT / 'manifest.json').read_text())


@pytest.fixture
def upstream():
    path = hermes_checkout()
    if not path.is_dir():
        if sys.platform == 'darwin':
            pytest.fail(f'Install the pinned Hermes checkout at {path} or set HERMES_CONTRACT_ROOT')
        pytest.skip('read-only Hermes checkout unavailable')
    return path


def test_inventory_exists():
    assert (ROOT / 'manifest.json').is_file()


def test_complete_inventory(manifest):
    for section, count in [('rpc', 283), ('events', 86), ('server_requests', 16),
                           ('routes', 292), ('external_routes', 13)]:
        rows = manifest[section]
        assert len(rows) == count
        assert len({r['name'] for r in rows}) == count
        assert all(r['classification'] in {'implemented', 'stub', 'unsupported'} for r in rows)
        assert all(r['reason'] and r['feature'] and r['source'] for r in rows)
    assert Counter(m for r in manifest['rpc'] for m in r['modes']) == {'legacy': 254, 'authority': 52}
    assert manifest['runtime_connected'] is False


def test_regeneration_matches_every_field(manifest, upstream):
    from harness.gateway.tools.extract_contract import extract_contract
    assert extract_contract(upstream) == manifest


def test_overlap_contracts(manifest):
    overlaps = {r['name']: r for r in manifest['rpc'] if len(r['modes']) == 2}
    fixtures = json.loads((Path(__file__).parent / 'gateway_contract_overlap.json').read_text())
    assert len(overlaps) == len(fixtures) == 23
    assert set(overlaps) == set(fixtures)
    for name, expected in fixtures.items():
        for mode in ('legacy', 'authority'):
            actual = overlaps[name]['modes'][mode]
            assert {key: actual[key] for key in expected[mode]} == expected[mode]
    assert overlaps['prompt.submit']['modes']['legacy']['params'] != overlaps['prompt.submit']['modes']['authority']['params']


def test_server_requests_and_websockets(manifest):
    assert Counter(r['classification'] for r in manifest['server_requests']) == {
        'implemented': 2, 'stub': 2, 'unsupported': 12}
    assert {r['name'] for r in manifest['server_requests'] if r['classification'] == 'implemented'} == {'approval', 'clarify'}
    ws = [r for r in manifest['routes'] if r['method'] == 'WEBSOCKET']
    assert len(ws) == 8
    assert {r['path'] for r in ws if r['classification'] == 'implemented'} == {'/api/ws'}
    assert all(r['classification'] == 'unsupported' for r in ws if r['path'] != '/api/ws')


def test_publication_constraints(manifest):
    events = {r['name']: r for r in manifest['events']}
    assert events['message.delta']['publication'] == 'internal_only'
    assert events['message.interim']['publication'] == 'internal_only'
    assert events['message.complete']['publication'] == 'verified_final_only'
    assert events['reasoning.delta']['publication'] == 'status_only'
    assert events['session.tools_changed']['classification'] == 'stub'
    assert events['subagent.start']['classification'] == 'unsupported'


def test_error_code_has_no_upstream_collision(upstream):
    from harness.gateway.tools.extract_contract import error_code_collisions
    assert error_code_collisions(upstream) == []


def test_rules_partition_source_without_unused_entries(manifest):
    from harness.gateway.policy import expand
    from harness.gateway.rpc_rules import RPC_RULES
    for mode in ('legacy', 'authority'):
        expected = {r['name'] for r in manifest['rpc'] if mode in r['modes']}
        classified = [name for rule_mode, patterns, *_ in RPC_RULES if rule_mode == mode
                      for pattern in patterns for name in expand(pattern)]
        assert len(classified) == len(set(classified))
        assert set(classified) == expected


def test_rest_exceptions_and_auth_adapter_targets(manifest):
    routes = {r['name']: r for r in manifest['routes'] + manifest['external_routes']}
    for name in ['GET /api/config/raw', 'POST /api/mcp/catalog/install',
                 'POST /api/skills/hub/install', 'POST /auth/native/token',
                 'GET /api/profiles/{name}/setup-command']:
        assert routes[name]['classification'] == 'unsupported'
    for name in ['GET /api/auth/me', 'POST /api/auth/ws-ticket', 'POST /auth/logout']:
        assert routes[name]['classification'] == 'implemented'
    assert routes['PUT /api/config']['classification'] == 'stub'
    assert routes['GET /api/memory']['reason'] == 'deferred_milestone_3'


def test_upstream_revision_is_pinned(manifest):
    assert manifest['upstream_commit'] == '5894e750c3dba980d43b25aa29e3958149ba2c61'


def test_collision_scan_detects_alternate_integer_spellings(tmp_path):
    from harness.gateway.tools.extract_contract import error_code_collisions
    (tmp_path / 'gateway').mkdir()
    (tmp_path / 'tui_gateway').mkdir()
    (tmp_path / 'gateway/codes.py').write_text('ERROR = -32_004\nOTHER = -0x7d04\n')
    assert error_code_collisions(tmp_path) == ['gateway/codes.py:1', 'gateway/codes.py:2']


@pytest.mark.parametrize('name', ['worker.adopt', 'worker.persist', 'worker.register'])
def test_external_worker_control_is_unsupported(manifest, name):
    row = next(r for r in manifest['rpc'] if r['name'] == name)
    for entry in (row, row['modes']['authority']):
        assert entry['classification'] == 'unsupported'
        assert entry['reason'] == 'external_worker_control_not_needed'
        assert '외부 worker' in entry['detail'] and '사용자 불필요' in entry['detail']


@pytest.mark.parametrize('path', [
    '/api/sessions/import', '/api/sessions/owner-backfill',
    '/api/sessions/prune', '/api/sessions/bulk-delete',
])
def test_operator_session_routes_are_unsupported(manifest, path):
    row = next(r for r in manifest['routes'] if r['name'] == f'POST {path}')
    assert row['classification'] == 'unsupported'
    assert row['reason'] == 'cross_user_session_management'
    assert '다른 사용자 세션' in row['detail']


def test_memory_reset_only_is_denied(manifest):
    rows = [r for r in manifest['routes'] if r['path'].startswith('/api/memory')]
    assert rows
    for row in rows:
        if row['name'] == 'POST /api/memory/reset':
            assert row['classification'] == 'unsupported'
            assert row['reason'] == 'global_memory_reset_before_user_isolation'
            assert '사용자별 메모리 격리' in row['detail']
        else:
            assert row['classification'] == 'stub'
            assert row['reason'] == 'deferred_milestone_3'


def test_individual_delete_and_queue_policies_are_preserved(manifest):
    rpc = {r['name']: r for r in manifest['rpc']}
    for name in ['prompt.steer_queued', 'prompt.replace_queued']:
        assert rpc[name]['classification'] == 'stub'
    assert rpc['session.delete']['modes']['legacy']['classification'] == 'stub'
    assert rpc['session.delete']['modes']['authority']['classification'] == 'implemented'
    for mode in ('legacy', 'authority'):
        for name in ('session.steer', 'config.get', 'config.set'):
            assert rpc[name]['modes'][mode]['classification'] == 'implemented'
        assert rpc['session.redirect']['modes'][mode]['classification'] == 'unsupported'
    assert rpc['prompt.submit']['modes']['authority']['denied_actions'] == ['redirect', 'queue_edit']
    routes = {r['name']: r for r in manifest['routes']}
    assert routes['DELETE /api/sessions/{session_id}']['classification'] == 'stub'


def test_local_save_and_server_download_are_distinguished(manifest):
    rpc = {r['name']: r for r in manifest['rpc']}
    routes = {r['name']: r for r in manifest['routes']}
    for row, status, reason, detail in [
        (rpc['session.save'], 'unsupported', 'local_file_save_disabled', '로컬 파일 저장'),
        (rpc['session.export'], 'stub', 'server_download_deferred', '서버 다운로드'),
        (routes['GET /api/sessions/{session_id}/export'], 'stub',
         'server_download_deferred', '서버 다운로드'),
    ]:
        assert row['classification'] == status
        assert row['reason'] == reason
        assert detail in row['detail']
        assert row['feature'] == (row['path'] if 'path' in row else row['name'])


def test_host_identity_retains_classification_without_confirmed_host_fields(manifest, upstream):
    import ast
    tree = ast.parse((upstream / 'hermes_cli/web_routers/status.py').read_text())
    handler = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                   and n.name == 'get_host_identity')
    response = next(n.value for n in ast.walk(handler) if isinstance(n, ast.Return))
    assert {k.value for k in response.keys} == {'ok', 'protocolVersion', 'pid', 'role', 'servesSpa'}
    row = next(r for r in manifest['routes'] if r['name'] == 'GET /api/host/identity')
    assert row['classification'] == 'implemented'


def test_http_mcp_denies_stdio_and_sse_without_reclassification(manifest):
    rows = [r for r in manifest['routes'] if r['path'].startswith('/api/mcp/')]
    assert rows
    for row in rows:
        assert row['denied_actions'] == ['stdio', 'SSE']
        assert row['classification'] == ('unsupported' if row['path'] == '/api/mcp/catalog/install' else 'stub')


def test_classification_counts(manifest):
    sections = {
        'rpc': (manifest['rpc'], (43, 54, 186)),
        'events': (manifest['events'], (35, 9, 42)),
        'server_requests': (manifest['server_requests'], (2, 2, 12)),
        'http': ([r for r in manifest['routes'] if r['method'] != 'WEBSOCKET'], (23, 71, 190)),
        'ws': ([r for r in manifest['routes'] if r['method'] == 'WEBSOCKET'], (1, 0, 7)),
        'external': (manifest['external_routes'], (3, 0, 10)),
    }
    for section, (rows, counts) in sections.items():
        actual = Counter(r['classification'] for r in rows)
        assert tuple(actual[status] for status in ('implemented', 'stub', 'unsupported')) == counts, section
        assert len(rows) == sum(counts)

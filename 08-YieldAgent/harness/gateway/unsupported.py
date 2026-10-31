"""Pure policy rejection frames; no authentication or handler execution.

Order: caller-supplied auth/ownership error > mode/name lookup > policy refusal.
`None` means the policy lane permits dispatch, NOT success or an installed handler.
PR-2 must authenticate first and PR-3+ must supply the actual handler. Request IDs
are echoed; transport code must not call this for JSON-RPC notifications.
"""
from copy import deepcopy

from .policy import UPSTREAM


def validate_mode(mode):
    if mode not in {'legacy', 'authority'}:
        raise ValueError(f'Unknown RPC mode: {mode}')


def _frame(request_id, error):
    return {'jsonrpc': '2.0', 'id': request_id, 'error': deepcopy(error)}


def _not_found(request_id):
    return _frame(request_id, {'code': -32601, 'message': 'Method not found'})


def rpc_rejection(manifest, name, request_id, *, mode='legacy', auth_error=None):
    if auth_error is not None:
        return _frame(request_id, auth_error)
    validate_mode(mode)
    entry = next((row['modes'][mode] for row in manifest['rpc']
                  if row['name'] == name and mode in row['modes']), None)
    if entry is None:
        return _not_found(request_id)
    if entry['classification'] == 'implemented':
        return None
    return _frame(request_id, {'code': -32004, 'message': 'unsupported', 'data': {
        'reason': entry['reason'], 'feature': entry['feature'], 'upstream': UPSTREAM}})


def http_rejection(manifest, method, path, *, auth_error=None):
    """Accept an exact route-template path, not a URL/path resolver. WS is separate."""
    if auth_error is not None:
        return deepcopy(auth_error)
    if method.upper() == 'WEBSOCKET':
        raise ValueError('HTTP 501 is not a WebSocket upgrade/close policy')
    entry = next((row for row in manifest['routes'] + manifest['external_routes']
                  if row['method'] == method.upper() and row['path'] == path), None)
    if entry is None:
        return 404, {'error': {'code': 'not_found'}}
    if entry['classification'] == 'implemented':
        return None
    return 501, {'error': {'code': 'unsupported', 'reason': entry['reason'],
                           'feature': entry['feature'], 'upstream': UPSTREAM}}


def server_request_rejection(manifest, name, request_id):
    """Client decline per liveness.py: -32601, never the window-specific 4404.

    approval/clarify are handled by future pending-control adapters. The remaining
    requests use the documented error alternative, not fabricated typed successes.
    """
    entry = next((row for row in manifest['server_requests'] if row['name'] == name), None)
    if entry is not None and entry['classification'] == 'implemented':
        return None
    return _not_found(request_id)

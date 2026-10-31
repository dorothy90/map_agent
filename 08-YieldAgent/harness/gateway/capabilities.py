"""The separate yield.capabilities contract (never gateway.capabilities).

M1 target classifications are retained, but PR-1 advertises no connected upstream
handlers. Later integration must deliberately update runtime availability after
contract/auth tests; classification alone is never proof of support.
"""
from copy import deepcopy

from .policy import STATUSES
from .unsupported import validate_mode

RPC_METHOD = 'yield.capabilities'


def yield_capabilities(manifest, *, mode='legacy'):
    """Return a detached, JSON-serializable PR-1 response without I/O."""
    validate_mode(mode)
    entries = {row['name']: row['modes'][mode] for row in manifest['rpc'] if mode in row['modes']}
    return {
        'schema_version': manifest['schema_version'], 'upstream': manifest['upstream'],
        'rpc_mode': mode, 'classification_scope': manifest['classification_scope'],
        'runtime_connected': False,
        'classification': {status: sorted(name for name, entry in entries.items()
                                         if entry['classification'] == status) for status in STATUSES},
        'methods': {name: {'status': 'unsupported', 'classification': entry['classification'],
                           'reason': 'handler_not_connected' if entry['classification'] == 'implemented' else entry['reason'],
                           'feature': entry['feature'],
                           'denied_actions': list(entry.get('denied_actions', []))}
                    for name, entry in entries.items()},
        **{section: deepcopy(manifest[section]) for section in
           ('events', 'server_requests', 'routes', 'external_routes')},
        'features': deepcopy(manifest['boundaries']),
    }

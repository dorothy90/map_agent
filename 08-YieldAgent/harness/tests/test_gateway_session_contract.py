"""Offline contract checks for the principal-bound gateway session store."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_no_sqlite_or_original_storage_imports():
    import harness.gateway.session_store as storage
    source = Path(storage.__file__).read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or '']
        else:
            continue
        assert not any(name.startswith(('sqlite3', 'hermes_state')) for name in names)
    assert 'state.db' not in source


def test_paging_and_message_display_pure_logic():
    from harness.gateway.session_store import _page, _display_rows
    rows = [{'id': i, 'role': 'user', 'content': str(i), 'timestamp': i,
             'active': 1, 'compacted': 0} for i in range(5)]
    assert [r['id'] for r in _page(rows, 2, 1, True)] == [2, 3]
    assert _page(rows, 0, 0, False) == []
    assert _page(rows, -1, -3, False) == rows
    assert _page(rows, None, 99, False) == []
    copied = {**rows[1], 'id': 8}
    assert [r['id'] for r in _display_rows([*rows, copied])] == [0, 8, 2, 3, 4]


def test_principal_required():
    from harness.gateway.session_store import MongoSessionStore
    for bad in (None, '', ' ', 1):
        with pytest.raises(ValueError, match='principal_id'):
            MongoSessionStore(None, bad)


def test_summary_carrier_and_removed_uid_display():
    from harness.gateway.session_store import _display_rows
    original = {'id': 1, 'role': 'user', 'content': 'ask', 'timestamp': 1,
                'active': 0, 'compacted': 1, 'message_uid': 'u'}
    other = {**original, 'id': 2, 'content': 'next', 'message_uid': 'v', 'active': 1, 'compacted': 0}
    carrier = {**original, 'id': 3, 'content': 'summary', 'active': 1, '_compressed_summary': True}
    assert [r['id'] for r in _display_rows([original, other, carrier])] == [3, 2]
    removed = {**carrier, 'id': 4, 'active': 0, 'compacted': 0}
    assert [r['id'] for r in _display_rows([original, other, carrier, removed], drop_removed=True)] == [2]


def test_stable_tool_call_identity_after_argument_rewrite():
    from harness.gateway.session_store import _display_rows
    old = {'id': 1, 'role': 'assistant', 'content': None, 'timestamp': 1, 'active': 0,
           'compacted': 1, 'tool_calls': [{'id': 'call-1', 'function': {'arguments': 'large'}}]}
    new = {**old, 'id': 3, 'active': 1, 'tool_calls': [{'id': 'call-1', 'function': {'arguments': 'pruned'}}]}
    assert [r['id'] for r in _display_rows([old, new])] == [3]


def test_storage_public_scope_and_default_shapes():
    from harness.gateway.session_store import MongoSessionStore, _public_message, _public_session
    store = MongoSessionStore(SimpleNamespace(db=SimpleNamespace(
        gateway_sessions=None, gateway_messages=None, gateway_message_counters=None)), 'alice')
    assert store._scope(principal_id='mallory', session_id='s') == {'principal_id': 'alice', 'session_id': 's'}
    with pytest.raises(AttributeError):
        store.principal_id = 'mallory'
    row = _public_session({'id': 's', 'principal_id': 'alice', '_id': 'private', 'next_seq': 4,
                           'model_config': '{"_branched_from": " root "}'})
    assert row['message_count'] == 0
    assert row['_branched_from'] == 'root'
    assert not {'principal_id', '_id', 'next_seq'} & row.keys()
    assert _public_session({'deleted': True}) is None
    message = _public_message({'id': 1, 'content': ['parts'], '_compressed_summary': 1})
    assert message['_compressed_summary'] is True
    assert message['content'] == ['parts']
    assert 'display_identity' not in message


def test_compaction_boundaries_and_inherited_fork_markers():
    from harness.gateway.session_store import MongoSessionStore
    parent = {'id': 'p', 'end_reason': 'compression'}
    child = {'parent_session_id': 'p', 'source': 'cli', 'model_config': '{"_branched_from":"older"}'}
    # Upstream's ancestor gate binds markers to the parent; forward-chain SQL
    # uses presence. Preserve this pinned distinction instead of normalizing it.
    assert not MongoSessionStore._fork(child, parent)
    assert MongoSessionStore._fork(child, parent, presence=True)
    child['model_config'] = '{"_reset_from":"p"}'
    assert MongoSessionStore._fork(child, parent)
    child['model_config'] = None
    child['session_key'] = 'key'
    assert MongoSessionStore._fork(child, {'end_reason': 'session_reset', 'session_key': 'key'})


def test_profile_inheritance_respects_gateway_key_namespace():
    from harness.gateway.session_store import _same_profile_namespace
    assert _same_profile_namespace(None, 'agent:other:chat')
    assert _same_profile_namespace('agent:main:chat1', 'agent:main:chat2')
    assert not _same_profile_namespace('agent:main:chat', 'agent:other:chat')


def test_listing_chain_includes_nonpreferred_continuation_siblings():
    from harness.gateway.session_store import _listing_chain
    rows = [
        {'id': 'root', 'end_reason': 'compression'},
        {'id': 'preferred', 'parent_session_id': 'root', 'end_reason': 'compression'},
        {'id': 'sibling', 'parent_session_id': 'root', 'end_reason': 'shutdown'},
        {'id': 'tip', 'parent_session_id': 'preferred', 'end_reason': None},
        {'id': 'branch', 'parent_session_id': 'root', 'model_config': '{"_branched_from":"root"}'},
    ]
    assert set(_listing_chain('root', {r['id']: r for r in rows})) == {'root', 'preferred', 'sibling', 'tip'}

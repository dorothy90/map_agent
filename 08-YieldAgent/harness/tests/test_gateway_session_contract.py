"""Offline contract checks; parse original definitions without importing its DB runtime."""
import ast
import asyncio
import inspect
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from harness.tests.local_test_resources import hermes_checkout

# Optional override for a read-only checkout of pinned Hermes 5894e750.
ORIGIN = Path(os.environ.get('HERMES_SESSION_CONTRACT_SOURCE') or hermes_checkout())


def original_classes():
    assert (ORIGIN / 'hermes_state.py').is_file(), 'Set HERMES_SESSION_CONTRACT_SOURCE to the Hermes 5894e750 checkout'
    classes = {}
    for path in ORIGIN.glob('hermes_state*.py'):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.ClassDef):
                classes[node.name] = node
    def hierarchy(name):
        node = classes[name]
        return [node, *(ancestor for base in node.bases if isinstance(base, ast.Name) and base.id in classes
                        for ancestor in hierarchy(base.id))]
    return hierarchy('SessionDB')


def original_methods():
    methods = {}
    for cls in reversed(original_classes()):
        for node in cls.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith('_'):
                methods[node.name] = node
    return methods


def source_signature(node):
    # Execute only a signature with a pass body, never Hermes imports or constructors.
    node = ast.FunctionDef(name=node.name, args=node.args, body=[ast.Pass()],
                           decorator_list=[], returns=node.returns, type_comment=None)
    mod = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), node], type_ignores=[])
    scope = {}
    exec(compile(ast.fix_missing_locations(mod), '<original SessionDB signature>', 'exec'), scope)
    return inspect.signature(scope[node.name])


def parameter_contract(sig):
    return [(p.name, p.kind, p.default) for p in sig.parameters.values()]


def test_original_public_signatures_and_explicit_unsupported_inventory():
    from harness.gateway.session_db import SessionDB, IMPLEMENTED_METHODS, UNSUPPORTED_METHODS
    originals = original_methods()
    assert set(IMPLEMENTED_METHODS).isdisjoint(UNSUPPORTED_METHODS)
    assert set(IMPLEMENTED_METHODS) | set(UNSUPPORTED_METHODS) == set(originals)
    for name in IMPLEMENTED_METHODS:
        assert parameter_contract(inspect.signature(getattr(SessionDB, name))) == parameter_contract(source_signature(originals[name])), name
        assert not inspect.iscoroutinefunction(getattr(SessionDB, name))
    db = SessionDB.__new__(SessionDB)
    for name in UNSUPPORTED_METHODS:
        with pytest.raises(NotImplementedError, match=name):
            getattr(db, name)()


def test_no_sqlite_or_original_storage_imports():
    import harness.gateway.session_db as adapter
    import harness.gateway.session_store as storage
    for module in (adapter, storage):
        tree = ast.parse(Path(module.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or '']
            else:
                continue
            assert not any(name.startswith(('sqlite3', 'hermes_state')) for name in names)
        assert 'state.db' not in Path(module.__file__).read_text()


def test_sync_boundary_rejects_event_loop_and_offloads_successfully():
    from harness.gateway.session_db import SessionDB

    async def scenario():
        loop = asyncio.get_running_loop()
        async def get_session(session_id):
            assert asyncio.get_running_loop() is loop
            return {'id': session_id}
        core = SimpleNamespace(get_session=get_session)
        db = SessionDB(core, loop=loop)
        with pytest.raises(RuntimeError, match='to_thread'):
            db.get_session('s')
        assert await asyncio.to_thread(db.get_session, 's') == {'id': 's'}
        db.close()
        with pytest.raises(RuntimeError, match='closed'):
            await asyncio.to_thread(db.get_session, 's')
    asyncio.run(scenario())


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


def test_contract_inventory_includes_nested_message_mixins():
    from harness.gateway.session_db import UNSUPPORTED_METHODS
    assert {'append_completion_note', 'append_delegation_delivery', 'get_latest_todo_result'} <= set(UNSUPPORTED_METHODS)


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

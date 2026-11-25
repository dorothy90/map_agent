from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from harness.web_kanban import WEB_KANBAN_TOOLS, install_web_kanban, read_wafer_item


@pytest.fixture
def boards(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'engine'))
    monkeypatch.setenv('HERMES_DISABLE_LAZY_INSTALLS', '1')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    from tools.registry import ToolRegistry
    events = []

    def board(employee='Alice', session='one', database='app', call='call-1', validate=lambda *args: None):
        registry = ToolRegistry()
        run = dict(principal_id=employee, session_id=session, run_id=session)
        install_web_kanban(registry, tmp_path / employee, database, run, invocation_id=lambda: call, emit=events.append,
                           validate_members=validate)
        return registry

    return board, events


def invoke(registry, tool, **args):
    return json.loads(registry.dispatch('kanban_' + tool, args))


def group(lot_id, *wf_ids):
    return dict(label='Ring defect', lot_id=lot_id, wf_ids=list(wf_ids))


def test_two_chats_one_native_card_exact_maps_and_authenticated_snapshot(boards, tmp_path):
    board, events = boards
    first = board()
    assert {s['function']['name'] for s in first.get_definitions(set(WEB_KANBAN_TOOLS))} == set(WEB_KANBAN_TOOLS)
    created = invoke(first, 'create', title='Ring defect', body='Sample maps', oper='test A', lot_cd='product X',
                     wafer_groups=[group('A', 1, 3), group('B', 3, 5)])
    tid = created['task_id']
    second = board(session='two')
    assert invoke(second, 'list', title='Ring defect')['tasks'][0]['id'] == tid
    changes = dict(task_id=tid, body='New samples', wafer_groups=[group('B', 3, 8), group('C', 8)])
    updated = invoke(second, 'comment', **changes)
    assert invoke(second, 'comment', **changes) == updated
    got = updated['wafer_item']
    assert got['wafer_count'] == 6
    assert got['wafer_groups'] == [group('A', 1, 3), group('B', 3, 5, 8), group('C', 8)]
    assert got['added_wafer_groups'] == [group('B', 8), group('C', 8)]
    assert read_wafer_item(tmp_path / 'Alice', 'app', tid) == invoke(second, 'show', task_id=tid)['wafer_item'] == got
    assert got['lot_cd'] == 'product X' and got['oper'] == 'test A'
    assert updated['comments'] == ['New samples']
    assert invoke(first, 'create', title='Ring defect', body='Sample maps', oper='test A', lot_cd='product X',
                  wafer_groups=[group('A', 1, 3), group('B', 3, 5)]) == created
    assert events[-2] == dict(type='wafer_item_saved', name='kanban_comment', invocation_id='call-1', wafer_item=got)
    assert 'error' in invoke(second, 'create', title='Ring defect', oper='other test')
    seen = invoke(second, 'comment', task_id=tid, wafer_groups=[group('B', 8)])['wafer_item']
    assert seen['wafer_count'] == 6 and not seen['added_wafer_groups']
    assert read_wafer_item(tmp_path / 'Alice', 'app', tid)['added_wafer_groups'] == got['added_wafer_groups']
    for other_profile, other_db in [('Bob', 'app'), ('Alice', 'other-db')]:
        with pytest.raises(LookupError):
            read_wafer_item(tmp_path / other_profile, other_db, tid)
        other = board(employee=other_profile, database=other_db)
        assert invoke(other, 'list')['tasks'] == []
        assert 'error' in invoke(other, 'show', task_id=tid)
        assert 'error' in invoke(other, 'comment', task_id=tid, body='foreign')
    private_board = tmp_path / 'Alice/web-kanban' / hashlib.sha256(b'app').hexdigest() / 'kanban.db'
    with sqlite3.connect(private_board) as conn:
        assert conn.execute('SELECT id,body,status,assignee FROM tasks').fetchall() == [(tid, 'Sample maps', 'blocked', None)]
        assert not conn.execute('SELECT * FROM task_runs').fetchall()


def test_same_title_atomic_concurrent_registration_and_append(boards):
    board, _ = boards
    def save(n):
        return invoke(board(session=str(n)), 'create', title='Ring defect', wafer_groups=[group('LOT', n, 4)])
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(save, range(12)))
    tid, = {r['task_id'] for r in results}
    item = invoke(board(), 'show', task_id=tid)['wafer_item']
    assert item['wafer_count'] == 12 and set(item['wafer_groups'][0]['wf_ids']) == set(range(12))
    assert invoke(board(), 'list')['count'] == 1


@pytest.mark.parametrize('bad', [
    {'board': 'foreign'}, {'assignee': 'manager'}, {'project': 'other'}, {'initial_status': 'running'},
    {'session_id': 'fake'}, {'wafer_groups': [{'label': 'x', 'lot_id': 'LOT', 'wf_ids': ['2']}]},
])
def test_refuse_model_employee_and_execution_overrides(boards, bad):
    board, _ = boards
    registry = board()
    assert 'error' in invoke(registry, 'create', title='Private', **bad)
    assert not invoke(registry, 'list')['tasks']


def test_board_store_rejects_symlink(boards, tmp_path):
    board, _ = boards
    alice, bob = tmp_path/'Alice', tmp_path/'Bob'
    alice.mkdir()
    bob.mkdir()
    (alice/'web-kanban').symlink_to(bob, target_is_directory=True)
    assert 'error' in invoke(board(), 'create', title='Private')
    assert not list(bob.iterdir())


def test_validate_exact_map_members_before_writing(boards, monkeypatch):
    from contextlib import closing
    from importlib import import_module
    from harness.web_kanban import _validate_map_members

    board, events = boards
    maps = import_module('harness.tools.load_wafer_maps')
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE MAP (LOT_ID TEXT, WF_ID INT, LOT_CD TEXT, OPER_DET_DESC TEXT)')
    db.executemany('INSERT INTO MAP VALUES (?, ?, ?, ?)', [
        ('A', 1, '4SA', 'Cold'), ('A', 1, '4SA', 'Hot'), ('A', 2, '4SS', 'Hot'), ('B', 2, '4SA', 'Cold')])

    class FixtureConnection:
        def cursor(self):
            return closing(db.cursor())
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(maps, '_connection', FixtureConnection)
    registry = board(validate=_validate_map_members)
    new = dict(title='Ring defect', oper='Hot', lot_cd='4SA')
    assert 'A-SPLIT' in invoke(registry, 'create', **new, wafer_groups=[group('A', 1), group('A-SPLIT', 1)])['error']
    assert 'error' in invoke(registry, 'create', **new, wafer_groups=[group('A', 1, 2)])
    assert not invoke(registry, 'list')['tasks'] and not events
    add = dict(**new, wafer_groups=[group('A', 1)])
    created = invoke(registry, 'create', **add)
    tid = created['task_id']
    error = invoke(registry, 'comment', task_id=tid, wafer_groups=[group('A', 1), group('B', 2)])['error']
    assert 'Cold' in error and 'Hot' in error and '"wf_id": 2' in error
    assert invoke(registry, 'show', task_id=tid)['wafer_item'] == created['wafer_item']
    db.close()
    assert invoke(registry, 'create', **add) == created


def test_complete_board_item_and_read_original_handoff(boards, tmp_path):
    board, _ = boards
    created = invoke(board(), 'create', title='Ring defect', wafer_groups=[group('A', 1, 3)])
    tid = created['task_id']
    completion = {'task_id': tid, 'summary': 'Equipment log reviewed', 'result': 'L2 drift'}
    for other in (board(employee='Bob'), board(database='other')):
        assert 'error' in invoke(other, 'complete', **completion)
    assert 'error' in invoke(board(), 'complete', task_id=tid, summary='  ')
    def finish(n):
        return invoke(board(session=str(n)), 'complete', **completion)
    with ThreadPoolExecutor(3) as pool:
        receipts = list(pool.map(finish, range(3)))
    assert all(r['ok'] and r['task']['status'] == 'done' for r in receipts)
    shown = invoke(board(session='later'), 'show', task_id=tid)
    assert shown['task']['summary'] == completion['summary']
    assert shown['task']['result'] == completion['result']
    assert shown['wafer_item']['wafer_groups'] == [group('A', 1, 3)]
    assert invoke(board(), 'list', title='Ring defect')['tasks'][0]['status'] == 'done'
    assert 'error' in invoke(board(), 'complete', task_id=tid, summary='Different')
    assert invoke(board(), 'show', task_id=tid)['task'] == shown['task']
    path = tmp_path / 'Alice/web-kanban' / hashlib.sha256(b'app').hexdigest() / 'kanban.db'
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT status,result FROM tasks').fetchall() == [('done', 'L2 drift')]
        assert conn.execute('SELECT outcome,summary FROM task_runs').fetchall() == [('completed', 'Equipment log reviewed')]


def test_personal_board_native_lifecycle_and_gate(boards):
    board, events = boards
    chat = board()
    parent = invoke(chat, 'create', title='Parent')['task_id']
    child = invoke(chat, 'create', title='Child', wafer_groups=[group('A', 1)])['task_id']
    assert 'error' in invoke(chat, 'request_review', task_id=child, summary='Pending')
    assert invoke(chat, 'unblock', task_id=child)['status'] == 'ready'
    assert invoke(chat, 'link', parent_id=parent, child_id=child)['gated']
    assert 'error' in invoke(chat, 'link', parent_id=child, child_id=parent)
    assert 'error' in invoke(board(employee='Eve'), 'unblock', task_id=parent)
    assert 'error' in invoke(chat, 'complete', task_id=child, summary='premature')
    assert invoke(chat, 'complete', task_id=parent, summary='done')['ok']
    assert invoke(chat, 'block', task_id=child, reason='Operator needed', kind='dependency')['block_kind'] == 'needs_input'
    assert invoke(chat, 'schedule', task_id=child, reason='Waiting for operator')['status'] == 'scheduled'
    assert invoke(chat, 'unblock', task_id=child)['status'] == 'ready'
    assert invoke(chat, 'request_review', task_id=child, summary='Wafer inspected')['status'] == 'review'
    detail = invoke(board(session='next'), 'show', task_id=child)
    assert detail['task']['status'] == 'review' and detail['wafer_item']['wafer_count'] == 1
    assert detail['task']['summary'] == 'Wafer inspected' and detail['parents'] == [parent]
    assert invoke(chat, 'show', task_id=parent)['children'] == [child]
    assert {'kanban_heartbeat', 'kanban_request_changes'} <= set(WEB_KANBAN_TOOLS)


@pytest.mark.parametrize('unfinished_parent', [False, True])
def test_request_changes_personal_review(boards, tmp_path, unfinished_parent):
    board, _ = boards
    chat = board()
    created = invoke(chat, 'create', title='Review item', wafer_groups=[group('A', 1)])
    tid = created['task_id']
    invoke(chat, 'unblock', task_id=tid)
    invoke(chat, 'request_review', task_id=tid, summary='Inspect')
    if unfinished_parent:
        parent = invoke(chat, 'create', title='Parent')['task_id']
        invoke(chat, 'link', parent_id=parent, child_id=tid)
    result = invoke(chat, 'request_changes', task_id=tid, reason='Check calibration')
    assert result['task']['status'] == ('todo' if unfinished_parent else 'ready')
    assert result['wafer_item'] == created['wafer_item']
    assert 'Check calibration' in invoke(chat, 'show', task_id=tid)['comments']


def test_request_changes_errors(boards):
    board, _ = boards
    chat = board()
    tid = invoke(chat, 'create', title='Review item')['task_id']
    assert 'error' in invoke(chat, 'request_changes', task_id=tid, reason='Wrong state')
    invoke(chat, 'unblock', task_id=tid)
    invoke(chat, 'request_review', task_id=tid, summary='Inspect')
    for reason in ['', '   ']:
        assert 'error' in invoke(chat, 'request_changes', task_id=tid, reason=reason)
    for other in [board(employee='Bob'), board(database='other')]:
        assert 'error' in invoke(other, 'request_changes', task_id=tid, reason='Foreign')
    assert 'error' in invoke(chat, 'request_changes', task_id='unknown', reason='Missing')
    assert invoke(chat, 'show', task_id=tid)['task']['status'] == 'review'


@pytest.mark.parametrize('status', ['blocked', 'ready', 'review'])
@pytest.mark.parametrize('note', [None, 'Still inspecting'])
def test_personal_heartbeat_without_dispatcher_claim(boards, tmp_path, status, note):
    """Web heartbeat intentionally works without upstream running status or a claim/run."""
    board, _ = boards
    chat = board()
    tid = invoke(chat, 'create', title='Alive')['task_id']
    if status != 'blocked':
        invoke(chat, 'unblock', task_id=tid)
    if status == 'review':
        invoke(chat, 'request_review', task_id=tid, summary='Inspect')
    result = invoke(chat, 'heartbeat', task_id=tid, note=note)
    assert result['task']['status'] == status
    if note:
        assert note in invoke(chat, 'show', task_id=tid)['comments']
    path = tmp_path / 'Alice/web-kanban' / hashlib.sha256(b'app').hexdigest() / 'kanban.db'
    with sqlite3.connect(path) as conn:
        heartbeat, lock = conn.execute('SELECT last_heartbeat_at,claim_lock FROM tasks WHERE id=?', (tid,)).fetchone()
        assert heartbeat > 0 and lock is None
        for other in [board(employee='Bob'), board(database='other')]:
            assert 'error' in invoke(other, 'heartbeat', task_id=tid)
        assert 'error' in invoke(chat, 'heartbeat', task_id='unknown')
        conn.execute("UPDATE tasks SET status='archived' WHERE id=?", (tid,))
        conn.commit()
    assert 'error' in invoke(chat, 'heartbeat', task_id=tid)

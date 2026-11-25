"""Employee-owned Hermes cards for persistent collections of exact LOT/wafer pairs."""
from contextlib import closing
from functools import partial
import hashlib
import json
from pathlib import Path
from typing import Literal
import sqlite3
import stat
import time

from pydantic import Field

from .tools.load_wafer_maps import WaferGroup
from .types import Contract


class _Create(Contract):
    title: str = Field(min_length=1, max_length=100)
    oper: str | None = Field(default=None, min_length=1, max_length=100,
                             description='Exact map inspection for this collection, if established.')
    lot_cd: str | None = Field(default=None, min_length=1, max_length=100,
                               description='Product code for this collection, if established.')
    body: str = ''
    wafer_groups: list[WaferGroup] = Field(default_factory=list, max_length=100)


class _Comment(Contract):
    task_id: str = Field(min_length=1)
    body: str = ''
    wafer_groups: list[WaferGroup] = Field(default_factory=list, max_length=100)


class _Show(Contract):
    task_id: str = Field(min_length=1)


class _CardTransition(_Show):
    pass


class _Block(_CardTransition):
    reason: str = Field(min_length=1)
    kind: Literal['dependency', 'needs_input', 'capability', 'transient'] | None = None


class _Schedule(_CardTransition):
    reason: str | None = None


class _Review(_CardTransition):
    summary: str = Field(min_length=1)


class _RequestChanges(_CardTransition):
    reason: str = Field(min_length=1)


class _Heartbeat(_CardTransition):
    note: str | None = None


class _Link(Contract):
    parent_id: str = Field(min_length=1)
    child_id: str = Field(min_length=1)


class _Complete(Contract):
    task_id: str = Field(min_length=1)
    summary: str | None = Field(default=None, description='Investigation conclusion to retain on the card.')
    result: str | None = Field(default=None, description='Optional result log. Supply a summary or a result.')


class _List(Contract):
    title: str | None = Field(default=None, description='Exact item title if known.')
    limit: int = Field(default=50, ge=1, le=200, strict=True)


_TOOLS = {
    'kanban_request_changes': (_RequestChanges, 'Return a personal review card to work, respecting parent dependencies, with a required reason.'),
    'kanban_heartbeat': (_Heartbeat, 'Record liveness and an optional progress note on a personal card without changing its status.'),
    'kanban_block': (_Block, 'Mark a ready or running card blocked with its reason. To park a card in scheduled status until later, use kanban_schedule.'),
    'kanban_unblock': (_CardTransition, 'Resume a blocked or parked card, respecting incomplete parent dependencies.'),
    'kanban_schedule': (_Schedule, 'Park a card pending an external condition. To resume, use kanban_unblock. '
                        'This changes card state only and sets no timer.'),
    'kanban_link': (_Link, 'Make the child card depend on completion of the parent card on your board.'),
    'kanban_request_review': (_Review, 'Move a ready or running personal card into review, recording the handoff summary.'),
    'kanban_create': (_Create, 'Register a persistent personal defect item with its exact wafer_groups. '
                      'A group contains the item label, lot_id and wf_ids. The card stays available in other chats.'),
    'kanban_list': (_List, 'Find saved personal defect items across chats. Use the title filter if the name is known.'),
    'kanban_show': (_Show, 'Read a personal card’s state, dependencies, handoff summary, notes and exact LOT/wafer selection.'),
    'kanban_complete': (_Complete, 'Mark a saved defect investigation done with its final summary or result.'),
    'kanban_comment': (_Comment, 'Append new wafer_groups or a note to an existing defect item. '
                       'Provide exact LOT IDs and wafer numbers in wafer_groups when adding wafers.'),
}
WEB_KANBAN_TOOLS = tuple(_TOOLS)
_RECORD = 'yield.wafer-members/v1'


def _board_path(profile: Path, database: str, *, create=False):
    home = profile / 'web-kanban' / hashlib.sha256(database.encode()).hexdigest()
    if home.resolve() != home:
        raise ValueError('Invalid web kanban store')
    if create:
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if home.exists():
        for entry in home.iterdir():
            try:
                info = entry.lstat()
            except FileNotFoundError:
                continue
            if entry.name == 'attachments' and stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Invalid web kanban store')
    return home / 'kanban.db'


def _groups(title, members):
    by_lot = {}
    for lot, wafer in members:
        by_lot.setdefault(lot, []).append(wafer)
    return [dict(label=title, lot_id=lot, wf_ids=numbers) for lot, numbers in by_lot.items()]


def _snapshot(conn, task_id, request_id=None):
    task = conn.execute('SELECT id,title,body,status,result FROM tasks WHERE id=?', (task_id,)).fetchone()
    if task is None:
        raise LookupError('No such item on this employee board')
    task = dict(task)
    if task['status'] == 'done':
        run = conn.execute("SELECT summary FROM task_runs WHERE task_id=? AND outcome='completed' ORDER BY id DESC LIMIT 1",
                           (task_id,)).fetchone()
        task['summary'] = run['summary'] if run else task['result']
    members = {}
    latest = replay = None
    filters = {}
    notes = []
    for row in conn.execute('SELECT body FROM task_comments WHERE task_id=? ORDER BY id', (task_id,)):
        try:
            record = json.loads(row['body'])
        except (ValueError, TypeError):
            record = None
        if not isinstance(record, dict) or record.get('type') != _RECORD:
            notes.append(row['body'])
            continue
        filters.update(record.get('filters', {}))
        added = [tuple(pair) for pair in record['added']]
        members.update(dict.fromkeys(added))
        if added:
            latest = added
        if record.get('note'):
            notes.append(record['note'])
        if request_id and record.get('request_id') == request_id:
            replay = added
            break
    title = task['title']
    item = dict(id=task_id, title=title, wafer_groups=_groups(title, members),
                added_wafer_groups=_groups(title, latest or ()), wafer_count=len(members), **filters)
    return dict(ok=True, task_id=task_id, task=task, comments=notes, wafer_item=item), members, replay


def read_wafer_item(profile: Path, database: str, task_id: str):
    """Read one committed selection from the caller's board without loading Hermes in the gateway."""
    path = _board_path(profile, database)
    if not path.is_file():
        raise LookupError('No such item on this employee board')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        result, _, _ = _snapshot(conn, task_id)
        return result['wafer_item']


def _item_key(title):
    return hashlib.sha256(title.strip().casefold().encode()).hexdigest()


def _validate_map_members(groups, lot_cd, oper):
    from .tools.load_wafer_maps import LoadWaferMapsInput, _connection, _filters

    selection = LoadWaferMapsInput(wafer_groups=groups, lot_cd=lot_cd)
    where, binds = _filters(selection)
    with _connection() as connection, connection.cursor() as cursor:
        cursor.execute(f'SELECT DISTINCT LOT_ID, WF_ID, OPER_DET_DESC FROM MAP WHERE {where}', binds)
        actual = {}
        for lot, wafer, inspection in cursor.fetchall():
            actual.setdefault((lot, wafer), set()).add(inspection)
    missing = []
    for group in groups:
        for wf in group.wf_ids:
            inspections = actual.get((group.lot_id, wf))
            if not inspections or (oper is not None and oper not in inspections):
                missing.append(dict(lot_id=group.lot_id, wf_id=wf, available_inspections=sorted(
                    inspection for inspection in inspections or () if inspection is not None)))
    if missing:
        raise ValueError('No changes saved. MAP has no matching measurement for these exact LOT_ID/WF_ID pairs '
                         f'with product={lot_cd!r}, inspection={oper!r}: '
                         + json.dumps(missing, ensure_ascii=False))


def _scope(cmd, item):
    saved = (item.get('lot_cd'), item.get('oper'))
    if isinstance(cmd, _Comment):
        return saved
    given = (cmd.lot_cd, cmd.oper)
    if item and any(value is not None and value != old for old, value in zip(saved, given)):
        raise ValueError('This item already has a different measurement scope. Use its saved product and inspection.')
    return tuple(old or value for old, value in zip(saved, given))


def install_web_kanban(registry, profile, database, run, *, invocation_id, emit, validate_members=_validate_map_members):
    from agent.redact import redact_sensitive_text
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing, write_txn
    from tools.registry import tool_error

    def complete(conn, cmd):
        summary = redact_sensitive_text((cmd.summary or '').strip(), force=True) or None
        result = redact_sensitive_text((cmd.result or '').strip(), force=True) or None
        if not (summary or result):
            raise ValueError('Supply the investigation summary or result to complete this item.')
        if kb.get_task(conn, cmd.task_id) is None:
            raise LookupError('No such item on this employee board')
        changed = kb.complete_task(conn, cmd.task_id, summary=summary, result=result)
        conn.execute('BEGIN')
        snapshot, _, _ = _snapshot(conn, cmd.task_id)
        if not changed:
            task = snapshot['task']
            if (task['status'], task.get('summary'), task['result']) != ('done', summary or result, result):
                raise ValueError('Could not complete this item. Read its current status and conclusion.')
        return snapshot

    def personal_transition(conn, cmd):
        with write_txn(conn):
            task = kb.get_task(conn, cmd.task_id)
            if task is None:
                raise LookupError('No such item on this employee board')
            if isinstance(cmd, _RequestChanges):
                reason = redact_sensitive_text(cmd.reason.strip(), force=True).strip()
                if not reason:
                    raise ValueError('Supply a non-empty reason for requesting changes.')
                if task.status != 'review':
                    raise ValueError('Changes can only be requested for a card in review.')
                status = kb._landing_status_after_parents(conn, cmd.task_id)
                conn.execute('UPDATE tasks SET status=? WHERE id=?', (status, cmd.task_id))
                kb.add_comment(conn, cmd.task_id, run['principal_id'], reason)
            else:
                if task.status == 'archived':
                    raise ValueError('Cannot heartbeat an archived card.')
                # Personal boards have no dispatcher: unlike upstream, no running claim is required.
                conn.execute('UPDATE tasks SET last_heartbeat_at=? WHERE id=?',
                             (int(time.time()), cmd.task_id))
                note = redact_sensitive_text((cmd.note or '').strip(), force=True).strip()
                if note:
                    kb.add_comment(conn, cmd.task_id, run['principal_id'], note)
            snapshot, _, _ = _snapshot(conn, cmd.task_id)
            snapshot['status'] = snapshot['task']['status']
            return snapshot

    def transition(conn, cmd):
        if isinstance(cmd, _Link):
            gated = kb.link_tasks(conn, cmd.parent_id, cmd.child_id)
            return dict(ok=True, parent_id=cmd.parent_id, child_id=cmd.child_id, gated=gated)
        task = kb.get_task(conn, cmd.task_id)
        if task is None:
            raise LookupError('No such item on this employee board')
        if isinstance(cmd, _Block):
            reason = redact_sensitive_text(cmd.reason.strip(), force=True)
            if not reason:
                raise ValueError('Supply the reason for blocking the card.')
            changed = kb.block_task(conn, cmd.task_id, reason=reason, kind=cmd.kind)
        elif isinstance(cmd, _Schedule):
            reason = redact_sensitive_text((cmd.reason or '').strip(), force=True) or None
            changed = kb.schedule_task(conn, cmd.task_id, reason=reason)
        elif isinstance(cmd, _Review):
            summary = redact_sensitive_text(cmd.summary.strip(), force=True)
            if not summary:
                raise ValueError('Supply the review handoff summary.')
            changed, detail = kb.request_review(conn, cmd.task_id, summary=summary, with_reason=True)
            if not changed:
                raise ValueError(detail)
        else:
            changed = kb.unblock_task(conn, cmd.task_id)
        if not changed:
            raise ValueError(f'This transition is unavailable from the current card status ({task.status}).')
        task = kb.get_task(conn, cmd.task_id)
        return dict(ok=True, task_id=task.id, status=task.status, block_kind=task.block_kind)

    def dispatch(name, args, **kwargs):
        try:
            model, _ = _TOOLS[name]
            cmd = model.model_validate(args)
            path = _board_path(profile, database, create=True)
            with connect_closing(db_path=path) as conn:
                if isinstance(cmd, (_RequestChanges, _Heartbeat)):
                    return json.dumps(personal_transition(conn, cmd), ensure_ascii=False)
                if isinstance(cmd, (_CardTransition, _Link)):
                    return json.dumps(transition(conn, cmd), ensure_ascii=False)
                if isinstance(cmd, _List):
                    if cmd.title:
                        rows = conn.execute('SELECT id,title,status FROM tasks WHERE idempotency_key=?',
                                            (_item_key(cmd.title),)).fetchall()
                        tasks = [dict(row) for row in rows]
                    else:
                        tasks = [dict(id=t.id, title=t.title, status=t.status)
                                 for t in kb.list_tasks(conn, limit=cmd.limit + 1, order_by='created-desc')]
                    return json.dumps(dict(tasks=tasks[:cmd.limit], count=min(len(tasks), cmd.limit),
                                           truncated=len(tasks) > cmd.limit), ensure_ascii=False)
                if isinstance(cmd, _Complete):
                    return json.dumps(complete(conn, cmd), ensure_ascii=False)
                if isinstance(cmd, _Show):
                    conn.execute('BEGIN')
                    result, _, _ = _snapshot(conn, cmd.task_id)
                    result['parents'] = kb.parent_ids(conn, cmd.task_id)
                    result['children'] = kb.child_ids(conn, cmd.task_id)
                    latest = kb.latest_run(conn, cmd.task_id)
                    if latest and latest.summary:
                        result['task']['summary'] = latest.summary
                    return json.dumps(result, ensure_ascii=False)
                call_id = invocation_id()
                request_id = hashlib.sha256(json.dumps([run['run_id'], call_id, name, args],
                                                       sort_keys=True).encode()).hexdigest()
                note = redact_sensitive_text(cmd.body.strip(), force=True)
                if isinstance(cmd, _Create):
                    found = conn.execute('SELECT id FROM tasks WHERE idempotency_key=? AND status!=?',
                                         (_item_key(cmd.title), 'archived')).fetchone()
                    task_id = found['id'] if found else None
                else:
                    if not cmd.wafer_groups and not note:
                        raise ValueError('Provide wafer_groups or a comment body.')
                    task_id = cmd.task_id
                before, replay = {}, None
                if task_id is not None:
                    before, _, replay = _snapshot(conn, task_id, request_id)
                lot_cd, oper = _scope(cmd, before.get('wafer_item', {}))
                if replay is None and cmd.wafer_groups:
                    validate_members(cmd.wafer_groups, lot_cd, oper)

                with write_txn(conn):
                    if isinstance(cmd, _Create):
                        task_id = kb.create_task(conn, title=cmd.title.strip(), body=note,
                            created_by=run['principal_id'], session_id=run['session_id'],
                            idempotency_key=_item_key(cmd.title), initial_status='blocked', workspace_kind='scratch')
                    result, members, replay = _snapshot(conn, task_id, request_id)
                    item = result['wafer_item']
                    added = replay
                    if replay is None:
                        stored = (item.get('lot_cd'), item.get('oper'))
                        if members or before or any(stored):
                            if stored != (lot_cd, oper):
                                raise ValueError('Item scope changed while registering; read it again before adding wafers.')
                        requested = dict.fromkeys((g.lot_id, wf) for g in cmd.wafer_groups for wf in g.wf_ids)
                        added = [pair for pair in requested if pair not in members]
                        filters = {key: value for key, value in [('lot_cd', lot_cd), ('oper', oper)]
                                   if value is not None and key not in item}
                        record = dict(type=_RECORD, request_id=request_id, added=added, filters=filters,
                                      note=note if isinstance(cmd, _Comment) else '')
                        kb.add_comment(conn, task_id, run['principal_id'], json.dumps(record, ensure_ascii=False))
                        result, _, _ = _snapshot(conn, task_id)
                    result['wafer_item']['added_wafer_groups'] = _groups(result['wafer_item']['title'], added)
            emit(dict(type='wafer_item_saved', name=name, invocation_id=call_id or request_id, wafer_item=result['wafer_item']))
            return json.dumps(result, ensure_ascii=False)
        except (ValueError, LookupError) as exc:
            return tool_error(str(exc))

    for name, (model, description) in _TOOLS.items():
        schema = dict(name=name, description=description, parameters=model.model_json_schema())
        registry.register(name=name, toolset='web_kanban', schema=schema, handler=partial(dispatch, name),
                          override=True, emoji='📋')

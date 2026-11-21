"""Bind web map reads to the user-confirmed selection for this analysis."""

import hashlib
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from ..map_intent import interpret_map_intent
from ..runtime.domain_process import DomainError, run_blocking
from ..store import LeaseLost


_SELECTION = ('lot_cd', 'oper', 'period')


def _database_time(value, timezone):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if value is not None and value.tzinfo is not None:
        value = value.astimezone(ZoneInfo(timezone)).replace(tzinfo=None)
    return value


def _selection(previous, intent, run_id):
    carry = previous if intent['action'] in ('continue', 'change') and previous else {}
    kind = intent['kind']
    if carry.get('kind') == 'scoped' and kind != 'scoped':
        raise ValueError('MAP_SCOPE_CONFLICT: a targeted lookup must start a separate analysis.')
    if carry.get('wafer_groups') and intent['action'] == 'continue' and not intent.get('wafer_groups'):
        raise ValueError('MAP_SCOPE_CONFLICT: reuse current profile memory_refs to continue a remembered lookup.')
    fields = (*_SELECTION, 'target_lot_ids', 'target_wf_ids', 'memory_refs', 'wafer_groups') if kind == 'targeted' else _SELECTION
    if intent['action'] == 'continue' and any(
        k in intent and k in carry and intent[k] != carry[k] for k in fields
    ):
        raise ValueError('MAP_SCOPE_CONFLICT: changing an established filter requires a user-directed change.')
    selection = {key: carry[key] for key in fields if key in carry}
    if 'wafer_groups' in intent:
        selection.pop('target_lot_ids', None)
        selection.pop('target_wf_ids', None)
    elif intent.get('target_lot_ids'):
        selection.pop('wafer_groups', None)
        selection.pop('memory_refs', None)
    selection.update({key: intent[key] for key in fields if key in intent})
    return {**selection, 'kind': kind, 'analysis_run_id': carry.get('analysis_run_id', run_id)}


def _required(scope):
    if scope['kind'] == 'scoped':
        return [key for key in _SELECTION if key not in scope]
    if not scope.get('target_lot_ids') and not scope.get('wafer_groups'):
        return ['target_lot_ids']
    if scope.get('period', {}).get('kind') == 'recent_weeks':
        return [key for key in ('lot_cd', 'oper') if key not in scope]
    return []


async def _resolve_window(scope, previous, timezone):
    period = scope.get('period')
    if not period or _required(scope):
        return
    if previous and 'start_time' in previous and all(scope.get(k) == previous.get(k) for k in _SELECTION):
        scope.update(start_time=previous['start_time'], end_time=previous['end_time'])
        return
    from .load_wafer_maps import resolve_map_window

    if period['kind'] == 'range':
        period = {**period, **{k: _database_time(period[k], timezone).isoformat() for k in ('start_time', 'end_time')}}
    parameters = (scope.get('lot_cd'), scope.get('oper'), period)
    if period['kind'] == 'recent_weeks':
        start, end = await run_blocking(resolve_map_window, *parameters)
    else:
        start, end = resolve_map_window(*parameters)
    scope.update(start_time=start.isoformat() if start else None, end_time=end.isoformat() if end else None)


def _apply(args, scope, timezone):
    if 'error' in scope:
        raise ValueError(scope['error'])
    missing = _required(scope)
    if missing:
        raise ValueError('MAP_SCOPE_INCOMPLETE: confirm ' + ', '.join(missing) + ' with the user before loading maps.')
    updates = {key: scope[key] for key in ('lot_cd', 'oper') if key in scope}
    if scope.get('wafer_groups'):
        if set(getattr(args, 'memory_refs', ())) != set(scope['memory_refs']) or args.lot_ids or args.wf_ids:
            raise ValueError('MAP_SCOPE_CONFLICT: supply the selected memory_refs without flat lot_ids or wf_ids.')
        if args.oper is not None and 'oper' not in scope:
            raise ValueError('MAP_SCOPE_INCOMPLETE: confirm the measurement stage for the remembered wafers.')
        # The reference lookup resolves the stage from the user's request and the DB catalog.
        # The model's optional restatement is logged in its original arguments.
        args = args.model_copy(update={'oper': scope.get('oper')})
    elif scope['kind'] == 'targeted':
        if not set(args.lot_ids).issubset(scope['target_lot_ids']):
            raise ValueError('MAP_SCOPE_CONFLICT: lot_ids includes a lot outside the user selection.')
        if scope.get('target_wf_ids'):
            if not set(args.wf_ids).issubset(scope['target_wf_ids']):
                raise ValueError('MAP_SCOPE_CONFLICT: wf_ids includes a wafer outside the user selection.')
            args = args.model_copy(update={'wf_ids': args.wf_ids or scope['target_wf_ids']})
    if 'period' in scope:
        updates.update(time_column='end_tm', start_time=_database_time(scope['start_time'], timezone),
                       end_time=_database_time(scope['end_time'], timezone))
    for key, confirmed in updates.items():
        given = getattr(args, key)
        if key in ('start_time', 'end_time'):
            given = _database_time(given, timezone)
        if given is not None and given != confirmed:
            raise ValueError(f'MAP_SCOPE_CONFLICT: {key}={given} conflicts with confirmed {key}={confirmed}.')
    return args.model_copy(update=updates), scope


async def effective_map_args(args, ctx):
    run = getattr(ctx, 'run', {}) or {}
    if run.get('chat_entrypoint') != 'stock_chat':
        if getattr(args, 'memory_refs', None):
            raise ValueError('MAP_SCOPE_UNRESOLVED: this invocation has no profile memory snapshot.')
        return args, None
    owner = {k: run[k] for k in ('principal_id', 'session_id')}
    current = {**owner, '_id': run['run_id'], 'epoch': run['epoch'], 'status': 'running', 'active': True}
    runs = ctx.store.runs
    timezone = ctx.settings.timezone or 'Asia/Seoul'
    async with ctx.map_scope_lock:
        history = list(ctx.map_scope_history)
        refs = sorted(set(getattr(args, 'memory_refs', ()) or ()))
        entries = getattr(ctx, 'map_memory_entries', {})
        if any(ref not in entries for ref in refs):
            raise ValueError('MAP_SCOPE_UNRESOLVED: use current profile memory_refs for the requested groups.')
        selected = {ref: entries[ref] for ref in refs}
        evidence = json.dumps([run['query'], history, refs, args.oper if selected else None],
                              sort_keys=True, ensure_ascii=False).encode()
        revision = hashlib.sha256(evidence).hexdigest()
        row = await runs.find_one({**current, 'lease_until': {'$gt': time.time()}}, {'map_scope': 1})
        if not row:
            raise LeaseLost('Run cancelled or lease lost')
        saved = row.get('map_scope')
        if saved and saved['revision'] == revision:
            return _apply(args, saved, timezone)
        previous = saved
        if not previous:
            prior = await runs.find_one({**owner, 'map_scope': {'$exists': True}, 'created_at': {'$lt': run['created_at']}},
                                        {'map_scope': 1}, sort=[('created_at', -1)])
            previous = (prior or {}).get('map_scope')
        # An ambiguous user selection should not change when the model repeats an
        # identical tool call. A clarification or another user turn can revise it.
        if previous and 'kind' not in previous:
            previous = None
        scope = {}
        try:
            intent = (await interpret_map_intent(ctx, previous, history, selected, args.oper) if selected
                      else await interpret_map_intent(ctx, previous, history))
            scope = _selection(previous, intent, run['run_id'])
            await _resolve_window(scope, previous if intent['action'] != 'start' else None, timezone)
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError) and exc.error_type != 'ValueError':
                raise
            reason = 'Invalid map selection' if isinstance(exc, ValidationError) else str(exc)
            scope['error'] = 'MAP_SCOPE_UNRESOLVED: ' + reason[:500]
        scope['revision'] = revision
        fence = {**current, 'lease_until': {'$gt': time.time()}, 'map_scope.revision': saved['revision'] if saved else None}
        written = await runs.update_one(fence, {'$set': {'map_scope': scope}})
        if not written.matched_count:
            await ctx.store.fence(run['run_id'], run['epoch'])
            winner = await runs.find_one({**current, 'map_scope.revision': revision}, {'map_scope': 1})
            if not winner:
                raise ValueError('MAP_SCOPE_CHANGED: retry with the current confirmed scope.')
            scope = winner['map_scope']
        return _apply(args, scope, timezone)

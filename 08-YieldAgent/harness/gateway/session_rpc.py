"""Mongo adapters of H/tui_gateway/methods_session.py (5894e750).

Only public projections: no agent objects, model/provider, filesystem or secrets.
Host run transcript projection has no upstream equivalent → PB:503,575,639,664.
"""
import hashlib
import json
import uuid

from .events import observed_run_usage, verified_answer_with_artifacts
from .execution import RpcError, string


class NotConnected(Exception):
    pass


def validate(params, allowed, *, mode):
    if not isinstance(params, dict) or params.keys() - set(allowed):
        raise RpcError('invalid_params', code=4000 if mode == 'legacy' else 4001)


def flag(params, key, mode):
    value = params.get(key, False)
    if type(value) is not bool:
        raise RpcError('invalid_params', code=4000 if mode == 'legacy' else 4001)
    return value


class SessionRPC:
    def __init__(self, scope, connection):
        self.db, self.execution, self.connection = scope.sessions, scope.execution, connection
        self.mode = connection.mode

    async def owned(self, sid):
        if not string(sid):
            raise RpcError('session_id required', code=4006 if self.mode == 'legacy' else 4001)
        row = await self.db.get_session(sid)
        if row is None:
            raise RpcError('not_found')
        return row

    async def history(self, sid, *, include_compacted=True):
        # PR-3 resolves ancestors/tip; PR-7a is the only assistant publication gate.
        sid = await self.db.resolve_resume_session_id(sid)
        await self.owned(sid)
        lineage = await self.db.get_compression_lineage(sid) or [sid]
        if sid in lineage:
            lineage = lineage[:lineage.index(sid) + 1]
        messages = []
        for ancestor in lineage:
            rows = await self.db.get_messages(ancestor, include_compacted=True)
            runs = await self.execution.store.runs.find({
                'principal_id': self.execution.principal_id, 'session_id': ancestor
            }).sort([('created_at', 1), ('_id', 1)]).to_list(None)
            # Stored raw assistant/tool/reasoning rows are not publication evidence.
            # PR-7a runs own their query+verified response; PR-3 rows are used for
            # sessions without host runs (there is no cross-store message identity).
            if runs and any(row['role'] == 'user' and row.get('display_kind') != 'hidden' for row in rows):
                # No reliable cross-store identity/order: do not silently drop
                # or duplicate pre-existing transcript turns.
                raise NotConnected()
            if not runs:
                if not include_compacted:
                    rows = await self.db.get_messages(ancestor, include_compacted=False)
                tool_calls = {}
                for row in rows:
                    if row.get('display_kind') == 'hidden':
                        continue
                    if row['role'] == 'assistant':
                        calls = row.get('tool_calls')
                        for call in calls if isinstance(calls, list) else []:
                            if not isinstance(call, dict):
                                continue
                            function = call.get('function') if isinstance(call.get('function'), dict) else {}
                            call_id = call.get('id') or call.get('tool_call_id')
                            call_id = call_id if isinstance(call_id, str) else ''
                            name = function.get('name') or call.get('name') or call.get('tool_name')
                            name = name if isinstance(name, str) and name else 'tool'
                            raw_args = function.get('arguments', call.get('args', {}))
                            if isinstance(raw_args, str):
                                try:
                                    raw_args = json.loads(raw_args)
                                except (TypeError, ValueError):
                                    raw_args = {}
                            args = raw_args if isinstance(raw_args, dict) else {}
                            try:
                                json.dumps(args, allow_nan=False)
                            except (TypeError, ValueError):
                                args = {}
                            if call_id:
                                tool_calls[call_id] = (name, args)
                    elif row['role'] == 'user':
                        messages.append({'role': 'user', 'text': row.get('content') or '',
                                         'row_id': row['id']})
                    elif row['role'] == 'tool':
                        call_id = row.get('tool_call_id')
                        call_id = call_id if isinstance(call_id, str) else ''
                        name, args = tool_calls.get(call_id, (row.get('tool_name') or row.get('name') or 'tool', {}))
                        name = name if isinstance(name, str) and name else 'tool'
                        preview = json.dumps(args, ensure_ascii=False, separators=(',', ':')) if args else ''
                        if len(preview) > 80:
                            preview = preview[:77] + '...'
                        messages.append({'role': 'tool', 'name': name or 'tool', 'context': preview,
                                         'tool_call_id': call_id, 'args': args})
            previous_native = None
            for run in runs:
                messages.append({'role': 'user', 'text': run['query']})
                native = run.get('native_messages')
                if isinstance(native, list):
                    if previous_native is not None and native[:len(previous_native)] == previous_native:
                        turn = native[len(previous_native):]
                    else:
                        start = None
                        prompt = run.get('goal_request', run['query'])
                        for index, item in enumerate(native):
                            if (isinstance(item, dict) and item.get('role') == 'user'
                                    and item.get('content') == prompt):
                                start = index
                        turn = native[start:] if start is not None else native
                    previous_native = native
                    tool_calls = {}
                    for item in turn:
                        if not isinstance(item, dict):
                            continue
                        if item.get('role') == 'assistant':
                            calls = item.get('tool_calls')
                            for call in calls if isinstance(calls, list) else []:
                                if not isinstance(call, dict):
                                    continue
                                function = call.get('function') if isinstance(call.get('function'), dict) else {}
                                call_id = call.get('id') or call.get('tool_call_id')
                                call_id = call_id if isinstance(call_id, str) else ''
                                name = function.get('name') or call.get('name') or call.get('tool_name')
                                name = name if isinstance(name, str) and name else 'tool'
                                raw_args = function.get('arguments', call.get('args', {}))
                                if isinstance(raw_args, str):
                                    try:
                                        raw_args = json.loads(raw_args)
                                    except (TypeError, ValueError):
                                        raw_args = {}
                                args = raw_args if isinstance(raw_args, dict) else {}
                                try:
                                    json.dumps(args, allow_nan=False)
                                except (TypeError, ValueError):
                                    args = {}
                                if call_id:
                                    tool_calls[call_id] = (name, args)
                        elif item.get('role') == 'tool':
                            call_id = item.get('tool_call_id')
                            call_id = call_id if isinstance(call_id, str) else ''
                            name, args = tool_calls.get(call_id,
                                (item.get('tool_name') or item.get('name') or 'tool', {}))
                            name = name if isinstance(name, str) and name else 'tool'
                            preview = json.dumps(args, ensure_ascii=False, separators=(',', ':')) if args else ''
                            if len(preview) > 80:
                                preview = preview[:77] + '...'
                            messages.append({'role': 'tool', 'name': name or 'tool', 'context': preview,
                                             'tool_call_id': call_id, 'args': args})
                answer = verified_answer_with_artifacts(run)
                if answer is not None:
                    messages.append({'role': 'assistant', 'text': answer})
        return messages

    async def info(self, sid):
        row = await self.owned(sid)
        # H/contracts/canonical.py:175-202; optional unsafe fields omitted.
        return {'source': row.get('source') or 'cli', 'lazy': False,
                'profile_id': self.execution.profile_id,
                'profile_name': self.execution.profile_id,
                'desktop_protocol': 'hermes-gateway-v1',
                'title': row.get('title') or '', 'started_at': row['started_at']}

    async def public_list_row(self, row):
        """Shared WS/REST allowlist; raw session metadata is never returned."""
        value = {k: row[k] for k in ('id', 'started_at', 'source')}
        value['title'] = row.get('title') or ''
        value['message_count'] = len(await self.history(row['id']))
        return value

    async def snapshot(self, sid, params):
        await self.owned(sid)
        # Take the replay boundary BEFORE history. Concurrent new events may be
        # repeated in history, but cannot be skipped by the subsequent pump.
        state = await self.execution.snapshot(sid)
        handle = await self.execution.handle(sid)
        messages = await self.history(sid)
        omitted = flag(params, 'omit_messages', self.mode)
        deferred = flag(params, 'defer_history', self.mode)
        info = await self.info(sid)
        open_requests = (await self.connection.open_requests(sid) if self.mode == 'legacy' else [])
        # All fallible reads precede subscription registration; failed snapshots
        # cannot leave a permanently unarmed subscription owned by a dead task.
        subscription = await self.connection.subscribe(sid, state, replace=True)
        self.connection.bind_run(sid, state.get('active_run'))
        result = {'session_id': sid, 'stored_session_id': sid,
                  'message_count': len(messages), 'messages': [] if omitted or deferred else messages,
                  'info': info, 'running': state['active_run'] is not None}
        if deferred:
            # H/methods_session.py:885-890,899-914,958-978: defer the wire
            # transcript, retain its count. Yield has no background agent/history
            # build, so never claim hydrating/resuming; REST reads this projection.
            # H/session_registry.py:648-655: pending input takes precedence over
            # working/idle. No agent construction exists here to report starting.
            result.update(hydrating=False, status=('waiting' if state['prompts'] else
                          'working' if result['running'] else 'idle'))
        if self.mode == 'authority':
            # H/contracts/canonical_sessions.py:133-156. No queued admission
            # lane is enabled; any admitted run is represented by running/handle.
            result.update(authority_epoch=handle['authority_epoch'],
                replay_epoch=state['replay_epoch'], last_sequence=state['last_sequence'],
                subscription_id=subscription, revision=handle['revision'],
                execution_generation=handle['execution_generation'], pending=[], prompts=state['prompts'])
        else:
            result['info'] = {'title': result['info']['title'], 'stored_session_id': sid,
                              'profile_name': self.execution.profile_id, 'running': result['running']}
            result['open_requests'] = open_requests
            if omitted and not deferred:
                result['messages_omitted'] = True
        return result

    async def dispatch(self, method, params):
        if method == 'session.create':
            return await self.create(params)
        if method in ('session.list', 'session.most_recent'):
            return await self.listing(method, params)
        if method in ('session.active_list', 'session.stats'):
            # Required live-agent/model fields or untracked actual token counters.
            # H/contracts/sessions.py:271-285; canonical.py:212-239; PB:635.
            raise NotConnected()
        if method not in {'session.resume', 'session.activate', 'session.history',
                          'session.title', 'session.info', 'session.close', 'session.detach', 'session.usage'}:
            raise NotConnected()
        allowed = {'session_id'}
        if method in ('session.resume', 'session.activate'):
            allowed |= {'cols', 'omit_messages'}
        if method == 'session.resume':
            allowed |= {'source', 'lazy', 'defer_history', 'eager_build', 'close_on_disconnect',
                        'inline_images', 'title', 'latest', 'workspace', 'editor', 'api_key', 'display_projection'}
        if method == 'session.title':
            allowed.add('title')
        if method == 'session.detach':
            allowed |= {'subscription_id', 'end_reason'}
        validate(params, allowed, mode=self.mode)
        sid = params.get('session_id')
        await self.owned(sid)
        if method == 'session.resume':
            # H/contracts/sessions.py:185-195; canonical_sessions.py:62-74.
            # H/agent_factory.py:34-38 accepts any source string (including
            # desktop). This host reuses the stored row; no runtime platform or
            # persisted source is changed by the caller's surface hint.
            if params.get('source') is not None and not isinstance(params['source'], str):
                raise RpcError('invalid_params', code=4000 if self.mode == 'legacy' else 4001)
            flag(params, 'defer_history', self.mode)
            for key in allowed - {'session_id', 'cols', 'omit_messages', 'inline_images',
                                  'source', 'defer_history'}:
                if params.get(key) not in (None, False, ''):
                    raise NotConnected()
            sid = await self.db.resolve_resume_session_id(sid)
            return await self.snapshot(sid, params)
        if method == 'session.activate':
            return await self.snapshot(sid, params)
        if method == 'session.info':
            return await self.info(sid)
        if method == 'session.usage':
            return await self.usage(sid)
        if method == 'session.history':
            messages = await self.history(sid)
            return {'count': len(messages), 'messages': messages}
        if method == 'session.title':
            if 'title' not in params:
                return {'title': await self.db.get_session_title(sid) or '', 'session_key': sid}
            title = params['title']
            if not isinstance(title, str) or not title.strip():
                raise RpcError('title required', code=4021)
            try:
                await self.db.set_session_title(sid, title)
            except ValueError as exc:
                # PR-3 fixed validation messages, never a DB/driver exception.
                raise RpcError(str(exc), code=4022) from None
            return {'title': await self.db.get_session_title(sid) or '', 'pending': False}
        if method == 'session.detach':
            token = params.get('subscription_id')
            if not string(token):
                raise RpcError('invalid_params')
            if params.get('end_reason'):
                # Last-viewer durable finalization needs host-wide subscriptions.
                raise NotConnected()
            detached = await self.connection.detach(sid, token)
            return {'session_id': sid, 'subscription_id': token, 'detached': detached}
        # H/methods_session.py:2177-2182: close interrupts, keeps stored history.
        await self.execution.interrupt(sid, {'session_id': sid}, mode='legacy')
        closed = await self.connection.detach(sid)
        return {'closed': closed, 'messages': []}

    async def usage(self, sid):
        # H/methods_session.py:1448; contracts/common.py:28-57. The host saves
        # actual native counters in control.py:193-200, not reserved budgets.
        lineage = await self.db.get_compression_lineage(sid) or [sid]
        totals = dict.fromkeys(('input', 'output', 'total', 'calls'), 0)
        for ancestor in lineage:
            runs = await self.execution.store.runs.find({
                'principal_id': self.execution.principal_id, 'session_id': ancestor
            }).to_list(None)
            if not runs and await self.db.get_messages(ancestor, include_compacted=True):
                raise NotConnected()  # imported history has no host usage evidence
            for run in runs:
                usage = observed_run_usage(run)
                if usage is None:
                    raise NotConnected()
                for field, value in usage.items():
                    totals[field] += value
        return totals

    async def create(self, params):
        # H/contracts/sessions.py:118-147; canonical_sessions.py:23-61.
        supported = {'source', 'title', 'request_id' if self.mode == 'authority' else 'idempotency_key'}
        unavailable = ({'cwd', 'row_cwd', 'model', 'provider', 'base_url', 'api_key', 'toolsets',
            'reasoning', 'max_turns', 'ignore_rules', 'yolo', 'safe_mode', 'ignore_user_config',
            'skills', 'checkpoints', 'accept_hooks', 'pass_session_id', 'editor', 'hidden',
            'follow_profile_config', 'service_tier', 'cwd_explicit', 'run_budget', 'verbose', 'client_env'}
            if self.mode == 'authority' else {'cols', 'cwd', 'cwd_explicit', 'messages',
            'parent_session_id', 'model', 'provider', 'reasoning_effort', 'fast', 'service_tier',
            'close_on_disconnect', 'hidden', 'room_plumbing', 'follow_profile_config'})
        validate(params, supported | unavailable, mode=self.mode)
        if params.keys() & unavailable:
            raise NotConnected()  # known contract, no host implementation/effect
        # H/agent_factory.py:27-38: without Desktop env, legacy defaults to tui.
        source = params.get('source', 'cli') if self.mode == 'authority' else params.get('source') or 'tui'
        if not isinstance(source, str) or (self.mode == 'authority' and
                source not in {'cli', 'tui', 'gui', 'acp', 'tool', 'oneshot'}):
            raise RpcError('invalid_params', code=4000 if self.mode == 'legacy' else 4001)
        title = params.get('title')
        if title is not None and (not isinstance(title, str) or not title.strip() or len(title) > 100):
            raise RpcError('invalid_params', code=4000 if self.mode == 'legacy' else 4001)
        identity = params.get('request_id' if self.mode == 'authority' else 'idempotency_key')
        if identity is not None and not string(identity):
            raise RpcError('invalid_params')
        # Host-owned deterministic ID: no upstream equivalent → PB:575,664.
        sid = (hashlib.sha256((self.execution.principal_id + '\0' + identity).encode()).hexdigest()
               if identity else uuid.uuid4().hex)
        await self.db.create_session(sid, source, profile_name=self.execution.profile_id)
        if title is not None and not await self.db.get_session_title(sid):
            try:
                await self.db.set_session_title(sid, title)
            except ValueError as exc:
                raise RpcError(str(exc), code=4022) from None
        result = await self.snapshot(sid, {})
        if self.mode == 'legacy':
            result = {key: result[key] for key in ('session_id', 'stored_session_id', 'message_count', 'messages', 'info')}
        return result

    async def listing(self, method, params):
        validate(params, () if method == 'session.most_recent' else
                 ('scope', 'limit', 'title', 'include_hidden') if self.mode == 'authority' else
                 ('limit', 'title', 'include_hidden'), mode=self.mode)
        scope = params.get('scope', 'live')
        if scope not in ('live', 'stored'):
            raise RpcError('invalid_params')
        # There is no process-owned agent inventory in this host.
        if self.mode == 'authority' and scope == 'live':
            raise NotConnected()
        limit = params.get('limit') or 200
        if type(limit) is not int or limit < 0:
            raise RpcError('invalid_params', code=4000 if self.mode == 'legacy' else 4001)
        title = params.get('title')
        if title is not None and not isinstance(title, str):
            raise RpcError('invalid_params')
        rows = await self.db.list_sessions_rich(limit=max(limit * 2, 200) if not title else -1,
            include_hidden=flag(params, 'include_hidden', self.mode), order_by_last_active=True)
        # H/methods_session.py:124-149; hermes_state_sessions.py:239.
        rows = [r for r in rows if (r.get('source') or '').strip().lower() not in
                ('kanban', 'tool', 'oneshot')]
        if title:
            rows = [r for r in rows if r.get('title') == title]
        rows = rows[:limit]
        if method == 'session.most_recent':
            rows = [r for r in rows if r.get('source') != 'unknown']
            return ({'session_id': rows[0]['id'], 'title': rows[0].get('title') or '',
                     'started_at': rows[0]['started_at'], 'source': rows[0]['source']}
                    if rows else {'session_id': None})
        values = []
        for row in rows:
            value = await self.public_list_row(row)
            if self.mode == 'authority':
                value.update(session_id=row['id'], running=bool(await self.execution._active(row['id'])))
            if row.get('_lineage_root_id') and self.mode == 'legacy':
                value['_lineage_root_id'] = row['_lineage_root_id']
            values.append(value)
        return {'sessions': values, **({'scope': scope} if self.mode == 'authority' else {})}

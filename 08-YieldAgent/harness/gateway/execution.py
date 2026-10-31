"""Unconnected async Hermes execution adapters (5894e750; PB:664,702).

Verified principal_id and profile_id are injected by the host; no authentication,
transport, credentials, tool grants, or model construction happens here.
"""
import asyncio
import hashlib
import json
import time

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from harness.store import Conflict
from .events import SessionEvents, verified_answer
from .pending_controls import PendingControls
from .pending_wire import PendingControlError


class RpcError(Exception):
    """Typed wire failure; unexpected storage/programming exceptions propagate."""
    def __init__(self, reason, *, code=4001, data=None):
        # H/gateway/session_controls.py:321-326.
        self.error = {'code': code, 'message': reason}
        if code == 4001:
            self.error['data'] = {'reason': reason, **(data or {})}
        elif data is not None:
            self.error['data'] = data
        super().__init__(reason)


def unsupported(feature):
    # PB:644: do not turn an unsupported action into an admitted plain prompt.
    raise RpcError('unsupported', code=-32004, data={
        'reason': 'unsupported', 'feature': feature, 'upstream': '5894e750'})


def fields(params, required, optional=()):
    if not isinstance(params, dict) or not set(required) <= params.keys() or params.keys() - set(required) - set(optional):
        raise RpcError('invalid_params')


def string(value):
    return isinstance(value, str) and bool(value)


def submit_values(params):
    # H/gateway/session_controls.py:750-771; canonical_sessions.py:205-237.
    allowed = ('submission_id', 'input_id', 'queued', 'attachments', 'finite', 'unattended',
               'surface', 'voice_context', 'interrupted', 'voice_turn', 'display_kind',
               'title_preview', 'turn_author', 'api_text', 'model_override', 'clarify_batch')
    fields(params, ('session_id', 'text'), allowed)
    identity = params.get('submission_id') or params.get('input_id')
    if not string(identity) or not isinstance(params['text'], str) or not params['text'].strip():
        raise RpcError('invalid_params')
    for key in ('queued', 'finite', 'unattended', 'interrupted', 'voice_turn', 'clarify_batch'):
        if key in params and type(params[key]) is not bool:
            raise RpcError('invalid_params')
    for key in allowed:
        if key not in ('submission_id', 'input_id', 'clarify_batch') and params.get(key) not in (None, False, '', []):
            unsupported('prompt.submit.' + key)
    return identity, params['text']


def receipt_value(row, run, profile_id, session_id):
    # H/contracts/canonical.py:29-51. run_id is host extension → PB:503,702.
    status, outcome = 'queued', None
    if run.get('status') == 'unknown':
        status = 'unknown'
    elif not run.get('active'):
        status = 'terminal'
        outcome = {'completed': 'completed', 'cancelled': 'interrupted'}.get(run['status'], 'failed')
    elif run.get('status') != 'created':
        status = 'started'
    return {'admission_id': row['admission_id'], 'ref': {'profile_id': profile_id, 'session_id': session_id},
            'sequence': row['sequence'], 'status': status, 'outcome': outcome,
            'authority_epoch': row['authority_epoch'], 'execution_generation': run['epoch'] if status != 'queued' else None,
            'run_id': run['run_id']}


class Execution:
    def __init__(self, controller, principal_id, *, profile_id):
        if not string(principal_id) or not principal_id.strip() or not string(profile_id):
            raise ValueError('Verified principal_id and server-owned profile_id required')
        self.controller, self.store = controller, controller.store
        self.principal_id, self.profile_id = principal_id, profile_id
        self.receipts = self.store.db.gateway_receipts
        self.counters = self.store.db.gateway_receipt_counters
        self.events = SessionEvents(self.store, principal_id)
        self.pending = PendingControls(self.store, principal_id)

    def _scope(self, session_id, **extra):
        return {'principal_id': self.principal_id, 'session_id': session_id, **extra}

    async def _session(self, session_id, params=None):
        if not string(session_id) or (params is not None and params.get('session_id', session_id) != session_id):
            raise RpcError('invalid_params')
        row = await self.store.db.gateway_sessions.find_one({
            'principal_id': self.principal_id, 'id': session_id, 'deleted': {'$ne': True}})
        if not row:
            raise RpcError('not_found')
        return row

    async def _active(self, session_id):
        return await self.store.runs.find_one(self._scope(session_id, active=True))

    async def _receipt_for_run(self, session_id, run):
        key = self._scope(session_id, request_id=run['request_id'])
        row = await self.receipts.find_one(key)
        if row is None:
            # Allocation can leave unused admission numbers, never duplicate numbers.
            # The run is already the admission authority; a crash here is repaired by
            # request_id on submit or deterministic admission_id on receipt (PB:702).
            try:
                counter = await self.counters.find_one_and_update(self._scope(session_id),
                    {'$inc': {'sequence': 1}}, upsert=True, return_document=ReturnDocument.AFTER)
            except DuplicateKeyError:
                counter = await self.counters.find_one_and_update(self._scope(session_id),
                    {'$inc': {'sequence': 1}}, return_document=ReturnDocument.AFTER)
            row = {**key, 'admission_id': run['run_id'], 'run_id': run['run_id'],
                   'sequence': counter['sequence'], 'authority_epoch': run['epoch'],
                   'query_digest': hashlib.sha256(run['query'].encode()).hexdigest()}
            try:
                await self.receipts.insert_one(row)
            except DuplicateKeyError:
                row = await self.receipts.find_one(key)
        value = receipt_value(row, run, self.profile_id, session_id)
        # Immutable receipt identity; current state has ONE durable authority:
        # the referenced Mongo run. Never store a second, potentially stale state.
        return value

    async def submit(self, session_id, params):
        await self._session(session_id, params)
        identity, text = submit_values(params)
        key = self._scope(session_id, request_id=identity)
        # A same-ID retry can repair a missing receipt without launching again.
        run = await self.store.runs.find_one(key)
        if run is not None:
            if run['query'] != text:
                raise RpcError('admission_conflict')
            if run['status'] != 'created':
                return await self._receipt_for_run(session_id, run)
        # RunController uses a nonwaiting distributed session lock. Contending
        # callers retry that admission, not a new ID; active unique index decides.
        deadline = time.monotonic() + 5
        for attempt in range(100):
            try:
                run = await self.controller.start(self.principal_id, session_id, identity, text)
                run = await self.store.get_run(self.principal_id, run['run_id'])
                return await self._receipt_for_run(session_id, run)
            except Conflict:
                run = await self.store.runs.find_one(key)
                if run is None:
                    # The admission may become visible between these two reads.
                    # Only a different identity proves the session is busy.
                    run = await self._active(session_id)
                    if run is not None and run['request_id'] != identity:
                        raise RpcError('session_busy') from None
                if run is not None:
                    if run['query'] != text:
                        raise RpcError('admission_conflict') from None
                    if run['status'] != 'created':
                        return await self._receipt_for_run(session_id, run)
                remaining = deadline - time.monotonic()
                if attempt == 99 or remaining <= 0:
                    # Unresolved storage contention is not another admission.
                    # H/gateway/session_controls.py:329-331 retryable wire error;
                    # host retry budget (PB:702): at most 100 attempts / 5 seconds.
                    raise RpcError('storage_unavailable', code=5001,
                        data={'reason': 'storage_unavailable'}) from None
                await asyncio.sleep(min(.05, remaining))

    async def receipt(self, session_id, params):
        await self._session(session_id, params)
        fields(params, ('session_id', 'admission_id'), ('include_result',))
        if not string(params['admission_id']) or ('include_result' in params and params['include_result'] is not None and type(params['include_result']) is not bool):
            raise RpcError('invalid_params')
        run = await self.store.runs.find_one(self._scope(session_id, run_id=params['admission_id']))
        if not run:
            raise RpcError('not_found')
        value = await self._receipt_for_run(session_id, run)
        # include_result remains optional. No raw native_messages/answer/usage dump.
        if params.get('include_result') is True and (answer := verified_answer(run)) is not None:
            value['result'] = {'final_response': answer}
        return value

    async def cancel(self, session_id, params):
        fields(params, ('session_id', 'admission_id'))
        value = await self.receipt(session_id, params)
        # H/session_controls.py:803-817: started is unaffected, queued is retired.
        if value['status'] == 'queued':
            run = await self.store.get_run(self.principal_id, value['run_id'])
            if run['status'] == 'created':
                await self._cancel_run(run, statuses=['created'])
        return await self.receipt(session_id, params)

    async def _stale_generation(self, session_id):
        # H/session_controls.py:782-786: return fresh owner fences, not the
        # rejected request or a run snapshot whose cancellation CAS just failed.
        current = await self.handle(session_id)
        return RpcError('stale_generation', data={
            'execution_generation': current['execution_generation']})

    async def _cancel_run(self, run, *, statuses=None):
        # Controller.cancel is not generation-fenced. Claim this exact run/epoch
        # before calling it; acquire() cannot restart a cancelling run. PB:698.
        changed = await self.store.runs.update_one(self._scope(run['session_id'],
            run_id=run['run_id'], epoch=run['epoch'], active=True,
            status={'$in': statuses or ['created', 'running', 'unknown', 'cancelling', 'waiting_user']}),
            {'$set': {'status': 'cancelling'}})
        if not changed.matched_count:
            raise await self._stale_generation(run['session_id'])
        await self.controller.cancel(self.principal_id, run['run_id'])

    async def recover_unknown(self, session_id):
        """Explicit host recovery hook; expired running lease only, never a live owner.

        Original owner restart → unknown: hermes_state_runtime.py:442-457.
        Host adaptation PB:702,715: fence the run so ordinary recovery cannot
        relaunch uncertain side effects. The host must call this BEFORE recover().
        """
        await self._session(session_id)
        run = await self._active(session_id)
        if run and run['status'] == 'running' and run.get('lease_until', 0) < time.time():
            await self.store.runs.update_one(self._scope(session_id, run_id=run['run_id'],
                epoch=run['epoch'], active=True, status='running', lease_until={'$lt': time.time()}),
                {'$set': {'status': 'unknown'}})
        return await self.snapshot(session_id)

    async def resolve_unknown(self, session_id, params):
        fields(params, ('session_id', 'admission_id', 'execution_generation'))
        generation = params['execution_generation']
        value = await self.receipt(session_id, {k: params[k] for k in ('session_id', 'admission_id')})
        if type(generation) is not int or generation != value['execution_generation']:
            raise await self._stale_generation(session_id)
        if value['status'] == 'terminal' and value['outcome'] == 'interrupted':
            return value
        # H/hermes_state_runtime.py:651-675: terminal interrupted, never requeue.
        run = await self.store.get_run(self.principal_id, value['run_id'])
        if run['epoch'] != generation:
            raise await self._stale_generation(session_id)
        # Persist the resolution intent with the execution fence. Retries can
        # finish a cancelled await/process death after this write, without IDs.
        await self.store.runs.update_one(self._scope(session_id, run_id=run['run_id'],
            epoch=generation, active=True, status='unknown'), {'$set': {
                'status': 'cancelling', 'gateway_unknown_resolution': generation}})
        run = await self.store.get_run(self.principal_id, run['run_id'])
        if run['status'] == 'cancelled' and run['epoch'] == generation:
            return await self.receipt(session_id, {k: params[k] for k in ('session_id', 'admission_id')})
        if run['status'] != 'cancelling' or run.get('gateway_unknown_resolution') != generation:
            raise await self._stale_generation(session_id)
        await self.controller.cancel(self.principal_id, run['run_id'])
        return await self.receipt(session_id, {k: params[k] for k in ('session_id', 'admission_id')})

    async def interrupt(self, session_id, params, *, mode='authority', expected_run_id=None):
        await self._session(session_id, params)
        fields(params, ('session_id',), ('execution_generation',) if mode == 'authority' else ())
        run = await self._active(session_id)
        if mode == 'authority':
            generation = params.get('execution_generation')
            if type(generation) is not int or generation < 0:
                raise RpcError('invalid_params')
            current_generation = run['epoch'] if run else (await self.handle(session_id))['execution_generation']
            if generation != current_generation:
                raise await self._stale_generation(session_id)
            # PR-6b requires run-local epochs, unlike Hermes session generations.
            # Ambiguous across runs: require a host-bound snapshot run identity.
            # Never accept that binding from arbitrary RPC params.
            if run and expected_run_id is None and await self.store.runs.count_documents(
                    self._scope(session_id)) > 1:
                raise await self._stale_generation(session_id)
            if run and expected_run_id is not None and expected_run_id != run['run_id']:
                raise await self._stale_generation(session_id)
        if run:
            await self._cancel_run(run)
            await self.pending.cancel(run_id=run['run_id'], reason='interrupt')
            await self._publish_pending()
        if mode == 'legacy':
            return {'status': 'interrupted' if run else 'not_interrupted'}
        return await self.handle(session_id)

    async def handle(self, session_id):
        session = await self._session(session_id)
        run = await self._active(session_id)
        latest = run or await self.store.runs.find_one(self._scope(session_id), sort=[('created_at', -1), ('_id', -1)])
        mark = await self.events.watermark(session_id)
        return {'ref': {'profile_id': self.profile_id, 'session_id': session_id},
                'instance_id': mark['replay_epoch'], 'authority_epoch': latest['epoch'] if latest else 0,
                'revision': session.get('runtime_revision', 0),
                'execution_generation': latest['epoch'] if latest else session.get('runtime_generation', 0),
                'execution_state': 'unknown' if run and run['status'] == 'unknown' else 'running' if run else 'idle'}

    async def _current_prompts(self, session_id):
        # Admission shares this lock. A new lower run-local epoch cannot appear
        # while PR-6a snapshot retires older generations across the session.
        try:
            async with self.store.session_lock(self.principal_id, session_id):
                return await self._prompts_locked(session_id)
        except Conflict:
            raise RpcError('session_busy') from None

    async def _prompts_locked(self, session_id):
        from harness.native_hermes import pending_snapshot, publish_stored_prompt_events
        # Epoch is RUN scoped: only the row's OWN terminal/advanced run proves
        # staleness. An earlier active-run snapshot cannot prove a new row stale.
        rows = await self.pending.pending_prompts.find(self._scope(session_id, status='pending')).to_list(None)
        for row in rows:
            owner = await self.store.runs.find_one(self._scope(session_id, run_id=row.get('run_id')))
            if not owner or not owner['active'] or owner['status'] == 'unknown' or row['execution_generation'] < owner['epoch']:
                await self.pending.cancel(row['prompt_id'], reason='generation_changed')
        await publish_stored_prompt_events(self.store, self.pending)
        run = await self.store.runs.find_one(self._scope(session_id, active=True))
        if not run or run['status'] not in ('running', 'created'):
            return []
        return list(await pending_snapshot(self.store, self.principal_id, session_id, run['epoch']))

    async def snapshot(self, session_id):
        """Host resume fragment; not a fabricated full session.resume response."""
        await self._session(session_id)
        prompts = await self._current_prompts(session_id)
        run = await self._active(session_id)
        mark = await self.events.ingest(session_id)
        return {'session_id': session_id, 'active_run': ({k: run[k] for k in ('run_id', 'status', 'epoch')} if run else None),
                'prompts': prompts, **mark, 'epoch': mark['replay_epoch']}

    async def status(self, session_id, params):
        await self._session(session_id, params)
        fields(params, ('session_id',))
        state = await self.snapshot(session_id)
        # H/contracts/sessions.py:482: output string. Host text → PB:664.
        return {'output': json.dumps(state, ensure_ascii=False)}

    async def _publish_pending(self):
        from harness.native_hermes import publish_stored_prompt_events
        await publish_stored_prompt_events(self.store, self.pending)

    async def _pending_row(self, session_id, prompt_id, generation=None):
        run = await self._active(session_id)
        if not run or run['status'] != 'running':
            return None
        if generation is not None and (type(generation) is not int or generation != run['epoch']):
            return None
        row = await self.pending.pending_prompts.find_one(self._scope(session_id,
            run_id=run['run_id'], execution_generation=run['epoch'],
            **{'$or': [{'prompt_id': prompt_id}, {'authority_ids': prompt_id}]}))
        return row

    async def respond(self, session_id, params, *, kind):
        await self._session(session_id, params)
        field = 'answer' if kind == 'clarify' else 'choice'
        fields(params, ('session_id', 'execution_generation', 'prompt_id', field))
        if not string(params['prompt_id']) or type(params['execution_generation']) is not int or params['execution_generation'] < 0 or not isinstance(params[field], str):
            raise RpcError('invalid_params')
        if not await self._pending_row(session_id, params['prompt_id'], params['execution_generation']):
            return {'status': 'already_resolved', 'prompt_id': params['prompt_id']}
        try:
            result = await self.pending.respond(session_id, params['execution_generation'], params['prompt_id'], {field: params[field]}, kind)
        except PendingControlError:
            raise RpcError('invalid_params') from None
        await self._publish_pending()
        return result

    async def request_answer(self, session_id, params):
        await self._session(session_id)
        fields(params, ('id', 'result'))
        if not string(params['id']) or not isinstance(params['result'], dict):
            raise RpcError('id and an object result required', code=4002)
        row = await self._pending_row(session_id, params['id'])
        if row is None:
            return {'status': 'expired'}
        try:
            result = await self.pending.respond_legacy(session_id, row['execution_generation'], row['prompt_id'], params['result'], row['kind'])
        except PendingControlError:
            raise RpcError('invalid_params') from None
        await self._publish_pending()
        return {'status': 'ok' if result['status'] == 'resolved' else 'expired'}

    async def approval_pending(self, session_id, params):
        await self._session(session_id, params)
        fields(params, ('session_id',))
        prompts = await self._current_prompts(session_id)
        approvals = []
        for prompt in prompts:
            if prompt['kind'] == 'approval':
                frame = await self.pending.legacy_request(session_id, prompt['execution_generation'], prompt['prompt_id'])
                if frame:
                    approvals.append({k: v for k, v in frame['params'].items() if k != 'session_id'})
        return {'approvals': approvals}

    async def approval_respond_legacy(self, session_id, params):
        await self._session(session_id, params)
        fields(params, ('session_id',), ('choice', 'all', 'request_id'))
        if 'all' in params and params['all'] is not None and type(params['all']) is not bool:
            raise RpcError('invalid_params')
        approvals = (await self.approval_pending(session_id, {'session_id': session_id}))['approvals']
        identity = params.get('request_id')
        selected = [p for p in approvals if identity is None or p['request_id'] == identity]
        if not params.get('all'):
            selected = selected[:1]
        resolved = 0
        for prompt in selected:
            reply = await self.request_answer(session_id, {'id': prompt['request_id'], 'result': {'choice': params.get('choice') or 'deny'}})
            resolved += reply['status'] == 'ok'
        return {'resolved': resolved}

    async def clarify_lock(self, session_id, params):
        await self._session(session_id)
        fields(params, ('request_id', 'question_id'), ('answer',))
        if not string(params['request_id']) or not string(params['question_id']):
            raise RpcError('request_id and question_id required', code=4002)
        answer = params.get('answer', '')
        if answer is not None and not isinstance(answer, str):
            answer = json.dumps(answer, ensure_ascii=False)
        while True:
            row = await self._pending_row(session_id, params['request_id'])
            if row is None:
                return {'status': 'expired'}
            row = await self.pending._active(session_id, row['execution_generation'], row['prompt_id'])
            if row is None:
                await self._publish_pending()
                return {'status': 'expired'}
            if row['kind'] != 'clarify' or params['question_id'] not in {q['qid'] for q in row['questions']}:
                raise RpcError('invalid_params')
            if isinstance(answer, str) and len(answer) > 16384:
                raise RpcError('invalid_params')
            answers = {**row['answers'], params['question_id']: answer}
            remaining = [q['qid'] for q in row['questions'] if q['qid'] not in answers]
            if not remaining:
                # Uses PR-6a CAS/finalization, not a parallel decision authority.
                won = await self.pending._finish(row, {'answers': answers, 'outcome': 'submitted'}, 'resolved', 'response')
            else:
                changed = await self.pending.pending_prompts.update_one(self._scope(session_id,
                    prompt_id=row['prompt_id'], status='pending', version=row['version'],
                    **{'$or': [{'expires_at': None}, {'expires_at': {'$gt': time.time()}}]}),
                    {'$set': {'answers': answers}, '$inc': {'version': 1}})
                won = bool(changed.modified_count)
                if won:
                    await self.pending._audit('response', row, bodies={'answer': answer},
                        audit_id=row['prompt_id'] + ':lock:' + str(row['version']))
            if won:
                await self._publish_pending()
                return {'status': 'ok', 'remaining': remaining}

    async def rpc(self, method, session_id, params, *, rid=None, mode='authority', expected_run_id=None):
        """Pure async dispatch/envelope boundary for PR-7b; no routes registered."""
        try:
            if mode not in ('authority', 'legacy'):
                raise RpcError('invalid_params')
            if not isinstance(params, dict):
                raise RpcError('invalid_params')
            if method == 'clarify.respond' and mode == 'authority':
                value = await self.respond(session_id, params, kind='clarify')
            elif method == 'approval.respond':
                value = (await self.respond(session_id, params, kind='approval') if mode == 'authority'
                         else await self.approval_respond_legacy(session_id, params))
            elif method == 'session.interrupt':
                value = await self.interrupt(session_id, params, mode=mode, expected_run_id=expected_run_id)
            elif method == 'session.events.since':
                await self._session(session_id, params)
                fields(params, ('session_id',), ('last_sequence', 'last_seen', 'replay_epoch') if mode == 'authority' else ('last_seen', 'replay_epoch'))
                sequence = params.get('last_sequence', params.get('last_seen', 0))
                epoch = params.get('replay_epoch')
                if type(sequence) is not int or sequence < 0 or (epoch is not None and not isinstance(epoch, str)):
                    raise RpcError('invalid_params')
                if mode == 'legacy':
                    prompts = await self._current_prompts(session_id)
                    open_requests = {}
                    for prompt in prompts:
                        row = await self._pending_row(session_id, prompt['prompt_id'], prompt['execution_generation'])
                        if row:
                            frame = await self.pending.legacy_request(session_id, row['execution_generation'], row['prompt_id'])
                            if frame:
                                open_requests[row['prompt_id']] = {k: frame[k] for k in ('id', 'method', 'params')}
                    value = await self.events.since_legacy(session_id, sequence)
                    value['open_requests'] = list(open_requests.values())
                else:
                    value = await self.events.since(session_id, epoch, sequence)
            else:
                handlers = {'prompt.submit': self.submit, 'prompt.receipt': self.receipt,
                    'prompt.cancel': self.cancel, 'prompt.resolve_unknown': self.resolve_unknown,
                    'session.status': self.status, 'clarify.lock': self.clarify_lock,
                    'approval.pending': self.approval_pending, 'request.answer': self.request_answer}
                if method not in handlers:
                    raise RpcError('Method not found', code=-32601)
                if mode == 'legacy' and method.startswith('prompt.'):
                    # Legacy has no submission identity or receipt contract. Do not
                    # advertise exactly-once behavior by silently inventing an ID.
                    unsupported(method + '.legacy')
                value = await handlers[method](session_id, params)
            return {'jsonrpc': '2.0', 'id': rid, 'result': value}
        except RpcError as exc:
            return {'jsonrpc': '2.0', 'id': rid, 'error': exc.error}
        except PendingControlError:
            return {'jsonrpc': '2.0', 'id': rid, 'error': RpcError('invalid_params').error}

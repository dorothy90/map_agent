"""Host-owned pending storage. No transport publication or tool execution.

No upstream persistence counterpart → PB:391,607,663,731. Wire behavior is
H/gateway/session_pending_controls.py:16-101 (5894e750). All database operations
are principal-scoped. Polling supports another instance/process's responses.
Callers must supply the current host generation and independently authorize the
tool; a human decision is never a capability grant (PB:576).
"""
import asyncio
from copy import deepcopy
import time
import uuid
from weakref import WeakKeyDictionary

from pymongo.errors import DuplicateKeyError

from . import pending_wire as w


class PendingControls:
    # Session allowances deliberately die with the host store/process, never Mongo.
    _sessions = WeakKeyDictionary()
    POLL_SECONDS = .05

    def __init__(self, harness_store, principal_id):
        if not isinstance(principal_id, str) or not principal_id.strip():
            raise ValueError('A nonempty principal_id is required')
        self.harness = harness_store
        self._principal_id = principal_id
        self.pending_prompts = harness_store.db.pending_prompts
        self.approval_rules = harness_store.db.approval_rules
        self.approval_audit = harness_store.db.approval_audit
        self._session_allowances = self._sessions.setdefault(harness_store, set())
        self._wake = {}
        self._events = []

    @property
    def principal_id(self):
        return self._principal_id

    def _scope(self, **fields):
        return {**fields, 'principal_id': self.principal_id}

    async def setup(self):
        await self.pending_prompts.create_index([('principal_id', 1), ('prompt_id', 1)], unique=True)
        await self.pending_prompts.create_index([('principal_id', 1), ('authority_ids', 1)], unique=True)
        await self.pending_prompts.create_index([('principal_id', 1), ('session_id', 1), ('status', 1), ('execution_generation', 1)])
        await self.pending_prompts.create_index([('principal_id', 1), ('run_id', 1), ('status', 1)])
        await self.approval_rules.create_index([('principal_id', 1), ('rule_id', 1)], unique=True)
        await self.approval_rules.create_index([('principal_id', 1), ('tool_name', 1), ('rule_key', 1), ('revoked_at', 1)])
        await self.approval_rules.create_index([('principal_id', 1), ('tool_name', 1), ('rule_key', 1)], unique=True)
        await self.approval_audit.create_index([('principal_id', 1), ('audit_id', 1)], unique=True)
        await self.approval_audit.create_index([('principal_id', 1), ('ts', 1)])

    def drain_events(self):
        """Return local event descriptors. Publication/sequence/replay belongs to 6b."""
        events, self._events = self._events, []
        return events

    def _prompts(self, row):
        if row['kind'] == 'approval':
            return [deepcopy(row['prompt'])]
        return w.clarify_prompts(row['prompt_id'], row['execution_generation'], row['questions'],
                                 row['answers'], timeout_s=row['timeout_s'])

    async def _audit(self, event, row=None, *, decision=None, bodies=None, audit_id=None):
        row = row or {}
        record = w.audit_record(self.principal_id, event, kind=row.get('kind'),
            prompt_id=row.get('prompt_id'), run_id=row.get('run_id'), session_id=row.get('session_id'),
            tool_name=row.get('tool_name'), decision=decision, bodies=bodies)
        identity = audit_id or uuid.uuid4().hex
        try:
            await self.approval_audit.update_one(self._scope(audit_id=identity),
                {'$setOnInsert': {**record, 'audit_id': identity}}, upsert=True)
        except DuplicateKeyError:
            # Concurrent waiters may repair the same durable decision.
            if not await self.approval_audit.find_one(self._scope(audit_id=identity)):
                raise

    async def _insert(self, row, bodies, *, publish_request=True):
        await self.pending_prompts.insert_one(self._scope(**row))
        await self._audit('request', row, bodies=bodies, audit_id=row['prompt_id'] + ':request')
        if publish_request:
            for prompt in self._prompts(row):
                self._events.append(w.build_request_event(row['session_id'], prompt))
        return row['prompt_id']

    def _base(self, session_id, generation, prompt_id, run_id, kind, timeout_s):
        w.validate_generation(generation)
        w.validate_timeout(timeout_s)
        if not all(isinstance(x, str) and x for x in (session_id, prompt_id)):
            raise w.PendingControlError()
        now = time.time()
        return dict(prompt_id=prompt_id, session_id=session_id, execution_generation=generation,
                    run_id=run_id, kind=kind, status='pending', version=0, created_at=now,
                    timeout_s=timeout_s, expires_at=now + timeout_s if timeout_s is not None else None)

    async def register_clarify(self, session_id, generation, entry, *, timeout_s=300, run_id=None):
        """Entry: clarify_id/question/choices/multi_select, or {clarify_id, questions}.

        Default 300: H/tui_gateway/server.py:1094-1115. Explicit None is unlimited;
        zero is an already-exhausted host budget (PR-6a effective_timeout contract).
        """
        if not isinstance(entry, dict):
            entry = {key: getattr(entry, key) for key in ('clarify_id', 'question', 'choices', 'multi_select')}
        identity = entry['clarify_id']
        questions = entry.get('questions')
        if questions is None:
            questions = [{'qid': entry.get('qid', identity), 'question': entry['question'],
                          'choices': entry.get('choices'), 'multi_select': entry.get('multi_select', False)}]
        normalized = w.normalize_questions(questions)
        row = self._base(session_id, generation, identity, run_id, 'clarify', timeout_s)
        row.update(questions=normalized, answers={}, authority_ids=[
            w.question_prompt_id(identity, normalized, i) for i in range(len(normalized))])
        return await self._insert(row, {'question': '\n'.join(q['question'] for q in questions)})

    async def register(self, session_id, generation, data, *, timeout_s=300, run_id=None,
                       tool_allowed=False, effect=None):
        """Register an approval for an already-authorized effectful synthetic/caller tool.

        Callers check their tool allowlist before registration and again at invocation time.
        """
        tool = data.get('tool_name')
        if (tool_allowed is not True or effect not in ('artifact', 'persistent_write')
                or not isinstance(tool, str) or not tool
                or tool in w.SERVER_REQUEST_POLICY
                or tool in ('terminal', 'shell', 'file', 'browser', 'sudo', 'secret')):
            await self._audit('denied', {'kind': 'approval', 'session_id': session_id,
                'run_id': run_id, 'tool_name': tool}, decision='deny')
            raise PermissionError('Approval cannot grant a tool capability')
        # File-edit approval context is outside PB's disabled file-tool boundary.
        if 'edit' in data:
            raise PermissionError('File edit approvals are disabled')
        identity = data['request_id']
        prompt = w.approval_prompt(generation, data, timeout_s=timeout_s)
        row = self._base(session_id, generation, identity, run_id, 'approval', timeout_s)
        legacy = w.legacy_approval_request(session_id, identity, data)
        row.update(prompt=prompt, legacy_params=legacy['params'], authority_ids=[identity],
                   tool_name=tool, rule_key=w.rule_key(tool, data.get('description', '')))
        # Every invocation consults Mongo, even when there is a memory session grant.
        rule = await self.approval_rules.find_one(self._scope(tool_name=tool, rule_key=row['rule_key'], revoked_at=None))
        auto_rule = bool(rule and 'always' in prompt['choices'])
        auto_session = self._session_key(row) in self._session_allowances and 'session' in prompt['choices']
        await self._insert(row, {'command': data.get('command', ''), 'description': data.get('description', '')},
                           publish_request=not (auto_rule or auto_session))
        if auto_rule:
            await self._finish(row, {'choice': 'always'}, 'resolved', 'auto_allowed_by_rule', create_rule=False)
        elif auto_session:
            await self._finish(row, {'choice': 'session'}, 'resolved', 'auto_allowed_by_session', create_rule=False)
        return identity

    def _session_key(self, row):
        return (self.principal_id, row['session_id'], row['tool_name'], row['rule_key'])

    def clear_session(self, session_id):
        self._session_allowances.difference_update({key for key in self._session_allowances
                                                   if key[:2] == (self.principal_id, session_id)})

    async def _finalize(self, row):
        """Idempotently finish post-CAS work; wait/reconnect can repair an interrupted caller.

        No cross-collection transaction requirement (localhost Mongo may be standalone).
        A decision is consumable only after rule/audit persistence finishes.
        """
        identity, result = row['prompt_id'], row['result']
        choice = result.get('choice')
        if row['kind'] == 'approval' and row.get('create_rule') and choice == 'always':
            # One stable rule per principal/tool/condition. An old finalizer must
            # never resurrect a grant revoked AFTER this human decision.
            rule_id = str(uuid.uuid5(uuid.NAMESPACE_URL, row['rule_key']))
            try:
                await self.approval_rules.update_one(self._scope(rule_id=rule_id), {'$setOnInsert': self._scope(
                    rule_id=rule_id, tool_name=row['tool_name'], rule_key=row['rule_key'],
                    created_at=row['settled_at'], revoked_at=None)}, upsert=True)
            except DuplicateKeyError:
                if not await self.approval_rules.find_one(self._scope(rule_id=rule_id)):
                    raise
            await self.approval_rules.update_one(self._scope(rule_id=rule_id,
                revoked_at={'$type': 'number', '$lt': row['settled_at']}),
                {'$set': {'revoked_at': None, 'created_at': row['settled_at']}})
            await self._audit('rule_created', row, decision='always', audit_id=identity + ':rule_created')
        if row['kind'] == 'approval' and choice == 'session':
            self._session_allowances.add(self._session_key(row))
        bodies = {'result': result}
        if row['kind'] == 'clarify':
            bodies['answer'] = result['answers']
        await self._audit(row['resolution_event'], row, decision=choice or result.get('outcome'),
                          bodies=bodies, audit_id=identity + ':settled')
        if row['resolution_event'] == 'response' and choice == 'deny':
            await self._audit('denied', row, decision='deny', audit_id=identity + ':denied')
        await self.pending_prompts.update_one(self._scope(prompt_id=identity, status=row['status']),
                                              {'$set': {'finalized': True}})
        if identity in self._wake:
            self._wake[identity].set()

    async def _finish(self, row, result, status, event, *, create_rule=True, reason=None):
        fields = dict(status=status, result=result, resolution_event=event,
                      settled_at=time.time(), create_rule=create_rule, finalized=False)
        query = self._scope(prompt_id=row['prompt_id'], status='pending', version=row['version'])
        # An answer cannot win after the persisted deadline, even if validation took time.
        if event in ('response', 'auto_allowed_by_rule', 'auto_allowed_by_session'):
            query['$or'] = [{'expires_at': None}, {'expires_at': {'$gt': time.time()}}]
        changed = await self.pending_prompts.update_one(query, {'$set': fields, '$inc': {'version': 1}})
        if not changed.modified_count:
            return False
        for prompt in self._prompts(row):
            self._events.append(w.build_settled_event(row['session_id'], prompt))
        if status == 'cancelled' and row['kind'] == 'approval':
            self._events.append(w.build_cancelled_event(row['session_id'], row['session_id'],
                                                       reason or 'interrupt', [row['prompt_id']]))
        await self._finalize({**row, **fields})
        return True

    async def _expire(self, row, *, stale=False, reason=None):
        result = {'choice': 'deny'} if row['kind'] == 'approval' else {
            'answers': row['answers'], 'outcome': 'cancelled' if stale else 'timed_out'}
        return await self._finish(row, result, 'cancelled' if stale else 'timed_out',
                                  'cancelled' if stale else 'timed_out', create_rule=False, reason=reason)

    async def snapshot(self, session_id, generation):
        w.validate_generation(generation)
        rows = await self.pending_prompts.find(self._scope(session_id=session_id)).to_list(None)
        prompts = []
        for row in rows:
            if row['status'] != 'pending':
                if not row.get('finalized'):
                    await self._finalize(row)
                continue
            if row['execution_generation'] < generation:
                await self._expire(row, stale=True, reason='generation_changed')
            elif row['expires_at'] is not None and row['expires_at'] <= time.time():
                await self._expire(row)
            elif row['execution_generation'] == generation:
                prompts.extend(self._prompts(row))
        return tuple(prompts)

    async def _active(self, session_id, generation, prompt_id):
        w.validate_generation(generation)
        row = await self.pending_prompts.find_one(self._scope(session_id=session_id,
            authority_ids=prompt_id))
        if row is None:
            row = await self.pending_prompts.find_one(self._scope(session_id=session_id, prompt_id=prompt_id))
        if not row or row['status'] != 'pending':
            return None
        if row['execution_generation'] != generation:
            if row['execution_generation'] < generation:
                await self._expire(row, stale=True, reason='generation_changed')
            return None
        if row['expires_at'] is not None and row['expires_at'] <= time.time():
            await self._expire(row)
            return None
        return row

    async def respond(self, session_id, generation, prompt_id, response, kind='approval'):
        reply = {'status': 'already_resolved', 'prompt_id': prompt_id}
        while row := await self._active(session_id, generation, prompt_id):
            if row['kind'] != kind:
                raise w.PendingControlError()
            prompt = next((p for p in self._prompts(row) if p['prompt_id'] == prompt_id), None)
            if prompt is None:
                return reply
            value = w.validate_response(prompt, response, kind)
            if kind == 'approval':
                won = await self._finish(row, {'choice': value}, 'resolved', 'response')
            else:
                answer = w.answer_for_prompt(row['prompt_id'], row['questions'], prompt_id, value)
                answers = {**row['answers'], **answer}
                if len(answers) == len(row['questions']):
                    won = await self._finish(row, {'answers': answers, 'outcome': 'submitted'}, 'resolved', 'response')
                else:
                    changed = await self.pending_prompts.update_one(self._scope(prompt_id=row['prompt_id'],
                        status='pending', version=row['version'], **{'$or': [
                            {'expires_at': None}, {'expires_at': {'$gt': time.time()}}]}),
                        {'$set': {'answers': answers}, '$inc': {'version': 1}})
                    won = bool(changed.modified_count)
                    if won:
                        await self._audit('response', row, bodies={'answer': value},
                                          audit_id=row['prompt_id'] + ':answer:' + prompt_id)
                        self._events.append(w.build_settled_event(session_id, prompt))
            if won:
                return {**reply, 'status': 'resolved'}
        return reply

    async def legacy_request(self, session_id, generation, prompt_id):
        row = await self._active(session_id, generation, prompt_id)
        if row is None:
            return None
        if row['kind'] == 'approval':
            return w.legacy_approval_request(session_id, row['prompt_id'], row['legacy_params'])
        return w.legacy_clarify_request(session_id, row['prompt_id'], row['questions'],
                                        answers=row['answers'] or None)

    async def respond_legacy(self, session_id, generation, prompt_id, result, kind):
        reply = {'status': 'already_resolved', 'prompt_id': prompt_id}
        while row := await self._active(session_id, generation, prompt_id):
            if row['kind'] != kind:
                raise w.PendingControlError()
            parsed = (w.parse_approval_result(result, row['legacy_params']['choices']) if kind == 'approval'
                      else w.parse_clarify_result(result, row['questions'], row['answers']))
            # `all` is preserved as wire data, never grants another pending invocation.
            outcome = parsed.get('outcome')
            status = outcome if outcome in ('timed_out', 'cancelled') else 'resolved'
            event = status if status != 'resolved' else 'response'
            if await self._finish(row, parsed, status, event):
                return {**reply, 'status': 'resolved'}
        return reply

    async def wait(self, prompt_id, timeout=None):
        w.validate_timeout(timeout)
        deadline = time.monotonic() + timeout if timeout is not None else None
        wake = self._wake.setdefault(prompt_id, asyncio.Event())
        try:
            while True:
                wake.clear()
                row = await self.pending_prompts.find_one(self._scope(prompt_id=prompt_id))
                if row is None:
                    raise LookupError('Pending prompt not found')
                if row['status'] != 'pending':
                    if not row.get('finalized'):
                        await self._finalize(row)
                    return deepcopy(row['result'])
                remaining = self.POLL_SECONDS
                if row['expires_at'] is not None:
                    remaining = min(remaining, row['expires_at'] - time.time())
                if deadline is not None:
                    remaining = min(remaining, deadline - time.monotonic())
                if remaining <= 0:
                    await self._expire(row)
                    continue
                try:
                    await asyncio.wait_for(wake.wait(), remaining)
                except asyncio.TimeoutError:
                    pass
        finally:
            # Existing waiters retain their Event; polling is the cross-instance fallback.
            if self._wake.get(prompt_id) is wake:
                self._wake.pop(prompt_id, None)

    async def cancel(self, prompt_id=None, *, run_id=None, reason='interrupt'):
        if (prompt_id is None) == (run_id is None):
            raise w.PendingControlError()
        query = {'prompt_id': prompt_id} if prompt_id is not None else {'run_id': run_id}
        start = len(self._events)
        while rows := await self.pending_prompts.find(self._scope(status='pending', **query)).to_list(None):
            for row in rows:
                await self._expire(row, stale=True, reason=reason)
        return deepcopy(self._events[start:])

    async def list_rules(self):
        return await self.approval_rules.find(self._scope(revoked_at=None), {'_id': 0}).to_list(None)

    async def revoke_rule(self, rule_id):
        changed = await self.approval_rules.update_one(self._scope(rule_id=rule_id, revoked_at=None),
                                                       {'$set': {'revoked_at': time.time()}})
        if not changed.modified_count:
            return False
        row = await self.approval_rules.find_one(self._scope(rule_id=rule_id))
        await self._audit('rule_revoked', {**row, 'kind': 'approval'}, decision='deny',
                          audit_id=rule_id + ':revoked:' + str(row['revoked_at']))
        return True

    async def list_audit(self):
        return await self.approval_audit.find(self._scope(), {'_id': 0}).sort('ts', 1).to_list(None)

    async def decline_server_request(self, request_name, params):
        result = w.decline_server_request(request_name, params)
        if result is not None:
            # Only the method name is recorded; params are deliberately not inspected.
            await self._audit('declined_server_request', {'kind': request_name}, decision='deny')
        return result

"""Mongo session replay, Hermes 5894e750 event_replay.py:1-198 / session_events.py:1-89.

Host persistence/CAS/pull ingestion: no upstream equivalent → PB:575,664,698-715.
Only projected run events enter this log. No transport or background worker.
"""
from copy import deepcopy
import hashlib
import json
import time
import uuid
from urllib.parse import quote, urlencode

from pymongo.errors import DuplicateKeyError

from . import publication

# H/tui_gateway/event_replay.py:29-35. Bytes count frozen params WITHOUT seq.
_REPLAY_BUFFER_MAX = 512
_REPLAY_SESSIONS_MAX = 64
_REPLAY_BUFFER_BYTES_MAX = 4 * 1024 * 1024
_REPLAY_PROCESS_BYTES_MAX = 64 * 1024 * 1024


def event_frame(params):
    # H/tui_gateway/server.py:717-718; PB:503.
    return {'jsonrpc': '2.0', 'method': 'event', 'params': deepcopy(params)}


def replay_gap(session_id, epoch, latest_seq):
    # H/gateway/session_events.py:60-63: deliberately NOT stamped/retained.
    return event_frame({'type': 'session.replay_gap', 'session_id': session_id,
                        'payload': {'replay_epoch': epoch, 'latest_seq': latest_seq}})


def new_log(principal_id, session_id):
    return {'principal_id': principal_id, 'session_id': session_id, 'version': 0,
            'epoch': uuid.uuid4().hex, 'seq': 0, 'evicted_through': 0, 'ring': [],
            'bytes': 0, 'opened_at': None, 'forgotten': False, 'cursors': {}}


def retain(log, params):
    """Pure original ring transition; caller commits counter AND frozen ring atomically."""
    params = deepcopy(params)
    params.pop('seq', None)
    params['replay_epoch'] = log['epoch']
    frozen = json.dumps(params, ensure_ascii=False, separators=(',', ':')).encode('utf-8', errors='surrogatepass')
    log['seq'] += 1
    if log['forgotten']:
        log['evicted_through'] = log['seq']
        return
    if log['opened_at'] is None:
        log['opened_at'] = time.time_ns()
    size = len(frozen)
    if size > _REPLAY_BUFFER_BYTES_MAX or size > _REPLAY_PROCESS_BYTES_MAX:
        log['evicted_through'] = log['seq']
        return
    log['ring'].append({'seq': log['seq'], 'frozen': frozen, 'size': size})
    log['bytes'] += size
    while len(log['ring']) > _REPLAY_BUFFER_MAX or log['bytes'] > _REPLAY_BUFFER_BYTES_MAX:
        evict_one(log)


def evict_one(log):
    item = log['ring'].pop(0)
    log['bytes'] -= item['size']
    log['evicted_through'] = max(log['evicted_through'], item['seq'])


def drop_ring(log):
    log.update(ring=[], bytes=0, opened_at=None, evicted_through=log['seq'])


def replay(log, epoch, after_seq):
    # H/gateway/session_events.py:77-89 (authority); no envelopes in events.
    if type(after_seq) is not int or after_seq < 0 or (epoch is not None and not isinstance(epoch, str)):
        raise ValueError('invalid_params')
    missing = epoch != log['epoch'] or after_seq > log['seq'] or after_seq < log['evicted_through']
    events = [] if missing else [
        {**json.loads(bytes(item['frozen']).decode('utf-8', errors='surrogatepass')), 'seq': item['seq']}
        for item in log['ring'] if item['seq'] > after_seq]
    return {'events': events, 'latest_seq': log['seq'], 'last_sequence': log['seq'],
            'epoch': log['epoch'], 'replay_epoch': log['epoch'], 'truncated': missing,
            'snapshot_required': missing, 'count': len(events)}


def verified_answer(run):
    """Reuse PR-5 settle, with host-saved verification evidence, never a raw completion.

    native_hermes.py:485-500 saves candidate digest/verdict; control.py:193-203
    saves answer_text separately from rendered tables. PB:639 requires this fence.
    """
    if run.get('active') or run.get('status') != 'completed':
        return None
    reviews = [o for o in run.get('observations', []) if o.get('tool_name') == 'verify_answer']
    reports = [{'verdict': o.get('scope', {}).get('verdict', 'unknown'), 'issues': []} for o in reviews]
    digests = [o.get('validated_arguments', {}).get('candidate_sha256') for o in reviews]
    answer = run.get('answer_text')
    decision = publication.settle({'completed': True, 'final_response': answer}, reports, digests)
    return decision['answer_text'] if decision['status'] == 'completed' else None


def verified_answer_with_artifacts(run):
    """Append stored display deliverables only after the answer passes verification."""
    answer = verified_answer(run)
    if answer is None:
        return None
    attachments = []
    seen = set()
    query = urlencode({'session_id': run.get('session_id', '')})
    for observation in run.get('observations', []):
        for artifact in observation.get('artifact_refs', []):
            artifact_id = artifact.get('artifact_id')
            mime = artifact.get('mime')
            if not isinstance(artifact_id, str) or not artifact_id or mime not in ('image/png', 'text/html'):
                continue
            key = (artifact_id, mime)
            if key in seen:
                continue
            seen.add(key)
            title = str(artifact.get('title') or ('Image' if mime == 'image/png' else 'HTML report'))
            title = title.replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]').replace('\n', ' ')
            url = f"/harness/artifacts/{quote(artifact_id, safe='')}?{query}"
            attachments.append(f'![{title}]({url})' if mime == 'image/png' else f'[{title}]({url})')
    return '\n\n'.join([answer, *attachments])


def project_run_event(run, event):
    """Additional host projection (existing publication.py has no event projector).

    No original equivalent → PB:503-505,639. Unknown events fail closed.
    Internal tool lifecycle names are translated to the Hermes wire contract;
    run_finished becomes message.complete only for the PR-5 verified final answer.
    Model-authored complete is withheld.
    """
    kind, payload = event['type'], event.get('payload') or {}
    if kind in ('message.delta', 'message.interim', 'message.complete', 'commentary'):
        return None
    if kind == 'run_finished':
        answer = verified_answer_with_artifacts(run)
        if answer is not None:
            return {'type': 'message.complete', 'payload': {'text': answer}}
        return {'type': kind, 'payload': {'status': run.get('status')}}
    if kind in ('reasoning.delta', 'reasoning.available', 'thinking.delta', 'status.update'):
        return {'type': kind, 'payload': {'kind': 'status', 'text': '', 'status': 'running'}}
    if kind in ('reasoning', 'thinking', 'agent.print', 'message.start', 'progress', 'run_started'):
        return {'type': kind, 'payload': {'status': 'running'}}
    if kind == 'tool_started':
        tool_id = payload.get('tool_id') or payload.get('invocation_id')
        projected = {'tool_id': tool_id, 'name': payload.get('name')}
        for key in ('args', 'context'):
            if key in payload:
                projected[key] = deepcopy(payload[key])
        return {'type': 'tool.start', 'payload': projected}
    if kind == 'tool_finished':
        tool_id = payload.get('tool_id') or payload.get('invocation_id')
        projected = {'tool_id': tool_id, 'name': payload.get('name')}
        for key in ('args', 'result', 'summary'):
            if key in payload:
                projected[key] = deepcopy(payload[key])
        duration = payload.get('duration_s', payload.get('elapsed_seconds'))
        if duration is not None:
            projected['duration_s'] = duration
        return {'type': 'tool.complete', 'payload': projected}
    fields = {
        'tool.start': ('tool_id', 'name', 'args', 'context'),
        'tool.complete': ('tool_id', 'name', 'args', 'result', 'summary', 'duration_s'),
        'tool.generating': ('name',),
        'tool.progress': ('tool_id', 'name', 'progress', 'total', 'percent', 'message'),
        'session.title': ('session_id', 'title'),
        'session.resume_progress': ('phase', 'status', 'message_count'),
        'session.reclaimed': ('session_id', 'stored_session_id', 'reason'),
        'clarify.request': ('kind', 'prompt_id', 'execution_generation', 'question', 'choices', 'multi_select', 'timeout_s'),
        'approval.request': ('kind', 'prompt_id', 'execution_generation', 'command', 'description', 'choices', 'timeout_s'),
        'clarify.settled': ('prompt_id', 'execution_generation'),
        'approval.settled': ('prompt_id', 'execution_generation'),
        'approval.cancelled': ('session_id', 'stored_session_id', 'reason', 'cancelled_count', 'request_ids'),
        'yield.input_required': ('prompt_id', 'kind', 'generation', 'goal_revision'),
    }.get(kind)
    if fields is None:
        return None
    return {'type': kind, 'payload': {k: deepcopy(payload[k]) for k in fields if k in payload}}


class SessionEvents:
    def __init__(self, store, principal_id):
        if not isinstance(principal_id, str) or not principal_id.strip():
            raise ValueError('A nonempty principal_id is required')
        self.store, self.principal_id = store, principal_id
        self.logs = store.db.gateway_event_logs

    def _scope(self, session_id):
        return {'principal_id': self.principal_id, 'session_id': session_id}

    async def _owned(self, session_id):
        if not await self.store.db.gateway_sessions.find_one({
                'principal_id': self.principal_id, 'id': session_id, 'deleted': {'$ne': True}}):
            raise LookupError('not_found')

    async def _read(self, session_id):
        await self._owned(session_id)
        key = self._scope(session_id)
        row = await self.logs.find_one(key)
        if row is None:
            try:
                await self.logs.insert_one(new_log(self.principal_id, session_id))
            except DuplicateKeyError:
                pass
            row = await self.logs.find_one(key)
        return row

    async def _commit(self, before, after):
        # One Mongo atomic compare-and-swap is the counter, dedupe and publication
        # linearization point. No allocate-then-insert holes after a crash.
        after['version'] = before['version'] + 1
        result = await self.logs.replace_one({'_id': before['_id'], 'version': before['version']}, after)
        return bool(result.modified_count)

    async def ingest(self, session_id):
        await self._owned(session_id)
        runs = self.store.runs.find(self._scope(session_id)).sort([('created_at', 1), ('_id', 1)])
        async for run in runs:
            token = hashlib.sha256(run['run_id'].encode()).hexdigest()
            while True:
                before = await self._read(session_id)
                after = deepcopy(before)
                cursor = after['cursors'].get(token, {'sequence': 0, 'event_ids': []})
                seen = set(cursor['event_ids'])
                fresh = sorted((e for e in run.get('events', []) if e['sequence'] > cursor['sequence']), key=lambda e: e['sequence'])
                if not fresh:
                    break
                for event in fresh:
                    projected = project_run_event(run, event) if event['event_id'] not in seen else None
                    if projected is not None:
                        projected['session_id'] = session_id
                        # H/gateway/session_events.py:38-45 stamps before freezing.
                        # Pending events already name their owning generation;
                        # preserve it even if this run has since been reacquired.
                        projected['execution_generation'] = projected['payload'].get(
                            'execution_generation', run['epoch'])
                        projected['payload'].update(run_id=run['run_id'], sequence=event['sequence'], event_id=event['event_id'])
                        retain(after, projected)
                    seen.add(event['event_id'])
                    after['cursors'][token] = {'sequence': event['sequence'], 'event_ids': sorted(seen)}
                if await self._commit(before, after):
                    break
            # Do not cross a still-active snapshot into the next run's events.
            if run.get('active'):
                break
        await self._trim_global()
        return await self.watermark(session_id)

    async def _trim_global(self):
        """FIFO original caps across the shared Mongo replay cache.

        Per-session bounds are atomic. Cross-document caps converge before a
        read returns; overlapping writers can transiently exceed the global cap.
        No transaction/replica-set assumption (PB:575; documented PR-7a limit).
        """
        while True:
            rows = await self.logs.find({'opened_at': {'$ne': None}}).sort([('opened_at', 1), ('_id', 1)]).to_list(None)
            total = sum(r['bytes'] for r in rows)
            if len(rows) <= _REPLAY_SESSIONS_MAX and total <= _REPLAY_PROCESS_BYTES_MAX:
                return
            row = rows[0] if len(rows) > _REPLAY_SESSIONS_MAX else next(r for r in rows if r['ring'])
            after = deepcopy(row)
            if len(rows) > _REPLAY_SESSIONS_MAX:
                drop_ring(after)
            else:
                while after['ring'] and total > _REPLAY_PROCESS_BYTES_MAX:
                    size = after['ring'][0]['size']
                    evict_one(after)
                    total -= size
            await self._commit(row, after)

    async def watermark(self, session_id):
        row = await self._read(session_id)
        return {'replay_epoch': row['epoch'], 'last_sequence': row['seq']}

    async def since(self, session_id, epoch, after_seq=0):
        # Validate before doing storage work.
        if type(after_seq) is not int or after_seq < 0 or (epoch is not None and not isinstance(epoch, str)):
            raise ValueError('invalid_params')
        await self.ingest(session_id)
        return replay(await self._read(session_id), epoch, after_seq)

    async def rebuild(self, session_id):
        """Host-only invalidation, no RPC: retain seq/dedupe, rotate epoch, discard ring."""
        while True:
            before = await self._read(session_id)
            after = deepcopy(before)
            drop_ring(after)
            after['epoch'] = uuid.uuid4().hex
            if await self._commit(before, after):
                return await self.watermark(session_id)

    async def since_legacy(self, session_id, last_seen=0):
        # H/methods_session.py:2309-2324: epoch parameter is ignored, a truncated
        # replay still returns its retained tail. Open requests belong to PR-6.
        await self.ingest(session_id)
        row = await self._read(session_id)
        events = [{**json.loads(bytes(item['frozen']).decode('utf-8', errors='surrogatepass')), 'seq': item['seq']}
                  for item in row['ring'] if item['seq'] > last_seen]
        return {'events': events, 'latest_seq': row['seq'], 'truncated': last_seen < row['evicted_through'],
                'count': len(events), 'epoch': row['epoch']}

    async def forget_session(self, session_id):
        while True:
            before = await self._read(session_id)
            after = deepcopy(before)
            drop_ring(after)
            after['forgotten'] = True
            if await self._commit(before, after):
                return

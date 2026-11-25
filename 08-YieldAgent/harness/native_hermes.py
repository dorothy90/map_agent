"""Shared gateway services for stock Hermes chat runs and saved conversations."""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import uuid
from datetime import datetime, timezone

import yaml

from . import native_ipc as ipc
from .gateway.pending_controls import PendingControls
from .gateway.pending_wire import (effective_timeout,
    clarify_tool_result, question_prompt_id, CLARIFY_TIMEOUT_NOTICE)
from .isolation.backend import principal_hash

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_COMMIT = '5894e750c3dba980d43b25aa29e3958149ba2c61'


def live_commentary(message):
    if message.get('delta') is True:
        return 'message.delta', {'text': message['content']}
    if 'already_streamed' in message:
        return 'message.interim', {'text': message['content'],
                                   'already_streamed': bool(message['already_streamed'])}
    return None


async def coalesced_worker_messages(reader, remaining_seconds):
    """Batch text for 90ms, including idle flushes, and preserve IPC boundaries."""
    pending = None
    chunks = []
    deadline = None
    loop = asyncio.get_running_loop()
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(reader.readline())
            timeout = max(.1, remaining_seconds())
            if chunks:
                timeout = min(timeout, max(0, deadline - loop.time()))
            done, _ = await asyncio.wait({pending}, timeout=timeout)
            if not done:
                if chunks:
                    yield {'type': 'commentary', 'content': ''.join(chunks), 'delta': True}
                    chunks = []
                    continue
                raise asyncio.TimeoutError
            line = pending.result()
            pending = None
            message = ipc.decode(line) if line else None
            if message and message['type'] == 'commentary' and message.get('delta') is True:
                if not chunks:
                    deadline = loop.time() + .09
                chunks.append(message['content'])
                if loop.time() < deadline:
                    continue
            elif line and message is None:
                continue
            if chunks:
                yield {'type': 'commentary', 'content': ''.join(chunks), 'delta': True}
                chunks = []
            if not line:
                break
            if message and not (message['type'] == 'commentary' and message.get('delta') is True):
                yield message
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


def native_history(previous):
    from .context import session_context
    from langchain_core.messages import convert_to_openai_messages
    native_at = next((i for i, item in enumerate(previous) if item.get('native_messages')), None)
    if native_at is None:
        messages, _, _ = session_context(previous)
        return convert_to_openai_messages(messages)
    history = list(previous[native_at]['native_messages'])
    for item in reversed(previous[:native_at]):
        history.append({'role': 'user', 'content': item.get('goal_request', item['query'])})
        answer = item.get('answer_text', item.get('answer', ''))
        if answer:
            history.append({'role': 'assistant', 'content': answer})
    return history


def prompt_generation(run):
    # No upstream epoch mapping → PB:517,663; HarnessStore.event:298-309 fences epoch.
    return run['epoch']


def prompt_history(messages):
    """Do not persist clarify questions/answers in native_messages (PB:391,607)."""
    hidden = {call['id'] for message in messages for call in message.get('tool_calls', [])
              if call.get('function', {}).get('name') == 'clarify'}
    result = []
    for message in messages:
        if message.get('tool_call_id') in hidden:
            continue
        if message.get('tool_calls'):
            message = {**message, 'tool_calls': [c for c in message['tool_calls'] if c['id'] not in hidden]}
            if not message['tool_calls']:
                message.pop('tool_calls')
                if not message.get('content'):
                    continue
        result.append(message)
    return result


async def pending_snapshot(store, principal_id, session_id, generation):
    """PR-7 entry point; no route. H/gateway/session_pending_controls.py:53-64."""
    pending = PendingControls(store, principal_id)
    await pending.setup()
    prompts = await pending.snapshot(session_id, generation)
    await publish_stored_prompt_events(store, pending)
    return prompts


def prompt_event_key(event):
    payload = event['payload']
    return 'native-prompt:' + event['type'] + ':' + (payload.get('prompt_id') or ','.join(payload.get('request_ids', [])))


async def publish_clarify_completion(store, row, prompt_id):
    """One result per parked authority row; answers come only from pending state."""
    if row['kind'] != 'clarify' or prompt_id not in row['authority_ids']:
        return
    for index, question in enumerate(row['questions']):
        authority_id = question_prompt_id(row['prompt_id'], row['questions'], index)
        if row['status'] == 'pending' and authority_id != prompt_id:
            continue
        reply = row.get('result') or {}
        answers = {**row['answers'], **(reply.get('answers') or {})}
        # A batch can settle one answered row while the other questions still wait.
        outcome = 'submitted' if row['status'] == 'pending' else reply['outcome']
        notice = reply.get('notice') or (CLARIFY_TIMEOUT_NOTICE if outcome == 'timed_out' else None)
        args = {'questions': [{'question': question['question'], 'choices': question.get('choices') or [],
                               'multi_select': question.get('multi_select', False)}]}
        await store.event(row['run_id'], 'tool_finished', {
            'name': 'clarify', 'tool_id': authority_id, 'invocation_id': authority_id,
            'args': args, 'result': clarify_tool_result([question], answers, outcome, notice),
            'status': 'success', 'is_error': False,
        }, epoch=row['execution_generation'], event_key='native-clarify-complete:' + authority_id)


async def publish_stored_prompt_events(store, pending):
    """PR-7 helpers must drain their own 6a instance, including cancellation reason.

    No upstream persistence counterpart → PB:663. Old-generation descriptors
    cannot attach to a recovered run's new epoch. Never query audit bodies.
    """
    events = pending.drain_events()
    for event in events:
        payload = event['payload']
        identity = payload.get('prompt_id') or payload['request_ids'][0]
        row = await pending.pending_prompts.find_one({'principal_id': pending.principal_id,
            'authority_ids': identity})
        if row and row.get('run_id'):
            await store.event(row['run_id'], event['type'], payload,
                epoch=row['execution_generation'], event_key=prompt_event_key(event))
            if event['type'] == 'clarify.settled':
                await publish_clarify_completion(store, row, identity)
    return events


async def cancel_run_prompts(store, principal_id, run_id, reason):
    """Recovery hook, intentionally not wired into control.py (PB:663)."""
    pending = PendingControls(store, principal_id)
    await pending.setup()
    await pending.cancel(run_id=run_id, reason=reason)
    return await publish_stored_prompt_events(store, pending)


class NativePrompts:
    """Publish pending descriptors/results without journaling native_messages.

    Host lifetime adapter has no upstream counterpart → PB:517,576,663,731.
    Remote instances keep their own event queues: reconcile settled IDs with the
    durable row using the SAME 6a builder, with stable event keys for deduplication.
    """
    def __init__(self, context, pending):
        self.context, self.pending = context, pending
        self.run = context.run
        self.rows = {}
        self.open = {}
        self.settled = set()
        self.lock = asyncio.Lock()
        self.closed = False

    async def publish(self):
        from .gateway.pending_wire import build_settled_event
        async with self.lock:
            events = self.pending.drain_events()
            for identity in list(self.rows):
                row = await self.pending.pending_prompts.find_one({
                    'principal_id': self.pending.principal_id, 'prompt_id': identity})
                if row is None:
                    continue
                self.rows[identity] = row
                live = ({question_prompt_id(identity, row['questions'], i)
                         for i, q in enumerate(row['questions']) if q['qid'] not in row['answers']}
                        if row['kind'] == 'clarify' and row['status'] == 'pending'
                        else {identity} if row['status'] == 'pending' else set())
                for prompt_id, (parent, prompt) in list(self.open.items()):
                    if parent == identity and prompt_id not in live:
                        events.append(build_settled_event(self.run['session_id'], prompt))
            for event in events:
                kind, payload = event['type'], event['payload']
                prompt_id = payload.get('prompt_id')
                if kind == 'clarify.settled':
                    row = next(row for row in self.rows.values() if prompt_id in row['authority_ids'])
                    await publish_clarify_completion(self.context.store, row, prompt_id)
                if kind.endswith('.settled'):
                    if prompt_id in self.settled:
                        continue
                    self.settled.add(prompt_id)
                    self.open.pop(prompt_id, None)
                await self.context.store.event(self.run['run_id'], kind, payload,
                    epoch=prompt_generation(self.run),
                    event_key=prompt_event_key(event))
                if kind.endswith('.request'):
                    parent = next(identity for identity, row in self.rows.items() if prompt_id in row['authority_ids'])
                    self.open[prompt_id] = (parent, payload)
                    expires = self.rows[parent]['expires_at']
                    expires = datetime.fromtimestamp(expires, timezone.utc).isoformat() if expires is not None else None
                    # Only metadata here: question/choices/command stay in the pending event.
                    await self.context.store.event(self.run['run_id'], 'yield.input_required', {
                        'interrupt_id': prompt_id, 'request_id': prompt_id,
                        'generation': prompt_generation(self.run), 'goal_revision': self.run.get('goal_revision', 0),
                        'kind': payload['kind'], 'message': '입력이 필요합니다.', 'fields': [],
                        'expires_at': expires, 'response_method': payload['kind'] + '.respond'},
                        epoch=prompt_generation(self.run), event_key='native-input:' + prompt_id)
            metadata = {identity: {'kind': prompt['kind'],
                'expires_at': datetime.fromtimestamp(self.rows[parent]['expires_at'], timezone.utc).isoformat()
                    if self.rows[parent]['expires_at'] is not None else None}
                for identity, (parent, prompt) in self.open.items()}
            await self.context.store.runs.update_one({'_id': self.run['run_id'], 'epoch': prompt_generation(self.run)},
                {'$set': {'pending_input': metadata}} if metadata else {'$unset': {'pending_input': ''}})

    async def wait(self, identity, timeout):
        waiter = None
        try:
            await self.publish()
            waiter = asyncio.create_task(self.pending.wait(identity, timeout=timeout))
            while not waiter.done():
                await self.context.check()
                await asyncio.wait({waiter}, timeout=.05)
                await self.publish()
            result = await waiter
            await self.context.check()  # Approval must not outlive the run's execution right.
            return result
        finally:
            if waiter is not None:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            await self.pending.cancel(identity, reason='interrupt')
            await self.publish()

    async def clarify(self, params):
        async with self.lock:
            if self.closed:
                return {'answers': {}, 'outcome': 'cancelled'}
            await self.context.check()
            timeout = effective_timeout(300, self.context.remaining_seconds())
            identity = await self.pending.register_clarify(self.run['session_id'], prompt_generation(self.run),
                {'clarify_id': 'srq-' + uuid.uuid4().hex[:12], 'questions': params.get('questions')},
                timeout_s=timeout, run_id=self.run['run_id'])
            self.rows[identity] = await self.pending.pending_prompts.find_one({
                'principal_id': self.pending.principal_id, 'prompt_id': identity})
        result = await self.wait(identity, timeout)
        if result.get('outcome') == 'timed_out':
            return {**result, 'notice': CLARIFY_TIMEOUT_NOTICE}
        return result

    async def cancel(self, reason):
        async with self.lock:
            self.closed = True
            await self.pending.cancel(run_id=self.run['run_id'], reason=reason)
        await self.publish()


async def record_skill_result(context, executor, message):
    name = message['name']
    if name not in ('skills_list', 'skill_view', 'skill_manage'):
        raise ValueError('Unexpected Hermes skill tool')
    payload = message['result']
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {'error': payload}
    if not isinstance(payload, dict):
        payload = {'error': 'Invalid Hermes skill response', 'response': payload}
    args = message['args']
    if name == 'skill_manage':
        operations = args.get('operations')
        args = {'operations': [
            {k: op[k] for k in ('action', 'name', 'file_path') if k in op}
            for op in operations if isinstance(op, dict)
        ]} if isinstance(operations, list) else {}
    status = 'error' if payload.get('error') or payload.get('success') is False else 'success'
    run = context.run
    observation = await context.store.put_result(run['principal_id'], run['session_id'],
        {'run_id': run['run_id'], 'invocation_id': message['invocation_id'], 'tool_name': name,
         'validated_arguments': args, 'status': status,
         'summary': name, 'provenance': {'source_system': 'hermes', 'data_origin': 'live'}},
        [payload], [])
    await executor.publish(observation, run)
    return observation


def result_status(result):
    if result.get('failed'):
        return 'failed'
    if result.get('partial') or result.get('interrupted'):
        return 'partial'
    if result.get('error'):
        return 'failed'
    if not result.get('completed'):
        return 'partial'
    return 'completed'


def profile_path(principal_id):
    return ROOT / '.runtime/hermes/profiles' / principal_hash(principal_id)


@contextmanager
def profile_write_lock(profile):
    # Profile migrations and user edits share this lock.
    if os.name == 'nt':
        import msvcrt
        # Windows locks byte ranges on files. A persistent lockfile keeps all
        # writers on one inode when config.yaml is atomically replaced.
        fd = os.open(profile / '.profile-write.lock', os.O_RDWR | os.O_CREAT, 0o600)
        try:
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            yield
        finally:
            os.close(fd)
        return
    import fcntl
    fd = os.open(profile, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def prepare_profile(principal_id):
    profile = profile_path(principal_id)
    profile.mkdir(parents=True, exist_ok=True)
    with profile_write_lock(profile):
        _prepare_profile_config(profile)
    return profile


def _prepare_profile_config(profile):
    config = profile / 'config.yaml'
    content = (yaml.safe_load(config.read_text(encoding='utf-8')) or {}) if config.exists() else {}
    # This worker has no host terminal tool; probing its package manager is irrelevant.
    content.setdefault('agent', {})
    content.setdefault('delegation', {})
    managed_profile = (content['agent'].get('environment_probe') is False
                       and content['delegation'].get('orchestrator_enabled') is False)
    changed = not managed_profile
    # Old managed profiles forced the model upgrade off on every run. Restore the
    # native default once, leaving later user choices and other title options alone.
    title_migration = profile / '.native-title-default-v1'
    title_generation = content.get('auxiliary', {}).get('title_generation', {})
    search = content.get('tools', {}).get('tool_search')
    if (not title_migration.exists() and managed_profile
            and isinstance(search, dict) and search.get('enabled') == 'off'
            and title_generation.get('model_upgrade_enabled') is False):
        del title_generation['model_upgrade_enabled']
        changed = True
    content['agent']['environment_probe'] = False
    content['delegation']['orchestrator_enabled'] = False
    terminal = content.get('terminal') or {}
    managed_fields = {'backend', 'cwd', 'ssh_host', 'ssh_user', 'ssh_port', 'ssh_key',
                      'env_passthrough', 'persistent_shell'}
    if (isinstance(terminal, dict) and terminal.keys() == managed_fields
            and terminal['backend'] == 'ssh' and terminal['ssh_user'] == 'yieldrun'
            and terminal['ssh_host'] in {'localhost', '127.0.0.1', '::1'}
            and terminal['persistent_shell'] is False and terminal['env_passthrough'] == []):
        del content['terminal']
        changed = True
    # Earlier web profiles set these two values together on every run.
    # Remove that managed pair once; Hermes then supplies its native defaults.
    search_migration = profile / '.native-tool-search-default-v1'
    if (not search_migration.exists() and managed_profile and isinstance(search, dict)
            and search.get('enabled') == 'off' and search.get('defer') == []):
        del search['enabled'], search['defer']
        changed = True
    if changed:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=profile, delete=False) as pending:
            pending.write(yaml.safe_dump(content, allow_unicode=True))
        os.replace(pending.name, config)
    if not title_migration.exists():
        title_migration.touch()
    if not search_migration.exists():
        search_migration.touch()

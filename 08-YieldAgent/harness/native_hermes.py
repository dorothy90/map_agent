"""Adapter to the bundled Hermes engine using the backend Python interpreter."""
import asyncio
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import yaml

from .executor import ToolExecutor
from .instructions import load_native_instructions
from .native_tools import domain_model_result
from .store import LeaseLost, BudgetExceeded
from .gateway import publication
from .gateway.events import SessionEvents
from . import native_ipc as ipc
from .gateway.pending_controls import PendingControls
from .gateway.pending_wire import (effective_timeout, decline_server_request,
    clarify_tool_result, question_prompt_id, CLARIFY_TIMEOUT_NOTICE)
from .isolation.backend import (
    cleanup_isolated_run,
    cleanup_profile_results,
    prepare_isolated_run,
    principal_hash,
)

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_COMMIT = '5894e750c3dba980d43b25aa29e3958149ba2c61'
# Hermes gateway/restart.py:45: post-interrupt grace before process teardown.
INTERRUPT_GRACE_SECONDS = 12


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


async def stop_worker(process, send, tasks, *, grace=INTERRUPT_GRACE_SECONDS, terminate_grace=3):
    for task in tasks:
        task.cancel()
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=grace)
        for task in done:
            if not task.cancelled():
                task.exception()
        for task in pending:
            task.cancel()
    if process.returncode is not None:
        return
    try:
        await asyncio.wait_for(send(ipc.control('interrupt')), grace)
    except (BrokenPipeError, ConnectionResetError, asyncio.TimeoutError):
        pass
    import signal
    for sig in (None, signal.SIGTERM, signal.SIGKILL):
        if sig is not None:
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), grace if sig is None else terminate_grace)
            return
        except asyncio.TimeoutError:
            if sig == signal.SIGKILL:
                raise


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

    async def approve(self, name, description, effect):
        async with self.lock:
            if self.closed:
                return False
            await self.context.check()
            timeout = effective_timeout(300, self.context.remaining_seconds())
            identity = await self.pending.register(self.run['session_id'], prompt_generation(self.run), {
                'request_id': 'srq-' + uuid.uuid4().hex[:12], 'tool_name': name,
                'command': f'<{name}> (plugin approval rule)', 'description': description},
                timeout_s=timeout, run_id=self.run['run_id'], tool_allowed=True, effect=effect)
            self.rows[identity] = await self.pending.pending_prompts.find_one({
                'principal_id': self.pending.principal_id, 'prompt_id': identity})
        return (await self.wait(identity, timeout)).get('choice') in ('once', 'session', 'always')

    async def cancel(self, reason):
        async with self.lock:
            self.closed = True
            await self.pending.cancel(run_id=self.run['run_id'], reason=reason)
        await self.publish()


async def handle_server_request(prompts, request, params, *, review_id=None):
    # H/run_agent.py:253 has no approval callback. Unbound worker approvals cannot
    # authorize a host invocation; only NativeBridge's pre-execution gate can.
    if request == 'approval':
        return {'choice': 'deny'}
    if request == 'clarify':
        if prompts is None or review_id:
            return {'answers': {}, 'outcome': 'cancelled'}
        return await prompts.clarify(params)
    if prompts is not None:
        return await prompts.pending.decline_server_request(request, params)
    return decline_server_request(request, params)


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


class NativeBridge:
    def __init__(self, registry, context, *, approval_tools=frozenset(), pending=None):
        self.approval_tools = frozenset(approval_tools)
        self.pending = pending
        self.registry = registry
        self.context = context
        self.allowed = {name for name, spec in registry.tools.items() if spec.effect in ('read', 'artifact')}
        settings = getattr(context, 'settings', None)
        requested = getattr(settings, 'native_allowed_tools', None)
        if requested is None:
            self.allowed &= {'query_db', 'list_tables', 'read_result',
                'render_yield_table', 'load_wafer_maps', 'run_python'}
        else:
            unknown = set(requested) - set(registry.tools)
            if unknown:
                raise ValueError('native_allowed_tools contains unknown tools: '
                    + ', '.join(sorted(unknown)))
            self.allowed &= set(requested) | {'read_result'}
        self.executor = ToolExecutor(registry, context)
        self.observations = []
        self.review_observations = []

    def schemas(self):
        return [tool for tool in self.registry.model_tools() if tool['function']['name'] in self.allowed]

    async def record_skill_result(self, message):
        observation = await record_skill_result(self.context, self.executor, message)
        self.observations.append(observation.model_dump(mode='json'))

    async def invoke(self, call, *, review_id=None):
        if call['name'] not in self.allowed:
            raise PermissionError('Tool is outside this run capability set')
        if review_id and call['name'] != 'read_result':
            raise PermissionError('Verifier can only read stored results')
        spec = self.registry.tools[call['name']]
        if call['name'] in self.approval_tools and spec.effect != 'read':
            # Host gate: no upstream counterpart → PB:576 D05 / server-owned capability.
            # H/tools/approval.py:850-890,1121-1147: silence is fail-closed BLOCKED.
            if review_id or self.pending is None or not await self.pending.approve(call['name'], spec.description, spec.effect):
                return {'status': 'denied', 'error': 'BLOCKED: tool approval was not granted'}
        executor = ToolExecutor(self.registry, replace(self.context, parent_invocation_id=review_id)) if review_id else self.executor
        observation = await executor.execute(call)
        (self.review_observations if review_id else self.observations).append(observation.model_dump(mode='json'))
        return domain_model_result(observation)


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


def _verification_evidence(observations):
    """Return bounded evidence previews and all known result IDs."""
    from .types import ToolObservation

    def shorten(value, limit):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        return text if len(text) <= limit else text[:limit - 1] + '…'

    def compact_cell(value):
        if value is None or isinstance(value, (bool, int, float)):
            return value if len(json.dumps(value, ensure_ascii=False)) <= 160 else shorten(value, 160)
        return shorten(value, 160)

    evidence, known = [], set()
    for raw in observations:
        obs = ToolObservation.model_validate(raw)
        known.add(obs.result_id)
        known.update(obs.source_result_ids)
        table_columns = {column for table in obs.tables for column in table.columns}
        columns = table_columns or set(obs.columns)
        rows = []
        if obs.status != 'error':
            sources = ((table.table_id, table.preview_rows) for table in obs.tables)
            if not obs.tables:
                sources = ((None, obs.preview_rows),)
            for table_id, preview_rows in sources:
                for row in preview_rows:
                    sample = {key: compact_cell(value) for key, value in row.items()}
                    if table_id is not None:
                        sample = {'table_id': table_id, **sample}
                    rows.append(sample)
                    if len(rows) == 5:
                        break
                if len(rows) == 5:
                    break
        evidence.append({'result_id': obs.result_id, 'tool_name': obs.tool_name,
            'status': obs.status, 'summary': shorten(obs.summary, 240),
            'args_summary': shorten(obs.validated_arguments, 400),
            'total_rows': sum(table.total_rows for table in obs.tables) if obs.tables else obs.total_rows,
            'column_count': len(columns), 'preview_rows': rows})

    def size():
        return len(json.dumps(evidence, ensure_ascii=False))

    for item in reversed(evidence):
        while size() > 20_000 and item['preview_rows']:
            item['preview_rows'].pop()
    for item in reversed(evidence):
        for field in ('args_summary', 'summary'):
            if size() <= 20_000:
                break
            item.pop(field, None)
    while evidence and size() > 20_000:
        evidence.pop()
    return evidence, sorted(known)


def native_prompts_enabled(settings):
    return bool(settings.native_prompts or settings.no_yield_tools)


def profile_path(principal_id):
    return ROOT / '.runtime/hermes/profiles' / principal_hash(principal_id)


@contextmanager
def profile_write_lock(profile):
    # A run can refresh its terminal settings while the user changes a skill.
    fd = os.open(profile, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def prepare_profile(principal_id, isolation=None):
    profile = profile_path(principal_id)
    profile.mkdir(parents=True, exist_ok=True)
    with profile_write_lock(profile):
        _prepare_profile_config(profile, isolation)
    return profile


def _prepare_profile_config(profile, isolation):
    config = profile / 'config.yaml'
    content = (yaml.safe_load(config.read_text()) or {}) if config.exists() else {}
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
    if isolation is not None:
        if isolation.get('backend') in {'local', 'seatbelt'}:
            terminal = {
                'backend': 'local', 'cwd': isolation['workdir'],
                'env_passthrough': [], 'persistent_shell': False,
            }
        else:
            terminal = {
                'backend': 'ssh', 'cwd': isolation['workdir'],
                'ssh_host': isolation['ssh_host'], 'ssh_user': isolation['ssh_user'],
                'ssh_port': isolation['ssh_port'], 'ssh_key': str(isolation['ssh_key']),
                'env_passthrough': [], 'persistent_shell': False,
            }
        changed = changed or content.get('terminal') != terminal
        content['terminal'] = terminal
    else:
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
        with tempfile.NamedTemporaryFile(mode='w', dir=profile, delete=False) as pending:
            pending.write(yaml.safe_dump(content, allow_unicode=True))
        os.replace(pending.name, config)
    if not title_migration.exists():
        title_migration.touch()
    if not search_migration.exists():
        search_migration.touch()


async def run_native(context, registry, *, history=None):
    run, settings = context.run, context.settings
    use_native_prompts = native_prompts_enabled(settings)
    prior_usage = run.get('usage', {})
    for kind, limit in (('models', settings.model_limit), ('tokens', settings.token_limit)):
        if prior_usage.get(kind, 0) >= limit:
            return {'status': 'partial', 'stop_reason': kind + '_limit',
                'answer': '실행 예산을 모두 사용하여 추가 모델 호출 없이 중지했습니다.',
                'observations': run.get('observations', []), 'result_ids': run.get('result_ids', [])}
    python = sys.executable
    bridge = NativeBridge(registry, context)
    isolation = None
    if settings.no_yield_tools:
        isolation = await asyncio.to_thread(
            prepare_isolated_run, settings.isolation, run['run_id'], run['principal_id'])
        await asyncio.to_thread(cleanup_profile_results, settings.isolation, run['principal_id'])
    async def cleanup_remote():
        if isolation is not None:
            await asyncio.to_thread(
                cleanup_isolated_run, settings.isolation, run['run_id'], run['principal_id'])
    try:
        profile = (prepare_profile(run['principal_id'], isolation) if isolation is not None
                   else prepare_profile(run['principal_id']))
    except BaseException:
        await cleanup_remote()
        raise
    profile_config = profile / 'config.yaml'
    config = (yaml.safe_load(profile_config.read_text()) or {}) if profile_config.exists() else {}
    skill_config = config.get('skills') or {}
    if skill_config.get('external_dirs') or skill_config.get('create_dir'):
        await cleanup_remote()
        raise ValueError('Native runs use per-user skills; external skill directories are outside the profile')
    if skill_config.get('inline_shell'):
        await cleanup_remote()
        raise ValueError('Skill inline_shell would run commands outside the analysis environment')
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if key in os.environ}
    env.update(HERMES_HOME=str(profile), PYTHONUNBUFFERED='1', HERMES_DISABLE_LAZY_INSTALLS='1',
        HERMES_TIMEZONE=settings.timezone or config.get('timezone') or 'Asia/Seoul')
    try:
        requested_at = datetime.fromisoformat(run.get('created_at') or '')
        if requested_at.tzinfo is None:
            requested_at = requested_at.replace(tzinfo=ZoneInfo(env['HERMES_TIMEZONE']))
    except (TypeError, ValueError):
        requested_at = datetime.now(timezone.utc)
    try:
        request_context = {'requested_at': requested_at.astimezone(ZoneInfo(env['HERMES_TIMEZONE'])).isoformat(),
            'timezone': env['HERMES_TIMEZONE']}
        workspace = tempfile.TemporaryDirectory(prefix="yield-hermes-")
    except BaseException:
        await cleanup_remote()
        raise
    try:
        await context.store.runs.update_one({'_id': run['run_id'], 'epoch': run['epoch'], 'status': 'running'},
            {'$set': {'request_context': request_context}})
        run['request_context'] = request_context
        # Keep Hermes worker diagnostics instead of discarding them (stdout carries the RPC stream).
        log_dir = ROOT / '.runtime/hermes/logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / f"native-worker-{run['run_id']}.log", 'ab') as worker_log:
            process = await asyncio.create_subprocess_exec(str(python), '-I', str(Path(__file__).with_name('native_worker.py')),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=worker_log,
                env=env, cwd=workspace.name, limit=16 * 1024 * 1024, start_new_session=True)
    except BaseException:
        workspace.cleanup()
        await cleanup_remote()
        raise
    write_lock = asyncio.Lock()
    async def send(payload):
        async with write_lock:
            wire = ipc.encode(payload) if 'type' in payload else json.dumps(payload, ensure_ascii=False, default=str) + '\n'
            process.stdin.write(wire.encode())
            await process.stdin.drain()
    startup = {'model': settings.model, 'base_url': settings.base_url,
        'api_key': settings.api_key.get_secret_value(), 'max_tokens': settings.max_output_tokens,
        'temperature': settings.temperature, 'reasoning_effort': settings.reasoning_effort,
        'call_timeout': settings.call_timeout,
        'parallel_tool_limit': settings.parallel_tool_limit,
        'max_iterations': settings.model_limit - prior_usage.get('models', 0), 'run_budget_seconds': context.remaining_seconds(),
        'token_limit': max(0, settings.token_limit - prior_usage.get('tokens', 0)), 'session_id': run['session_id'],
        'query': run.get('goal_request', run['query']), 'instructions': load_native_instructions(),
        'verification_instructions': Path(__file__).with_name('instructions').joinpath('verifier.md').read_text(),
        'request_context': request_context,
        'tools': bridge.schemas(), 'history': history or [], 'upstream_commit': UPSTREAM_COMMIT,
        'no_yield_tools': settings.no_yield_tools}
    # An omitted allowlist enables Hermes-native executors outside our adapter.
    startup['allowed_tools'] = sorted(bridge.allowed)
    if isolation is not None:
        startup['isolation'] = isolation
    if tools_log := os.environ.get('HARNESS_LOG_TOOLS_PAYLOAD'):
        startup['tools_log'] = tools_log
    if use_native_prompts:
        startup['native_prompts'] = True
    startup['instructions'] += ('\n\n서버가 제공한 요청 기준정보(JSON)다. 상대 기간은 이 요청 시각과 시간대를 기준으로 해석한다. '
        '데이터의 관측 기간은 요청의 시간 기준과 별개이며, 사용자가 기준을 지정한 경우 그 지정을 우선한다.\n'
        + json.dumps(request_context, ensure_ascii=False))
    tasks = set()
    reviews = {}
    wafer_map_contract_reviews = {}
    artifact_reviews = {}
    verification_observations = []
    reports, verified_candidates = [], []
    last_usage = None
    async def invoke(message):
        try:
            review_id = message.get('review_id')
            if review_id and review_id not in reviews:
                raise PermissionError('Unknown verification invocation')
            value = await bridge.invoke({'id': message.get('tool_call_id') or message['call_id'],
                'name': message['name'], 'args': message['args']}, review_id=review_id)
        except asyncio.CancelledError:
            await send(ipc.reply(message['call_id'], ipc.CANCELLED))
            raise
        except Exception as exc:
            from .runtime.domain_process import safe_error_message
            value = {'error': safe_error_message(exc), 'status': 'error'}
        await send(ipc.reply(message['call_id'], value))
    result = None
    prompts = None
    live_events = SessionEvents(context.store, run['principal_id'])
    messages = coalesced_worker_messages(process.stdout, context.remaining_seconds)
    async def server_request(message):
        try:
            value = await handle_server_request(prompts, message['request'], message['params'],
                review_id=message.get('review_id') or (next(iter(reviews), None)))
        except asyncio.CancelledError:
            value = {'answers': {}, 'outcome': 'cancelled'} if message['request'] == 'clarify' else {'choice': 'deny'}
            await send(ipc.reply(message['call_id'], value))
            raise
        except (LeaseLost, BudgetExceeded):
            value = {'answers': {}, 'outcome': 'cancelled'} if message['request'] == 'clarify' else {'choice': 'deny'}
        except Exception:
            # Never echo request/response bodies or parser exception text.
            value = {'error': {'code': -32602, 'message': 'Invalid params'}}
        await send(ipc.reply(message['call_id'], value))
    try:
        if use_native_prompts:
            pending = PendingControls(context.store, run['principal_id'])
            prompts = NativePrompts(context, pending)
            await pending.setup()
            bridge.pending = prompts
        await send(startup)
        async for message in messages:
            if message['type'] == 'step':
                last_usage = message
            await context.check()
            kind = message['method'] if message['type'] == 'call' else message['type']
            if kind == 'skill_read':
                version = {key: message[key] for key in ('name', 'resource', 'sha256')}
                await context.store.runs.update_one(
                    {'_id': run['run_id'], 'epoch': run['epoch'], 'status': 'running'},
                    {'$addToSet': {'skill_reads': version}})
            elif kind == 'skill_result':
                await bridge.record_skill_result(message)
            elif kind == 'tool_call':
                task = asyncio.create_task(invoke(message))
                tasks.add(task)
            elif kind == 'server_request':
                task = asyncio.create_task(server_request(message))
                tasks.add(task)
            elif kind == 'verification_start':
                review_id = message['review_id']
                from .native_verification import inspect_wafer_map_contracts
                review_observations = bridge.observations
                artifact_bundle = {}
                selected = None
                from .artifact_delivery import ARTIFACT_DELIVERY, artifact_links
                if run.get('artifact_delivery') == ARTIFACT_DELIVERY:
                    from .native_verification import inspect_answer_artifacts
                    artifact_review = await inspect_answer_artifacts(context.store,
                        run['principal_id'], run['session_id'], message['candidate'])
                    artifact_reviews[review_id] = artifact_review
                    selected = artifact_review['selected_result_ids']
                    review_observations = list({item['result_id']: item for item in
                        [*bridge.observations, *artifact_review['observations']]}.values())
                    artifact_bundle = {'artifact_reference_review': {
                        key: value for key, value in artifact_review.items() if key != 'observations'},
                        'artifact_catalog': [dict(ref, status=item['status'])
                            for item in review_observations
                            for ref in artifact_links(run['session_id'], item['result_id'], item.get('artifact_refs', []))]}
                wafer_map_contract_review = await inspect_wafer_map_contracts(
                    context.store, run['principal_id'], run['session_id'], review_observations,
                    selected_result_ids=selected)
                wafer_map_contract_reviews[review_id] = wafer_map_contract_review
                reviews[review_id] = {'attempt': message['attempt'],
                    **publication.candidate_digest(message['candidate'])}
                context_preview = json.dumps(reviews[review_id], ensure_ascii=False, separators=(',', ':'))
                if len(context_preview) > 80:
                    context_preview = context_preview[:77] + '...'
                await context.store.event(run['run_id'], 'tool_started', {'name': 'verify_answer',
                    'invocation_id': review_id, 'state': 'running', 'elapsed_seconds': 0,
                    'tool_id': review_id, 'args': reviews[review_id], 'context': context_preview}, epoch=run['epoch'])
                evidence, known = _verification_evidence(review_observations)
                await send(ipc.reply(message['call_id'], {'request': startup['query'],
                    'request_context': request_context,
                    'current_time': datetime.now(timezone.utc).isoformat(), 'prior_dialogue': history or [],
                    'candidate': message['candidate'], 'evidence': evidence, 'known_result_ids': known,
                    'wafer_map_contract_review': wafer_map_contract_review, **artifact_bundle}))
            elif kind == 'verification_finished':
                review_id = message['review_id']
                started_review = reviews.pop(review_id)
                from .native_verification import enforce_wafer_map_contract_review
                report = enforce_wafer_map_contract_review(
                    message['report'], wafer_map_contract_reviews.pop(review_id))
                if review_id in artifact_reviews:
                    report = enforce_wafer_map_contract_review(report, artifact_reviews.pop(review_id))
                reports.append(report)
                verified_candidates.append(started_review['candidate_sha256'])
                status = 'success' if report['verdict'] == 'pass' else 'partial'
                summary = {'pass': '답변 검증 통과', 'fail': '답변 보완 필요', 'unknown': '답변 검증 미완료'}[report['verdict']]
                observation = await context.store.put_result(run['principal_id'], run['session_id'],
                    {'run_id': run['run_id'], 'invocation_id': review_id, 'tool_name': 'verify_answer',
                     'validated_arguments': started_review,
                     'status': status, 'summary': summary + (' · ' + '; '.join(report['issues']) if report['issues'] else ''),
                     'source_result_ids': report['result_ids'], 'scope': {'verdict': report['verdict'],
                         'verifier_exit': report.get('verifier_exit', {})},
                     'provenance': {'elapsed_seconds': message['elapsed'], 'data_origin': 'live'},
                     'error': None if status == 'success' else {'message': '; '.join(report['issues'])}}, [], [])
                await bridge.executor.publish(observation, run)
                verification_observations.append(observation.model_dump(mode='json'))
                await send(ipc.reply(message['call_id'], {'saved': True, 'report': report}))
            elif message['type'] == 'commentary':
                live = live_commentary(message)
                if live is not None:
                    await live_events.publish_live(run['session_id'], *live)
                else:
                    payload = publication.commentary_event(settings, message['content'])
                    if payload is not None:
                        await context.store.event(run['run_id'], 'commentary', payload, epoch=run['epoch'])
            elif message['type'] == 'step':
                await context.store.runs.update_one({'_id': run['run_id'], 'epoch': run['epoch'], 'status': 'running'},
                    {'$set': {'usage.tokens': prior_usage.get('tokens', 0) + message['tokens'],
                        'usage.models': prior_usage.get('models', 0) + message['models']}})
                await context.store.event(run['run_id'], 'progress', {'name': 'reasoning',
                    'message': 'Hermes 원본 엔진이 요청과 도구 결과를 확인하고 있습니다.'}, epoch=run['epoch'])
            elif message['type'] == 'result':
                result = message['result']
                break
            elif message['type'] == 'error':
                raise RuntimeError(message['message'])
        if result is None:
            raise RuntimeError('Native Hermes worker exited without a result')
        await context.store.runs.update_one({'_id': run['run_id'], 'epoch': run['epoch'], 'status': 'running'},
            {'$set': {'usage.tokens': prior_usage.get('tokens', 0) + (result.get('total_tokens') or 0),
                'usage.models': prior_usage.get('models', 0) + (result.get('api_calls') or 0)}})
        observations = bridge.observations
        decision = publication.settle(result, reports, verified_candidates)
        # Unverified tool evidence is intentionally unchanged (see PORTING_BASELINE PB:523).
        return {**decision, 'answer': decision['answer_text'],
            'observations': observations + bridge.review_observations + verification_observations, 'result_ids': [o['result_id'] for o in observations],
            'native_messages': prompt_history(result.get('messages', [])) if use_native_prompts else result.get('messages', []), 'native_usage': {k: result.get(k) for k in
                ('api_calls', 'input_tokens', 'output_tokens', 'total_tokens')},
            'native_engine': {'name': 'NousResearch/hermes-agent', 'commit': UPSTREAM_COMMIT,
                'verification': result.get('verification', [])}}
    finally:
        await messages.aclose()

        async def drain_usage():
            nonlocal last_usage
            while line := await process.stdout.readline():
                message = ipc.decode(line)
                if message and message['type'] == 'step':
                    last_usage = message

        async def cleanup():
            # Drain stdout while waiting: a full pipe must not obstruct interrupt
            # handling or lose an already emitted, unconfirmed model reservation.
            # Exited workers can still have unread reservations buffered in stdout.
            reader = asyncio.create_task(drain_usage())
            try:
                try:
                    if prompts is not None:
                        await prompts.cancel('interrupt' if result is None else 'turn_ended')
                finally:
                    await stop_worker(process, send, tasks)
                    await cleanup_remote()
            finally:
                if reader:
                    await asyncio.wait({reader}, timeout=3)
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
                if last_usage is not None and result is None:
                    # Cancellation may already have changed status; epoch still
                    # fences this worker from a resumed run. Never refund here.
                    await context.store.runs.update_one({'_id': run['run_id'], 'epoch': run['epoch']},
                        {'$set': {'usage.tokens': prior_usage.get('tokens', 0) + last_usage['tokens'],
                            'usage.models': prior_usage.get('models', 0) + last_usage['models']}})
        cleanup_task = asyncio.create_task(cleanup())
        cancelled_during_cleanup = False
        try:
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    cancelled_during_cleanup = True
            await cleanup_task
            if cancelled_during_cleanup:
                raise asyncio.CancelledError
        finally:
            workspace.cleanup()

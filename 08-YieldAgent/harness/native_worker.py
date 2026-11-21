"""JSON-lines transport only. Reasoning and tool orchestration are upstream Hermes."""
import hashlib
import json
from functools import wraps
import os
import sys
import threading
import uuid
import time
from queue import Empty
from pathlib import Path

# -I ignores PYTHONDONTWRITEBYTECODE; keep the bundled engine read-only too.
sys.dont_write_bytecode = True
# Both projects contain a top-level tools package. Resolve the pinned engine's
# packages before this adapter's directory in the separate interpreter.
sys.path.insert(0, str(Path(__file__).resolve().with_name('engine')))
sys.path.append(str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parents[1]))
import native_ipc as ipc


class WorkerControl:
    def __init__(self, pending):
        self.pending = pending
        self.agents = []
        self.lock = threading.Lock()
        self.interrupted = False

    def add(self, agent):
        with self.lock:
            self.agents.append(agent)
            interrupted = self.interrupted
        if interrupted:
            agent.interrupt()

    def remove(self, agent):
        with self.lock:
            self.agents[:] = [active for active in self.agents if active is not agent]

    def snapshot(self):
        with self.lock:
            return list(self.agents)

    def handle(self, message):
        if message['action'] != 'interrupt':
            return
        self.pending.close(ipc.CANCELLED)
        with self.lock:
            self.interrupted = True
            agents = list(self.agents)
        for active in agents:
            try:
                active.interrupt()
            except Exception:
                print('Worker interrupt callback failed', file=sys.stderr)


def observe_skill_reads(skills_tool, emit):
    """Hermes uses skill_view for both model tool calls and configured auto-loads."""
    view = skills_tool.skill_view

    @wraps(view)
    def observed(*args, **kwargs):
        result = view(*args, **kwargs)
        payload = json.loads(result)
        if payload.get('success') and not payload.get('is_binary') and isinstance(payload.get('content'), str):
            emit({'type': 'skill_read', 'name': payload['name'], 'resource': payload.get('file') or 'SKILL.md',
                  'sha256': hashlib.sha256(payload['content'].encode('utf-8')).hexdigest()})
            if kwargs.get('preprocess') is False:
                emit({'type': 'skill_result', 'invocation_id': 'preload-' + uuid.uuid4().hex,
                      'name': 'skill_view', 'args': {'name': payload['name']}, 'result': result})
        return result

    skills_tool.skill_view = observed


def configure_engine(registry, executor, limit, agent_class):
    if type(limit) is not int or not 1 <= limit <= 8:
        raise ValueError('parallel_tool_limit must be between 1 and 8')
    executor._MAX_TOOL_WORKERS = min(executor._MAX_TOOL_WORKERS, limit)
    def denied(args, **kwargs):
        print('D09: delegation call denied', file=sys.stderr)
        return json.dumps({'error': 'delegate_disabled', 'status': 'error',
            'message': '하위 에이전트는 이 서비스에서 사용할 수 없습니다.', 'code': 'D09'}, ensure_ascii=False)
    for name in registry.get_tool_names_for_toolset('delegation'):
        registry.get_entry(name).handler = denied
    # Upstream tool_executor._resolve_sequential_dispatch and invoke_tool's
    # INLINE_TOOL_EXECUTORS bypass the registry for delegate_task.
    agent_class._dispatch_delegate_task = lambda self, args: registry.get_entry('delegate_task').handler(args)


def enabled_toolsets(startup):
    if startup.get('allowed_tools') is not None:
        return ['yield_backend', 'yield_evidence', 'skills'] + (
            ['clarify'] if native_prompts_enabled(startup) else [])
    return ['yield_backend', 'yield_evidence', 'skills', 'memory', 'delegation'] \
        + (['code_execution', 'file', 'terminal', 'todo', 'session_search', 'vision', 'clarify']
           if startup.get('no_yield_tools', False) else []) \
        + (['clarify'] if native_prompts_enabled(startup) and not startup.get('no_yield_tools', False) else [])


def native_prompts_enabled(startup):
    return bool(startup.get('native_prompts', False) or startup.get('no_yield_tools', False))


def register_backend_tools(startup, registry, handler):
    allowed = startup.get('allowed_tools')
    allowed = None if allowed is None else set(allowed)
    for tool in startup['tools']:
        schema = tool['function']
        if allowed is not None and schema['name'] not in allowed:
            continue
        registry.register(name=schema['name'],
            toolset='yield_evidence' if schema['name'] == 'read_result' else 'yield_backend',
            schema=schema, handler=handler(schema['name']), description=schema['description'])


def refresh_backend_tool_pin(db, startup):
    session = db.get_session(startup['session_id'])
    if not session or not session.get('tool_names'):
        return False
    try:
        pin = json.loads(session['tool_names'])
    except (TypeError, ValueError):
        return False
    if not isinstance(pin, dict) or pin.get('version') is None or not isinstance(pin.get('tools'), list):
        return False
    if any(not isinstance(tool, dict) or not isinstance(tool.get('function'), dict)
           or not isinstance(tool['function'].get('name'), str) for tool in pin['tools']):
        return False
    allowed = startup.get('allowed_tools')
    current = {tool['function']['name']: tool for tool in startup['tools']
               if allowed is None or tool['function']['name'] in allowed}
    refreshed = [current.get(tool['function']['name'], tool) for tool in pin['tools']]
    if refreshed == pin['tools']:
        return False
    db.update_session_tool_names(startup['session_id'], {**pin, 'tools': refreshed})
    return True


def engine_provider(base_url):
    from agent.model_metadata import _infer_provider_from_url
    return _infer_provider_from_url(base_url) or 'custom'


def configure_runtime_tool_availability(registry, startup):
    if not startup.get('no_yield_tools', False):
        return
    if startup.get('api_key'):
        # The isolation scrub removes provider keys from os.environ. Vision still uses
        # the main model credential bound below, so its import-time env probe is stale.
        registry.get_entry('vision_analyze').check_fn = lambda: True
    refusal = json.dumps({'status': 'error',
        'error': 'Background processes are unsupported by the confined yieldrun runtime.'})
    terminal = registry.get_entry('terminal')
    terminal_handler = getattr(terminal, 'handler', None)
    if callable(terminal_handler) and not getattr(terminal_handler, '_yield_foreground_only', False):
        original = terminal.handler
        def foreground_only(args, **kwargs):
            if args.get('background', False):
                return refusal
            return original(args, **kwargs)
        foreground_only._yield_foreground_only = True
        terminal.handler = foreground_only
    process = registry.get_entry('process_manage')
    process_handler = getattr(process, 'handler', None)
    if callable(process_handler):
        process.handler = lambda args, **kwargs: refusal
    if not callable(terminal_handler) and not callable(process_handler):
        return
    from tools import terminal_tool
    terminal_tool.spawn_background_process = lambda **kwargs: {
        'status': 'error', 'error': json.loads(refusal)['error']}
    from tools import code_kernel_remote
    code_kernel_remote.execute_in_remote_kernel = lambda *args, **kwargs: None


def constrain_session_search(search_module):
    def profile_local_db(profile):
        if profile is None or not str(profile).strip():
            return None
        raise ValueError('cross-profile session search is disabled')
    search_module._resolve_profile_db = profile_local_db


def configure_agent_environment(isolation):
    if not isinstance(isolation, dict):
        raise RuntimeError('verified isolation configuration is required')
    backend = isolation.get('backend', 'ssh')
    if backend == 'ssh' and isolation.get('ssh_user') != 'yieldrun':
        raise RuntimeError('verified yieldrun isolation configuration is required')
    if backend not in {'ssh', 'seatbelt'}:
        raise RuntimeError('verified isolation configuration is required')
    workdir = Path(isolation['workdir'])
    for name in list(os.environ):
        upper = name.upper()
        if (upper in {'PYTHONPATH', 'PYTHONHOME', 'BASH_ENV', 'ENV', 'SSH_AUTH_SOCK', 'SSH_AGENT_PID'}
                or upper.endswith(('_PASSWORD', '_SECRET', '_TOKEN', '_API_KEY'))
                or upper.startswith(('ORACLE_', 'MONGO_', 'DATABASE_', 'HARNESS_MONGO_',
                                     'AWS_', 'AZURE_', 'GOOGLE_'))):
            os.environ.pop(name, None)
    venv = Path(isolation['analysis_venv'])
    os.environ.update({
        'TERMINAL_ENV': 'local' if backend == 'seatbelt' else 'ssh', 'TERMINAL_CWD': str(workdir),
        'VIRTUAL_ENV': str(venv), 'PATH': str(venv / 'bin') + os.pathsep + os.defpath,
        'HERMES_WRITE_SAFE_ROOT': str(workdir),
    })
    if backend == 'ssh':
        os.environ.update({
            'TERMINAL_SSH_HOST': isolation['ssh_host'], 'TERMINAL_SSH_USER': isolation['ssh_user'],
            'TERMINAL_SSH_PORT': str(isolation['ssh_port']), 'TERMINAL_SSH_KEY': isolation['ssh_key'],
        })
    else:
        os.environ['TERMINAL_LOCAL_PERSISTENT'] = 'false'
    # The adapter validates the fixed target and disables profile synchronization.
    engine_dir = str(Path(__file__).resolve().with_name('engine'))
    if engine_dir not in sys.path:
        sys.path.insert(0, engine_dir)
    from harness.isolation.backend import isolation_backend
    from harness.config import IsolationSettings, SeatbeltIsolationSettings
    if backend == 'seatbelt':
        config = SeatbeltIsolationSettings.model_validate({key: isolation[key] for key in (
            'backend', 'home', 'work_root', 'analysis_venv', 'launcher', 'sandbox')})
    else:
        config = IsolationSettings.model_validate({key: isolation[key] for key in (
            'ssh_host', 'ssh_user', 'ssh_port', 'ssh_key', 'known_hosts', 'home', 'work_root',
            'analysis_venv', 'launcher', 'sandbox')})
    run_id = isolation.get('run_id')
    principal_id = isolation.get('principal_id')
    if not run_id or not principal_id:
        raise RuntimeError('exact authenticated isolation scope is required')
    isolation_backend(config, run_id, principal_id).install_engine_adapter()
    return workdir


def verify_isolated_engine(startup):
    if not startup.get('no_yield_tools', False):
        return
    from tools.terminal_tool import _get_env_config
    from tools import terminal_tool_backends
    config = _get_env_config()
    isolation = startup['isolation']
    backend = isolation.get('backend', 'ssh')
    verified = (
        config.get('cwd') == isolation['workdir']
        and ((backend == 'seatbelt' and config.get('env_type') == 'local'
              and getattr(terminal_tool_backends._LocalEnvironment, '_yield_seatbelt_isolated', False))
             or (backend == 'ssh' and config.get('env_type') == 'ssh'
                 and getattr(terminal_tool_backends._SSHEnvironment, '_yield_isolated', False)))
    )
    if not verified:
        raise RuntimeError('Hermes isolated backend was replaced or misconfigured')


def install_tools_payload_logger(path):
    if not path:
        return
    from agent import turn_api_request
    original = turn_api_request._fire_pre_api_request_hook

    def log_tools(agent, api_kwargs, *args, **kwargs):
        names = []
        for tool in api_kwargs.get('tools') or []:
            function = tool.get('function') or {}
            name = function.get('name') or tool.get('name')
            if name:
                names.append(name)
        record = {'api_request_id': kwargs.get('api_request_id'), 'tools': names}
        with open(path, 'a', encoding='utf-8') as output:
            output.write(json.dumps(record, ensure_ascii=False) + '\n')
        return original(agent, api_kwargs, *args, **kwargs)

    turn_api_request._fire_pre_api_request_hook = log_tools


def prompt_options(startup, rpc, current_review):
    """H/tui_gateway/server.py:1104-1115; run_agent.py:253 has no approval callback."""
    if not native_prompts_enabled(startup):
        return {}

    def clarify(questions):
        cancelled = {'answers': {}, 'outcome': 'cancelled'}
        if current_review():
            return cancelled
        wire = [{'qid': q['qid'], 'question': q['question'], 'choices': q.get('choices'),
                 'multi_select': bool(q.get('multi_select', False))} for q in questions]
        try:
            result = rpc('server_request', request='clarify', params={'questions': wire})
        except (Empty, BrokenPipeError, ConnectionError):
            return cancelled
        if not isinstance(result, dict) or 'error' in result or result.get('outcome') == 'cancelled':
            return cancelled
        return {key: result[key] for key in ('answers', 'outcome', 'notice') if key in result}

    return {'clarify_callback': clarify}


def main():
    input_stream = getattr(sys.stdin, 'buffer', sys.stdin)
    startup = json.loads(input_stream.readline())
    if startup.get('no_yield_tools', False):
        configure_agent_environment(startup.get('isolation'))
    output = sys.stdout
    sys.stdout = sys.stderr  # Upstream diagnostic output must not corrupt RPC.
    lock = threading.Lock()
    pending = ipc.Waiters()
    controller = WorkerControl(pending)
    deadline = time.monotonic() + startup['run_budget_seconds']
    review_id = None
    review_known_ids = set()
    def emit(message):
        with lock:
            output.write(ipc.encode(message))
            output.flush()
    threading.Thread(target=ipc.receive, args=(input_stream, pending, controller.handle), daemon=True).start()
    def rpc(kind, **payload):
        identity, queue = pending.register()
        try:
            if pending.closed is None:
                emit(ipc.call(kind, identity, **payload))
            return queue.get(timeout=max(.1, deadline - time.monotonic()))
        finally:
            pending.discard(identity)
    active_tool_call = threading.local()
    def tool_start(call_id, _name, _args):
        active_tool_call.id = call_id
    def tool_complete(call_id, name, args, result):
        if name in ('skills_list', 'skill_view', 'skill_manage'):
            emit({'type': 'skill_result', 'invocation_id': call_id, 'name': name,
                  'args': args, 'result': result})
        if getattr(active_tool_call, 'id', None) == call_id:
            del active_tool_call.id
    def handler(name):
        def call(args, **kwargs):
            if review_id and name != 'read_result':
                return json.dumps({'error': 'Verifier can only read stored results'})
            result = rpc('tool_call', name=name, args=args, review_id=review_id,
                tool_call_id=getattr(active_tool_call, 'id', None))
            if review_id and result.get('status') in ('success', 'partial', 'empty'):
                review_known_ids.add(result['result_id'])
                review_known_ids.update(result.get('source_result_ids', []))
            return json.dumps(result, ensure_ascii=False)
        return call
    budget = None
    restore_gate = None
    agent = None
    db = None
    terminal = None
    agents = controller.agents
    try:
        from native_verification import REVIEW_SCHEMA, checked_report, unknown, final_messages
        from native_budget import install_http_budget
        from native_stop_gate import NativeVerificationGate
        def budget_update(snapshot):
            emit({'type': 'step', 'count': 0, **snapshot})
            if budget is not None and budget.exhausted_reason:
                for active in agents:
                    active.interrupt()
        budget = install_http_budget(model_limit=startup['max_iterations'],
            token_limit=startup['token_limit'], max_output_tokens=startup['max_tokens'],
            base_url=startup['base_url'], on_usage=budget_update, call_timeout=startup['call_timeout'])
        os.environ['HERMES_API_TIMEOUT'] = str(startup['call_timeout'])
        from tools import skills_tool
        observe_skill_reads(skills_tool, emit)
        from tools.registry import registry
        if startup.get('no_yield_tools', False):
            from tools.registry import discover_builtin_tools
            discover_builtin_tools()
        configure_runtime_tool_availability(registry, startup)
        verify_isolated_engine(startup)
        register_backend_tools(startup, registry, handler)
        from run_agent import AIAgent
        from agent import title_generator, tool_executor
        # The legacy planner/verifier owns a fixed model budget and never projects
        # model-generated titles. Keep its opt-out local to this process.
        title_generator._model_title_upgrade_enabled = lambda: False
        # Import registers delegation before replacing only its model-facing handler.
        from tools import delegate_tool
        configure_engine(registry, tool_executor, startup.get('parallel_tool_limit', 8), AIAgent)
        from tools import session_search_tool
        constrain_session_search(session_search_tool)
        install_tools_payload_logger(startup.get('tools_log'))
        from gateway.session_context import declare_stateless_channel
        # This worker exits after its response; delegated results must arrive before then.
        declare_stateless_channel()
        def usage():
            return budget.snapshot()
        def step(count, previous):
            with budget.lock:
                used = usage()
                emit({'type': 'step', 'count': count, **used})
            if budget.exhausted_reason or time.monotonic() >= deadline:
                for active in agents:
                    active.interrupt()
        provider = engine_provider(startup['base_url']) if startup.get('no_yield_tools', False) else None
        if provider:
            from agent.auxiliary_client import set_runtime_main
            set_runtime_main(provider, startup['model'], base_url=startup['base_url'],
                api_key=startup['api_key'], api_mode='chat_completions', session_id=startup['session_id'])
        from hermes_state_registry import acquire
        db = acquire(Path(os.environ['HERMES_HOME']) / 'state.db')
        if db.get_session(startup['session_id']):
            db.reopen_session(startup['session_id'])
        refresh_backend_tool_pin(db, startup)
        agent = AIAgent(model=startup['model'], base_url=startup['base_url'], api_key=startup['api_key'],
            provider=provider,
            enabled_toolsets=enabled_toolsets(startup),
            **prompt_options(startup, rpc, lambda: review_id),
            max_iterations=startup['max_iterations'], max_tokens=startup['max_tokens'],
            run_budget_seconds=startup['run_budget_seconds'], session_db=db,
            session_id=startup['session_id'], platform='api_server',
            reasoning_config={'enabled': True, 'effort': startup['reasoning_effort']} if startup.get('reasoning_effort') else None,
            request_overrides={'temperature': startup['temperature']},
            quiet_mode=True, skip_context_files=False, load_soul_identity=False,
            step_callback=step,
            stream_delta_callback=lambda text: emit({'type': 'commentary', 'content': text, 'delta': True}),
            interim_assistant_callback=lambda content, already_streamed=False: emit({
                'type': 'commentary', 'content': content, 'already_streamed': bool(already_streamed)}),
            tool_start_callback=tool_start, tool_complete_callback=tool_complete)
        controller.add(agent)
        from tools.delegate_tool import _build_child_preserving_parent_tools, _run_single_child
        from tools.delegation_output_schema import extract_json_candidate
        review_attempt = 0
        def review(candidate):
            nonlocal review_id, review_attempt
            remaining = startup['max_iterations'] - usage()['models']
            budget_unavailable = (remaining < 1 or time.monotonic() >= deadline
                or usage()['tokens'] >= startup['token_limit'])
            review_attempt += 1
            review_id = 'verify-' + uuid.uuid4().hex
            review_known_ids.clear()
            started = time.monotonic()
            entry = None
            verifier_exit = None
            try:
                bundle = rpc('verification_start', review_id=review_id, candidate=candidate.get('final_response', ''), attempt=review_attempt)
                if budget_unavailable:
                    report = unknown('검증에 필요한 실행 예산이 남아 있지 않습니다.')
                    verifier_exit = {'status': 'not_started', 'exit_reason': 'budget_unavailable'}
                elif bundle.get('error'):
                    report = unknown('저장된 검증 근거를 불러오지 못했습니다.')
                    verifier_exit = {'status': 'not_started', 'exit_reason': 'evidence_unavailable'}
                else:
                    goal = startup['verification_instructions'] + '\n\n출력 JSON schema:\n' + json.dumps(REVIEW_SCHEMA) + '\n\n검토 자료(JSON):\n' + json.dumps(bundle, ensure_ascii=False)
                    child = _build_child_preserving_parent_tools(task_index=review_attempt, goal=goal,
                        context=None, toolsets=['yield_evidence'], model=None,
                        max_iterations=min(6, remaining), task_count=1, parent_agent=agent)
                    child.run_budget_seconds = max(.1, deadline - time.monotonic())
                    child.max_tokens = startup['max_tokens']
                    child.step_callback = step
                    child._delegate_output_schema = REVIEW_SCHEMA
                    controller.add(child)
                    try:
                        entry = _run_single_child(review_attempt, goal, child=child, parent_agent=agent)
                    finally:
                        if not isinstance(entry, dict) or entry.get('status') != 'timeout':
                            controller.remove(child)
                    # Use the same JSON envelope parsing as Hermes' schema check.
                    entry['summary'] = extract_json_candidate(entry.get('summary') or '')
                    report = checked_report(entry, set(bundle['known_result_ids']) | review_known_ids)
            except Exception:
                report = unknown('검증 에이전트 실행 중 오류가 발생했습니다.')
                if entry is None:
                    verifier_exit = {'status': 'error', 'exit_reason': 'exception'}
            finally:
                current_id, review_id = review_id, None
            if budget.exhausted_reason:
                report = unknown('공유 실행 예산에서 추가 모델 호출이 차단되었습니다: ' + budget.exhausted_reason)
            report['verifier_exit'] = ({key: entry[key]
                for key in ('status', 'exit_reason', 'truncated')
                if isinstance(entry, dict) and key in entry} if isinstance(entry, dict)
                else verifier_exit or {'status': 'error', 'exit_reason': 'exception'})
            if budget.exhausted_reason:
                report['verifier_exit']['budget_reason'] = budget.exhausted_reason
            settled = rpc('verification_finished', review_id=current_id, report=report, elapsed=time.monotonic() - started)
            step(0, None)
            return settled.get('report') or unknown('호스트의 최종 검증 판정을 받지 못했습니다.')
        gate = NativeVerificationGate(review, lambda: emit({'type': 'commentary',
            'content': '검증에서 확인한 문제를 보완하고 결과를 다시 확인하겠습니다.'}))
        restore_gate = gate.install(agent)
        result = agent.run_conversation(startup['query'], system_message=startup['instructions'],
            conversation_history=startup['history'])
        result = gate.finish(result)
        if budget.exhausted_reason:
            result.update(completed=False, partial=True, turn_exit_reason=budget.exhausted_reason)
        messages = final_messages(result.get('messages', []), [], result['final_response'],
            turn_id=result.get('turn_id'), current_turn_user_idx=result.get('current_turn_user_idx'),
            conversation_history=startup['history'], user_message=startup['query'])
        totals = usage()
        result.update(messages=messages, input_tokens=totals['input_tokens'], output_tokens=totals['output_tokens'],
            total_tokens=totals['tokens'], api_calls=totals['models'])
        terminal = {'type': 'result', 'result': result}
    except Exception as exc:
        message = str(exc).replace(startup.get('api_key') or 'UNUSED_SECRET', '[redacted]')
        terminal = {'type': 'error', 'message': type(exc).__name__ + ': ' + message[:1000]}
    finally:
        if restore_gate:
            restore_gate()
        if any(active is not agent for active in controller.snapshot()):
            from hermes_state_registry import stats
            settle_deadline = time.monotonic() + 10
            while stats()['total_refcounts'] > 1 and time.monotonic() < settle_deadline:
                time.sleep(.05)
            if stats()['total_refcounts'] <= 1:
                for active in controller.snapshot():
                    if active is not agent:
                        controller.remove(active)
        if agent is not None:
            session_messages = getattr(agent, '_session_messages', None)
            if isinstance(session_messages, list) and session_messages:
                try:
                    agent._persist_session(session_messages, startup['history'])
                except Exception:
                    print('Worker final session persist failed', file=sys.stderr)
            try:
                agent.close()
            except Exception:
                print('Worker parent cleanup failed', file=sys.stderr)
            controller.remove(agent)
        if db is not None:
            from hermes_state_registry import release_or_close
            try:
                release_or_close(db)
            except Exception:
                print('Worker session store cleanup failed', file=sys.stderr)
        # Keep HTTP admission active until this dedicated interpreter exits.
    if terminal is not None:
        emit(terminal)


if __name__ == '__main__':
    main()
    # The daemon receiver can still hold stdin's buffered lock. After main's
    # cleanup, flush output and bypass interpreter teardown of that live reader.
    sys.__stdout__.flush()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)

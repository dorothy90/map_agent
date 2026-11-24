"""Run-scoped Hermes process. Domain tool calls stay in this process."""
import asyncio
import json
import logging
import os
import signal
from pathlib import Path
import sys
import threading
import uuid

HERE = Path(__file__).resolve().parent


def bind_provider_secret(provider, base_url, key):
    from agent.secret_scope import set_secret_scope
    from providers import get_provider_profile

    # Native auxiliary tools check the profile's named credential even when the
    # main agent received an explicit key. Keep it out of subprocess environments.
    profile = get_provider_profile(provider)
    if profile and profile.env_vars and key and base_url.rstrip('/') == (profile.base_url or '').rstrip('/'):
        return set_secret_scope({profile.env_vars[0]: key}, profile_home=os.environ['HERMES_HOME'])


async def resume_session(db, store, run):
    from agent.replay_cleanup import canonicalize_replay_history
    from harness.native_hermes import native_history

    session = db.resolve_resume_session_id(run['session_id'])
    if db.get_session(session) is None:
        # A pre-migration session can have only a web transcript. Import it
        # once; native sessions always resume from their own profile's state.db.
        owner = {key: run[key] for key in ('principal_id', 'session_id')}
        previous = []
        async for turn in store.runs.find({
            **owner, 'active': False, 'created_at': {'$lt': run['created_at']},
        }).sort('created_at', -1):
            if (turn.get('native_engine') or {}).get('history_source') == 'hermes_session_db':
                raise RuntimeError('The native Hermes session is missing from the employee profile')
            previous.append(turn)
            if turn.get('native_messages'):
                break
        if previous:
            imported = db.import_sessions([{
                'id': session, 'source': 'yield_web', 'messages': native_history(previous),
            }])
            if not imported['ok']:
                raise ValueError('Could not import the existing web session into Hermes')
    raw, _ = db.get_resume_conversations(session)
    return session, canonicalize_replay_history(raw)


def refresh_web_tool_pin(db, session, saved, tools, web_schema_names):
    # Upstream resume carries old direct tools forward even after they become deferred.
    if not saved or not saved.get('tool_names'):
        return
    try:
        pin = json.loads(saved['tool_names'])
    except (TypeError, ValueError):
        return  # Hermes rebuilds an unreadable pin itself.
    visible = {tool['function']['name']: tool for tool in tools}
    if isinstance(pin, list):
        # Names-only pins need rebuilding when the web catalog changes.
        if any(not isinstance(name, str) for name in pin) or set(pin) != visible.keys():
            db.update_session_tool_names(session, None)
    elif isinstance(pin, dict) and isinstance(pin.get('tools'), list):
        # Keep the cached prefix/order except where the current web contracts changed.
        # tool_search's description contains this employee's deferred catalog.
        refreshed = []
        for tool in pin['tools']:
            if isinstance(tool, str):
                if tool in visible:
                    refreshed.append(visible[tool])
                continue
            schema = tool.get('function') if isinstance(tool, dict) else None
            name = schema.get('name') if isinstance(schema, dict) else None
            if isinstance(name, str) and name in visible:
                refreshed.append(visible[name] if name in web_schema_names or name == 'tool_search' else tool)
        retained = {tool['function']['name'] for tool in refreshed}
        refreshed.extend(tool for name, tool in visible.items() if name not in retained)
        if refreshed != pin['tools']:
            db.update_session_tool_names(session, {**pin, 'tools': refreshed})


async def chat(request, emit, waiters=None):
    from harness.config import Settings
    from harness.executor import ExecutionContext, ToolExecutor
    from harness.gateway.pending_controls import PendingControls
    from harness.native_hermes import NativePrompts, prompt_history, result_status
    from harness.native_tools import register_yield_tools
    from harness.shared_file_tools import SHARED_FILE_TOOLS, install_shared_file_policy
    from harness.web_delegation import configure_web_delegation
    from harness.native_budget import install_http_budget
    from harness.store import HarnessStore
    from harness.tools.registry import domain_registry
    from harness.instructions import web_turn_instructions
    from harness.chat_tool_helpers import constrain_session_search, engine_provider, observe_skill_reads
    from tools import skills_tool, session_search_tool
    from tools.approval_context import _approval_tool_call_id
    from tools.registry import registry
    from toolsets import create_custom_toolset
    from run_agent import AIAgent
    from agent.session_persistence import adopt_unanswered_turn
    from hermes_state_registry import acquire, release_or_close
    from gateway.platforms.base import BasePlatformAdapter
    from agent.auxiliary_client import _RELAY_AUX_CALL_CONTEXT, set_runtime_main
    from agent.title_generator import wait_for_title_upgrades
    from agent import tool_executor
    from agent.secret_scope import reset_secret_scope

    settings = Settings.model_validate(request['settings'])
    tool_executor._MAX_TOOL_WORKERS = min(tool_executor._MAX_TOOL_WORKERS, settings.parallel_tool_limit)
    store = HarnessStore(settings.mongo_uri, settings.mongo_db)
    db = budget = secret_scope = None
    try:
        run = await store.fence(request['run_id'], request['epoch'])
        if any(run[key] != request[key] for key in ('principal_id', 'session_id')):
            raise PermissionError('Hermes chat ownership changed')
        ctx = ExecutionContext(store, settings, run)
        loop = asyncio.get_running_loop()
        agent = None
        stopping = False
        invocations = set()
        published_files = {}

        class ChatToolExecutor(ToolExecutor):
            async def execute(self, call):
                if stopping:
                    raise asyncio.CancelledError
                task = asyncio.current_task()
                invocations.add(task)
                try:
                    return await super().execute(call)
                finally:
                    invocations.discard(task)

        def interrupt():
            nonlocal stopping
            stopping = True
            for task in list(invocations):
                task.cancel()
            if agent is not None:
                agent.interrupt()

        loop.add_signal_handler(signal.SIGTERM, interrupt)
        executor = ChatToolExecutor(domain_registry(), ctx)
        code_enabled = settings.web_python_enabled
        allowed = set(executor.registry.tools if settings.native_allowed_tools is None else settings.native_allowed_tools)
        allowed.difference_update({'run_python', 'execute_code'})
        yield_names = register_yield_tools(registry, executor, loop,
            invocation_id=_approval_tool_call_id.get, allowed=allowed)
        if code_enabled:
            from harness import native_ipc as ipc
            from tools import code_kernel, code_execution_tool
            from copy import deepcopy

            # The stock schema advertises host RPC stubs. The web kernel's OS boundary
            # supports Python and explicitly published files; keep the original inputs.
            schema = deepcopy(code_execution_tool.EXECUTE_CODE_SCHEMA)
            schema['description'] = (
                'Run Python in this chat’s isolated persistent Hermes kernel. Variables and imports survive '
                'later calls and turns. Read query_db CSV paths with pd.read_csv. Use print for output. '
                'read_result(export_for_python=true) exports other stored tables as full CSVs. Use '
                'emit_table and emit_plot for chat artifacts. Write finished files to publish_directory '
                '(a Path) to attach them to this reply.'
            )
            schema['parameters']['properties']['code']['description'] = 'Python source to execute in this session.'
            entry = registry.get_entry('execute_code')
            entry.schema = schema
            entry.description = schema['description']
            code_execution_tool.build_execute_code_schema = lambda *args, **kwargs: schema

            def isolated_cell(code, *args, reset=False, **kwargs):
                identity, queue = waiters.register()
                try:
                    emit(ipc.call('tool_call', identity, name='execute_code',
                                  invocation_id=_approval_tool_call_id.get() or uuid.uuid4().hex,
                                  args={'code': code, 'reset': reset}))
                    result = queue.get(timeout=max(1, ctx.remaining_seconds()))
                    for ref in result.get('artifact_refs', []):
                        if path := ref.get('output_path'):
                            published_files[path] = f'MEDIA:"{path}"'
                    return json.dumps(result, ensure_ascii=False)
                finally:
                    waiters.discard(identity)

            # Preserve upstream argument/approval checks and route both terminal profiles
            # through the authenticated gateway kernel. Neither path can launch on the host.
            code_kernel.execute_in_session_kernel = isolated_cell
            code_execution_tool._execute_remote = isolated_cell
        install_shared_file_policy(registry, settings.shared_yield_dir)
        create_custom_toolset('web_shared_files', 'Read shared yield files and images',
                              tools=list(SHARED_FILE_TOOLS))
        configure_web_delegation()
        constrain_session_search(session_search_tool)
        observe_skill_reads(skills_tool, emit)
        pending = PendingControls(store, run['principal_id'])
        await pending.setup()
        prompts = NativePrompts(ctx, pending)

        def clarify(questions):
            return asyncio.run_coroutine_threadsafe(prompts.clarify({'questions': questions}), loop).result()

        def tool_complete(call_id, name, args, result):
            if name in ('skills_list', 'skill_view', 'skill_manage'):
                emit({'type': 'skill_result', 'name': name, 'args': args, 'result': result, 'invocation_id': call_id})

        remaining_models = settings.model_limit - run['usage']['models']
        budget = install_http_budget(model_limit=remaining_models,
            token_limit=settings.token_limit - run['usage']['tokens'], max_output_tokens=settings.max_output_tokens,
            base_url=settings.base_url, call_timeout=settings.call_timeout,
            is_optional_call=lambda: (_RELAY_AUX_CALL_CONTEXT.get() or {}).get('task') == 'title_generation',
            on_usage=lambda snap: emit({'type': 'step', 'count': 0, **snap}))
        db = acquire(Path(os.environ['HERMES_HOME']) / 'state.db')
        session, history = await resume_session(db, store, run)
        manual_title = request.get('manual_title')

        def sync_manual_title():
            try:
                db.set_session_title(session, manual_title)
            except ValueError:
                # Older web titles can conflict with the native unique-title index.
                logging.getLogger(__name__).warning('Could not mirror the manual title for session %s', session)

        def publish_title(title, source):
            emit({'type': 'session_title', 'title': title, 'source': source})

        saved = db.get_session(session)
        if saved:
            db.reopen_session(session)
            if manual_title:
                if (saved['title'], saved['title_source']) != (manual_title, 'user'):
                    sync_manual_title()
            elif saved['title'] and saved['title_source'] in ('derived', 'llm'):
                publish_title(saved['title'], saved['title_source'])
        provider = engine_provider(settings.base_url)
        api_key = settings.api_key.get_secret_value()
        secret_scope = bind_provider_secret(provider, settings.base_url, api_key)
        set_runtime_main(provider, settings.model, base_url=settings.base_url, api_key=api_key,
                         api_mode='chat_completions', session_id=session)
        def web_text(text):
            return BasePlatformAdapter.strip_media_directives_for_display(text)

        def display_history(messages):
            # The web transcript renders clarify cards from events. Model replay reads state.db.
            return [{**m, 'content': web_text(m['content'])}
                    if m.get('role') == 'assistant' and isinstance(m.get('content'), str) else m
                    for m in prompt_history(messages)]

        # A file-delivery directive can be split across model chunks.
        pending_line = ''

        def stream_delta(text):
            nonlocal pending_line
            complete, newline, pending_line = (pending_line + text).rpartition('\n')
            if newline:
                emit({'type': 'commentary', 'content': web_text(complete) + newline, 'delta': True})

        def interim(text, already_streamed=False):
            nonlocal pending_line
            if already_streamed and pending_line:
                emit({'type': 'commentary', 'content': web_text(pending_line), 'delta': True})
            pending_line = ''
            emit({'type': 'commentary', 'content': web_text(text), 'already_streamed': already_streamed})

        def converse():
            nonlocal agent
            agent = AIAgent(model=settings.model, base_url=settings.base_url, api_key=api_key,
                provider=provider, session_id=session, session_db=db, platform='yield_web',
                ephemeral_system_prompt=web_turn_instructions(request['request_context'], code_enabled=code_enabled),
                enabled_toolsets=['yield', 'skills', 'memory', 'session_search', 'clarify',
                                 'web_shared_files', 'todo', 'delegation'] + (['code_execution'] if code_enabled else []),
                max_iterations=max(1, remaining_models), max_tokens=settings.max_output_tokens,
                run_budget_seconds=ctx.remaining_seconds(), quiet_mode=True,
                reasoning_config={'enabled': True, 'effort': settings.reasoning_effort} if settings.reasoning_effort else None,
                request_overrides={'temperature': settings.temperature}, clarify_callback=clarify,
                stream_delta_callback=stream_delta, interim_assistant_callback=interim,
                tool_complete_callback=tool_complete)
            agent._on_session_title = publish_title
            try:
                refresh_web_tool_pin(db, session, saved, agent.tools, (*yield_names, *SHARED_FILE_TOOLS, 'execute_code'))
                if stopping:
                    return {'interrupted': True, 'turn_exit_reason': 'interrupted'}
                query = run.get('goal_request', run['query'])
                if run['epoch'] > 1 and history and history[-1]['role'] == 'user':
                    adopt_unanswered_turn(history, query, agent)
                return agent.run_conversation(query,
                                              conversation_history=history,
                                              title_user_message='' if manual_title else run['query'])
            finally:
                if manual_title and not saved:
                    sync_manual_title()
                wait_for_title_upgrades(timeout=min(10.0, ctx.remaining_seconds()))
                agent.close()

        result = await asyncio.to_thread(converse)
        status = result_status(result)
        answer = web_text(result.get('final_response') or '').strip()
        missing = [tag for path, tag in published_files.items()
                   if tag not in answer and f"]({path})" not in answer]
        if missing:
            answer = "\n\n".join(filter(None, [answer, *missing]))
        if budget.exhausted_reason:
            status = 'partial'
        if not answer:
            if status == 'completed':
                status = 'partial'
            answer = '실행이 종료되어 최종 답변을 생성하지 못했습니다.'
        totals = budget.snapshot()
        return {'answer': answer, 'answer_text': answer, 'status': status,
                'stop_reason': budget.exhausted_reason or result.get('turn_exit_reason') or status,
                'native_messages': display_history(result.get('messages', [])),
                'native_usage': {'api_calls': totals['models'], 'input_tokens': totals['input_tokens'],
                                 'output_tokens': totals['output_tokens'], 'total_tokens': totals['tokens']}}
    finally:
        if secret_scope is not None:
            reset_secret_scope(secret_scope)
        if db is not None:
            release_or_close(db)
        if budget is not None:
            budget.close()
        store.client.close()


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(HERE / 'engine'), str(HERE.parent)]
    output, sys.stdout = sys.stdout, sys.stderr
    lock = threading.Lock()

    def emit(message):
        with lock:
            output.write(json.dumps(message, ensure_ascii=False, default=str) + '\n')
            output.flush()

    from hermes_cli.plugins_discovery import suppress_plugin_discovery
    with suppress_plugin_discovery():
        from harness import native_ipc as ipc
        request = json.loads(sys.stdin.buffer.readline())
        waiters = ipc.Waiters()
        threading.Thread(target=ipc.receive, args=(os.fdopen(os.dup(0), 'rb'), waiters, lambda _: None),
                         daemon=True).start()
        result = asyncio.run(chat(request, emit, waiters))
    emit({'type': 'result', 'result': result})

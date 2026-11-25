"""Run-scoped Hermes process. Domain tool calls stay in this process."""
import asyncio
from functools import wraps
import json
import logging
import os
import re
import signal
from pathlib import Path
import sys
import threading
import uuid

HERE = Path(__file__).resolve().parent


# GLM may place a serialized function frame directly after prose, then hit its
# output limit before the closing tag. A literal mention of the tag is prose.
_WEB_TOOL_FRAME = re.compile(r"<tool_call>\s*[\w.:-]+\s*<arg_key>.*?(?:</tool_call>|\Z)", re.DOTALL)


# Preserve the canonical, URL-encoded download references produced by the store.
# Raw host paths still pass through Hermes' normal display filter.
_WEB_OUTPUT_MEDIA = re.compile(r'(MEDIA:"/outputs/[^/\s"]+/[^/\s"]+/[^/\s"]+")')


def visible_web_text(text):
    from gateway.platforms.base import BasePlatformAdapter
    parts = _WEB_OUTPUT_MEDIA.split(_WEB_TOOL_FRAME.sub("", text))
    return "".join(part if index % 2 else BasePlatformAdapter.strip_media_directives_for_display(part)
                   for index, part in enumerate(parts))


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

    job_id = run.get('web_schedule_job_id')
    session = db.resolve_resume_session_id(f"cron_{job_id}_{run['run_id']}" if job_id else run['session_id'])
    if not job_id and db.get_session(session) is None:
        # A pre-migration session can have only a web transcript. Import it
        # once; native sessions always resume from their own profile's state.db.
        owner = {key: run[key] for key in ('principal_id', 'session_id')}
        previous = []
        async for turn in store.runs.find({
            **owner, 'active': False, 'web_schedule_job_id': {'$exists': False}, 'created_at': {'$lt': run['created_at']},
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


async def schedule_context(store, run):
    from cron.scheduler import _is_cron_silence_response
    from cron.scheduler_prompt import (_clip_to_context_budget, _prepend_context_block,
                                        _SELF_CONTEXT_INTRO, _UPSTREAM_CONTEXT_INTRO)
    from harness.gateway.events import published_answer

    context = ''
    options = run.get('web_schedule_options') or {}
    for ref in options.get('context_from') or ():
        source = run['web_schedule_job_id'] if ref.lower() == 'self' else ref
        query = {'principal_id': run['principal_id'], 'session_id': run['session_id'],
                 'web_schedule_job_id': source, 'created_at': {'$lt': run['created_at']},
                 'status': 'completed', 'active': False}
        async for prior in store.runs.find(query).sort('created_at', -1):
            answer = published_answer(prior)
            if not answer or _is_cron_silence_response(answer):
                continue
            own = source == run['web_schedule_job_id']
            context = _prepend_context_block(context, "Your previous run's output" if own else f"Output from job '{source}'",
                _SELF_CONTEXT_INTRO if own else _UPSTREAM_CONTEXT_INTRO, _clip_to_context_budget(answer))
            break
    return context


async def attach_schedule_reports(db, store, session, run):
    from cron.scheduler import _is_cron_silence_response
    from cron.scheduler_delivery import _cron_mirror_message
    from harness.gateway.events import published_answer

    query = {'principal_id': run['principal_id'], 'session_id': run['session_id'],
             'created_at': {'$lt': run['created_at']}, 'status': 'completed',
             'web_schedule_options.attach_to_session': True, 'web_schedule_mirrored': {'$ne': True}}
    attached = False
    async for prior in store.runs.find(query).sort('created_at', 1):
        answer = published_answer(prior)
        if answer and not _is_cron_silence_response(answer):
            if db.get_session(session) is None:
                db.create_session(session, 'yield_web')
            marker = 'web-cron:' + prior['run_id']
            if not db.has_platform_message_id(session, marker):
                job = {'id': prior['web_schedule_job_id'], 'name': prior['web_schedule_options'].get('name')}
                db.append_message(session, 'user', _cron_mirror_message(job, answer), platform_message_id=marker)
            attached = True
        await store.runs.update_one({'_id': prior['_id']}, {'$set': {'web_schedule_mirrored': True}})
    return attached


def turn_query(run, context=None):
    prompt = run.get('goal_request', run['query'])
    if not (job_id := run.get('web_schedule_job_id')):
        return prompt
    from cron.scheduler import _build_job_prompt

    return _build_job_prompt({'id': job_id, 'prompt': prompt, 'skills': run.get('web_schedule_skills', [])},
                             runtime_data_prompt=context)


def outdated_descriptions(history, permitted, get_schema):
    """Identify stale tool_describe results without changing the saved conversation."""
    latest = {}
    for message in history:
        if message.get('role') != 'tool' or (message.get('tool_name') or message.get('name')) != 'tool_describe':
            continue
        try:
            result = json.loads(message['content'])
        except (KeyError, TypeError, ValueError):
            continue
        described = result.get('tools') if isinstance(result, dict) else None
        if isinstance(described, dict):
            latest.update((name, described[name]) for name in permitted.intersection(described))
    return {name for name, previous in latest.items()
            if (current := get_schema(name)) and
            previous != {key: current.get(key) for key in ('description', 'parameters')}}


def require_current_descriptions(registry, pending):
    # A web worker has its own registry. Only guard tools with a superseded contract.
    def guarded(name, handler):
        @wraps(handler)
        def invoke(args, **kwargs):
            if name in pending:
                return json.dumps({'error': f'The saved schema for {name} has changed. '
                                   'Call tool_describe for this tool, then choose the action for the user’s request.'})
            return handler(args, **kwargs)
        return invoke

    for name in pending:
        entry = registry.get_entry(name)
        entry.handler = guarded(name, entry.handler)


def is_mcp_display_tool(name, args):
    if name == 'manage_connections' or name.startswith('mcp__'):
        return True
    if name != 'tool_call' or not isinstance(args, dict):
        return False
    calls = args.get('calls', [args])
    return isinstance(calls, list) and bool(calls) and all(
        isinstance(call, dict) and isinstance(call.get('name'), str) and call['name'].startswith('mcp__')
        for call in calls)


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
                refreshed.append(visible[name] if name in web_schema_names or name == 'tool_search' or name.startswith('mcp__') else tool)
        retained = {tool['function']['name'] for tool in refreshed}
        refreshed.extend(tool for name, tool in visible.items() if name not in retained)
        if refreshed != pin['tools']:
            db.update_session_tool_names(session, {**pin, 'tools': refreshed})


async def chat(request, emit, waiters=None):
    from harness.config import Settings
    from harness.instructions import web_turn_instructions
    from harness.executor import ExecutionContext, ToolExecutor
    from harness.gateway.pending_controls import PendingControls
    from harness.native_hermes import NativePrompts, prompt_history, result_status
    from harness.native_tools import register_yield_tools
    from harness.personal_file_tools import PERSONAL_FILE_TOOLS, install_personal_file_policy
    from harness.shared_file_tools import SHARED_FILE_TOOLS, install_shared_file_policy
    from harness.web_kanban import WEB_KANBAN_TOOLS, install_web_kanban
    from harness.web_kanban_attachments import ATTACHMENT_TOOLS, install_attachment_tools, read_chat_artifact
    from harness.web_projects import current_project, install_web_projects
    from harness.web_cron_tool import install_web_cron_tool
    from harness.web_delegation import configure_web_delegation
    from harness.web_mcp_tools import attach_employee_mcp_toolsets, shutdown_employee_mcp_tools
    from harness.native_budget import install_http_budget
    from harness.store import HarnessStore
    from harness.tools.registry import native_domain_registry
    from harness.chat_tool_helpers import constrain_session_search, engine_provider, observe_skill_reads
    from tools import skills_tool, session_search_tool
    from tools.approval_context import _approval_tool_call_id
    from tools.registry import registry
    from toolsets import create_custom_toolset
    from run_agent import AIAgent
    from agent.session_persistence import adopt_unanswered_turn
    from hermes_state_registry import acquire, release_or_close
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
        schedule_options = run.get('web_schedule_options') or {}
        provider = engine_provider(settings.base_url)
        if schedule_options.get('model'):
            if provider != schedule_options.get('provider'):
                raise ValueError('The pinned schedule provider is unavailable.')
            settings = settings.model_copy(update={'model': schedule_options['model']})
        chat_row = None
        if not run.get('web_schedule_job_id'):
            chat_row = await store.db.gateway_sessions.find_one({
                'principal_id': run['principal_id'], 'id': run['session_id'],
                'deleted': {'$ne': True}}, {'ended_at': 1})
        can_group_chats = chat_row is not None
        can_schedule = request.get('web_scheduling_enabled') is True and can_group_chats and chat_row.get('ended_at') is None
        ctx = ExecutionContext(store, settings, run, web_map_jobs_enabled=(
            can_group_chats and request.get("web_map_jobs_enabled") is True))
        loop = asyncio.get_running_loop()
        if can_group_chats:
            def session_project(project):
                task = current_project(store.db.gateway_sessions, run, project)
                return asyncio.run_coroutine_threadsafe(task, loop).result()
            install_web_projects(registry, Path(os.environ['HERMES_HOME']), store.db.name, session_project)
            create_custom_toolset('web_projects', 'Group your chats by project', tools=['desktop_project'])
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
                    obs = await super().execute(call)
                    for ref in obs.artifact_refs:
                        if path := ref.get("output_path"):
                            published_files[path] = f'MEDIA:"{path}"'
                    return obs
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
        executor = ChatToolExecutor(native_domain_registry(), ctx)
        allowed = set(executor.registry.tools if settings.native_allowed_tools is None else settings.native_allowed_tools)
        can_use_kanban = 'load_wafer_maps' in allowed
        if can_use_kanban:
            profile = Path(os.environ['HERMES_HOME'])
            install_web_kanban(registry, profile, store.db.name, run,
                               invocation_id=_approval_tool_call_id.get, emit=emit)
            def read_source(cmd, cap):
                return asyncio.run_coroutine_threadsafe(read_chat_artifact(store, run, cmd, cap), loop).result()
            install_attachment_tools(registry, profile, store.db.name, run, read_source=read_source,
                                     publish=published_files.__setitem__)
            create_custom_toolset('web_kanban', 'Personal persistent defect items',
                                  tools=[*WEB_KANBAN_TOOLS, *ATTACHMENT_TOOLS])
        code_enabled = settings.web_python_enabled
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
        from cron.scheduler import _resolve_cron_disabled_toolsets
        from hermes_cli.config import load_config_readonly

        cron_disabled = _resolve_cron_disabled_toolsets(load_config_readonly())
        selected_toolsets = schedule_options.get('enabled_toolsets')
        personal_files_enabled = not run.get('web_schedule_job_id') or (
            'web_personal_files' not in cron_disabled
            and (selected_toolsets is None or 'web_personal_files' in selected_toolsets))
        install_personal_file_policy(registry, Path(os.environ['HERMES_HOME']), store.db.name,
                                     settings.shared_yield_dir, enabled=personal_files_enabled)
        create_custom_toolset('web_personal_files', 'Edit personal workspace files', tools=list(PERSONAL_FILE_TOOLS))
        web_toolsets = ['yield', 'skills', 'memory', 'session_search', 'web_shared_files', 'web_personal_files',
                        'todo', 'delegation']
        if can_use_kanban:
            web_toolsets.append('web_kanban')
        if code_enabled:
            web_toolsets.append('code_execution')
        schedule_toolsets = sorted(set(web_toolsets) - set(cron_disabled))
        mcp_toolsets = []
        if not run.get('web_schedule_job_id'):
            from harness.web_connections import install_web_connections
            install_web_connections(registry)
            create_custom_toolset('web_connections', 'Manage employee-local MCP servers', tools=['manage_connections'])
            web_toolsets.append('web_connections')
            mcp_toolsets = attach_employee_mcp_toolsets(web_toolsets, scheduled=False)
        web_toolsets.append('clarify')
        if can_group_chats:
            web_toolsets.append('web_projects')
        if can_schedule:
            web_toolsets.append('cronjob')
        install_web_cron_tool(registry, Path(os.environ['HERMES_HOME']), run, database=store.db.name,
                              enabled=can_schedule, invocation_id=_approval_tool_call_id.get, store=store,
                              model=settings.model, provider=provider, toolsets=schedule_toolsets)
        configure_web_delegation(schedule_options.get('enabled_toolsets'))
        constrain_session_search(session_search_tool)
        observe_skill_reads(skills_tool, emit)
        pending = PendingControls(store, run['principal_id'])
        await pending.setup()
        prompts = NativePrompts(ctx, pending)

        def clarify(questions):
            return asyncio.run_coroutine_threadsafe(prompts.clarify({'questions': questions}), loop).result()

        def tool_start(call_id, name, args):
            if call_id and is_mcp_display_tool(name, args):
                emit({'type': 'tool_start', 'invocation_id': call_id, 'name': name,
                      'args': args if isinstance(args, dict) else {}})

        def tool_complete(call_id, name, args, result):
            if call_id and is_mcp_display_tool(name, args):
                emit({'type': 'tool_complete', 'invocation_id': call_id, 'name': name,
                      'args': args if isinstance(args, dict) else {}, 'result': result})
            if name == 'tool_describe' and stale:
                try:
                    described = json.loads(result) if isinstance(result, str) else result
                except ValueError:
                    described = None
                if isinstance(described, dict) and isinstance(described.get('tools'), dict):
                    stale.difference_update(described['tools'])
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
        if not run.get('web_schedule_job_id') and await attach_schedule_reports(db, store, session, run):
            session, history = await resume_session(db, store, run)
        prior_output = await schedule_context(store, run) if schedule_options.get('context_from') else None
        permitted = set(yield_names)
        for toolset in mcp_toolsets:
            permitted.update(registry.get_tool_names_for_toolset(toolset))
        if can_group_chats:
            permitted.add('desktop_project')
        if can_schedule:
            permitted.add('cronjob_manage')
        stale = outdated_descriptions(history, permitted, registry.get_schema)
        scheduled = bool(run.get('web_schedule_job_id'))
        manual_title = None if scheduled else request.get('manual_title')

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
            elif not scheduled and saved['title'] and saved['title_source'] in ('derived', 'llm'):
                publish_title(saved['title'], saved['title_source'])
        api_key = settings.api_key.get_secret_value()
        secret_scope = bind_provider_secret(provider, settings.base_url, api_key)
        set_runtime_main(provider, settings.model, base_url=settings.base_url, api_key=api_key,
                         api_mode='chat_completions', session_id=session)
        def display_history(messages):
            # The web transcript renders clarify cards from events. Model replay reads state.db.
            return [{**m, 'content': visible_web_text(m['content'])}
                    if m.get('role') == 'assistant' and isinstance(m.get('content'), str) else m
                    for m in prompt_history(messages)]

        # A file-delivery directive can be split across model chunks.
        pending_line = ''

        def stream_delta(text):
            nonlocal pending_line
            buffered = pending_line + text
            # Keep multi-line tool frames together so later argument lines
            # cannot escape the display filter in a different chunk.
            boundary = buffered.rfind('\n') + 1
            if buffered.rfind('<tool_call>') > buffered.rfind('</tool_call>'):
                boundary = min(boundary, buffered.rfind('<tool_call>'))
            pending_line = buffered[boundary:]
            if boundary:
                emit({'type': 'commentary', 'content': visible_web_text(buffered[:boundary]), 'delta': True})

        def interim(text, already_streamed=False):
            nonlocal pending_line
            if already_streamed and pending_line:
                emit({'type': 'commentary', 'content': visible_web_text(pending_line), 'delta': True})
            pending_line = ''
            emit({'type': 'commentary', 'content': visible_web_text(text), 'already_streamed': already_streamed})

        def converse():
            nonlocal agent
            instructions = web_turn_instructions(request['request_context'], code_enabled=code_enabled,
                                                 kanban_enabled=can_use_kanban)
            enabled = web_toolsets
            if (selected := schedule_options.get('enabled_toolsets')) is not None:
                enabled = [name for name in enabled if name in selected]
            agent = AIAgent(model=settings.model, base_url=settings.base_url, api_key=api_key,
                provider=provider, session_id=session, session_db=db, platform='cron' if scheduled else 'yield_web',
                ephemeral_system_prompt=instructions,
                disabled_toolsets=cron_disabled if scheduled else None,
                skip_background_review=scheduled,
                enabled_toolsets=enabled,
                max_iterations=max(1, remaining_models), max_tokens=settings.max_output_tokens,
                run_budget_seconds=ctx.remaining_seconds(), quiet_mode=True,
                reasoning_config={'enabled': True, 'effort': settings.reasoning_effort} if settings.reasoning_effort else None,
                request_overrides={'temperature': settings.temperature}, clarify_callback=clarify,
                stream_delta_callback=stream_delta, interim_assistant_callback=interim,
                tool_start_callback=tool_start, tool_complete_callback=tool_complete)
            if not scheduled:
                agent._on_session_title = publish_title
            try:
                direct = {tool['function']['name'] for tool in agent.tools}
                if 'tool_describe' in direct:
                    stale.difference_update(direct)
                else:
                    stale.clear()
                if stale:
                    require_current_descriptions(registry, stale)
                    agent.ephemeral_system_prompt += ('\n\nLoad the updated schemas with tool_describe before use: '
                                                      + ', '.join(sorted(stale)))
                refresh_web_tool_pin(db, session, saved, agent.tools,
                                     (*yield_names, *SHARED_FILE_TOOLS, *PERSONAL_FILE_TOOLS, *WEB_KANBAN_TOOLS, *ATTACHMENT_TOOLS,
                                      'execute_code', 'cronjob_manage', 'desktop_project'))
                if stopping:
                    return {'interrupted': True, 'turn_exit_reason': 'interrupted'}
                query = turn_query(run, prior_output)
                if run['epoch'] > 1 and history and history[-1]['role'] == 'user':
                    adopt_unanswered_turn(history, query, agent)
                return agent.run_conversation(query,
                                              conversation_history=history,
                                              title_user_message='' if manual_title or scheduled else run['query'])
            finally:
                if manual_title and not saved:
                    sync_manual_title()
                wait_for_title_upgrades(timeout=min(10.0, ctx.remaining_seconds()))
                agent.close()

        result = await asyncio.to_thread(converse)
        status = result_status(result)
        answer = visible_web_text(result.get('final_response') or '').strip()
        # Files placed in publish_directory were explicitly submitted for delivery.
        # Keep the model's prose and expose each new download even if it omitted the tag.
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
        shutdown_employee_mcp_tools()
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

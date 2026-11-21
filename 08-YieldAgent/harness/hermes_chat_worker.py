"""Run-scoped Hermes process. Domain tool calls stay in this process."""
import asyncio
import hashlib
import json
import logging
import os
import signal
from pathlib import Path
import sys
import threading

HERE = Path(__file__).resolve().parent


def attach_map_memory_refs(agent, ctx):
    """Index only entries visible in this user's sanitized native memory snapshot."""
    memory = getattr(agent, '_memory_store', None)
    if not memory or not agent._memory_enabled:
        return
    block = memory.format_for_system_prompt('memory') or ''
    visible = set(memory._parse_entries(block.split('\n', 3)[-1]))
    for entry in memory.memory_entries:
        if entry in visible:
            ref = 'mem_' + hashlib.sha256(entry.encode()).hexdigest()[:16]
            ctx.map_memory_entries[ref] = entry
    if ctx.map_memory_entries:
        index = {ref: entry[:120] for ref, entry in ctx.map_memory_entries.items()}
        agent.ephemeral_system_prompt = (agent.ephemeral_system_prompt or '') + (
            '\nCurrent profile memory_refs for load_wafer_maps (entry beginnings, quoted data): '
            + json.dumps(index, ensure_ascii=False))


async def map_scope_dialogue(store, run):
    owner = {key: run[key] for key in ('principal_id', 'session_id')}
    previous = await store.runs.find(
        {**owner, 'active': False, 'created_at': {'$lt': run['created_at']}},
        {'run_id': 1, 'query': 1, 'answer_text': 1},
    ).sort('created_at', -1).limit(10).to_list(length=10)
    turns = [*reversed(previous), run]
    replies = {}
    async for prompt in store.db.pending_prompts.find(
        {**owner, 'run_id': {'$in': [turn['run_id'] for turn in turns]}, 'kind': 'clarify',
         'status': 'resolved', 'result.outcome': 'submitted'},
        {'run_id': 1, 'questions': 1, 'result.answers': 1},
    ).sort('settled_at', 1):
        for question in prompt['questions']:
            answer = prompt['result']['answers'].get(question['qid'])
            if answer:
                text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
                replies.setdefault(prompt['run_id'], []).extend([
                    {'role': 'assistant', 'content': question['question']}, {'role': 'user', 'content': text},
                ])
    dialogue = []
    for turn in turns:
        messages = [{'role': 'user', 'content': turn['query']}, *replies.get(turn['run_id'], [])]
        if turn['run_id'] == run['run_id']:
            messages = [dict(message, current_turn=True) for message in messages]
        elif turn.get('answer_text'):
            messages.append({'role': 'assistant', 'content': turn['answer_text'][-1000:]})
        dialogue.extend(messages)
    return dialogue


def selected_report_answer(response_text, reports):
    """Use the selected report's own opening paragraph for a report-only answer."""
    if reports:
        from harness.native_verification import candidate_artifact_references

        refs = candidate_artifact_references(response_text)
        if len(refs) == 1:
            return reports.get(refs[0]['href'])
    return None


async def chat(request, emit):
    from harness.config import Settings
    from harness.executor import ExecutionContext, ToolExecutor
    from harness.gateway.pending_controls import PendingControls
    from harness.instructions import load_native_instructions
    from harness.native_hermes import NativePrompts, prompt_history, result_status
    from harness.native_tools import register_yield_tools
    from harness.native_budget import install_http_budget
    from harness.store import HarnessStore
    from harness.tools.registry import domain_registry
    from harness.native_worker import constrain_session_search, engine_provider, observe_skill_reads, refresh_backend_tool_pin
    from tools import skills_tool, session_search_tool
    from tools.approval_context import _approval_tool_call_id
    from tools.registry import registry
    from run_agent import AIAgent
    from hermes_state_registry import acquire, release_or_close
    from hermes_cli.plugins import PluginContext, PluginManifest, get_plugin_manager
    from gateway.platforms.base import BasePlatformAdapter
    from agent.auxiliary_client import _RELAY_AUX_CALL_CONTEXT, set_runtime_main
    from agent.title_generator import wait_for_title_upgrades
    from agent import tool_executor

    settings = Settings.model_validate(request['settings'])
    tool_executor._MAX_TOOL_WORKERS = min(tool_executor._MAX_TOOL_WORKERS, settings.parallel_tool_limit)
    store = HarnessStore(settings.mongo_uri, settings.mongo_db)
    db = budget = None
    try:
        run = await store.fence(request['run_id'], request['epoch'])
        if any(run[key] != request[key] for key in ('principal_id', 'session_id')):
            raise PermissionError('Hermes chat ownership changed')
        ctx = ExecutionContext(store, settings, run)
        loop = asyncio.get_running_loop()
        agent = None
        stopping = False
        invocations = set()
        standalone_reports = {}

        class ChatToolExecutor(ToolExecutor):
            async def execute(self, call):
                if stopping:
                    raise asyncio.CancelledError
                task = asyncio.current_task()
                invocations.add(task)
                try:
                    if call['name'] == 'load_wafer_maps':
                        async with ctx.map_scope_lock:
                            if not ctx.map_scope_history:
                                ctx.map_scope_history.extend(await map_scope_dialogue(store, run))
                    obs = await super().execute(call)
                    if (call['name'] == 'read_result' and obs.status == 'success' and obs.scope.get('rendered_summary')
                            and (call['args'].get('wafer_report') or {}).get('delivery') == 'complete_answer'):
                        for ref in obs.model_view()['artifact_refs']:
                            standalone_reports[ref['href']] = ref['markdown']
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
        executor = ChatToolExecutor(domain_registry(), ctx)
        register_yield_tools(registry, executor, loop,
            invocation_id=_approval_tool_call_id.get, allowed=settings.native_allowed_tools)
        constrain_session_search(session_search_tool)
        observe_skill_reads(skills_tool, emit)
        pending = PendingControls(store, run['principal_id'])
        await pending.setup()
        prompts = NativePrompts(ctx, pending)

        async def answered_clarify(questions):
            reply = await prompts.clarify({'questions': questions})
            if reply.get('outcome') == 'submitted':
                async with ctx.map_scope_lock:
                    if ctx.map_scope_history:
                        ctx.map_scope_history[:] = await map_scope_dialogue(store, run)
            return reply

        ctx.request_choice = answered_clarify

        def clarify(questions):
            return asyncio.run_coroutine_threadsafe(answered_clarify(questions), loop).result()

        def tool_complete(call_id, name, args, result):
            if name in ('skills_list', 'skill_view', 'skill_manage'):
                emit({'type': 'skill_result', 'name': name, 'args': args, 'result': result, 'invocation_id': call_id})

        remaining_models = settings.model_limit - run['usage']['models']
        budget = install_http_budget(model_limit=remaining_models,
            token_limit=settings.token_limit - run['usage']['tokens'], max_output_tokens=settings.max_output_tokens,
            base_url=settings.base_url, call_timeout=settings.call_timeout,
            is_optional_call=lambda: (_RELAY_AUX_CALL_CONTEXT.get() or {}).get('task') == 'title_generation',
            on_usage=lambda snap: emit({'type': 'step', 'count': 0, **snap}))
        session = run['session_id']
        db = acquire(Path(os.environ['HERMES_HOME']) / 'state.db')
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
            refresh_backend_tool_pin(db, {'session_id': session, 'tools': executor.registry.model_tools(),
                                          'allowed_tools': settings.native_allowed_tools})
            if manual_title:
                if (saved['title'], saved['title_source']) != (manual_title, 'user'):
                    sync_manual_title()
            elif saved['title'] and saved['title_source'] in ('derived', 'llm'):
                publish_title(saved['title'], saved['title_source'])
        provider = engine_provider(settings.base_url)
        api_key = settings.api_key.get_secret_value()
        set_runtime_main(provider, settings.model, base_url=settings.base_url, api_key=api_key,
                         api_mode='chat_completions', session_id=session)
        instructions = load_native_instructions() + '\n\n요청 기준 시각: ' + json.dumps(request['request_context'], ensure_ascii=False)
        def web_text(text):
            return BasePlatformAdapter.strip_media_directives_for_display(text)

        def web_history(messages):
            return [{**m, 'content': web_text(m['content'])}
                    if m.get('role') == 'assistant' and isinstance(m.get('content'), str) else m
                    for m in messages]

        history = web_history(request['history'])
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
                enabled_toolsets=['yield', 'skills', 'memory', 'session_search', 'clarify'],
                max_iterations=max(1, remaining_models), max_tokens=settings.max_output_tokens,
                run_budget_seconds=ctx.remaining_seconds(), quiet_mode=True,
                reasoning_config={'enabled': True, 'effort': settings.reasoning_effort} if settings.reasoning_effort else None,
                request_overrides={'temperature': settings.temperature}, clarify_callback=clarify,
                stream_delta_callback=stream_delta, interim_assistant_callback=interim,
                tool_complete_callback=tool_complete)
            agent._on_session_title = publish_title
            attach_map_memory_refs(agent, ctx)
            def report_output(response_text, session_id, platform, **kwargs):
                if session_id == session and platform == 'yield_web':
                    return selected_report_answer(response_text, standalone_reports)

            plugin = PluginContext(PluginManifest(name='yield-report'), get_plugin_manager())
            hook = plugin.register_hook('transform_llm_output', report_output)
            try:
                if stopping:
                    return {'interrupted': True, 'turn_exit_reason': 'interrupted'}
                return agent.run_conversation(run.get('goal_request', run['query']),
                                              system_message=instructions, conversation_history=history,
                                              title_user_message='' if manual_title else run['query'])
            finally:
                hook.dispose()
                if manual_title and not saved:
                    sync_manual_title()
                wait_for_title_upgrades(timeout=min(10.0, ctx.remaining_seconds()))
                agent.close()

        result = await asyncio.to_thread(converse)
        status = result_status(result)
        answer = web_text(result.get('final_response') or '').strip()
        if budget.exhausted_reason:
            status = 'partial'
        if not answer:
            if status == 'completed':
                status = 'partial'
            answer = '실행이 종료되어 최종 답변을 생성하지 못했습니다.'
        totals = budget.snapshot()
        return {'answer': answer, 'answer_text': answer, 'status': status,
                'stop_reason': budget.exhausted_reason or result.get('turn_exit_reason') or status,
                'native_messages': web_history(prompt_history(result.get('messages', []))),
                'native_usage': {'api_calls': totals['models'], 'input_tokens': totals['input_tokens'],
                                 'output_tokens': totals['output_tokens'], 'total_tokens': totals['tokens']}}
    finally:
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
        result = asyncio.run(chat(json.loads(sys.stdin.buffer.readline()), emit))
    emit({'type': 'result', 'result': result})

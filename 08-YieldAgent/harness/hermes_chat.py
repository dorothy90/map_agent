"""Run one upstream Hermes conversation for the existing web gateway."""
import asyncio
import json
import logging
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml

from . import native_ipc as ipc
from .executor import ToolExecutor
from .gateway.events import SessionEvents
from .gateway.session_store import MongoSessionStore
from .isolation.backend import cleanup_isolated_run, cleanup_profile_results, prepare_isolated_run
from .native_hermes import (
    ROOT, UPSTREAM_COMMIT, cancel_run_prompts, coalesced_worker_messages,
    live_commentary, prepare_profile, record_skill_result,
)


async def run_chat(context):
    settings, run, store = context.settings, context.run, context.store
    usage = run.get('usage', {})
    for resource, limit, label in (('models', settings.model_limit, '모델 호출'),
                                   ('tokens', settings.token_limit, '토큰')):
        if usage.get(resource, 0) >= limit:
            observations = run.get('observations', [])
            return {'status': 'partial', 'answer': f'{label} 한도에 도달해 실행을 종료했습니다.',
                    'stop_reason': resource + '_limit', 'observations': observations,
                    'result_ids': [o['result_id'] for o in observations]}
    principal, session, epoch = run['principal_id'], run['session_id'], run['epoch']
    run_id = run['run_id']
    profile = prepare_profile(principal)
    profile_config = yaml.safe_load((profile / 'config.yaml').read_text(encoding='utf-8')) or {}
    skills = profile_config.get('skills') or {}
    if any(skills.get(key) for key in ('create_dir', 'external_dirs', 'inline_shell')):
        raise ValueError('Web chat skills must stay within the authenticated profile')
    zone = ZoneInfo(settings.timezone or profile_config.get('timezone') or 'Asia/Seoul')
    requested = datetime.fromisoformat(run['created_at'])
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=zone)
    request_context = {'requested_at': requested.astimezone(zone).isoformat(), 'timezone': str(zone)}
    await store.runs.update_one({'_id': run_id, 'epoch': epoch, 'status': 'running'},
                                {'$set': {'request_context': request_context}})
    config = settings.model_dump(mode='json', exclude={'api_key'})
    if settings.shared_yield_dir is None:
        shared = ROOT / '.runtime/shared-yield'
        shared.mkdir(mode=0o700, parents=True, exist_ok=True)
        config['shared_yield_dir'] = str(shared)
    config.update(api_key=settings.api_key.get_secret_value(), mongo_db=store.db.name)
    request = {'run_id': run_id, 'epoch': epoch, 'principal_id': principal, 'session_id': session,
               'settings': config, 'request_context': request_context,
               'web_scheduling_enabled': getattr(context, 'web_scheduling_enabled', False),
               'web_map_jobs_enabled': getattr(context, 'web_map_jobs_enabled', False)}
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
           'ORACLE_USER', 'ORACLE_PASSWORD', 'ORACLE_DSN', 'ORACLE_CALL_TIMEOUT_MS') if k in os.environ}
    env.update(HOME=str(profile), HERMES_HOME=str(profile), HERMES_DISABLE_LAZY_INSTALLS='1',
               PYTHON_DOTENV_DISABLED='1',
               HERMES_TIMEZONE=str(zone), HERMES_API_TIMEOUT=str(settings.call_timeout),
               HERMES_WRITE_SAFE_ROOT=str(profile))
    windows = sys.platform == 'win32'
    if windows:
        # Python's shell/DLL and TLS/network initialization needs the OS root;
        # keep profile and application data scoped to this employee.
        env['SystemRoot'] = os.environ['SystemRoot']
        env.update(USERPROFILE=str(profile), APPDATA=str(profile), LOCALAPPDATA=str(profile))
    log_dir = ROOT / '.runtime/hermes/logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    gateway_session = await store.db.gateway_sessions.find_one(
        {'principal_id': principal, 'id': session, 'deleted': {'$ne': True}}, {'title': 1, 'title_source': 1})
    events = SessionEvents(store, principal) if gateway_session else None
    if gateway_session and gateway_session.get('title') and gateway_session.get('title_source') in ('user', None):
        request['manual_title'] = gateway_session['title']

    async def publish_title(title):
        await store.event(run_id, 'session.title', {'session_id': session, 'title': title}, epoch=epoch)

    audit = None
    process = None
    messages = None
    steer_task = None
    stdin_lock = asyncio.Lock()
    tool_starts = {}
    prepared = False
    job = None
    workspace = None
    session_scope = {'session_id': session} if windows else {}
    try:
        if settings.isolation:
            prepare = asyncio.create_task(asyncio.to_thread(
                prepare_isolated_run, settings.isolation, run_id, principal, **session_scope))
            try:
                await asyncio.shield(prepare)
                prepared = True
            except asyncio.CancelledError:
                await asyncio.gather(prepare, return_exceptions=True)
                prepared = not prepare.cancelled() and prepare.exception() is None
                raise
            await asyncio.to_thread(cleanup_profile_results, settings.isolation, principal, **session_scope)
        workspace = tempfile.TemporaryDirectory(prefix='yield-chat-')
        if windows:
            env.update(TEMP=workspace.name, TMP=workspace.name)
        with (log_dir / f'stock-chat-{run_id}.log').open('ab') as log:
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-I', '-X', 'utf8', str(Path(__file__).with_name('hermes_chat_worker.py')),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log,
                start_new_session=not windows, env=env, cwd=workspace.name, limit=16 * 1024 * 1024)
            if windows:
                from .isolation.windows_launcher import attach_process_tree
                # The worker waits for the authenticated request on stdin before
                # starting Hermes or any domain subprocess.
                job = attach_process_tree(process.pid)
            process.stdin.write(json.dumps(request, ensure_ascii=False).encode() + b'\n')
            await process.stdin.drain()

            async def send(message):
                async with stdin_lock:
                    process.stdin.write(ipc.encode(message).encode())
                    await process.stdin.drain()

            async def forward_steers():
                sent = set()
                while True:
                    try:
                        current = await store.runs.find_one(
                            {'_id': run_id, 'epoch': epoch, 'status': 'running'}, {'steer_inbox': 1})
                        for entry in (current or {}).get('steer_inbox', []):
                            if entry['id'] not in sent:
                                await send(ipc.control('steer', id=entry['id'], text=entry['text']))
                                sent.add(entry['id'])
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    except Exception:
                        logging.getLogger(__name__).warning('Could not forward Hermes steer', exc_info=True)
                    await asyncio.sleep(.25)

            steer_task = asyncio.create_task(forward_steers())
            result = None
            messages = coalesced_worker_messages(process.stdout, context.remaining_seconds)
            async for message in messages:
                await context.check()
                kind = message['type']
                if kind == 'call' and message['method'] == 'tool_call' and message['name'] == 'execute_code':
                    if not settings.web_python_enabled or context.session_kernels is None:
                        raise PermissionError('Python requires a session-owned OS sandbox')
                    if audit is None:
                        from .tools.registry import native_domain_registry
                        audit = ToolExecutor(native_domain_registry(), context)
                    if 'execute_code' not in audit.registry.tools:
                        audit.registry.add(context.session_kernels.spec())
                    obs = await audit.execute({'name': 'execute_code', 'args': message['args'],
                                               'id': message['invocation_id']})
                    response = dict(obs.scope.get('execute_code') or {'status': 'error', 'error': obs.summary})
                    view = obs.model_view()
                    response.update(result_id=obs.result_id, artifact_refs=view['artifact_refs'], tables=view['tables'])
                    await send(ipc.reply(message['call_id'], response))
                    continue
                if kind == 'result':
                    result = message['result']
                    break
                if kind == 'session_title' and gateway_session:
                    try:
                        await MongoSessionStore(store, principal).apply_native_title(
                            session, message['title'], message['source'], on_saved=publish_title)
                    except Exception:
                        logging.getLogger(__name__).warning('Could not sync Hermes session title', exc_info=True)
                elif kind == 'commentary':
                    live = live_commentary(message)
                    if live and events is not None:
                        await events.publish_live(session, *live)
                        if live[0] == 'message.interim':
                            await store.runs.update_one(
                                {'_id': run_id, 'epoch': epoch, 'status': 'running'},
                                {'$push': {'public_interims': live[1]['text']}})
                elif kind in ('tool_start', 'tool_complete'):
                    call_id = message['invocation_id']
                    args = message.get('args', {})
                    args_text = json.dumps(args, ensure_ascii=False)
                    bounded_args = args if len(args_text.encode('utf-8')) <= 64 * 1024 else {}
                    payload = {'name': message['name'], 'invocation_id': call_id,
                               'tool_id': call_id, 'args': bounded_args}
                    if kind == 'tool_start':
                        tool_starts[call_id] = time.monotonic()
                        preview = json.dumps(bounded_args, ensure_ascii=False, separators=(',', ':'))
                        payload['context'] = preview if len(preview) <= 80 else preview[:77] + '...'
                        await store.event(run_id, 'tool_started', payload,
                                          event_key=call_id + ':started', epoch=epoch)
                    else:
                        tool_result = message.get('result')
                        if isinstance(tool_result, str):
                            try:
                                tool_result = json.loads(tool_result)
                            except ValueError:
                                pass
                        payload['is_error'] = isinstance(tool_result, dict) and (
                            bool(tool_result.get('error')) or tool_result.get('isError') is True
                            or tool_result.get('ok') is False or tool_result.get('success') is False
                            or tool_result.get('status') == 'error')
                        text = tool_result if isinstance(tool_result, str) else json.dumps(tool_result, ensure_ascii=False)
                        if len(text.encode('utf-8')) > 64 * 1024:
                            text = text.encode('utf-8')[:63 * 1024].decode('utf-8', errors='ignore')
                            tool_result = ({'local_mcp': True, 'preview': text[:8 * 1024], 'truncated': True}
                                      if message['name'] == 'manage_connections' and isinstance(tool_result, dict)
                                      and tool_result.get('local_mcp') is True else text)
                        payload.update(result=tool_result, duration_s=max(0, time.monotonic() - tool_starts.pop(call_id, time.monotonic())))
                        await store.event(run_id, 'tool_finished', payload,
                                          event_key=call_id + ':finished', epoch=epoch)
                elif kind == 'skill_read':
                    await store.runs.update_one({'_id': run_id, 'epoch': epoch, 'status': 'running'},
                        {'$addToSet': {'skill_reads': {k: message[k] for k in ('name', 'resource', 'sha256')}}})
                elif kind == 'skill_result':
                    if audit is None:
                        from .tools.registry import native_domain_registry
                        audit = ToolExecutor(native_domain_registry(), context)
                    await record_skill_result(context, audit, message)
                elif kind == 'wafer_item_saved' and message['name'] in ('kanban_create', 'kanban_comment'):
                    tool_id = message['invocation_id']
                    await store.event(run_id, 'tool_finished', {
                        'name': message['name'], 'tool_id': tool_id,
                        'result': {'status': 'success', 'wafer_item': message['wafer_item']},
                    }, event_key=tool_id + ':wafer-item-saved', epoch=epoch)
                elif kind == 'step':
                    await store.runs.update_one({'_id': run_id, 'epoch': epoch, 'status': 'running'},
                        {'$set': {'usage.models': usage.get('models', 0) + message['models'],
                                  'usage.tokens': usage.get('tokens', 0) + message['tokens']}})
            steer_task.cancel()
            await asyncio.gather(steer_task, return_exceptions=True)
            process.stdin.close()
            if await process.wait() or result is None:
                raise RuntimeError('Hermes chat worker exited without a completed response')
        saved = await store.runs.find_one({'_id': run_id, 'epoch': epoch}, {'observations': 1})
        observations = (saved or {}).get('observations', [])
        return {**result, 'observations': observations,
                'result_ids': [o['result_id'] for o in observations],
                'native_engine': {'name': 'NousResearch/hermes-agent', 'commit': UPSTREAM_COMMIT,
                                  'entrypoint': 'stock_chat', 'history_source': 'hermes_session_db'}}
    finally:
        if steer_task is not None and not steer_task.done():
            steer_task.cancel()
            await asyncio.gather(steer_task, return_exceptions=True)
        if messages is not None:
            await messages.aclose()
        if process is not None and not process.stdin.is_closing():
            process.stdin.close()
        if job is not None:
            job.close()
        if process is not None and process.returncode is None:
            try:
                if windows:
                    if job is None:
                        process.kill()  # Failed to assign job before sending the request.
                    await process.communicate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(process.communicate(), timeout=5)
                    except asyncio.TimeoutError:
                        os.killpg(process.pid, signal.SIGKILL)
                        await process.communicate()
            except ProcessLookupError:
                await process.wait()
        try:
            if workspace is not None:
                workspace.cleanup()
            await cancel_run_prompts(store, principal, run_id, 'turn_ended')
        finally:
            if prepared:
                active_error = sys.exc_info()[0] is not None
                try:
                    await asyncio.to_thread(cleanup_isolated_run, settings.isolation, run_id, principal, **session_scope)
                except Exception:
                    if not active_error:
                        raise
                    logging.getLogger(__name__).warning("Could not clean up the failed chat run", exc_info=True)

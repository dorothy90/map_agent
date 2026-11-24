"""Run one upstream Hermes conversation for the existing web gateway."""
import asyncio
import json
import logging
import os
from pathlib import Path
import signal
import sys
import tempfile
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
    profile_config = yaml.safe_load((profile / 'config.yaml').read_text()) or {}
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
               'settings': config, 'request_context': request_context}
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
           'ORACLE_USER', 'ORACLE_PASSWORD', 'ORACLE_DSN', 'ORACLE_CALL_TIMEOUT_MS') if k in os.environ}
    env.update(HOME=str(profile), HERMES_HOME=str(profile), HERMES_DISABLE_LAZY_INSTALLS='1',
               PYTHON_DOTENV_DISABLED='1',
               HERMES_TIMEZONE=str(zone), HERMES_API_TIMEOUT=str(settings.call_timeout),
               HERMES_WRITE_SAFE_ROOT=str(profile))
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
    prepared = False
    try:
        if settings.isolation:
            prepare = asyncio.create_task(asyncio.to_thread(
                prepare_isolated_run, settings.isolation, run_id, principal))
            try:
                await asyncio.shield(prepare)
                prepared = True
            except asyncio.CancelledError:
                await asyncio.gather(prepare, return_exceptions=True)
                prepared = not prepare.cancelled() and prepare.exception() is None
                raise
            await asyncio.to_thread(cleanup_profile_results, settings.isolation, principal)
        with tempfile.TemporaryDirectory(prefix='yield-chat-') as work, (log_dir / f'stock-chat-{run_id}.log').open('ab') as log:
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-I', str(Path(__file__).with_name('hermes_chat_worker.py')),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log,
                start_new_session=True, env=env, cwd=work, limit=16 * 1024 * 1024)
            process.stdin.write(json.dumps(request, ensure_ascii=False).encode() + b'\n')
            await process.stdin.drain()
            result = None
            messages = coalesced_worker_messages(process.stdout, context.remaining_seconds)
            async for message in messages:
                await context.check()
                kind = message['type']
                if kind == 'call' and message['method'] == 'tool_call' and message['name'] == 'execute_code':
                    if not settings.web_python_enabled or context.session_kernels is None:
                        raise PermissionError('Python requires a session-owned OS sandbox')
                    if audit is None:
                        from .tools.registry import domain_registry
                        audit = ToolExecutor(domain_registry(), context)
                    if 'execute_code' not in audit.registry.tools:
                        audit.registry.add(context.session_kernels.spec())
                    obs = await audit.execute({'name': 'execute_code', 'args': message['args'],
                                               'id': message['invocation_id']})
                    response = dict(obs.scope.get('execute_code') or {'status': 'error', 'error': obs.summary})
                    view = obs.model_view()
                    response.update(result_id=obs.result_id, artifact_refs=view['artifact_refs'], tables=view['tables'])
                    process.stdin.write(ipc.encode(ipc.reply(message['call_id'], response)).encode())
                    await process.stdin.drain()
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
                elif kind == 'skill_read':
                    await store.runs.update_one({'_id': run_id, 'epoch': epoch, 'status': 'running'},
                        {'$addToSet': {'skill_reads': {k: message[k] for k in ('name', 'resource', 'sha256')}}})
                elif kind == 'skill_result':
                    if audit is None:
                        from .tools.registry import domain_registry
                        audit = ToolExecutor(domain_registry(), context)
                    await record_skill_result(context, audit, message)
                elif kind == 'step':
                    await store.runs.update_one({'_id': run_id, 'epoch': epoch, 'status': 'running'},
                        {'$set': {'usage.models': usage.get('models', 0) + message['models'],
                                  'usage.tokens': usage.get('tokens', 0) + message['tokens']}})
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
        if messages is not None:
            await messages.aclose()
        if process is not None and not process.stdin.is_closing():
            process.stdin.close()
        if process is not None and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.communicate(), timeout=5)
                except asyncio.TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.communicate()
            except ProcessLookupError:
                await process.wait()
        try:
            await cancel_run_prompts(store, principal, run_id, 'turn_ended')
        finally:
            if prepared:
                active_error = sys.exc_info()[0] is not None
                try:
                    await asyncio.to_thread(cleanup_isolated_run, settings.isolation, run_id, principal)
                except Exception:
                    if not active_error:
                        raise
                    logging.getLogger(__name__).warning("Could not clean up the failed chat run", exc_info=True)

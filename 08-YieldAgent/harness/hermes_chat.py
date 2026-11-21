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

from .executor import ToolExecutor
from .gateway.events import SessionEvents
from .gateway.session_store import MongoSessionStore
from .isolation.backend import cleanup_isolated_run, cleanup_profile_results, prepare_isolated_run
from .native_hermes import (
    ROOT, UPSTREAM_COMMIT, cancel_run_prompts, coalesced_worker_messages,
    live_commentary, prepare_profile, record_skill_result,
)


async def run_chat(context, *, history=None):
    settings, run, store = context.settings, context.run, context.store
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
    config.update(api_key=settings.api_key.get_secret_value(), mongo_db=store.db.name)
    request = {'run_id': run_id, 'epoch': epoch, 'principal_id': principal, 'session_id': session,
               'history': history or [], 'settings': config, 'request_context': request_context}
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

    prior = run.get('usage', {})
    audit = None
    process = None
    try:
        if settings.isolation:
            prepare = asyncio.create_task(asyncio.to_thread(
                prepare_isolated_run, settings.isolation, run_id, principal))
            try:
                await asyncio.shield(prepare)
            except asyncio.CancelledError:
                await asyncio.gather(prepare, return_exceptions=True)
                raise
            await asyncio.to_thread(cleanup_profile_results, settings.isolation, principal)
        with tempfile.TemporaryDirectory(prefix='yield-chat-') as work, (log_dir / f'stock-chat-{run_id}.log').open('ab') as log:
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-I', str(Path(__file__).with_name('hermes_chat_worker.py')),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log,
                start_new_session=True, env=env, cwd=work, limit=16 * 1024 * 1024)
            process.stdin.write(json.dumps(request, ensure_ascii=False).encode() + b'\n')
            await process.stdin.drain()
            process.stdin.close()
            result = None
            async for message in coalesced_worker_messages(process.stdout, context.remaining_seconds):
                await context.check()
                kind = message['type']
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
                        {'$set': {'usage.models': prior.get('models', 0) + message['models'],
                                  'usage.tokens': prior.get('tokens', 0) + message['tokens']}})
            if await process.wait() or result is None:
                raise RuntimeError('Hermes chat worker exited without a completed response')
        saved = await store.runs.find_one({'_id': run_id, 'epoch': epoch}, {'observations': 1})
        observations = (saved or {}).get('observations', [])
        return {**result, 'observations': observations,
                'result_ids': [o['result_id'] for o in observations],
                'native_engine': {'name': 'NousResearch/hermes-agent', 'commit': UPSTREAM_COMMIT,
                                  'entrypoint': 'stock_chat'}}
    finally:
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
            if settings.isolation:
                await asyncio.to_thread(cleanup_isolated_run, settings.isolation, run_id, principal)

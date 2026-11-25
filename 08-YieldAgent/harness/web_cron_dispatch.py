"""Admit each due report occurrence into the web conversation that scheduled it."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging

from .gateway.execution import Execution, RpcError
from .native_hermes import profile_path
from .web_cron_worker import _cron_store, occurrence_id, schedule_fence, validate_store


logger = logging.getLogger(__name__)


def _due(job, principal, database, now):
    origin = job.get('origin')
    repeat = job.get('repeat')
    schedule = job.get('schedule')
    if (not isinstance(origin, dict) or origin.get('platform') != 'yield_web'
            or origin.get('user_id') != principal or origin.get('database') != database
            or not isinstance(origin.get('chat_id'), str) or not origin['chat_id']
            or job.get('deliver') != 'origin' or job.get('state') != 'scheduled'
            or not isinstance(schedule, dict) or schedule.get('kind') not in {'once', 'interval', 'cron'}
            or not isinstance(repeat, dict) or type(repeat.get('completed')) is not int
            or repeat['completed'] < 0 or 'times' not in repeat
            or not isinstance(job.get('id'), str) or not job['id']
            or not isinstance(job.get('prompt'), str) or (not job['prompt'].strip() and not job.get('skills'))
            or not isinstance(job.get('skills', []), list)
            or any(not isinstance(name, str) or not name.strip() for name in job.get('skills', []))
            or any(job.get(key) for key in ('script', 'no_agent', 'monitor_script', 'monitor_url',
                       'workdir', 'base_url', 'interpreter'))):
        return False
    if job.get('failure_deliver') not in (None, '', 'origin'):
        return False
    for field in ('context_from', 'enabled_toolsets'):
        value = job.get(field)
        if value is not None and (not isinstance(value, list) or any(not isinstance(k, str) or not k for k in value)):
            return False
    times = repeat['times']
    if times is not None and (type(times) is not int or times <= repeat['completed']):
        return False
    if schedule['kind'] == 'once' and (times != 1 or repeat['completed'] != 0):
        return False
    if schedule['kind'] == 'interval' and (type(schedule.get('minutes')) is not int or schedule['minutes'] <= 0):
        return False
    if schedule['kind'] == 'cron' and not schedule.get('expr'):
        return False
    try:
        scheduled = datetime.fromisoformat(job['next_run_at'])
    except (KeyError, TypeError, ValueError):
        return False
    return scheduled.tzinfo is not None and (scheduled <= now or isinstance(job.get('web_trigger'), dict))


class WebCronDispatcher:
    def __init__(self, controller, *, principal=None):
        self.controller = controller
        self.store = controller.store
        self.principal = principal
        self.cache = {}

    async def tick(self):
        sessions = self.store.db.gateway_sessions
        scope = {'principal_id': self.principal} if self.principal is not None else {}
        for principal in await sessions.distinct('principal_id', scope):
            try:
                await self._profile(principal)
            except Exception:
                logger.exception('Could not check employee web schedules')

    async def _profile(self, principal):
        home = profile_path(principal) / 'web-schedules'
        try:
            stamp = (home / 'cron' / 'jobs.json').stat()
        except FileNotFoundError:
            self.cache.pop(principal, None)
            return
        revision = (stamp.st_ino, stamp.st_mtime_ns, stamp.st_size)
        if self.cache.get(principal, (None,))[0] != revision:
            jobs = await asyncio.to_thread(_cron_store, home, 'list')
            self.cache[principal] = revision, jobs
        now = datetime.now(timezone.utc)
        for job in self.cache[principal][1]:
            if _due(job, principal, self.store.db.name, now):
                validate_store(home)
                with schedule_fence(home, job['id'], blocking=False) as locked:
                    if locked:
                        admission = asyncio.create_task(self._dispatch(home, principal, job['id']))
                        try:
                            await asyncio.shield(admission)
                        except asyncio.CancelledError:
                            # Keep the fence through an already-issued Mongo write or cron subprocess.
                            await asyncio.gather(admission, return_exceptions=True)
                            raise

    async def _dispatch(self, home, principal, job_id):
        # The cached scan may predate a cancellation or another worker advancing this job.
        job = await asyncio.to_thread(_cron_store, home, 'get', job_id=job_id)
        if not job or not _due(job, principal, self.store.db.name, datetime.now(timezone.utc)):
            return
        if job.get('web_trigger') is not None:
            job = await asyncio.to_thread(_cron_store, home, 'recover_trigger', job_id=job_id,
                                         zone=job['origin'].get('timezone'))
        sid = job['origin']['chat_id']
        submission = occurrence_id(job)
        session = await self.store.db.gateway_sessions.find_one({
            'principal_id': principal, 'id': sid, 'deleted': {'$ne': True}, 'ended_at': None},
            {'profile_name': 1})
        error = 'Original chat is unavailable'
        retire = session is None
        if session is not None:
            execution = Execution(self.controller, principal, profile_id=session.get('profile_name') or 'default')
            try:
                text = job['prompt']
                if extra := job.get('manual_run_prompt'):
                    text += f"\n\n## Run Context\n{extra}"
                receipt = await execution.submit(sid, {'session_id': sid, 'submission_id': submission,
                    'text': text}, web_schedule_job_id=job_id, web_schedule_skills=job.get('skills') or [],
                    web_schedule_options={k: job[k] for k in ('name', 'context_from', 'enabled_toolsets',
                        'attach_to_session', 'model', 'provider') if job.get(k) is not None})
            except RpcError as exc:
                if str(exc) in ('session_busy', 'storage_unavailable'):
                    return
                error = str(exc)
                retire = error == 'not_found'
            except LookupError:
                retire = True  # The session was closed or deleted during admission.
            else:
                if receipt['status'] != 'terminal':
                    return
                error = receipt['outcome']
                if error == 'completed':
                    published = await execution.receipt(sid, {'session_id': sid,
                        'admission_id': receipt['admission_id'], 'include_result': True})
                    error = None if published.get('result') else 'Chat answer was unavailable'
        await asyncio.to_thread(_cron_store, home, 'finish', zone=job['origin'].get('timezone'),
                                job_id=job_id, success=error is None, error=error,
                                execution_id=submission, retire=retire)

    async def watch(self):
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception('Web schedule poll failed')
            await asyncio.sleep(5)


@asynccontextmanager
async def gateway_schedule_lifespan(app, *, principal=None):
    """Mount once on an authenticated authority router after its host opens GatewayServices."""
    controller = app.state.gateway.controller
    dispatcher = WebCronDispatcher(controller, principal=principal)
    task = asyncio.create_task(dispatcher.watch())
    controller.web_scheduler = dispatcher
    try:
        yield
    finally:
        controller.web_scheduler = None
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

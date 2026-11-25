"""Access a single employee's native cron store without importing Hermes into FastAPI."""
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


_CANCELLED = 'Cancelled from the originating web chat.'


def _cron_store(home, operation, *, zone=None, **fields):
    env = {'HERMES_HOME': str(home), 'HOME': str(home), 'PATH': os.defpath,
           'PYTHON_DOTENV_DISABLED': '1', 'HERMES_DISABLE_LAZY_INSTALLS': '1'}
    if zone:
        env['HERMES_TIMEZONE'] = zone
    result = subprocess.run(
        [sys.executable, '-I', str(Path(__file__).resolve())],
        input=json.dumps({'operation': operation, **fields}), capture_output=True, text=True,
        env=env, check=True, timeout=20,
    )
    return json.loads(result.stdout)


def occurrence_id(job):
    job_id = job['id']
    identity = job_id
    if job['schedule']['kind'] != 'once':
        instant = datetime.fromisoformat(job['next_run_at']).astimezone(timezone.utc)
        identity = json.dumps([job_id, instant.isoformat()], separators=(',', ':'))
    return 'web-schedule-' + hashlib.sha256(identity.encode()).hexdigest()


@contextmanager
def schedule_fence(home, job_id, *, blocking=True):
    """Serialize cancellation and admission for one job across gateway and chat processes."""
    name = '.web-' + hashlib.sha256(job_id.encode()).hexdigest() + '.lock'
    fd = os.open(home / 'cron' / name, getattr(os, 'O_NOFOLLOW', 0) | os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError as exc:
            if blocking or exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            yield False
        else:
            yield True
    finally:
        os.close(fd)


def validate_store(home, *, create=False):
    cron = home / 'cron'
    if cron.resolve() != cron:
        raise ValueError('Invalid web schedule store')
    if create:
        home.mkdir(mode=0o700, exist_ok=True)
        cron.mkdir(mode=0o700, exist_ok=True)
    for entry in cron.iterdir():
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue  # Native cron writes replace temporary files atomically.
        if stat.S_ISLNK(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
            raise ValueError('Invalid web schedule store')


def parse_web_schedule(jobs, spec, repeat):
    schedule = jobs.parse_schedule(spec)
    if schedule['kind'] == 'once':
        if datetime.fromisoformat(schedule['run_at']) <= datetime.now(timezone.utc) or repeat not in (None, 1):
            raise ValueError('A one-shot schedule must have a future time and run only once.')
    if schedule['kind'] == 'interval' and schedule['minutes'] <= 0:
        raise ValueError('A recurring interval must be positive.')
    return schedule


def finish_trigger(jobs, job):
    # Persist the web request identity before the native trigger. Both halves survive a worker restart.
    pending = job.get('web_trigger')
    if pending is None:
        return job
    if job.get('manual_run_at') != job.get('next_run_at') and jobs.is_job_runnable(job):
        jobs.trigger_job(job['id'], extra_prompt=pending.get('prompt'))
    return jobs.update_job(job['id'], {'web_trigger': None})


def edit_job(jobs, request):
    job_id, args = request['job_id'], request['args']
    action = args['action']
    if occurrence := request.get('settle'):
        if not jobs.mark_job_run(job_id, **occurrence):
            raise ValueError('The scheduled run is being finalized. Retry shortly.')
    job = jobs.get_job(job_id)
    if job:
        job = finish_trigger(jobs, job)
    if action == 'run' and job and request['call_id'] in job.get('web_edit_ids', []):
        return job
    if not job or jobs.is_terminal_job(job) or job.get('paused_reason') == _CANCELLED:
        raise ValueError('The schedule has ended.')
    previous = job.get('web_edit_ids', [])
    call_id = request['call_id']
    if call_id in previous and (call_id != previous[-1] or action == 'update'):
        return job
    history = previous if call_id in previous else [*previous, call_id]
    if action == 'run':
        if not jobs.is_job_runnable(job):
            raise ValueError('Resume this paused job before running it.')
        if job.get('manual_run_at') == job.get('next_run_at'):
            raise ValueError('A manual run is already queued for this schedule.')
        job = jobs.update_job(job_id, {'web_edit_ids': history, 'web_trigger': {'prompt': args.get('prompt')}})
        return finish_trigger(jobs, job)
    if action == 'update':
        updates = {k: args[k] for k in ('prompt', 'schedule', 'name', 'repeat', 'skills') if k in args}
        updates.update(request.get('options', {}))
        if not updates and args.get('pinned') is True and job.get('model'):
            return job
        if not updates:
            raise ValueError('Specify a prompt, schedule, name, or repeat limit to update.')
        if 'skills' in updates:
            updates['skill'] = next(iter(updates['skills']), None)
        if not (updates.get('prompt', job.get('prompt')) or '').strip() and not updates.get('skills', job.get('skills')):
            raise ValueError('Specify a task prompt or at least one skill.')
        if 'schedule' in updates:
            schedule = parse_web_schedule(jobs, updates['schedule'], args.get('repeat'))
            if schedule['kind'] == 'once' and job['schedule']['kind'] != 'once':
                # The dispatcher treats this new one-shot as a single unconsumed occurrence.
                updates['repeat'] = {'times': 1, 'completed': 0}
            updates['schedule'] = schedule
            if job['state'] == 'paused':
                updates['next_run_at'] = None  # The old slot belongs to the superseded schedule.
        elif job['schedule']['kind'] == 'once' and args.get('repeat', 1) != 1:
            raise ValueError('A one-shot runs only once.')
        if isinstance(updates.get('repeat'), int) and 0 < updates['repeat'] <= job['repeat']['completed']:
            raise ValueError('The repeat limit must leave at least one remaining run.')
        return jobs.update_job(job_id, {**updates, 'web_edit_ids': history})
    expected, desired = ('scheduled', 'paused') if action == 'pause' else ('paused', 'scheduled')
    if call_id in previous and job['state'] == desired:
        return job
    if job['state'] != expected:
        raise ValueError(f"Only a {expected} schedule can be changed by {action}.")
    if call_id not in previous:
        # Write the call identity before transitioning. A retry can finish an interrupted write.
        jobs.update_job(job_id, {'web_edit_ids': history})
    return jobs.pause_job(job_id) if action == 'pause' else jobs.resume_job(job_id)


def main():
    sys.path.insert(0, str(Path(__file__).resolve().with_name('engine')))
    from cron import jobs

    home = Path(os.environ['HERMES_HOME'])
    validate_store(home)
    request = json.load(sys.stdin)
    with jobs.use_cron_store(home):
        if request['operation'] == 'list':
            return [job for job in jobs.load_jobs() if jobs.is_job_runnable(job)]
        if request['operation'] == 'get':
            job = jobs.get_job(request['job_id'])
            return job if job and jobs.is_job_runnable(job) else None
        if request['operation'] == 'recover_trigger':
            return finish_trigger(jobs, jobs.get_job(request['job_id']))
        if request['operation'] == 'edit':
            try:
                return {'job': edit_job(jobs, request)}
            except (ValueError, OverflowError) as exc:
                return {'error': str(exc)}
        if request['operation'] == 'finish':
            job_id = request['job_id']
            settled = jobs.mark_job_run(job_id, request['success'], request['error'],
                                        execution_id=request['execution_id'])
            if settled and request.get('retire') and (job := jobs.get_job(job_id)) and jobs.is_job_runnable(job):
                jobs.pause_job(job_id, reason=request['error'])
            return settled
        raise ValueError('Unknown web schedule operation')


if __name__ == '__main__':
    print(json.dumps(main(), ensure_ascii=False))

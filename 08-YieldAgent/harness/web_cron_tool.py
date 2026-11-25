"""Bind Hermes schedules to the web chat that requested them."""
import asyncio
import hashlib
import json
from datetime import datetime, timezone

from .web_cron_worker import _CANCELLED, _cron_store, occurrence_id, parse_web_schedule, schedule_fence, validate_store


def install_web_cron_tool(registry, profile, run, *, database, enabled, invocation_id, store=None, model=None, provider=None, toolsets=()):
    from cron import jobs
    from hermes_time import get_timezone_name
    from tools.cronjob_prompt_scan import _scan_cron_prompt
    from tools.cronjob_tools import CRONJOB_SCHEMA, _apply_continuity, _canonical_skills

    description = (
        "Create, list, update, pause, resume, remove, or run a schedule in this chat. "
        "Jobs run once or repeatedly and post the answer here. List schedules to find the exact job_id. "
        "Use update to edit the existing job, "
        "pause to suspend, resume to restart, or run to execute the stored job now and post its result here. "
    )
    fields = CRONJOB_SCHEMA['parameters']['properties']
    properties = {
        **{key: fields[key] for key in ('paused', 'paused_reason', 'pinned', 'context_from', 'continuity',
                                          'attach_to_session', 'enabled_toolsets')},
        'paused': {**fields['paused'], 'description': 'Create with scheduling paused. Resume to enable future runs.'},
        'enabled_toolsets': {**fields['enabled_toolsets'], 'items': {'type': 'string', 'enum': list(toolsets)},
                            'description': 'Restrict a job to named toolsets already available to this employee in scheduled web chats. [] enables zero toolsets.'},
        'deliver': {'type': 'string', 'enum': ['origin'], 'description': 'Post to this authenticated chat.'},
        'failure_deliver': {'type': 'string', 'enum': ['origin', ''],
                            'description': 'Report failures in this chat. An empty string clears the override.'},
        'action': {**fields['action'], 'enum': ['create', 'list', 'update', 'pause', 'resume', 'remove', 'run'],
                   'description': 'Run now or manage this chat’s schedules.'},
        'prompt': {**fields['prompt'], 'description': (
            'The task to perform at each scheduled time. With skills attached, save only the inputs and '
            'overrides supplied for this job. The current skill supplies its procedure and output format at each '
            'fire. Without skills, give the self-contained task. On run, optional context applies only to that fire.'
        )},
        'schedule': {**fields['schedule'], 'description': (
            "Required for create. For one run, use a delay like 'in 5m' or an ISO timestamp. "
            "For a recurring interval use 'every 5m'. For a calendar schedule use a cron "
            "expression such as '0 9 * * 1-5' (09:00 on weekdays in the chat's timezone)."
        )},
        'name': fields['name'],
        'skills': {**fields['skills'], 'description': 'Ordered references to saved skills. Store their exact names from skills_list here; each '
                   'execution reads their current instructions. On update, [] clears.'},
        'repeat': {**fields['repeat'], 'minimum': 0, 'description': (
            'For recurring schedules, set the total number of runs if requested. '
            'Omit on create for indefinite recurrence. On update, 0 clears the repeat limit. A one-shot runs once.'
        )},
        'job_id': {**fields['job_id'], 'description': 'Required for update/pause/resume/remove/run. Exact schedule ID from list.'},
    }
    schema = {**CRONJOB_SCHEMA, 'description': description, 'parameters': {
        **CRONJOB_SCHEMA['parameters'], 'properties': properties, 'additionalProperties': False,
    }}
    origin = {'platform': 'yield_web', 'chat_id': run['session_id'], 'user_id': run['principal_id'],
              'database': database}

    loop = asyncio.get_running_loop() if store is not None else None

    def owns(job):
        return job and all((job.get('origin') or {}).get(k) == v for k, v in origin.items())

    def job_options(args, job=None):
        options = {k: args[k] for k in ('paused', 'paused_reason', 'enabled_toolsets', 'attach_to_session',
                                         'deliver', 'failure_deliver') if k in args}
        if 'enabled_toolsets' in args and set(args['enabled_toolsets']) - set(toolsets):
            raise ValueError('Select only the toolsets available to this scheduled web chat.')
        if 'context_from' in args or 'continuity' in args:
            refs = args.get('context_from', (job or {}).get('context_from') or [])
            if any(ref.lower() != 'self' and not owns(jobs.get_job(ref)) for ref in refs):
                raise ValueError('Every referenced schedule must belong to this chat.')
            options['context_from'] = (_apply_continuity(refs, args['continuity'])
                                       if 'continuity' in args else refs or None)
        if 'pinned' in args:
            if args['pinned'] and not (model and provider):
                raise ValueError('The current model and provider are unavailable for pinning.')
            if not args['pinned'] or not (job or {}).get('model'):
                options.update(model=model if args['pinned'] else None, provider=provider if args['pinned'] else None)
        return options

    def view(job):
        cancelled = job.get('paused_reason') == _CANCELLED
        return {**{key: job[key] for key in ('id', 'name', 'prompt', 'repeat')},
                **{k: job[k] for k in ('paused_reason', 'context_from', 'enabled_toolsets', 'attach_to_session',
                                       'deliver', 'failure_deliver', 'model', 'provider', 'last_status', 'last_error') if job.get(k) is not None},
                'pinned': bool(job.get('model')),
                'skills': job.get('skills') or [], 'state': 'cancelled' if cancelled else job['state'],
                'schedule': job['schedule_display'], 'next_run_at': job['next_run_at'] if job['state'] == 'scheduled' else None,
                'timezone': job['origin'].get('timezone'), 'run_at': job['schedule'].get('run_at')}

    def cancel(home, job_id):
        if not job_id:
            raise ValueError('Use the exact job_id from list to cancel a schedule.')
        with jobs._jobs_lock():
            job = jobs.get_job(job_id)
        if owns(job) and not jobs.is_terminal_job(job) and job.get('paused_reason') != _CANCELLED:
            with schedule_fence(home, job_id), jobs._jobs_lock():
                job = jobs.get_job(job_id)
                if owns(job) and not jobs.is_terminal_job(job) and job.get('paused_reason') != _CANCELLED:
                    # Keep the create request ID so replay cannot resurrect the schedule.
                    job = jobs.pause_job(job_id, reason=_CANCELLED)
        if owns(job) and job.get('paused_reason') == _CANCELLED:
            return {'success': True, 'job_id': job_id, 'status': 'cancelled'}
        return {'success': True, 'job_id': job_id, 'status': 'no active schedule with this ID in this chat'}

    async def admitted_occurrence(job):
        from .gateway.events import published_answer

        submission = occurrence_id(job)
        admitted = await store.runs.find_one({'principal_id': run['principal_id'], 'session_id': run['session_id'],
                                              'web_schedule_job_id': job['id'], 'request_id': submission})
        if not admitted:
            return None
        if admitted.get('active') or admitted['status'] == 'unknown':
            raise ValueError('This scheduled run is in progress. Retry when it finishes.')
        success = admitted['status'] == 'completed' and bool(published_answer(admitted))
        outcome = {'cancelled': 'interrupted', 'completed': 'Chat answer was unavailable'}
        return {'success': success, 'error': None if success else outcome.get(admitted['status'], 'failed'),
                'execution_id': submission}

    def edit(home, args):
        job_id = args.get('job_id')
        if not job_id or not owns(jobs.get_job(job_id)):
            raise ValueError('No editable schedule with this ID in this chat.')
        if 'prompt' in args:
            if not args['prompt'].strip() and args['action'] != 'update':
                raise ValueError('Specify a nonempty task prompt.')
            if error := _scan_cron_prompt(args.get('prompt', '')):
                raise ValueError(error)
        identity = [run['run_id'], job_id, 'run'] if args['action'] == 'run' else [run['run_id'], invocation_id(), args]
        call_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        with schedule_fence(home, job_id):
            job = jobs.get_job(job_id)
            if not owns(job):
                raise ValueError('No editable schedule with this ID in this chat.')
            settle = None
            # The chat can finish just before the dispatcher accounts its occurrence.
            if (store is not None and job['state'] == 'scheduled' and job.get('next_run_at')
                    and datetime.fromisoformat(job['next_run_at']) <= datetime.now(timezone.utc)):
                settle = asyncio.run_coroutine_threadsafe(admitted_occurrence(job), loop).result()
            result = _cron_store(home, 'edit', job_id=job_id, args=args, options=job_options(args, job), call_id=call_id,
                                 zone=job['origin'].get('timezone'), settle=settle)
        if 'error' in result:
            raise ValueError(result['error'])
        if args['action'] == 'run':
            return {'success': True, 'status': 'queued', 'job': view(result['job']),
                    'note': 'Accepted for background execution. The report will appear in this chat when completed.'}
        return {'success': True, 'job': view(result['job'])}

    def manage(args):
        if not enabled:
            raise ValueError('Scheduling is unavailable for this turn.')
        for key, value in args.items():
            field = properties.get(key, {})
            kinds = {'boolean': bool, 'array': list, 'integer': int, 'string': str}
            if type(value) is not kinds.get(field.get('type')) or ('enum' in field and value not in field['enum']):
                raise ValueError(f'Unsupported value for web scheduling argument {key}.')
            if isinstance(value, list) and any(not isinstance(item, str) or not item.strip() for item in value):
                raise ValueError(f'{key} requires a list of nonempty names.')
        if args.get('paused_reason', '').strip() == _CANCELLED:
            raise ValueError('This pause reason is reserved for cancellation.')
        if 'skills' in args:
            skills = args['skills']
            if not isinstance(skills, list) or any(not isinstance(s, str) or not s.strip() for s in skills):
                raise ValueError('skills must be an ordered list of skill names.')
            args = {**args, 'skills': _canonical_skills(None, skills)}
        repeat = args.get('repeat')
        if 'repeat' in args and (type(repeat) is not int or repeat < 0 or (repeat == 0 and args.get('action') != 'update')):
            raise ValueError('Use a positive repeat limit, or 0 to clear an existing recurring limit.')
        home = profile / 'web-schedules'
        validate_store(home, create=True)
        if args.get('action') in ('update', 'pause', 'resume', 'run'):
            allowed = {'action', 'job_id'}
            if args['action'] == 'update':
                allowed.update(properties.keys() - {'paused', 'paused_reason'})
            if args['action'] == 'run':
                allowed.add('prompt')
            if args.keys() - allowed:
                raise ValueError('Unexpected fields for this schedule action.')
            with jobs.use_cron_store(home):
                return edit(home, args)
        if args.get('action') in ('list', 'remove') and args.keys() - ({'action', 'job_id'} if args['action'] == 'remove' else {'action'}):
            raise ValueError('Unexpected fields for this schedule action.')
        if args.get('action') == 'remove':
            with jobs.use_cron_store(home):
                return cancel(home, args.get('job_id'))
        with jobs.use_cron_store(home), jobs._jobs_lock():
            owned = [job for job in jobs.load_jobs() if owns(job)]
            if args.get('action') == 'list':
                return {'success': True, 'jobs': [view(job) for job in owned if job.get('paused_reason') != _CANCELLED]}
            if args.get('action') != 'create' or 'job_id' in args or not (args.get('prompt', '').strip() or args.get('skills')):
                raise ValueError("Use create with a task prompt and schedule, or list to find this chat's jobs.")
            # Hermes carries the same tool call ID across retries. Match before parsing a
            # relative delay or rejecting an already-executed absolute time on replay.
            identity = invocation_id() or args
            request_id = hashlib.sha256(json.dumps([run['run_id'], identity], sort_keys=True).encode()).hexdigest()
            for job in owned:
                if job['origin'].get('request_id') == request_id:
                    return {'success': True, 'job': view(job)}
            if error := _scan_cron_prompt(args.get('prompt', '')):
                raise ValueError(error)
            spec = args.get('schedule', '')
            schedule = parse_web_schedule(jobs, spec, repeat)
            if schedule['kind'] == 'once':
                spec = schedule['run_at']
            job = jobs.create_job(args.get('prompt', ''), spec, name=args.get('name'), repeat=repeat, skills=args.get('skills'),
                                  **job_options(args), origin={**origin, 'request_id': request_id,
                                                            'timezone': get_timezone_name()})
            return {'success': True, 'job': view(job)}

    def handler(args):
        try:
            result = manage(args)
        except (ValueError, OverflowError) as exc:
            result = {'success': False, 'error': str(exc)}
        return json.dumps(result, ensure_ascii=False)

    registry.register(name=schema['name'], toolset='cronjob', schema=schema, handler=handler,
                      check_fn=lambda: enabled, description=description, emoji='⏰')

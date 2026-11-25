import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from harness.web_cron_tool import install_web_cron_tool


@pytest.fixture
def web_cron(tmp_path, monkeypatch):
    profile = tmp_path / 'profiles' / 'employee'
    profile.mkdir(parents=True)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'engine'))
    monkeypatch.setenv('HERMES_HOME', str(profile))
    monkeypatch.setenv('HERMES_TIMEZONE', 'Asia/Seoul')
    monkeypatch.setenv('HERMES_DISABLE_LAZY_INSTALLS', '1')
    from tools.registry import ToolRegistry

    def bound(principal='employee', session='chat', db='app', enabled=True, call='call'):
        registry = ToolRegistry()
        run = {'principal_id': principal, 'session_id': session, 'run_id': 'source-turn'}
        install_web_cron_tool(registry, profile, run, database=db, enabled=enabled, invocation_id=lambda: call, model='glm-test', provider='openrouter', toolsets=['yield'])
        return registry

    return bound


def invoke(registry, action='create', **args):
    return json.loads(registry.dispatch('cronjob_manage', {'action': action, **args}))


def test_one_shot_survives_replay_and_stays_with_its_origin(web_cron, tmp_path):
    registry = web_cron()
    tool = registry.get_definitions({'cronjob_manage'})[0]['function']
    from tools.tool_search_catalog import _short_desc
    schema = tool['parameters']
    assert all(action in _short_desc(tool['description']).lower() for action in schema['properties']['action']['enum'])
    from tools.cronjob_tools import CRONJOB_SCHEMA
    assert set(CRONJOB_SCHEMA['parameters']['properties']) - set(schema['properties']) == {'script', 'monitor', 'workdir', 'no_agent'}
    assert schema['additionalProperties'] is False
    request = {'schedule': 'in 5m', 'prompt': 'Summarize recent yield for 4SS.', 'name': 'Yield'}
    with ThreadPoolExecutor(2) as pool:
        replies = list(pool.map(lambda _: invoke(registry, **request), range(2)))
    assert replies[0] == replies[1]
    job = replies[0]['job']
    assert job['state'] == 'scheduled'
    assert abs((datetime.fromisoformat(job['run_at']) - datetime.now(timezone.utc)).total_seconds() - 300) < 5
    assert invoke(web_cron(), 'list')['jobs'] == [job]
    assert invoke(web_cron(session='another'), 'list')['jobs'] == []
    assert invoke(web_cron(principal='another'), 'list')['jobs'] == []
    assert invoke(web_cron(db='another'), 'list')['jobs'] == []
    profile = tmp_path / 'profiles/employee'
    raw, = json.loads((profile / 'web-schedules/cron/jobs.json').read_text())['jobs']
    assert raw['origin'] == {'platform': 'yield_web', 'user_id': 'employee', 'chat_id': 'chat',
                             'database': 'app', 'request_id': raw['origin']['request_id'], 'timezone': 'Asia/Seoul'}
    assert raw['origin']['request_id']
    assert raw['repeat'] == {'times': 1, 'completed': 0}
    assert raw['deliver'] == 'origin'
    assert not (profile / 'cron/jobs.json').exists()


def test_fixed_local_time_and_disabled_turns(web_cron):
    scheduled = web_cron(enabled=False)
    assert scheduled.get_definitions({'cronjob_manage'}) == []
    stamp = (datetime.now(timezone(timedelta(hours=9))) + timedelta(hours=1)).isoformat()
    args = {'prompt': 'Show the yield report.', 'schedule': stamp}
    assert not invoke(scheduled, **args)['success']
    assert invoke(web_cron(), **args)['job']['run_at'] == stamp


@pytest.mark.parametrize('linked', ['web-schedules', 'web-schedules/cron', 'web-schedules/cron/jobs.json'])
def test_store_cannot_redirect_an_employee_schedule(web_cron, tmp_path, linked):
    outside = tmp_path / 'other-profile'
    outside.mkdir()
    link = tmp_path / 'profiles/employee' / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    result = invoke(web_cron(), prompt='Show the yield report.', schedule='in 5m')
    assert not result['success']
    assert not list(outside.iterdir())


@pytest.mark.parametrize('overrides', [
    {'schedule': 'every 0m'}, {'schedule': '2000-02-02T12:00+09:00'},
    {'schedule': 'every 1m', 'repeat': 0}, {'schedule': 'every 1m', 'repeat': True},
    {'repeat': 2}, {'deliver': 'all'}, {'script': 'a.py'}, {'model': 'another'},
    {'enabled_toolsets': 'terminal'}, {'enabled_toolsets': ['terminal']}, {'monitor': 'https://example.org'}, {'failure_deliver': 'all'}, {'origin': {}}, {'action': 'run'}, {'prompt': ''},
])
def test_web_job_rejects_unsupported_inputs(web_cron, overrides):
    registry = web_cron()
    args = {'prompt': 'Show the yield report.', 'schedule': 'in 5m', **overrides}
    assert not invoke(registry, **args)['success']
    assert not invoke(registry, 'list')['jobs']


@pytest.mark.parametrize(('schedule', 'repeat'), [('every 5m', None), ('0 9 * * 1-5', 3)])
def test_recurring_jobs_follow_chat_timezone_and_requested_limit(web_cron, schedule, repeat):
    registry = web_cron()
    quota = {} if repeat is None else {'repeat': repeat}
    created = invoke(registry, prompt='Show yield.', schedule=schedule, **quota)['job']
    assert created['repeat'] == {'times': repeat, 'completed': 0}
    assert created['timezone'] == 'Asia/Seoul'
    next_run = datetime.fromisoformat(created['next_run_at'])
    assert next_run.utcoffset() == timedelta(hours=9)
    if schedule == 'every 5m':
        assert abs((next_run - datetime.now(timezone.utc)).total_seconds() - 300) < 5
    else:
        assert (next_run.hour, next_run.minute) == (9, 0)
        assert next_run.weekday() < 5
    assert invoke(registry, 'list')['jobs'] == [created]


def test_remove_checks_exact_owned_id_and_survives_retry(web_cron):
    owner = web_cron()
    request = {'prompt': 'Show yield.', 'schedule': 'every 1m', 'name': 'Yield report'}
    created = invoke(owner, **request)['job']
    job_id = created['id']
    assert invoke(owner, 'remove', job_id=created['name'])['status'] != 'cancelled'
    for foreign in [web_cron(principal='other'), web_cron(session='other'), web_cron(db='other')]:
        present = invoke(foreign, 'remove', job_id=job_id)
        missing = invoke(foreign, 'remove', job_id='missing')
        assert present['success'] and present['status'] == missing['status']
    assert invoke(owner, 'list')['jobs'] == [created]
    with ThreadPoolExecutor(2) as pool:
        replies = list(pool.map(lambda _: invoke(owner, 'remove', job_id=job_id), range(2)))
    assert replies[0] == replies[1] == {'success': True, 'job_id': job_id, 'status': 'cancelled'}
    assert invoke(web_cron(), **request)['job']['state'] == 'cancelled'
    assert invoke(web_cron(), 'list')['jobs'] == []


def test_owned_paused_schedule_can_be_updated_and_resumed_without_recreation(web_cron):
    tool = web_cron()
    job = invoke(tool, prompt='Original task.', schedule='every 3m', repeat=4)['job']
    key = {'job_id': job['id']}
    paused = invoke(tool, 'pause', **key)['job']
    assert paused['state'] == 'paused' and paused['next_run_at'] is None
    edit = dict(schedule='15 8 * * 1-5', name='New name', prompt='Revised task.')
    changed = invoke(tool, 'update', **key, **edit)['job']
    assert changed['id'] == job['id'] and changed['state'] == 'paused'
    assert changed['repeat'] == job['repeat'] and changed['prompt'] == edit['prompt']
    resumed = invoke(tool, 'resume', **key)['job']
    instant = datetime.fromisoformat(resumed['next_run_at'])
    assert (instant.hour, instant.minute) == (8, 15)
    assert instant.utcoffset() == timedelta(hours=9)
    assert resumed['state'] == 'scheduled'
    assert invoke(tool, 'pause', **key)['job'] == resumed  # replay older call
    assert invoke(tool, 'remove', **key)['status'] == 'cancelled'
    for action in ('resume', 'update', 'pause'):
        result = invoke(tool, action, **key, **(edit if action == 'update' else {}))
        assert not result['success']
    assert invoke(tool, 'list')['jobs'] == []


def test_mutations_require_exact_employee_chat_database_and_allowed_fields(web_cron):
    owner = web_cron()
    created = invoke(owner, prompt='Original report.', name='report', schedule='every 2m')['job']
    for action, args in [('pause', {}), ('resume', {}), ('update', {'name': 'Changed'})]:
        for foreign in [web_cron(principal='other'), web_cron(session='other'), web_cron(db='other')]:
            rejection = invoke(foreign, action, job_id=created['id'], **args)
            assert not rejection['success']
            assert rejection == invoke(foreign, action, job_id='absent', **args)
        assert not invoke(owner, action, job_id='report', **args)['success']
    assert not invoke(owner, 'update', job_id=created['id'], deliver='elsewhere')['success']
    assert not invoke(owner, 'resume', job_id=created['id'])['success']
    assert invoke(owner, 'list')['jobs'] == [created]


def test_native_resume_keeps_one_overdue_occurrence_and_uses_job_timezone(web_cron, tmp_path, monkeypatch):
    tool = web_cron()
    job = invoke(tool, schedule='0 9 * * *', prompt='Show yield.', repeat=3)['job']
    key = {'job_id': job['id']}
    invoke(tool, 'pause', **key)
    file = tmp_path / 'profiles/employee/web-schedules/cron/jobs.json'
    data = json.loads(file.read_text())
    slot = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    data['jobs'][0]['next_run_at'] = slot
    file.write_text(json.dumps(data))
    monkeypatch.setenv('HERMES_TIMEZONE', 'America/New_York')
    assert invoke(tool, 'resume', **key)['job']['next_run_at'] == slot
    changed = invoke(tool, 'update', schedule='30 16 * * *', **key)['job']
    local = datetime.fromisoformat(changed['next_run_at'])
    assert (local.utcoffset(), local.hour, local.minute) == (timedelta(hours=9), 16, 30)


def test_paused_job_can_be_cancelled_and_completed_job_cannot_reactivate(web_cron, tmp_path):
    tool = web_cron()
    job = invoke(tool, schedule='every 5m', prompt='Show yield.')['job']
    key = {'job_id': job['id']}
    assert invoke(tool, 'pause', **key)['success']
    assert invoke(tool, 'remove', **key)['status'] == 'cancelled'
    assert not invoke(tool, 'resume', **key)['success']
    from cron import jobs
    with jobs.use_cron_store(tmp_path / 'profiles/employee/web-schedules'):
        terminal = jobs.create_job('Second report.', 'every 3m', repeat=1, origin=jobs.get_job(job['id'])['origin'])
        assert jobs.mark_job_run(terminal['id'], True, execution_id='once')
    for action, args in [('resume', {}), ('pause', {}), ('update', {'repeat': 2})]:
        assert not invoke(tool, action, job_id=terminal['id'], **args)['success']


def test_edit_repeat_limit_and_switch_schedule_kinds(web_cron):
    tool = web_cron()
    job = invoke(tool, schedule='in 5m', prompt='Show yield.')['job']
    key = {'job_id': job['id']}
    assert not invoke(tool, 'update', repeat=3, **key)['success']
    recurring = invoke(tool, 'update', schedule='every 3m', **key)['job']
    assert recurring['repeat'] == {'times': None, 'completed': 0}
    assert invoke(tool, 'update', repeat=2, **key)['job']['repeat']['times'] == 2
    assert invoke(tool, 'update', repeat=0, **key)['job']['repeat']['times'] is None
    once = invoke(tool, 'update', schedule='in 2m', **key)['job']
    assert once['id'] == job['id'] and once['repeat'] == {'times': 1, 'completed': 0}


def test_manual_fire_skills_and_retry(web_cron, tmp_path):
    tool = web_cron()
    job = invoke(tool, schedule='0 8 * * *', skills=['수율일보', '수율일보'], prompt='4SA', repeat=3)['job']
    key = {'job_id': job['id']}
    assert job['skills'] == ['수율일보']
    assert not invoke(web_cron(session='other'), 'run', **key)['success']
    invoke(tool, 'pause', **key)
    assert not invoke(tool, 'run', **key)['success']
    invoke(tool, 'resume', **key)
    fire = invoke(tool, 'run', **key, prompt='This time only')
    assert fire['success'] and fire['job']['prompt'] == '4SA' and fire['status'] == 'queued'
    from cron import jobs
    from harness.web_cron_worker import occurrence_id
    with jobs.use_cron_store(tmp_path / 'profiles/employee/web-schedules'):
        queued = jobs.get_job(job['id'])
        assert queued['manual_run_at'] == queued['next_run_at']
        assert queued['manual_run_prompt'] == 'This time only'
        assert invoke(tool, 'run', **key, prompt='This time only') == fire
        assert jobs.mark_job_run(job['id'], True, execution_id=occurrence_id(queued))
        assert invoke(tool, 'run', **key, prompt='This time only')['success']
        done = jobs.get_job(job['id'])
        assert not done.get('manual_run_at') and done['repeat'] == {'times': 3, 'completed': 1}
        assert done['next_run_at'] == job['next_run_at']
    assert invoke(tool, 'update', skills=[], **key)['job']['skills'] == []
    assert invoke(tool, 'update', skills=['a', 'b', 'a'], **key)['job']['skills'] == ['a', 'b']


def test_skill_only_job_and_invalid_skill_lists(web_cron):
    tool = web_cron()
    for bad in ('single', [None], ['']):
        assert not invoke(tool, schedule='0 8 * * *', skills=bad)['success']
    job = invoke(tool, schedule='0 8 * * *', skills=['daily'])['job']
    assert job['prompt'] == '' and job['skills'] == ['daily']
    assert not invoke(tool, 'update', job_id=job['id'], skills=[])['success']

def test_owned_web_schedule_options_and_scoped_context(web_cron):
    first = invoke(web_cron(call='source'), prompt='Source', schedule='every 1h')['job']
    args = dict(prompt='Digest', schedule='every 1h', paused=True, paused_reason='Review first',
                context_from=[first['id']], continuity=True, attach_to_session=True,
                pinned=True, enabled_toolsets=[], deliver='origin', failure_deliver='origin')
    for foreign in [web_cron(session='other'), web_cron(principal='other'), web_cron(db='other')]:
        assert not invoke(foreign, **args)['success']
    job = invoke(web_cron(), **args)['job']
    assert job['context_from'] == [first['id'], 'self'] and job['state'] == 'paused'
    assert job['paused_reason'] == 'Review first' and job['next_run_at'] is None
    assert job['attach_to_session'] and job['enabled_toolsets'] == []
    assert job['pinned'] and job['model'] == 'glm-test' and job['provider'] == 'openrouter'
    updated = invoke(web_cron(), 'update', job_id=job['id'], continuity=False, pinned=False,
                     attach_to_session=False, failure_deliver='', enabled_toolsets=['yield'])['job']
    assert not updated['pinned'] and not updated['attach_to_session'] and not updated.get('failure_deliver')
    assert updated['context_from'] == [first['id']] and updated['enabled_toolsets'] == ['yield']
    assert not invoke(web_cron(), 'update', job_id=job['id'], context_from=['missing'])['success']

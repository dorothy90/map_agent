import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import uuid
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from harness import web_cron_dispatch as cron
from harness.control import RunController
from harness.gateway.execution import Execution
from harness.gateway.session_rpc import SessionRPC
from harness.gateway.session_store import MongoSessionStore
from harness.gateway.routes import build_gateway_router
from harness.store import HarnessStore


def test_due_report_survives_busy_chat_restart_and_two_workers(tmp_path, monkeypatch):
    async def scenario():
        store = HarnessStore(database='harness_web_cron_test_' + uuid.uuid4().hex)
        def profile(principal):
            return tmp_path / principal
        monkeypatch.setattr(cron, 'profile_path', profile)
        ctrl = RunController(store, SimpleNamespace(engine='hermes'))
        def dispatcher():
            return cron.WebCronDispatcher(ctrl, principal='employee')

        # Keep the model turn running until we supply a final answer.
        async def launch(run_id):
            await store.acquire(run_id, ctrl.owner)
        ctrl.launch = launch
        try:
            sessions = MongoSessionStore(store, 'employee')
            await sessions.setup()
            await sessions.create_session('original', 'gui', profile_name='default')
            for sid in ('ended', 'deleted'):
                await sessions.create_session(sid, 'gui')
            await sessions.end_session('ended', 'user')
            await sessions.set_session_deleted('deleted')
            await MongoSessionStore(store, 'colleague').create_session('original', 'gui')
            now = datetime.now(timezone.utc)
            def job(job_id, *, principal='employee', sid='original', database=store.db.name,
                    deliver='origin', when=now):
                return {'id': job_id, 'prompt': '최근 수율 보고서를 작성해줘', 'enabled': True,
                        'state': 'scheduled', 'deliver': deliver, 'schedule': {'kind': 'once', 'run_at': when.isoformat()},
                        'repeat': {'times': 1, 'completed': 0}, 'next_run_at': when.isoformat(),
                        'origin': {'platform': 'yield_web', 'user_id': principal, 'chat_id': sid, 'database': database}}
            home = profile('employee') / 'web-schedules'
            file = home / 'cron' / 'jobs.json'
            file.parent.mkdir(parents=True)
            file.write_text(json.dumps({'jobs': [{**job('main', when=now + timedelta(days=1)), 'skills': ['daily-report'],
                 'web_edit_ids': ['accepted'], 'web_trigger': {'prompt': '4SA'}}, job('future', when=now + timedelta(days=1)),
                job('forged', principal='colleague'), job('other-db', database='elsewhere'),
                job('other-destination', deliver='external'), job('ended', sid='ended'), job('deleted', sid='deleted')]}))
            other = profile('colleague') / 'web-schedules' / 'cron' / 'jobs.json'
            other.parent.mkdir(parents=True)
            other.write_text(json.dumps({'jobs': [job('foreign', principal='colleague')]}))
            busy = await ctrl.start('employee', 'original', 'busy', 'Another user turn')
            await dispatcher().tick()
            assert await store.runs.count_documents({}) == 1
            async def complete(run_id):
                run = await store.get_run('employee', run_id)
                await ctrl._save_context(run, {'native_engine': {'entrypoint': 'stock_chat'}, 'native_messages': [],
                                              'native_usage': {}, 'answer': '수율 보고서입니다.'})
                await store.finish(run_id, run['epoch'], 'completed', '수율 보고서입니다.', 'completed')
            await complete(busy['run_id'])
            workers = [dispatcher(), dispatcher()]
            await asyncio.gather(*(worker.tick() for worker in workers))
            scheduled = await store.runs.find_one({'web_schedule_job_id': 'main'})
            assert scheduled and (scheduled['principal_id'], scheduled['session_id']) == ('employee', 'original')
            assert scheduled['request_id'] == 'web-schedule-' + hashlib.sha256(b'main').hexdigest()
            assert scheduled['epoch'] == 1
            assert scheduled['web_schedule_skills'] == ['daily-report']
            assert scheduled['query'].endswith('## Run Context\n4SA')
            await complete(scheduled['run_id'])
            await asyncio.gather(*(worker.tick() for worker in workers))
            await dispatcher().tick()
            assert await store.runs.count_documents({}) == 2
            statuses = {row['id']: row for row in json.loads(file.read_text())['jobs']}
            assert statuses['main']['last_status'] == 'ok' and statuses['main']['repeat']['completed'] == 1
            assert all(statuses[key]['last_status'] == 'error' for key in ('ended', 'deleted'))
            assert all(statuses[key]['state'] == 'scheduled' for key in ('future', 'other-db', 'other-destination', 'forged'))
            execution = Execution(ctrl, 'employee', profile_id='default')
            rpc = SessionRPC(SimpleNamespace(sessions=sessions, execution=execution), SimpleNamespace(mode='authority'))
            history = await rpc.history('original')
            assert history[-2]['text'] == '예약한 작업 자동 실행'
            assert history[-1]['text'] == '수율 보고서입니다.'
            assert await store.db.gateway_receipts.count_documents({'run_id': scheduled['run_id']}) == 1
        finally:
            await ctrl.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_recurring_occurrences_and_cancellation_races(tmp_path, monkeypatch):
    async def scenario():
        store = HarnessStore(database='harness_repeat_test_' + uuid.uuid4().hex)
        ctrl = RunController(store, SimpleNamespace(engine='hermes'))
        monkeypatch.setattr(cron, 'profile_path', lambda principal: tmp_path / principal)
        home = tmp_path / 'employee' / 'web-schedules'
        file = home / 'cron' / 'jobs.json'
        file.parent.mkdir(parents=True)
        zone = ZoneInfo('America/New_York')
        overdue = (datetime.now(zone) - timedelta(days=14)).isoformat()
        job = {'id': 'weekly', 'name': 'Weekly', 'schedule_display': 'weekly', 'prompt': '주간 수율을 요약해줘', 'state': 'scheduled', 'enabled': True,
               'deliver': 'origin', 'repeat': {'times': 2, 'completed': 0}, 'next_run_at': overdue,
               'schedule': {'kind': 'cron', 'expr': '15 9 * * 1'},
               'origin': {'platform': 'yield_web', 'user_id': 'employee', 'chat_id': 'chat',
                          'database': store.db.name, 'timezone': str(zone)}}
        file.write_text(json.dumps({'jobs': [job]}))
        dispatcher = cron.WebCronDispatcher(ctrl)
        async def lease(run_id):
            await store.acquire(run_id, ctrl.owner)
        ctrl.launch = lease
        async def publish(run):
            await ctrl._save_context(run, {'native_engine': {'entrypoint': 'stock_chat'}, 'native_messages': [],
                                          'native_usage': {}, 'answer': '주간 수율 보고서'})
            await store.finish(run['run_id'], run['epoch'], 'completed', '주간 수율 보고서', 'completed')
        try:
            sessions = MongoSessionStore(store, 'employee')
            await sessions.setup()
            await sessions.create_session('chat', 'gui')
            busy = await ctrl.start('employee', 'chat', 'user', '일반 대화')
            await dispatcher.tick()
            assert await store.runs.count_documents({'web_schedule_job_id': 'weekly'}) == 0
            await publish(await store.get_run('employee', busy['run_id']))
            await asyncio.gather(dispatcher.tick(), cron.WebCronDispatcher(ctrl).tick())
            first = await store.runs.find_one({'web_schedule_job_id': 'weekly'})
            assert first and first['epoch'] == 1
            from harness.web_cron_tool import install_web_cron_tool
            monkeypatch.syspath_prepend(str(Path(cron.__file__).with_name('engine')))
            monkeypatch.setenv('HERMES_HOME', str(home.parent))
            from tools.registry import ToolRegistry
            registry = ToolRegistry()
            install_web_cron_tool(registry, home.parent,
                                  {'principal_id': 'employee', 'session_id': 'chat', 'run_id': 'manage-turn'},
                                  database=store.db.name, enabled=True, invocation_id=lambda: 'manage', store=store)
            def update_prompt():
                return json.loads(registry.dispatch('cronjob_manage', {
                    'action': 'update', 'job_id': 'weekly', 'prompt': '새로운 주간 수율 요약'}))
            assert not (await asyncio.to_thread(update_prompt))['success']  # Occurrence is admitted and still active.
            await publish(first)
            # Edit between gateway publication and the next polling tick: settle the old prompt once.
            changed = (await asyncio.to_thread(update_prompt))['job']
            assert changed['repeat'] == {'times': 2, 'completed': 1}
            await cron.WebCronDispatcher(ctrl).tick()
            record = json.loads(file.read_text())['jobs'][0]
            assert record['repeat'] == {'times': 2, 'completed': 1}
            next_slot = datetime.fromisoformat(record['next_run_at']).astimezone(zone)
            assert next_slot > datetime.now(zone)
            assert (next_slot.weekday(), next_slot.hour, next_slot.minute) == (0, 9, 15)
            await dispatcher.tick()
            assert await store.runs.count_documents({'web_schedule_job_id': 'weekly'}) == 1
            class NextWeek(datetime):
                @classmethod
                def now(cls, tz=None):
                    return (next_slot + timedelta(seconds=1)).astimezone(tz)
            monkeypatch.setattr(cron, 'datetime', NextWeek)
            await asyncio.gather(dispatcher.tick(), cron.WebCronDispatcher(ctrl).tick())
            second = await store.runs.find_one({'web_schedule_job_id': 'weekly', 'active': True})
            assert second and second['request_id'] != first['request_id']
            assert second['query'] == changed['prompt'] and first['query'] == job['prompt']
            await publish(second)
            await asyncio.gather(dispatcher.tick(), cron.WebCronDispatcher(ctrl).tick())
            record = json.loads(file.read_text())['jobs'][0]
            assert (record['repeat']['completed'], record['state'], record['next_run_at']) == (2, 'completed', None)
            assert await store.runs.count_documents({'web_schedule_job_id': 'weekly'}) == 2

            interval = {**job, 'id': 'cancelled', 'schedule': {'kind': 'interval', 'minutes': 1},
                        'repeat': {'times': None, 'completed': 0}}
            file.write_text(json.dumps({'jobs': [interval]}))
            def cancel(job_id):
                return json.loads(registry.dispatch('cronjob_manage', {'action': 'remove', 'job_id': job_id}))
            read, resume = threading.Event(), threading.Event()
            native_store = cron._cron_store
            def stale_scan(home, operation, **fields):
                result = native_store(home, operation, **fields)
                if operation == 'list':
                    read.set()
                    assert resume.wait(10)
                return result
            monkeypatch.setattr(cron, '_cron_store', stale_scan)
            scan = asyncio.create_task(dispatcher.tick())
            try:
                assert await asyncio.to_thread(read.wait, 5)
                assert cancel('cancelled')['status'] == 'cancelled'
            finally:
                resume.set()
                await scan
            assert await store.runs.count_documents({'web_schedule_job_id': 'cancelled'}) == 0

            interval['id'] = 'in-flight'
            file.write_text(json.dumps({'jobs': [interval]}))
            admitting, release = asyncio.Event(), asyncio.Event()
            normal_start = ctrl.start
            async def hold_admission(*args, **kwargs):
                admitting.set()
                await release.wait()
                return await normal_start(*args, **kwargs)
            ctrl.start = hold_admission
            scan = asyncio.create_task(dispatcher.tick())
            try:
                await asyncio.wait_for(admitting.wait(), 10)
                with cron.schedule_fence(home, 'in-flight', blocking=False) as acquired:
                    assert not acquired
                cancellation = asyncio.create_task(asyncio.to_thread(cancel, 'in-flight'))
            finally:
                release.set()
                await scan
            assert (await cancellation)['status'] == 'cancelled'
            current = await store.runs.find_one({'web_schedule_job_id': 'in-flight'})
            assert current and current['active']
            await publish(current)
            await cron.WebCronDispatcher(ctrl).tick()
            assert await store.runs.count_documents({'web_schedule_job_id': 'in-flight'}) == 1
            assert json.loads(file.read_text())['jobs'][0]['enabled'] is False
        finally:
            await ctrl.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_authority_router_runs_scheduler_without_a_browser(monkeypatch):
    async def scenario():
        started, stopped = asyncio.Event(), asyncio.Event()
        async def watch(self):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        monkeypatch.setattr(cron.WebCronDispatcher, 'watch', watch)
        app = FastAPI()
        controller = RunController(None, SimpleNamespace(engine='hermes'))
        app.state.gateway = SimpleNamespace(controller=controller)
        assert not controller.web_scheduling_enabled('employee')
        async with cron.gateway_schedule_lifespan(app, principal='employee'):
            assert controller.web_scheduling_enabled('employee')
            assert not controller.web_scheduling_enabled('colleague')
        assert not controller.web_scheduling_enabled('employee')
        app.include_router(build_gateway_router(SimpleNamespace(), rpc_mode='authority',
                           services=app.state.gateway, schedule_lifespan=cron.gateway_schedule_lifespan))
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(started.wait(), 1)
            assert all(controller.web_scheduling_enabled(owner) for owner in ('employee', 'colleague'))
        assert stopped.is_set()
        assert not controller.web_scheduling_enabled('employee')
    asyncio.run(scenario())


def test_chained_job_context_and_mirrors_use_only_published_origin_outputs(tmp_path, monkeypatch):
    from harness.hermes_chat_worker import attach_schedule_reports, schedule_context

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'engine'))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    from hermes_state import SessionDB

    async def scenario():
        store = HarnessStore(database='harness_web_cron_scope_' + uuid.uuid4().hex)
        db = SessionDB(db_path=tmp_path / 'state.db')
        now = datetime.now(timezone.utc)
        who = {'principal_id': 'employee', 'session_id': 'chat'}
        job = {'web_schedule_job_id': 'digest', 'web_schedule_options': {'context_from': ['source', 'self']},
               **who, 'created_at': now}
        def prior(text, age, *, source='source', **overrides):
            return {**who, 'run_id': uuid.uuid4().hex, 'status': 'completed', 'active': False,
                    'native_engine': {'entrypoint': 'stock_chat'}, 'answer_text': text,
                    'created_at': now - timedelta(seconds=age), 'web_schedule_job_id': source,
                    'web_schedule_options': {'attach_to_session': True}, **overrides}

        try:
            await store.runs.insert_many([
                prior('VALID SOURCE', 10), prior('VALID SELF', 8, source='digest'),
                prior('PRIVATE EMPLOYEE', 7, principal_id='other'), prior('PRIVATE CHAT', 6, session_id='other'),
                prior('PRIVATE DRAFT', 5, status='failed'), prior('NO_REPLY', 4), prior('FUTURE', -1)])
            context = await schedule_context(store, job)
            assert 'VALID SOURCE' in context and 'VALID SELF' in context
            assert "Output from job 'source'" in context and "Your previous run's output" in context
            assert all(text not in context for text in ('PRIVATE', 'FUTURE', 'NO_REPLY'))
            assert await attach_schedule_reports(db, store, 'chat', job)
            assert not await attach_schedule_reports(db, store, 'chat', job)
            messages = db.get_messages('chat')
            assert [(m['role'], m['content']) for m in messages] == [
                ('user', '[Cron delivery: source]\nVALID SOURCE'), ('user', '[Cron delivery: digest]\nVALID SELF')]
        finally:
            db.close()
            await store.client.drop_database(store.db.name)
            store.client.close()

    asyncio.run(scenario())

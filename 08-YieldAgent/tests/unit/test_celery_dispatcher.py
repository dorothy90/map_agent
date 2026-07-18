import pytest

from celery_app import celery_app
from celery_dispatcher import CeleryJobDispatcher
from settings import get_settings


class FakeCelery:
    def __init__(self):
        self.sent = []

    def send_task(self, name, args, task_id, queue):
        self.sent.append((name, args, task_id, queue))


@pytest.fixture
def fake_celery():
    return FakeCelery()


@pytest.mark.asyncio
async def test_dispatch_uses_job_and_sequence(fake_celery):
    dispatcher = CeleryJobDispatcher(fake_celery)

    task_id = await dispatcher.dispatch("job-1", 3)

    assert task_id == "job-1:3"
    assert fake_celery.sent == [
        ("yield_agent.run_job", ["job-1", 3], "job-1:3", "analysis")
    ]


@pytest.mark.asyncio
async def test_dispatch_offloads_broker_send(monkeypatch, fake_celery):
    calls = []

    async def fake_to_thread(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr("celery_dispatcher.asyncio.to_thread", fake_to_thread)

    await CeleryJobDispatcher(fake_celery).dispatch("job-1", 3)

    assert calls == [
        (
            fake_celery.send_task,
            ("yield_agent.run_job",),
            {
                "args": ["job-1", 3],
                "task_id": "job-1:3",
                "queue": "analysis",
            },
        )
    ]


def test_celery_uses_safe_worker_configuration():
    assert celery_app.conf.broker_url == get_settings().redis_url
    assert celery_app.conf.result_backend is None
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.result_serializer == "json"
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_soft_time_limit == 1740
    assert celery_app.conf.task_time_limit == 1800
    assert celery_app.conf.broker_transport_options == {"visibility_timeout": 2100}
    assert celery_app.conf.task_default_queue == "analysis"

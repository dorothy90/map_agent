import asyncio
import multiprocessing
import time
from pathlib import Path


def blocking_work(marker):
    Path(marker).write_text("started")
    time.sleep(30)
    return "late"


def fail_with_private_value():
    import os
    raise ValueError('invalid column METRIC; password=' + os.environ['TEST_DB_PASSWORD'])


def test_domain_failure_preserves_safe_cause_without_credentials(monkeypatch):
    import pytest
    from harness.runtime.domain_process import run_blocking, DomainError
    monkeypatch.setenv('TEST_DB_PASSWORD', 'private-password-value')
    with pytest.raises(DomainError) as failure:
        asyncio.run(run_blocking(fail_with_private_value))
    assert 'METRIC' in str(failure.value)
    assert 'private-password-value' not in str(failure.value)
    assert failure.value.error_type == 'ValueError'


def test_cancellation_reaps_actual_blocking_worker(tmp_path):
    from harness.runtime.domain_process import run_blocking
    async def scenario():
        before = {p.pid for p in multiprocessing.active_children()}
        marker = tmp_path / "worker"
        task = asyncio.create_task(run_blocking(blocking_work, str(marker)))
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            await asyncio.sleep(.02)
        assert marker.exists()
        task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert {p.pid for p in multiprocessing.active_children()} == before
    asyncio.run(scenario())

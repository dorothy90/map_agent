import asyncio
import time
from types import SimpleNamespace

import pytest

from harness.config import Settings
from harness.executor import ExecutionContext
from harness.store import BudgetExceeded


def test_default_active_budget_is_five_minutes(monkeypatch, isolated_settings_env):
    monkeypatch.delenv('HARNESS_ACTIVE_SECONDS', raising=False)
    assert Settings().active_seconds == 300
    assert Settings.from_env().active_seconds == 300


def test_call_stops_before_reserved_finalization_time_without_billing():
    async def scenario():
        run = {'run_id': 'r', 'epoch': 1, 'usage': {'active_seconds': 295}}
        async def fence(*args):
            return run
        class Store:
            async def reserve_model_call(self, *args, **kwargs):
                raise AssertionError('Reservation must not be consumed')
        store = Store()
        store.fence = fence
        ctx = ExecutionContext(store, Settings(), run)
        with pytest.raises(BudgetExceeded, match='active_time_reserve'):
            await ctx.model_call(SimpleNamespace(), [], reserve_seconds=10)
    asyncio.run(scenario())


def test_monotonic_elapsed_counts_between_heartbeat_updates():
    ctx = ExecutionContext(None, Settings(active_seconds=300), {'usage': {'active_seconds': 10}})
    ctx.clock_started = time.monotonic() - 20
    assert 269 <= ctx.remaining_seconds() <= 270

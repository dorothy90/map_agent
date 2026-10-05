import asyncio
import time
from types import SimpleNamespace

import pytest

from harness.config import Settings
from harness.executor import ExecutionContext
from harness.store import BudgetExceeded


def test_default_active_budget_is_five_minutes(monkeypatch):
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


def test_reasoning_timeout_transitions_to_reserved_final_answer():
    from langchain_core.messages import AIMessage
    from harness.nodes import Nodes
    from harness.tools.registry import ToolRegistry
    from harness.testing import ScriptedModel
    async def scenario():
        calls = []
        async def check(): return {'usage': {'tokens': 10000, 'models': 2, 'tools': 1}}
        async def model_call(*args, **kwargs):
            calls.append(kwargs.get('purpose'))
            if len(calls) == 1: raise TimeoutError()
            return AIMessage(content='', tool_calls=[{'name':'finish','args':{'answer':'확인된 결과입니다.'},'id':'f'}])
        ctx = SimpleNamespace(settings=Settings(), check=check, model_call=model_call,
            remaining_seconds=lambda *a: 190 if not calls else 180)
        node = Nodes(ScriptedModel([]), ToolRegistry(), ctx, '')
        state = {'goal': {'original_request': '분석'}, 'observations': [], 'messages': []}
        transition = await node.think(state)
        assert transition.get('force_finalize') is True
        assert transition.get('status') != 'partial'
        result = await node.think({**state, **transition})
        assert calls == ['reasoning', 'answer']
        assert result['pending'][0]['name'] == 'finish'
    asyncio.run(scenario())

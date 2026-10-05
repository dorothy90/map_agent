import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from harness.config import Settings
from harness.executor import ExecutionContext, estimate_model_tokens
from harness.store import BudgetExceeded, HarnessStore


@asynccontextmanager
async def context(**settings):
    store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
    try:
        await store.setup()
        run = await store.start_run("p", "s", "r", "q")
        run = await store.acquire(run["run_id"], "worker")
        yield ExecutionContext(store, Settings(**settings), run)
    finally:
        await store.client.drop_database(store.db.name)
        store.client.close()


class Provider:
    model_name = "fixture"

    def __init__(self, usage=None, fail=False, tools=None):
        self.usage, self.fail = usage, fail
        self.kwargs = {"tools": tools or []}
        self.calls, self.caps = 0, []

    def bind(self, **kwargs):
        self.caps.append(kwargs["max_tokens"])
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        if self.fail:
            raise ConnectionError("fixture")
        return AIMessage(content="answer", usage_metadata=self.usage)


async def usage(ctx):
    return (await ctx.store.get_run("p", ctx.run["run_id"]))["usage"]


def test_serialized_estimator_counts_tools_without_model_repr_or_secrets():
    class Opaque(Provider):
        def __str__(self):
            raise AssertionError("Do not serialize model configuration")
    model = Opaque()
    messages = [HumanMessage(content="hello")]
    base = estimate_model_tokens(model, messages, 128)
    model.kwargs["api_key"] = "sensitive" * 10000
    assert estimate_model_tokens(model, messages, 128) == base
    model.kwargs["tools"] = [{"type": "function", "function": {"name": "query", "description": "schema " * 1000}}]
    assert estimate_model_tokens(model, messages, 128) > base + 1000


def test_input_sections_are_estimates_and_do_not_log_content():
    from langchain_core.messages import SystemMessage
    from harness.executor import input_sections
    model = Provider(tools=[{'type': 'function', 'function': {'name': 'read'}}])
    sections = input_sections(model, [SystemMessage(content='private-skill-body',
        additional_kwargs={'input_section': 'skills'}), HumanMessage(content='private-question')], 300)
    assert sum(item['estimated_tokens'] for item in sections) == 300
    assert {'skills', 'conversation', 'tool_schemas'} <= {item['name'] for item in sections}
    assert 'private' not in str(sections)


def test_rejected_token_preflight_does_not_consume_model_or_child_counts():
    async def scenario():
        async with context(token_limit=4096, max_output_tokens=128) as ctx:
            ctx = replace(ctx, depth=1, namespace="child.")
            provider = Provider()
            with pytest.raises(BudgetExceeded, match="tokens_limit"):
                await ctx.model_call(provider, [HumanMessage(content="x" * 20000)], reserve_models=1)
            current = await usage(ctx)
            assert current["models"] == 0
            assert current["tokens"] == 0
            assert current.get("child", {}).get("models", 0) == 0
            assert provider.calls == 0
    asyncio.run(scenario())


def test_atomic_admission_prevents_concurrent_overbooking():
    async def scenario():
        async with context() as ctx:
            outcomes = await asyncio.gather(*[
                ctx.store.reserve_model_call(ctx.run["run_id"], ctx.run["epoch"], token_limit=1000, model_limit=10, tokens=600)
                for _ in range(8)
            ], return_exceptions=True)
            assert sum(not isinstance(value, Exception) for value in outcomes) == 1
            assert all(value is None or isinstance(value, BudgetExceeded) for value in outcomes)
            current = await usage(ctx)
            assert current["models"] == 1
            assert current["tokens"] == 600
    asyncio.run(scenario())


def test_output_cap_actual_reconciliation_and_durable_calibration():
    async def scenario():
        async with context(max_output_tokens=8192) as ctx:
            provider = Provider({"input_tokens": 800, "output_tokens": 23, "total_tokens": 823})
            messages = [HumanMessage(content="private-prompt")]
            before = ctx.estimate_input_tokens(provider, messages)
            await ctx.model_call(provider, messages, max_output_tokens=256, purpose="compaction")
            assert provider.caps == [256]
            assert (await usage(ctx))["tokens"] == 823
            saved = await ctx.store.get_run("p", ctx.run["run_id"])
            resumed = ExecutionContext(ctx.store, ctx.settings, saved)
            assert resumed.estimate_input_tokens(provider, messages) > before
            assert resumed.estimate_input_tokens(provider, messages) >= 800
            other = Provider()
            other.model_name = "different-model"
            assert resumed.estimate_input_tokens(other, messages) == before
            event = next(e for e in saved["events"] if e["type"] == "model_usage")
            assert event["payload"]["input_tokens"] == 800
            assert event["payload"]["output_tokens"] == 23
            assert event["payload"]["purpose"] == "compaction"
            assert "private-prompt" not in str(event)
    asyncio.run(scenario())


def test_retries_and_final_preserve_explicit_reserves(monkeypatch):
    monkeypatch.setattr("harness.executor.retry_delay", lambda *args: 0)
    async def scenario():
        async with context(model_limit=3, max_output_tokens=128) as ctx:
            provider = Provider(fail=True)
            messages = [HumanMessage(content="hello")]
            estimated = ctx.estimate_input_tokens(provider, messages) + 128
            with pytest.raises(ConnectionError) as failure:
                await ctx.model_call(provider, messages, final=True, reserve_models=1, reserve_tokens=1000, purpose="final")
            assert isinstance(failure.value.__cause__, BudgetExceeded)
            assert str(failure.value.__cause__) == "models_limit"
            assert provider.calls == 2
            current = await usage(ctx)
            assert current["models"] == 2
            assert current["tokens"] == 2 * estimated
            events = (await ctx.store.get_run("p", ctx.run["run_id"]))["events"]
            assert len([e for e in events if e["type"] == "model_usage"]) == 2
    asyncio.run(scenario())


def test_unknown_usage_stays_charged_and_final_keeps_review_tokens():
    async def scenario():
        async with context(token_limit=4096, max_output_tokens=128) as ctx:
            provider = Provider()
            messages = [HumanMessage(content="hello")]
            estimated = ctx.estimate_input_tokens(provider, messages) + 128
            await ctx.model_call(provider, messages, final=True, reserve_tokens=4096 - estimated)
            assert (await usage(ctx))["tokens"] == estimated
            with pytest.raises(BudgetExceeded, match="tokens_limit"):
                await ctx.model_call(provider, messages, final=True, reserve_tokens=4096 - estimated)
            assert provider.calls == 1
    asyncio.run(scenario())


def test_real_langchain_request_caps_output_and_preserves_bound_tools():
    import json
    import httpx
    from langchain_openai import ChatOpenAI

    async def scenario():
        requests = []
        async def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "fixture", "object": "chat.completion", "created": 1,
                "model": "fixture", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 80, "completion_tokens": 3, "total_tokens": 83}})
        async with context() as ctx, httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = ChatOpenAI(model="fixture", api_key="fixture", base_url="https://fixture.invalid/v1", http_async_client=client,
                max_tokens=8192).bind_tools([{"type": "function", "function": {"name": "query", "description": "Read data",
                "parameters": {"type": "object", "properties": {}}}}])
            await ctx.model_call(model, [HumanMessage(content="hello")], max_output_tokens=192)
            assert requests[0]["max_completion_tokens"] == 192
            assert requests[0]["tools"][0]["function"]["name"] == "query"
            assert (await usage(ctx))["tokens"] == 83
    asyncio.run(scenario())


def test_child_limit_rejection_is_atomic():
    async def scenario():
        async with context() as ctx:
            await ctx.store.runs.update_one({"_id": ctx.run["run_id"]}, {"$set": {"usage.child.models": 8}})
            ctx = replace(ctx, depth=1, namespace="child.")
            with pytest.raises(BudgetExceeded, match="child.models_limit"):
                await ctx.model_call(Provider(), [HumanMessage(content="hello")])
            current = await usage(ctx)
            assert current["models"] == 0
            assert current["tokens"] == 0
            assert current["child"]["models"] == 8
    asyncio.run(scenario())


def test_retry_preserves_token_reserve_without_consuming_rejected_model(monkeypatch):
    monkeypatch.setattr("harness.executor.retry_delay", lambda *args: 0)
    async def scenario():
        async with context(token_limit=4096, max_output_tokens=128) as ctx:
            provider = Provider(fail=True)
            messages = [HumanMessage(content="hello")]
            estimated = ctx.estimate_input_tokens(provider, messages) + 128
            with pytest.raises(ConnectionError) as failure:
                await ctx.model_call(provider, messages, reserve_tokens=4096 - estimated * 2)
            assert isinstance(failure.value.__cause__, BudgetExceeded)
            assert str(failure.value.__cause__) == "tokens_limit"
            assert provider.calls == 2
            assert (await usage(ctx))["models"] == 2
            assert (await usage(ctx))["tokens"] == estimated * 2
    asyncio.run(scenario())


def test_calibration_is_scoped_to_actual_model_configuration():
    async def scenario():
        async with context() as ctx:
            model = Provider({"input_tokens": 800, "output_tokens": 1, "total_tokens": 801})
            model.temperature = 0
            messages = [HumanMessage(content="hello")]
            uncalibrated = ctx.estimate_input_tokens(model, messages)
            await ctx.model_call(model, messages, max_output_tokens=128)
            model.temperature = 1
            assert ctx.estimate_input_tokens(model, messages) == uncalibrated
    asyncio.run(scenario())

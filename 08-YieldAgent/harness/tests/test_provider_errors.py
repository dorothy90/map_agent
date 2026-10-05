import asyncio
import uuid

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from openai import AuthenticationError, RateLimitError


@pytest.mark.parametrize("status, expected_calls", [(401, 1), (429, 3)])
def test_provider_auth_stops_and_rate_limit_retries_bounded(status, expected_calls, monkeypatch):
    from harness.config import Settings
    from harness.executor import ExecutionContext
    from harness.store import HarnessStore
    from harness.nodes import Nodes

    class Provider:
        calls = 0
        async def ainvoke(self, messages):
            self.calls += 1
            response = httpx.Response(status, request=httpx.Request("POST", "https://fixture.invalid"))
            error = AuthenticationError if status == 401 else RateLimitError
            raise error("fixture", response=response, body=None)

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        try:
            await store.setup()
            run = await store.start_run("p", "s", "r", "q")
            run = await store.acquire(run["run_id"], "worker")
            context = ExecutionContext(store, Settings(), run)
            model = Provider()
            with pytest.raises((AuthenticationError, RateLimitError)) as failure:
                await context.model_call(model, [HumanMessage(content="fixture")])
            assert model.calls == expected_calls
            stopped = Nodes.stopped(None, {}, failure.value)
            assert stopped["status"] == ("failed" if status == 401 else "partial")
            if status == 401:
                assert stopped["stop_reason"] == "llm_authentication"
            assert (await store.get_run("p", run["run_id"]))["usage"]["models"] == expected_calls
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_retry_delay_honors_provider_seconds_and_http_date():
    from harness.executor import retry_delay
    from datetime import datetime, timezone, timedelta
    from email.utils import format_datetime
    response = httpx.Response(429, headers={"retry-after": "12.5"}, request=httpx.Request("POST", "https://fixture.invalid"))
    error = RateLimitError("fixture", response=response, body=None)
    assert 12.5 <= retry_delay(error, 0) < 13.1
    response.headers["retry-after"] = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=15))
    assert 13 < retry_delay(error, 0) < 16


def test_budget_stop_explains_limit_and_does_not_mix_previous_run_results():
    from harness.nodes import Nodes
    from harness.store import BudgetExceeded
    state = {"run_id": "current", "observations": [
        {"run_id": "old", "result_id": "old-result", "status": "success", "scope": {"product": "이전 제품"}},
        {"run_id": "current", "result_id": "new-result", "status": "success", "scope": {"product": "현재 제품"}},
    ]}
    result = Nodes.stopped(None, state, BudgetExceeded("tokens_limit"))
    assert "토큰 예산" in result["answer"]
    assert "이전 제품" not in result["answer"]
    assert "현재 제품" in result["answer"]
    assert result["stop_reason"] == "tokens_limit"


@pytest.mark.parametrize("failure_type", ["timeout", "rate_limit", "server_error"])
def test_retry_budget_exhaustion_preserves_provider_failure_and_audit(failure_type, monkeypatch):
    from openai import InternalServerError
    from harness.config import Settings
    from harness.executor import ExecutionContext
    from harness.store import HarnessStore, BudgetExceeded
    from harness.nodes import Nodes

    monkeypatch.setattr("harness.executor.retry_delay", lambda *args: 0)
    if failure_type == "timeout":
        failure = TimeoutError("private provider detail")
    else:
        status = 429 if failure_type == "rate_limit" else 503
        response = httpx.Response(status, request=httpx.Request("POST", "https://fixture.invalid"))
        cls = RateLimitError if status == 429 else InternalServerError
        failure = cls("private provider detail", response=response, body=None)

    class Provider:
        calls = 0
        async def ainvoke(self, messages):
            self.calls += 1
            raise failure

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        try:
            await store.setup()
            run = await store.start_run("p", "s", "r", "q")
            run = await store.acquire(run["run_id"], "worker")
            ctx = ExecutionContext(store, Settings(token_limit=4096, max_output_tokens=2048), run)
            model = Provider()
            with pytest.raises(type(failure)) as caught:
                await ctx.model_call(model, [HumanMessage(content="fixture")], final=True, purpose="review")
            assert model.calls == 1
            assert caught.value is failure
            assert isinstance(caught.value.__cause__, BudgetExceeded)
            saved = await store.get_run("p", run["run_id"])
            usage = [e["payload"] for e in saved["events"] if e["type"] == "model_usage"]
            assert len(usage) == 1 and not usage[0]["usage_known"]
            assert saved["usage"]["tokens"] == usage[0]["reserved_tokens"]
            assert usage[0]["error_type"] == type(failure).__name__
            assert usage[0]["elapsed_seconds"] >= 0
            assert usage[0]["http_status"] == getattr(failure, "status_code", None)
            stopped_retry = next(e["payload"] for e in saved["events"] if e["type"] == "model_retry_stopped")
            assert stopped_retry["reason"] == "tokens_limit" and stopped_retry["purpose"] == "review"
            assert "private provider detail" not in str(saved["events"])
            result = Nodes.stopped(None, {}, caught.value, phase="review")
            assert result["stop_reason"] != "tokens_limit"
            assert "검증" in result["answer_text"] and "재시도" in result["answer_text"]
            assert "private provider detail" not in result["answer_text"]
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())

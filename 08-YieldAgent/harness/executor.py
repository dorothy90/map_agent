from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from dataclasses import dataclass, replace, field

import httpx
from openai import APIConnectionError
from opensearchpy.exceptions import ConnectionError as SearchConnectionError
from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout as RequestsTimeout
from pydantic import ValidationError
from langchain_core.messages import convert_to_openai_messages
from .config import Settings
from .store import Conflict, BudgetExceeded
from .types import now


class ToolAuthenticationError(RuntimeError):
    def __init__(self, tool_name, status_code):
        self.tool_name, self.status_code = tool_name, status_code
        super().__init__("Tool authentication failed")


def error_status(exc):
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    return code


def _input_token_units(model, messages):
    # Count the request content and schemas, never the model repr (which includes
    # clients, credentials and configuration unrelated to prompt tokens).
    request = {"messages": convert_to_openai_messages(messages)}
    kwargs = getattr(model, "kwargs", {})
    for key in ("tools", "functions", "response_format", "tool_choice"):
        if key in kwargs:
            request[key] = kwargs[key]
    serialized = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    return max(1, math.ceil(len(serialized.encode("utf-8")) / 3))


def estimate_model_tokens(model, messages, max_output_tokens):
    return math.ceil(_input_token_units(model, messages) * 1.1) + max_output_tokens


def input_sections(model, messages, total_estimate):
    """Allocate a request estimate by serialized content, never claim exact billing."""
    sizes = {}
    for message in messages:
        label = message.additional_kwargs.get('input_section', 'conversation')
        pieces = {label: message.content}
        if label == 'context':
            try:
                context = json.loads(message.content)
                pieces = {'evidence': context.pop('evidence', []), 'result_index': context.pop('result_index', []),
                    'memory': {key: context.pop(key, '') for key in ('summary', 'worklog')}, 'context': context}
            except (ValueError, TypeError):
                pass
        for name, content in pieces.items():
            sizes[name] = sizes.get(name, 0) + len(json.dumps(content, ensure_ascii=False, default=str).encode())
    schemas = {k: v for k, v in getattr(model, 'kwargs', {}).items() if k in ('tools', 'functions', 'tool_choice', 'response_format')}
    sizes['tool_schemas'] = len(json.dumps(schemas, ensure_ascii=False).encode())
    sizes['envelope'] = 1
    whole = sum(sizes.values())
    sections = [{'name': name, 'estimated_tokens': int(total_estimate * size / whole)} for name, size in sizes.items()]
    sections[-1]['estimated_tokens'] += total_estimate - sum(s['estimated_tokens'] for s in sections)
    return sections


def retryable(exc):
    code = error_status(exc)
    return bool(getattr(exc, "retryable", False)) or code == 429 or (isinstance(code, int) and code >= 500) or isinstance(exc, (TimeoutError, ConnectionError, APIConnectionError, httpx.TransportError, SearchConnectionError, RequestsConnectionError, RequestsTimeout))


def retry_delay(exc, attempt):
    value = getattr(getattr(exc, "response", None), "headers", {}).get("retry-after")
    delay = 2 ** attempt
    if value:
        try:
            delay = max(delay, float(value))
        except ValueError:
            try:
                delay = max(delay, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                pass
    return delay + random.uniform(0, .5)


@dataclass
class ExecutionContext:
    store: object
    settings: Settings
    run: dict
    depth: int = 0
    namespace: str = ""
    invocation_id: str = ""
    io_gate: object = field(default_factory=lambda: asyncio.Semaphore(2))
    clock_started: float = field(default_factory=time.monotonic)
    active_at_start: float | None = None
    protected_tokens: int = 0
    protected_models: int = 0
    protected_seconds: float = 0
    parent_invocation_id: str = ""

    def __post_init__(self):
        if self.active_at_start is None:
            self.active_at_start = self.run.get("usage", {}).get("active_seconds", 0)

    def remaining_seconds(self, current=None):
        recorded = (current or self.run).get("usage", {}).get("active_seconds", 0)
        elapsed = self.active_at_start + time.monotonic() - self.clock_started
        return max(0, self.settings.active_seconds - max(recorded, elapsed))

    async def check(self):
        run = await self.store.fence(self.run["run_id"], self.run["epoch"])
        if self.remaining_seconds(run) <= 0:
            raise BudgetExceeded("active_time_limit")
        return run

    def _calibration_key(self, model):
        base = getattr(model, "bound", model)
        kwargs = getattr(model, "kwargs", {})
        config = [type(base).__module__, type(base).__qualname__,
            kwargs.get("model", getattr(base, "model_name", getattr(base, "model", self.settings.model))),
            str(getattr(base, "openai_api_base", None) or self.settings.base_url),
            kwargs.get("temperature", getattr(base, "temperature", self.settings.temperature)),
            kwargs.get("reasoning_effort", getattr(base, "reasoning_effort", self.settings.reasoning_effort))]
        return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()

    def estimate_input_tokens(self, model, messages):
        calibration = self.run.get("token_calibration", {}).get(self._calibration_key(model), {})
        return math.ceil(_input_token_units(model, messages) * calibration.get("ratio", 1.0) * 1.1)

    async def model_call(self, model, messages, *, final=False, reserve_tokens=0,
                         reserve_models=0, reserve_seconds=0, max_output_tokens=None, purpose="reasoning"):
        reserve_tokens += self.protected_tokens
        reserve_models += self.protected_models
        reserve_seconds += self.protected_seconds
        cap = self.settings.max_output_tokens if max_output_tokens is None else min(max_output_tokens, self.settings.max_output_tokens)
        if cap < 1 or reserve_tokens < 0 or reserve_models < 0:
            raise ValueError("Output cap must be positive and reserves nonnegative")
        # LangChain bindings preserve bound tool schemas while overriding the
        # provider's actual output limit. Lightweight scripted providers need no binding.
        bounded_model = model.bind(max_tokens=cap) if callable(getattr(model, "bind", None)) else model
        run_id, epoch = self.run["run_id"], self.run["epoch"]
        key, units = self._calibration_key(model), _input_token_units(model, messages)
        last_error = None
        for attempt in range(3):
            current = await self.check()
            available_seconds = self.remaining_seconds(current) - reserve_seconds
            if available_seconds <= 0:
                failure = BudgetExceeded("active_time_reserve")
                if last_error is not None:
                    raise last_error from failure
                raise failure
            self.run["token_calibration"] = current.get("token_calibration", {})
            input_estimate = self.estimate_input_tokens(model, messages)
            estimated = input_estimate + cap
            try:
                await self.store.reserve_model_call(run_id, epoch,
                    model_limit=self.settings.model_limit - reserve_models,
                    token_limit=self.settings.token_limit - reserve_tokens, tokens=estimated,
                    child_kind=self.namespace + "models" if self.depth else None)
            except BudgetExceeded as exc:
                if last_error is not None:
                    await self.store.event(run_id, "model_retry_stopped", {"purpose": purpose,
                        "attempt": attempt + 1, "reason": str(exc),
                        "error_type": type(last_error).__name__, "http_status": error_status(last_error)}, epoch=epoch)
                    raise last_error from exc
                raise
            payload = {"purpose": purpose, "final": final, "attempt": attempt + 1,
                "model_config": key, "estimated_input_tokens": input_estimate,
                "max_output_tokens": cap, "reserved_tokens": estimated,
                "reserve_tokens": reserve_tokens, "reserve_models": reserve_models,
                "reserve_seconds": reserve_seconds,
                "input_tokens": None, "output_tokens": None, "total_tokens": None,
                "charged_tokens": estimated, "usage_known": False}
            payload['input_sections'] = input_sections(model, messages, input_estimate)
            started = time.monotonic()
            try:
                message = {"reasoning": "요청과 조회 자료를 확인하고 있습니다.",
                    "answer": "확인한 자료로 답변을 작성하고 있습니다.",
                    "review": "답변의 내용과 근거를 확인하고 있습니다.",
                    "compaction": "긴 대화의 맥락을 정리하고 있습니다."}.get(purpose, "모델 응답을 기다리고 있습니다.")
                await self.store.event(run_id, "progress", {"name": purpose, "message": message}, epoch=epoch)
                response = await asyncio.wait_for(bounded_model.ainvoke(messages), min(self.settings.call_timeout, available_seconds))
            except BaseException as exc:
                # Unknown provider billing keeps the full reservation, including
                # cancelled/timed-out attempts, and every retry keeps caller reserves.
                payload["status"] = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
                payload.update(error_type=type(exc).__name__, http_status=error_status(exc),
                    elapsed_seconds=round(time.monotonic() - started, 3))
                await self.store.event(run_id, "model_usage", payload, epoch=epoch)
                if not isinstance(exc, Exception) or not retryable(exc) or attempt == 2:
                    raise
                last_error = exc
                delay = retry_delay(exc, attempt)
                if delay >= self.remaining_seconds() - reserve_seconds:
                    raise last_error from BudgetExceeded("active_time_reserve")
                await self.store.event(run_id, "progress", {"message": "모델 호출 한도 또는 일시적 오류로 재시도를 기다립니다.", "attempt": attempt + 2, "retry_after_seconds": round(delay, 1)}, epoch=epoch)
                await asyncio.sleep(delay)
                continue
            await self.check()
            usage = getattr(response, "usage_metadata", None) or {}
            provider_usage = (getattr(response, "response_metadata", None) or {}).get("token_usage", {})
            input_tokens = usage.get("input_tokens", provider_usage.get("prompt_tokens"))
            output_tokens = usage.get("output_tokens", provider_usage.get("completion_tokens"))
            actual = usage.get("total_tokens", provider_usage.get("total_tokens"))
            if actual is None and input_tokens is not None and output_tokens is not None:
                actual = input_tokens + output_tokens
            update = {}
            if actual is not None:
                update["$inc"] = {"usage.tokens": actual - estimated}
            if input_tokens is not None and input_tokens > 0:
                update.setdefault("$inc", {})[f"token_calibration.{key}.samples"] = 1
                update["$max"] = {f"token_calibration.{key}.ratio": input_tokens / units}
            if update:
                await self.store.runs.update_one({"_id": run_id, "epoch": epoch, "status": "running", "active": True}, update)
                current = await self.check()
                self.run["token_calibration"] = current.get("token_calibration", {})
            payload.update(status="success", input_tokens=input_tokens, output_tokens=output_tokens,
                total_tokens=actual, charged_tokens=actual if actual is not None else estimated,
                usage_known=actual is not None, elapsed_seconds=round(time.monotonic() - started, 3))
            payload["input_token_details"] = usage.get("input_token_details", provider_usage.get("prompt_tokens_details", {}))
            payload["output_token_details"] = usage.get("output_token_details", provider_usage.get("completion_tokens_details", {}))
            await self.store.event(run_id, "model_usage", payload, epoch=epoch)
            return response


class ToolExecutor:
    def __init__(self, registry, context):
        self.registry, self.context = registry, context

    async def execute(self, call):
        ctx, store = self.context, self.context.store
        run = await ctx.check()
        name, args, call_id = call["name"], call.get("args", {}), ctx.namespace + call["id"]
        spec = self.registry.tools.get(name)
        existing = await store.calls.find_one({"run_id": run["run_id"], "invocation_id": call_id})
        if existing and existing["status"] == "succeeded":
            if existing["arguments"] != args or existing["tool_name"] != name:
                raise Conflict("Invocation ID reused")
            observation = await store.observation(run["principal_id"], run["session_id"], existing["result_id"])
            await self.publish(observation, run)
            return observation
        fingerprint = hashlib.sha256(json.dumps([name, args], sort_keys=True).encode()).hexdigest()
        repeated = await store.calls.count_documents({"run_id": run["run_id"], "fingerprint": fingerprint})
        if not existing and repeated >= 3:
            raise BudgetExceeded("repeated_action_without_progress")
        await store.claim_invocation(run["run_id"], call_id, name, args, run["epoch"], read_only=bool(spec and spec.read_only))
        await store.reserve_tool_call(run["run_id"], run["epoch"], invocation_id=call_id,
            tool_limit=ctx.settings.tool_limit, child_kind=ctx.namespace + "tools" if ctx.depth else None)
        await store.calls.update_one({"run_id": run["run_id"], "invocation_id": call_id}, {"$set": {"fingerprint": fingerprint}})
        started = time.monotonic()
        await store.event(run["run_id"], "tool_started", {"name": name, "invocation_id": call_id,
            "parent_invocation_id": ctx.parent_invocation_id or None, "state": "running", "elapsed_seconds": 0},
            event_key=call_id + ":started", epoch=run["epoch"])
        observation = {"run_id": run["run_id"], "invocation_id": call_id, "tool_name": name, "validated_arguments": args,
            "provenance": {"executed_at": now(), "source_system": name, "query_fingerprint": fingerprint, "data_origin": "live"}}
        rows, artifacts, tables = [], [], []
        try:
            if spec is None:
                raise ValueError("Unknown tool")
            for attempt in range(3):
                try:
                    async def invoke():
                        if name == "delegate_readonly":
                            return await self.registry.invoke(name, args, replace(ctx, invocation_id=call_id))
                        async with ctx.io_gate:
                            await ctx.check()
                            return await self.registry.invoke(name, args, replace(ctx, invocation_id=call_id))
                    remaining = ctx.remaining_seconds() - ctx.protected_seconds
                    if remaining <= 0:
                        raise BudgetExceeded("active_time_reserve")
                    result = await asyncio.wait_for(invoke(), min(spec.timeout, remaining,
                        ctx.settings.active_seconds if name == "delegate_readonly" else ctx.settings.call_timeout))
                    break
                except Exception as exc:
                    if not spec.read_only or not retryable(exc) or attempt == 2:
                        raise
                    delay = 2 ** attempt + random.uniform(0, .5)
                    if delay >= ctx.remaining_seconds() - ctx.protected_seconds:
                        raise
                    await asyncio.sleep(delay)
                    await ctx.check()
            rows, artifacts, tables = result.rows, result.artifacts, result.tables
            observation.update(status=result.status or ("success" if rows or artifacts or any(t.rows for t in tables) else "empty"), summary=result.summary,
                scope=result.scope, source_result_ids=result.source_result_ids)
            observation["provenance"]["data_origin"] = result.data_origin
        except (Conflict, asyncio.CancelledError):
            raise
        except Exception as exc:
            code = error_status(exc)
            if code in (401, 403):
                raise ToolAuthenticationError(name, code) from None
            message = "도구 실행에 실패했습니다. 다른 접근이나 필요한 설정을 확인하세요."
            if isinstance(exc, ValidationError):
                message = json.dumps(exc.errors(include_input=False, include_url=False), ensure_ascii=False, default=str)
            elif isinstance(exc, (ValueError, PermissionError)):
                message = str(exc)[:1000]
            elif hasattr(exc, 'safe_message'):
                message = exc.safe_message
            observation.update(status="error", error={"code": getattr(exc, 'error_type', type(exc).__name__),
                "source_code": getattr(exc, 'source_code', None), "retryable": retryable(exc), "safe_message": message}, summary=message)
        await ctx.check()
        observation['provenance'].update(elapsed_seconds=round(time.monotonic() - started, 3),
            parent_invocation_id=ctx.parent_invocation_id or None)
        obs = await store.put_result(run["principal_id"], run["session_id"], observation, rows, artifacts,
            **({'tables': tables} if tables else {}))
        await store.finish_invocation(run["run_id"], call_id, obs, run["epoch"])
        await self.publish(obs, run)
        return obs

    async def publish(self, obs, run):
        store = self.context.store
        await store.runs.update_one({"_id": run["run_id"], "epoch": run["epoch"], "status": "running"},
            {"$addToSet": {"observations": obs.model_dump(mode="json")}})
        await store.event(run["run_id"], "tool_finished", {"name": obs.tool_name, "status": obs.status,
            "state": obs.status, "invocation_id": obs.invocation_id,
            "parent_invocation_id": obs.provenance.get('parent_invocation_id'),
            "elapsed_seconds": obs.provenance.get('elapsed_seconds', 0),
            "result_id": obs.result_id, "summary": obs.summary}, event_key=obs.invocation_id + ":finished", epoch=run["epoch"])
        for artifact in obs.artifact_refs:
            await store.event(run["run_id"], "artifact", artifact, event_key="artifact:" + artifact["artifact_id"], epoch=run["epoch"])

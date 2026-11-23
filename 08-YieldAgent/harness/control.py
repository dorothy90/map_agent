from __future__ import annotations

import asyncio
import time
import uuid
import logging

from langsmith import tracing_context

from .executor import ExecutionContext
from .store import Conflict, LeaseLost
from .tools.registry import domain_registry


logger = logging.getLogger(__name__)


class RunController:
    def __init__(self, store, settings, *, registry_factory=domain_registry):
        self.store, self.settings = store, settings
        self.registry_factory = registry_factory
        self.chat_entrypoint = "stock_chat" if registry_factory is domain_registry else "legacy_verified"
        self.tasks = {}
        self.owner = str(uuid.uuid4())
        self.closing = False
        self.recovery_task = None

    async def start(self, principal_id, session_id, request_id, query):
        async with self.store.session_lock(principal_id, session_id):
            if await self.store.runs.find_one({"principal_id": principal_id, "session_id": session_id, "steer_pending": {"$exists": True}}):
                raise Conflict("A goal change is being recovered")
            run = await self.store.start_run(principal_id, session_id, request_id, query, engine=self.settings.engine,
                                             chat_entrypoint=self.chat_entrypoint)
        await self.launch(run["run_id"])
        return run

    async def launch(self, run_id):
        if run_id in self.tasks and not self.tasks[run_id].done():
            return
        existing = await self.store.runs.find_one({"_id": run_id})
        if existing and existing.get("active") and existing.get("runtime_version") != "harness/v2":
            raise Conflict("Active runtime version is incompatible; resume with its original version or cancel first")
        if existing and existing.get("engine") != "hermes":
            await self._reject_engine(existing)
            if not existing.get("active"):
                raise Conflict("Unsupported stored run engine; only hermes can execute")
            return
        run = await self.store.acquire(run_id, self.owner)
        if run:
            # Harness audit data stays in its owned Mongo store.
            with tracing_context(enabled=False):
                self.tasks[run_id] = asyncio.create_task(self._run(run))
            for key in list(self.tasks):
                if len(self.tasks) > 100 and self.tasks[key].done():
                    self.tasks.pop(key)

    async def _reject_engine(self, run):
        message = "Unsupported stored run engine: " + repr(run.get("engine")) + "; only hermes can execute"
        if run.get("active"):
            await self.store.runs.update_one(
                {"_id": run["run_id"], "epoch": run["epoch"], "active": True,
                 "status": "waiting_user", "lease_until": {"$lt": time.time()}},
                {"$set": {"status": "created"}})
            owned = await self.store.acquire(run["run_id"], self.owner)
            if owned:
                await self.store.finish(owned["run_id"], owned["epoch"], "failed", message, "unsupported_engine")
                await self.store.runs.update_one(
                    {"_id": owned["run_id"], "epoch": owned["epoch"], "active": False},
                    {"$unset": {"steer_pending": ""}})
            else:
                raise Conflict(message)
        elif run.get("steer_pending"):
            await self.store.runs.update_one(
                {"_id": run["run_id"], "epoch": run["epoch"], "active": False},
                {"$unset": {"steer_pending": ""}})

    async def _heartbeat(self, run):
        previous = time.monotonic()
        while True:
            await asyncio.sleep(5)
            current = time.monotonic()
            result = await self.store.renew(run["run_id"], run["epoch"], current - previous)
            previous = current
            if not result.modified_count:
                task = self.tasks.get(run["run_id"])
                if task:
                    task.cancel()
                return
            if current - run["started_monotonic"] + run["usage"]["active_seconds"] > self.settings.active_seconds:
                task = self.tasks.get(run["run_id"])
                if task:
                    task.cancel()
                return

    async def _run(self, run):
        run["started_monotonic"] = time.monotonic()
        worker = asyncio.current_task()
        heartbeat = asyncio.create_task(self._heartbeat(run))
        finishing = False
        remaining = max(0, self.settings.active_seconds - run['usage']['active_seconds'])
        deadline = asyncio.get_running_loop().call_later(remaining, worker.cancel)

        def stop_on_heartbeat_failure(task):
            if not task.cancelled() and task.exception() is not None:
                logger.warning("Run %s lease renewal failed: %s", run["run_id"], type(task.exception()).__name__)
                if not finishing:
                    worker.cancel()

        def stop_heartbeat():
            nonlocal finishing
            finishing = True
            heartbeat.cancel()
            deadline.cancel()

        heartbeat.add_done_callback(stop_on_heartbeat_failure)
        try:
            context = ExecutionContext(self.store, self.settings, run)
            await self.store.event(run["run_id"], "run_started", {"query": run["query"],
                "goal_revision": run["goal_revision"], "engine": "hermes"})
            if run.get('chat_entrypoint') == 'stock_chat':
                from .hermes_chat import run_chat
                result = await run_chat(context)
            else:
                from .native_hermes import native_history, run_native
                previous = await self.store.runs.find({"principal_id": run["principal_id"],
                    "session_id": run["session_id"], "active": False}).sort("created_at", -1).limit(10).to_list(length=10)
                result = await run_native(context, self.registry_factory(), history=native_history(previous))
            await context.check()
            stop_heartbeat()
            await self._save_context(run, result)
            await self.store.finish(run["run_id"], run["epoch"], result["status"], result["answer"], result["stop_reason"])
        except LeaseLost:
            stop_heartbeat()
            # finish is epoch-fenced: an old worker cannot finish its successor.
            await self.store.finish(run["run_id"], run["epoch"], "partial", "실행권이 만료되어 작업을 중지했습니다.", "lease_expired")
        except asyncio.CancelledError:
            stop_heartbeat()
            if self.closing:
                await self.store.runs.update_one({"_id": run["run_id"], "epoch": run["epoch"]}, {"$set": {"lease_until": 0}})
            else:
                current = await self.store.get_run(run["principal_id"], run["run_id"])
                cancelled = current["status"] == "cancelling"
                renewal_failed = heartbeat.done() and not heartbeat.cancelled() and heartbeat.exception() is not None
                answer = "실행권 갱신에 실패하여 작업을 중지했습니다." if renewal_failed else "실행 시간 한도에 도달했습니다."
                reason = "lease_renewal_failed" if renewal_failed else "active_time_limit"
                await self.store.finish(run["run_id"], run["epoch"], "cancelled" if cancelled else "partial",
                    "작업을 중지했습니다." if cancelled else answer, "user_cancelled" if cancelled else reason)
        except Exception as exc:
            logger.exception("Run %s failed", run["run_id"])
            stop_heartbeat()
            await self.store.finish(run["run_id"], run["epoch"], "failed", "실행을 완료하지 못했습니다. 연결과 실행 설정을 확인하세요.", type(exc).__name__)
        finally:
            stop_heartbeat()
            await asyncio.gather(heartbeat, return_exceptions=True)
            # Heartbeats may leave a final sub-five-second slice unaccounted.
            # Epoch fencing prevents a stopped predecessor charging its successor.
            await self.store.runs.update_one({'_id': run['run_id'], 'epoch': run['epoch']},
                {'$max': {'usage.active_seconds': run['usage']['active_seconds'] + time.monotonic() - run['started_monotonic']}})

    async def _save_context(self, run, result):
        if "native_messages" in result:
            await self.store.runs.update_one({"_id": run["run_id"], "epoch": run["epoch"], "status": "running"},
                {"$set": {key: result[key] for key in ("native_messages", "native_usage", "native_engine")}})
        from .context import memory_snapshot
        await self.store.runs.update_one({"_id": run["run_id"], "epoch": run["epoch"], "status": "running", "active": True}, {"$set": {
            "observations": result.get("observations", []), "result_ids": result.get("result_ids", []),
            "context_memory": memory_snapshot(result), "completion_review": result.get("completion_review", {}),
            "validation_issues": result.get("validation_issues", []),
            "answer_text": result.get("answer_text", result.get("answer", ""))}, "$unset": {"resume_input": ""}})

    async def cancel(self, principal_id, run_id):
        run = await self.store.get_run(principal_id, run_id)
        if not run["active"]:
            return run
        await self.store.runs.update_one({"_id": run_id, "active": True}, {"$set": {"status": "cancelling"}})
        task = self.tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        else:
            await self.store.finish(run_id, run["epoch"], "cancelled", "작업을 중지했습니다.", "user_cancelled")
        return await self.store.get_run(principal_id, run_id)

    async def submit_input(self, principal_id, run_id, payload):
        run = await self.store.get_run(principal_id, run_id)
        if run.get("engine") != "hermes":
            await self._reject_engine(run)
            raise Conflict("Unsupported stored run engine; only hermes can execute")
        if payload["kind"] == "steer":
            async with self.store.session_lock(principal_id, run["session_id"]):
                existing = await self.store.runs.find_one({"principal_id": principal_id, "session_id": run["session_id"], "request_id": payload["request_id"], "parent_run_id": run_id})
                if existing and existing.get("ready"):
                    if existing["query"] != str(payload["value"]):
                        raise Conflict("Request ID already used")
                    return existing
                run = await self.store.get_run(principal_id, run_id)
                pending = run.get("steer_pending")
                if pending and pending != payload:
                    raise Conflict("Another goal change is pending")
                if not pending:
                    updated = await self.store.runs.update_one({"_id": run_id, "active": True, "goal_revision": payload["goal_revision"]}, {"$set": {"steer_pending": payload}})
                    if not updated.modified_count:
                        raise Conflict("Stale goal revision")
                return await self._apply_steer(run, payload)
        for previous in run.get("user_inputs", []):
            if previous["request_id"] == payload["request_id"]:
                if previous["value"] != payload["value"]:
                    raise Conflict("Request ID already used")
                return run
        if not run["active"] or payload["goal_revision"] != run["goal_revision"]:
            raise Conflict("Stale goal revision")
        if run.get("runtime_version") != "harness/v2":
            raise Conflict("Active runtime version is incompatible; cancel or use its original version")
        if run["status"] != "waiting_user" or payload.get("interrupt_id") != run.get("question", {}).get("interrupt_id"):
            raise Conflict("Stale interrupt")
        entry = {"request_id": payload["request_id"], "value": payload["value"], "question": run["question"]}
        event = {"run_id": {"$literal": run_id}, "type": "input_received",
            "payload": {"$literal": {**entry, "interrupt_id": payload["interrupt_id"]}},
            "sequence": {"$add": [{"$size": "$events"}, 1]}, "event_id": {"$literal": str(uuid.uuid4())}}
        result = await self.store.runs.update_one({"_id": run_id, "status": "waiting_user", "goal_revision": payload["goal_revision"]},
            [{"$set": {"resume_input": {"$literal": payload["value"]},
                "input_request_id": {"$literal": payload["request_id"]}, "status": "created", "lease_until": 0,
                "user_inputs": {"$concatArrays": [{"$ifNull": ["$user_inputs", []]}, {"$literal": [entry]}]},
                "events": {"$concatArrays": ["$events", [event]]}}}])
        if not result.modified_count:
            raise Conflict("Input already handled")
        await self.launch(run_id)
        return await self.store.get_run(principal_id, run_id)

    async def _apply_steer(self, run, payload):
        if run.get("engine") != "hermes":
            await self._reject_engine(run)
            raise Conflict("Unsupported stored run engine; only hermes can execute")
        await self.cancel(run["principal_id"], run["run_id"])
        next_run = await self.store.start_run(run["principal_id"], run["session_id"], payload["request_id"], str(payload["value"]),
            ready=False, engine="hermes", chat_entrypoint=run.get("chat_entrypoint", "legacy_verified"))
        await self.store.runs.update_one({"_id": next_run["run_id"]}, {"$set": {"parent_run_id": run["run_id"], "goal_revision": run["goal_revision"] + 1,
            "goal_request": run.get("goal_request", run["query"]) + "\n사용자의 최신 수정: " + str(payload["value"]), "ready": True}})
        await self.store.runs.update_one({"_id": run["run_id"]}, {"$unset": {"steer_pending": ""}})
        await self.launch(next_run["run_id"])
        return await self.store.get_run(run["principal_id"], next_run["run_id"])

    async def recover(self):
        async for run in self.store.runs.find({"execution_kind": "harness", "runtime_version": "harness/v2",
                "engine": {"$ne": "hermes"}, "active": True,
                "status": {"$in": ["created", "running", "waiting_user"]}, "lease_until": {"$lt": time.time()}}):
            try:
                await self._reject_engine(run)
            except Conflict:
                continue
        async for run in self.store.runs.find({"execution_kind": "harness", "runtime_version": "harness/v2", "steer_pending": {"$exists": True}}):
            try:
                async with self.store.session_lock(run["principal_id"], run["session_id"]):
                    await self._apply_steer(run, run["steer_pending"])
            except Conflict:
                continue
        async for run in self.store.runs.find({"execution_kind": "harness", "runtime_version": "harness/v2", "ready": True, "steer_pending": {"$exists": False}, "active": True, "status": {"$in": ["created", "running"]}, "lease_until": {"$lt": time.time()}}):
            await self.launch(run["run_id"])
        async for run in self.store.runs.find({"execution_kind": "harness", "active": True, "status": "cancelling", "lease_until": {"$lt": time.time()}}):
            await self.store.finish(run["run_id"], run["epoch"], "cancelled", "작업을 중지했습니다.", "user_cancelled")

    async def watch_recovery(self):
        while True:
            await self.recover()
            await asyncio.sleep(10)

    async def close(self):
        self.closing = True
        if self.recovery_task:
            self.recovery_task.cancel()
            await asyncio.gather(self.recovery_task, return_exceptions=True)
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

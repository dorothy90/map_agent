"""Foreground deadline and durable delivery for web-initiated wafer map aggregations."""
import asyncio
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from pymongo import ReturnDocument

from .tools.registry import ToolResult

LOG = logging.getLogger(__name__)
LEASE_SECONDS = 60
MAX_ATTEMPTS = 3


async def dispatch_map_job(args, ctx, tool_timeout):
    owner = {key: ctx.run[key] for key in ("principal_id", "session_id", "run_id")}
    job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, ctx.run["run_id"] + ":map-job:" + ctx.invocation_id))
    jobs = ctx.store.db.map_jobs
    key = {"_id": job_id, **owner, "invocation_id": ctx.invocation_id}
    arguments = args.model_dump(mode="json", exclude_defaults=True, exclude_none=True)
    await jobs.update_one(key, {"$setOnInsert": {"arguments": arguments, "status": "queued",
        "delivery": "waiting", "created_at": time.time(), "timezone": ctx.settings.timezone}}, upsert=True)

    # Leave part of the existing call budget for the receipt, DB writes and final answer.
    timeout = min(ctx.settings.call_timeout, ctx.remaining_seconds() - ctx.protected_seconds, tool_timeout)
    deadline = time.monotonic() + max(0, timeout * 0.8)
    try:
        while True:
            job = await jobs.find_one(key, {"result_id": 1, "status": 1, "delivery": 1, "arguments": 1})
            if job["arguments"] != arguments:
                raise ValueError("Map invocation already belongs to different arguments")
            if job["delivery"] == "delivered":
                return ToolResult(status="success", scope={"background_map": {"job_id": job_id, "status": "delivered"}},
                                  summary="이 맵 요청의 처리 결과를 원래 채팅에 전달했습니다.")
            if job["delivery"] == "unavailable":
                raise PermissionError("원래 채팅으로 맵을 전달할 수 없습니다.")
            if job["delivery"] == "background":
                break
            if job["status"] == "completed":
                if not job.get("result_id"):
                    raise ValueError("맵 집계를 마치지 못했습니다.")
                claimed = await jobs.update_one({**key, "delivery": {"$in": ["waiting", "inline"]}},
                                                {"$set": {"delivery": "inline"}})
                if claimed.matched_count:
                    return await ctx.store.observation(owner["principal_id"], owner["session_id"], job["result_id"])
                continue
            if job["status"] in ("failed", "cancelled"):
                raise ValueError("맵 집계를 시작하지 못했습니다.")
            if time.monotonic() >= deadline:
                detached = await jobs.update_one({**key, "status": {"$in": ["queued", "running"]},
                    "delivery": "waiting"}, {"$set": {"delivery": "background", "detached_at": time.time()}})
                if detached.matched_count:
                    break
                continue
            await asyncio.sleep(min(.25, max(0, deadline - time.monotonic())))
    except asyncio.CancelledError:
        await jobs.update_one({**key, "delivery": "waiting"}, {"$set": {"status": "cancelled"}})
        raise
    return ToolResult(status="success", scope={"background_map": {"job_id": job_id, "status": "processing"}},
        summary="맵 집계가 진행 중입니다. 현재 요청은 접수로 마치고, 완료되면 이 채팅에 결과가 자동 표시됩니다.")


async def supervise_map_jobs(store, settings, *, principal=None):
    from .gateway import map_completion

    jobs = store.db.map_jobs
    scope = {"principal_id": principal} if principal is not None else {}
    children = []
    while True:
        try:
            children[:] = [process for process in children if process.poll() is None]
            # A detached notice host renews its own lease and normally survives a gateway restart.
            expired = {**scope, "status": "running", "lease_until": {"$lt": time.time()}}
            await jobs.update_many({**expired, "attempts": {"$gte": MAX_ATTEMPTS}}, {"$set": {"status": "failed"}})
            await jobs.update_many(expired, {"$set": {"status": "queued"}})
            if len(children) < settings.parallel_tool_limit:
                token = uuid.uuid4().hex
                job = await jobs.find_one_and_update({**scope, "status": "queued"}, {"$inc": {"attempts": 1}, "$set": {
                    "status": "running", "token": token, "lease_until": time.time() + LEASE_SECONDS}},
                    sort=[("created_at", 1)], return_document=ReturnDocument.AFTER)
                if job:
                    src = str(Path(__file__).resolve().parents[1])
                    env = {name: os.environ[name] for name in (
                        "PATH", "LANG", "LC_ALL", "SystemRoot", "SystemDrive", "TEMP", "TMP", "TMPDIR",
                        "ORACLE_USER", "ORACLE_PASSWORD", "ORACLE_DSN", "ORACLE_CALL_TIMEOUT_MS", "TNS_ADMIN",
                        "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH") if name in os.environ}
                    env.update(PYTHONPATH=src, PYTHON_DOTENV_DISABLED="1",
                               HARNESS_MONGO_URI=settings.mongo_uri, HARNESS_MONGO_DB=store.db.name)
                    process = subprocess.Popen([sys.executable, "-m", "harness.map_job_worker", "watch", job["_id"], token],
                        env=env, cwd=src, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, start_new_session=os.name == "posix")
                    children.append(process)
            async for job in jobs.find({**scope, "status": {"$in": ["completed", "failed"]},
                                        "delivery": "background", "retry_after": {"$not": {"$gt": time.time()}}}).limit(16):
                key = {"_id": job["_id"], "status": job["status"], "delivery": "background"}
                try:
                    if job["status"] == "failed":
                        await map_completion.publish_map_job_failure(store, job)
                    else:
                        await map_completion.publish_map_completion(store, job, job["notice"])
                except (PermissionError, LookupError):
                    await jobs.update_one(key, {"$set": {"delivery": "unavailable"}})
                except (BlockingIOError, TimeoutError):
                    await jobs.update_one(key, {"$set": {"retry_after": time.time() + 1}})
                except Exception:
                    LOG.exception("Could not deliver map job %s", job["_id"])
                    await jobs.update_one(key, {"$set": {"retry_after": time.time() + 5}})
                else:
                    await jobs.update_one(key, {"$set": {"delivery": "delivered", "delivered_at": time.time()}})
        except Exception:
            LOG.exception("Map job supervisor iteration failed")
            await asyncio.sleep(1)
        await asyncio.sleep(.25)

"""Run one trusted native aggregate and persist the original Hermes process exit notification."""
import asyncio
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import uuid

from .map_jobs import LEASE_SECONDS
from .store import HarnessStore
from .types import now


async def run(store, key):
    from .tools.load_wafer_maps import LoadWaferMapsInput, load_wafer_maps
    from .runtime.domain_process import safe_error_message
    from .executor import retry_delay, retryable

    jobs = store.db.map_jobs
    job = await jobs.find_one(key)
    if job is None:
        return
    principal, sid, rid = (job[k] for k in ("principal_id", "session_id", "run_id"))
    origin = await store.runs.find_one({"_id": rid, "principal_id": principal, "session_id": sid},
                                       {"status": 1, "principal_id": 1, "session_id": 1, "run_id": 1})
    chat = await store.db.gateway_sessions.find_one({"principal_id": principal, "id": sid, "deleted": {"$ne": True}}, {"_id": 1})
    call = await store.calls.find_one({"run_id": rid, "invocation_id": job["invocation_id"],
                                      "tool_name": "load_wafer_maps"}, {"arguments": 1, "fingerprint": 1})
    if not origin or not chat or not call or origin["status"] in ("failed", "cancelled", "cancelling"):
        raise PermissionError("Originating map invocation unavailable")
    args = LoadWaferMapsInput.model_validate(job["arguments"])
    if args != LoadWaferMapsInput.model_validate(call["arguments"]) or not args.aggregate:
        raise ValueError("Map job does not match its validated aggregate request")
    result_key = "map-job:" + job["_id"]
    result_id = str(uuid.uuid5(uuid.NAMESPACE_URL, rid + ":" + result_key))
    if not await store.results.find_one({"result_id": result_id, "principal_id": principal, "session_id": sid}, {"_id": 1}):
        executed_at = now()
        ctx = SimpleNamespace(run=origin, store=store, invocation_id=job["invocation_id"],
                              settings=SimpleNamespace(timezone=job["timezone"]))
        started = time.monotonic()
        meta = dict(run_id=rid, invocation_id=job["invocation_id"], tool_name="load_wafer_maps",
                    validated_arguments=call["arguments"])
        try:
            for attempt in range(3):
                try:
                    result = await load_wafer_maps(args, ctx)
                    break
                except Exception as exc:
                    if attempt == 2 or not retryable(exc):
                        raise
                    await asyncio.sleep(retry_delay(exc, attempt))
        except Exception as exc:
            message = getattr(exc, "safe_message", None) or (safe_error_message(exc) if isinstance(exc, ValueError)
                                                             else "맵 집계에 실패했습니다.")
            meta.update(status="error", summary=message, error={"code": type(exc).__name__, "safe_message": message})
            rows, artifacts, tables = [], [], []
        else:
            meta.update(status=result.status or "success", scope=result.scope, summary=result.summary,
                        source_result_ids=result.source_result_ids)
            rows, artifacts, tables = result.rows, result.artifacts, result.tables
        meta["provenance"] = {"source_system": "load_wafer_maps", "data_origin": "live",
                              "executed_at": executed_at, "query_fingerprint": call.get("fingerprint"),
                              "elapsed_seconds": round(time.monotonic() - started, 3)}
        obs = await store.put_result(principal, sid, meta, rows, artifacts, tables=tables, result_key=result_key)
        result_id = obs.result_id
    await jobs.update_one(key, {"$set": {"result_id": result_id}})


async def watch(store, key):
    jobs = store.db.map_jobs
    job = await jobs.find_one(key)
    if job is None:
        return
    engine = str(Path(__file__).resolve().with_name("engine"))
    with tempfile.TemporaryDirectory(prefix="yield-map-notice-") as home:
        os.environ["HERMES_HOME"] = home
        sys.path.insert(0, engine)
        from gateway.session_context import scoped_current_session_id
        from tools.process_registry import ProcessRegistry

        registry = ProcessRegistry()
        sid, job_id = job["session_id"], job["_id"]
        process = subprocess.Popen([sys.executable, "-m", "harness.map_job_worker", "run", job_id, key["token"]],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, errors="replace", start_new_session=os.name == "posix")
        try:
            with scoped_current_session_id(sid):
                session = registry.adopt_local(process, command="load_wafer_maps", session_key=sid,
                    cwd=os.getcwd(), task_id=job_id, owner_task_id=job_id, notify_on_complete=True)
            await jobs.update_one(key, {"$set": {"process_id": session.id, "started_at": time.time()}})
            while True:
                try:
                    event = await asyncio.to_thread(registry.completion_queue.get, True, LEASE_SECONDS // 4)
                except queue.Empty:
                    origin = await store.runs.find_one({"_id": job["run_id"], "principal_id": job["principal_id"],
                                                        "session_id": sid}, {"status": 1})
                    if not origin or origin["status"] in ("failed", "cancelled", "cancelling"):
                        await jobs.update_one(key, {"$set": {"status": "cancelled"}})
                        return
                    renewed = await jobs.update_one(key, {"$set": {"lease_until": time.time() + LEASE_SECONDS}})
                    if not renewed.matched_count:
                        return
                    continue
                expected = dict(type="completion", session_id=session.id, session_key=sid, task_id=job_id,
                                owner_task_id=job_id, command="load_wafer_maps")
                if any(event.get(name) != value for name, value in expected.items()):
                    raise ValueError("Hermes completion has the wrong job or origin")
                notice = {**expected, "exit_code": event["exit_code"], "completion_reason": event["completion_reason"]}
                await jobs.update_one(key, {"$set": {"status": "completed", "notice": notice,
                                                      "completed_at": time.time()}})
                return
        finally:
            if process.poll() is None:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    await asyncio.to_thread(subprocess.run, ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                await asyncio.to_thread(process.wait)


async def main(mode, job_id, token):
    store = HarnessStore(os.environ["HARNESS_MONGO_URI"], os.environ["HARNESS_MONGO_DB"])
    try:
        await {"run": run, "watch": watch}[mode](store, {"_id": job_id, "token": token, "status": "running"})
    finally:
        store.client.close()


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))

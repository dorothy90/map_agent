import asyncio
import uuid
from types import SimpleNamespace

from harness.map_jobs import dispatch_map_job, supervise_map_jobs
from harness.store import HarnessStore
from harness.tools.load_wafer_maps import LoadWaferMapsInput


def context(store):
    return SimpleNamespace(store=store, run=dict(principal_id="one", session_id="chat", run_id="turn"),
                           invocation_id="call", settings=SimpleNamespace(call_timeout=90, timezone="Asia/Seoul"),
                           remaining_seconds=lambda: 90, protected_seconds=0)


ARGS = LoadWaferMapsInput(lot_cd="4SA", oper="PT1C TEST", all_time=True, aggregate={"group_by": "wafer"})


def test_fast_aggregate_returns_its_stored_observation_only_once():
    async def exercise():
        store = HarnessStore(database="fast_map_" + uuid.uuid4().hex)
        ctx = context(store)
        try:
            request = asyncio.create_task(dispatch_map_job(ARGS, ctx, 5))
            jobs = store.db.map_jobs
            while not (job := await jobs.find_one({})):
                await asyncio.sleep(.01)
            meta = dict(run_id="turn", invocation_id="call", tool_name="load_wafer_maps", summary="native")
            result = await store.put_result("one", "chat", meta, [{"count": 12}], [], result_key="map-job:" + job["_id"])
            await jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "completed", "result_id": result.result_id}})
            assert await request == result
            assert await dispatch_map_job(ARGS, ctx, 5) == result
            assert (await jobs.find_one({}))["delivery"] == "inline"
            assert await jobs.count_documents({}) == await store.results.count_documents({}) == 1
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(exercise())


def test_slow_aggregate_keeps_original_job_and_never_returns_second_inline_result():
    async def exercise():
        store = HarnessStore(database="slow_map_" + uuid.uuid4().hex)
        ctx = context(store)
        try:
            receipt = await dispatch_map_job(ARGS, ctx, .02)
            job_id = receipt.scope["background_map"]["job_id"]
            job = await store.db.map_jobs.find_one({"_id": job_id})
            assert job["delivery"] == "background" and job["status"] == "queued"
            await store.db.map_jobs.update_one({"_id": job_id}, {"$set": {"status": "completed", "delivery": "delivered"}})
            assert (await dispatch_map_job(ARGS, ctx, .02)).scope["background_map"] == {
                "job_id": job_id, "status": "delivered"}
            assert await store.db.map_jobs.count_documents({}) == 1
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(exercise())


def test_request_cancelled_before_detach_does_not_publish():
    async def exercise():
        store = HarnessStore(database="cancel_map_" + uuid.uuid4().hex)
        try:
            request = asyncio.create_task(dispatch_map_job(ARGS, context(store), 5))
            while not await store.db.map_jobs.find_one({}):
                await asyncio.sleep(.01)
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            job = await store.db.map_jobs.find_one({})
            assert job["status"] == "cancelled" and job["delivery"] == "waiting"
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(exercise())


def test_fixed_gateway_only_claims_its_own_jobs(monkeypatch):
    async def exercise():
        store = HarnessStore(database="map_gateway_" + uuid.uuid4().hex)
        launches = []
        def launch(cmd, **kwargs):
            launches.append(cmd)
            return SimpleNamespace(poll=lambda: None)
        monkeypatch.setattr("harness.map_jobs.subprocess.Popen", launch)
        settings = SimpleNamespace(parallel_tool_limit=2, mongo_uri="mongodb://localhost:27017")
        try:
            await store.db.map_jobs.insert_many([dict(_id=p, principal_id=p, status="queued", created_at=i)
                                                 for i, p in enumerate(["two", "one"])])
            supervisor = asyncio.create_task(supervise_map_jobs(store, settings, principal="one"))
            try:
                async with asyncio.timeout(3):
                    while not launches:
                        await asyncio.sleep(.03)
            finally:
                supervisor.cancel()
                await asyncio.gather(supervisor, return_exceptions=True)
            assert [argv[-2] for argv in launches] == ["one"]
            assert (await store.db.map_jobs.find_one({"_id": "two"}))["status"] == "queued"
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(exercise())


def test_importing_tool_registry_preserves_service_oracle_connection(tmp_path):
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import sys

    src = Path(__file__).resolve().parents[2]
    shutil.copyfile(src / "common.py", tmp_path / "common.py")
    (tmp_path / ".env").write_text("ORACLE_DSN=dotenv-db\nORACLE_USER=file-user\n")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(tmp_path), str(src)]), "ORACLE_DSN": "service-db"}
    env.pop("ORACLE_USER", None)
    env.pop("PYTHON_DOTENV_DISABLED", None)
    subprocess.run([sys.executable, "-c", """
import os
from harness.tools.registry import native_domain_registry
native_domain_registry()
import common
assert common.ORACLE_DSN == os.environ["ORACLE_DSN"] == "service-db"
assert common.ORACLE_USER == os.environ["ORACLE_USER"] == "file-user"
"""], cwd=tmp_path, env=env, check=True)


def test_map_worker_reuses_result_after_transient_read_error(monkeypatch):
    from harness.map_job_worker import run
    from harness.tools.registry import ToolResult

    async def exercise():
        store = HarnessStore(database="retry_map_" + uuid.uuid4().hex)
        calls = []
        async def read(args, ctx):
            calls.append(args)
            if len(calls) == 1:
                raise ConnectionError("temporary")
            return ToolResult(status="empty", summary="empty")

        monkeypatch.setattr("harness.tools.load_wafer_maps.load_wafer_maps", read)
        monkeypatch.setattr("harness.executor.retry_delay", lambda *_: 0)
        owner = dict(principal_id="one", session_id="chat")
        key = dict(_id="job", token="lease", status="running")
        try:
            await store.runs.insert_one(dict(_id="turn", run_id="turn", status="running", **owner))
            await store.db.gateway_sessions.insert_one(dict(id="chat", principal_id="one"))
            await store.calls.insert_one(dict(run_id="turn", invocation_id="call", tool_name="load_wafer_maps",
                                              arguments=ARGS.model_dump(mode="json")))
            await store.db.map_jobs.insert_one(dict(**key, **owner, run_id="turn", invocation_id="call", timezone=None,
                                                    arguments=ARGS.model_dump(mode="json", exclude_defaults=True)))
            await run(store, key)
            await run(store, key)
            result = await store.db.map_jobs.find_one(key)
            assert (await store.observation("one", "chat", result["result_id"])).status == "empty"
            assert calls == [ARGS, ARGS]
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(exercise())

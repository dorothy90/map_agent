from contextlib import asynccontextmanager
import asyncio
import os
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

from .config import LocalIsolationSettings, SeatbeltIsolationSettings, Settings
from .control import RunController
from .isolation.backend import prepare_isolated_run
from .map_jobs import supervise_map_jobs
from .runtime.process import ProcessRuntime
from .router import router
from .store import HarnessStore


@asynccontextmanager
async def lifespan(app):
    if os.getenv("HARNESS_LOAD_DOTENV", "true").lower() == "true":
        load_dotenv()
    settings = Settings.from_env()
    store = HarnessStore(settings.mongo_uri, settings.mongo_db)
    await store.setup()
    app.state.harness = RunController(store, settings)
    app.state.harness.web_map_jobs_enabled = True
    await app.state.harness.recover()
    app.state.harness.recovery_task = asyncio.create_task(app.state.harness.watch_recovery())
    map_jobs = asyncio.create_task(supervise_map_jobs(store, settings,
        principal=getattr(app.state, "map_job_principal", None)))
    try:
        yield
    finally:
        await app.state.harness.close()
        map_jobs.cancel()
        await asyncio.gather(map_jobs, return_exceptions=True)
    store.client.close()


app = FastAPI(title="Yield Harness", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok", "runtime": "harness/v2"}


@app.get("/health/isolation")
async def isolation_health(request: Request):
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(403, "local health check only")
    settings = request.app.state.harness.settings
    if not isinstance(settings.isolation, (LocalIsolationSettings, SeatbeltIsolationSettings)):
        raise HTTPException(404, "Local isolation is not enabled")
    run_id = "health-" + uuid.uuid4().hex
    principal_id = settings.principal
    evidence = await asyncio.to_thread(
        prepare_isolated_run, settings.isolation, run_id, principal_id,
    )
    result = await ProcessRuntime(
        isolation=settings.isolation, run_id=run_id, principal_id=principal_id,
    ).execute("print('health-worker-ok')", {}, timeout=60)
    if result.get("status") != "success":
        raise HTTPException(503, "isolated worker failed")
    return {
        "status": "ok", "backend": settings.isolation.backend, "verified_uid": evidence["verified_uid"],
        "sandbox": evidence["selected_sandbox"], "worker_stdout": result.get("stdout", ""),
    }

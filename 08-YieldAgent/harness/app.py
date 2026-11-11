from contextlib import asynccontextmanager
import asyncio
import os
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from langgraph.checkpoint.mongodb import MongoDBSaver

from .config import Settings
from .control import RunController
from .config import SeatbeltIsolationSettings
from .isolation.backend import prepare_isolated_run
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
    with MongoDBSaver.from_conn_string(settings.mongo_uri, db_name=settings.mongo_db,
        checkpoint_collection_name="harness_checkpoints", writes_collection_name="harness_checkpoint_writes") as checkpointer:
        app.state.harness = RunController(store, settings, checkpointer)
        await app.state.harness.recover()
        app.state.harness.recovery_task = asyncio.create_task(app.state.harness.watch_recovery())
        try:
            yield
        finally:
            await app.state.harness.close()
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
    if not isinstance(settings.isolation, SeatbeltIsolationSettings):
        raise HTTPException(404, "Seatbelt isolation is not enabled")
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
        "status": "ok", "backend": "seatbelt", "verified_uid": evidence["verified_uid"],
        "sandbox": evidence["selected_sandbox"], "worker_stdout": result.get("stdout", ""),
    }

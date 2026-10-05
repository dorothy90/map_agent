from contextlib import asynccontextmanager
import asyncio

from dotenv import load_dotenv
from fastapi import FastAPI
from langgraph.checkpoint.mongodb import MongoDBSaver

from .config import Settings
from .control import RunController
from .router import router
from .store import HarnessStore


@asynccontextmanager
async def lifespan(app):
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

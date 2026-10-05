"""Real app/UI and free model; all domain tools disabled for model-only UI smoke."""
import uuid
import os
from contextlib import asynccontextmanager
from pathlib import Path

os.environ["HARNESS_MONGO_DB"] = "harness_ui_eval_" + uuid.uuid4().hex
from agent_server import app, lifespan as original_lifespan
from fastapi.staticfiles import StaticFiles
from harness.tools.registry import ToolRegistry


@asynccontextmanager
async def lifespan(application):
    async with original_lifespan(application):
        application.state.harness.registry_factory = ToolRegistry
        application.state.harness.settings.enabled = True
        yield


app.router.lifespan_context = lifespan
app.mount("/", StaticFiles(directory=str(Path(__file__).resolve().parents[2] / "yield_frontend" / "dist"), html=True), name="ui-smoke")

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Callable

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from identity import PlatformIdentity, get_platform_identity
from settings import Settings, get_settings


router = APIRouter(prefix="/health", tags=["health"])
PROBE_TIMEOUT_SECONDS = 1.0


def _nas_readable(root: Path) -> bool:
    if not root.is_dir():
        return False
    with os.scandir(root) as entries:
        next(entries, None)
    return True


def _nas_writable(root: Path) -> bool:
    if not _nas_readable(root):
        return False
    handle, name = tempfile.mkstemp(prefix=".health-", dir=root)
    os.close(handle)
    Path(name).unlink()
    return True


async def _bounded(probe) -> str:
    try:
        result = await asyncio.wait_for(probe, timeout=PROBE_TIMEOUT_SECONDS)
        return "ok" if result is not False else "failed"
    except Exception:
        return "failed"


async def _api_components(request: Request) -> dict[str, str]:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    redis, mongo, nas = await asyncio.gather(
        _bounded(request.app.state.redis.ping()),
        _bounded(request.app.state.motor_db.command("ping")),
        _bounded(asyncio.to_thread(_nas_readable, settings.artifact_root)),
    )
    return {"redis": redis, "mongo": mongo, "nas": nas}


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    components = await _api_components(request)
    ready_now = all(value == "ok" for value in components.values())
    body = {
        "status": "ready" if ready_now else "not_ready",
        "components": components,
    }
    return JSONResponse(body, status_code=200 if ready_now else 503)


@router.get("/dependencies")
async def dependencies(
    request: Request,
    _identity: PlatformIdentity = Depends(get_platform_identity),
):
    """Sanitized API dependencies; worker-only services are never called here."""
    components = await _api_components(request)
    components.update({"oracle": "worker_only", "llm": "worker_only"})
    return {"components": components}


def _probe_oracle(settings: Settings) -> bool:
    from common import _get_oracle_pool

    connection = _get_oracle_pool(settings).acquire()
    try:
        connection.ping()
    finally:
        connection.close()
    return True


def _probe_llm() -> bool:
    base_url = os.getenv("OPENROUTER_BASE_URL", "").rstrip("/")
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not base_url or not api_key:
        return False
    response = httpx.get(
        f"{base_url}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=PROBE_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return True


def probe_worker_dependencies(
    settings: Settings | None = None,
    *,
    llm_probe: Callable[[], bool] = _probe_llm,
) -> dict[str, str]:
    """Worker-only startup/diagnostic probe; never invoked by the API router."""
    settings = settings or get_settings()
    probes = {
        "nas": lambda: _nas_writable(settings.artifact_root),
        "oracle": lambda: _probe_oracle(settings),
        "llm": llm_probe,
    }
    result: dict[str, str] = {}
    for name, probe in probes.items():
        try:
            result[name] = "ok" if probe() else "failed"
        except Exception:
            result[name] = "failed"
    return result

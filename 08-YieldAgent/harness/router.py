from __future__ import annotations

import asyncio
import base64
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import Field, StrictBool

from .store import Conflict
from .types import Contract

router = APIRouter()


class StartRequest(Contract):
    session_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()), max_length=128)
    query: str = Field(min_length=1, max_length=20000)


class InputRequest(Contract):
    kind: Literal["input", "steer"] = "input"
    request_id: str = Field(min_length=1, max_length=128)
    goal_revision: int = Field(ge=1)
    interrupt_id: str | None = None
    value: str | dict[str, Any]


def controller(request):
    if not hasattr(request.app.state, "harness"):
        raise HTTPException(503, "Harness unavailable")
    return request.app.state.harness


def principal(request):
    # A verified auth middleware may set this. No identity is taken from the body.
    return getattr(request.state, "principal_id", None) or controller(request).settings.principal


def public_run(run):
    return {key: run.get(key) for key in ("run_id", "session_id", "query", "status", "answer", "stop_reason", "goal_revision", "usage", "question", "runtime_version", "observations")}


def input_text(value):
    if isinstance(value, dict):
        return "\n".join(f"{key}: {item}" for key, item in value.items())
    return str(value)


async def artifact_payload(request, session_id, artifact_id):
    ref, data = await controller(request).store.artifact(principal(request), session_id, artifact_id)
    mime = ref["mime"]
    kind = "html" if mime == "text/html" else "image" if mime.startswith("image/") else "pptx" if "presentation" in mime else "markdown"
    if mime == "application/octet-stream":
        kind = "file"
    if kind in ("html", "markdown"):
        content = data.decode()
        if mime == "text/plain":
            content = "```python\n" + content + "\n```"
    elif kind == "image":
        content = "data:" + mime + ";base64," + base64.b64encode(data).decode()
    else:
        from urllib.parse import urlencode
        content = f"/harness/artifacts/{ref['artifact_id']}?" + urlencode({"session_id": session_id})
    return {"artifact_id": ref["artifact_id"], "artifact_type": kind, "mime": mime, "title": ref["title"], "agent": "harness", "data": content}


async def table_payloads(request, session_id, result_id):
    observation = await controller(request).store.observation(principal(request), session_id, result_id)
    return [{"artifact_id": f"{result_id}:{table.table_id}", "artifact_type": "table", "mime": "application/json",
        "title": table.title, "agent": observation.tool_name,
        "data": json.dumps({"result_id": result_id, "session_id": session_id, **table.model_dump(exclude={"data_ref"})}, ensure_ascii=False)}
        for table in observation.tables if table.columns or table.total_rows or table.missing_reason]


async def owned_run(request, run_id):
    try:
        return await controller(request).store.get_run(principal(request), run_id)
    except PermissionError:
        raise HTTPException(404, "Run not found") from None


@router.post("/runs")
async def start_run(body: StartRequest, request: Request):
    try:
        run = await controller(request).start(principal(request), body.session_id, body.request_id, body.query)
        return public_run(run)
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    return public_run(await owned_run(request, run_id))


@router.get("/harness/products")
def product_catalog():
    from oracledb import Error as OracleError
    from .tools.yield_report_data import fetch_product_codes

    try:
        return {"products": fetch_product_codes()}
    except OracleError:
        raise HTTPException(503, "Product catalog unavailable") from None


class SkillToggle(Contract):
    enabled: StrictBool


def profile_skills(request, name=None, enabled=None):
    from .native_hermes import profile_path, profile_write_lock

    user = getattr(request.state, 'principal_id', None)
    if not user:
        raise HTTPException(401, 'Authenticated profile required')
    profile = profile_path(user)
    if profile.is_symlink():
        raise HTTPException(503, 'Skill profile unavailable')
    if not (profile / 'skills').is_dir():
        if name is not None:
            raise HTTPException(404, 'Skill not found')
        return {'skills': []}
    command = [sys.executable, '-I', str(Path(__file__).with_name('skill_catalog_worker.py'))]
    if name is not None:
        command.extend((name, json.dumps(enabled)))
    env = {'HERMES_HOME': str(profile), 'HOME': str(profile), 'PATH': os.defpath}
    try:
        with profile_write_lock(profile) if name is not None else nullcontext():
            result = subprocess.run(command, cwd=profile, env=env, capture_output=True, check=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        raise HTTPException(503, 'Skill profile unavailable') from None
    response = json.loads(result.stdout)
    if response.get('error') == 404:
        raise HTTPException(404, 'Skill not found')
    if response.get('error') == 409:
        raise HTTPException(409, 'This skill has a shared or required activation setting')
    return response


@router.get('/harness/skills')
def skill_catalog(request: Request):
    return profile_skills(request)


@router.put('/harness/skills/{name:path}/enabled')
def toggle_profile_skill(name: str, body: SkillToggle, request: Request):
    return profile_skills(request, name, body.enabled)


def profile_mcp(request, action, name=None, body=None):
    from . import native_hermes
    from .web_connections import guard_mcp_profile

    user = getattr(request.state, 'principal_id', None)
    if not user:
        raise HTTPException(401, '인증된 프로필이 필요해요.')
    profile = native_hermes.profile_path(user)
    mutation = action in ('add', 'edit', 'delete', 'enabled')
    try:
        guard_mcp_profile(profile)
        if mutation:
            profile = native_hermes.prepare_profile(user)
        elif not profile.exists():
            if action == 'list':
                return {'servers': []}
            if body is None:
                raise HTTPException(404, '서버를 찾을 수 없어요.')
            profile = native_hermes.prepare_profile(user)
        env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR')
               if k in os.environ}  # no DB credentials into user MCP commands
        env.update(HOME=str(profile), HERMES_HOME=str(profile), HERMES_DISABLE_LAZY_INSTALLS='1',
                   PYTHON_DOTENV_DISABLED='1', HERMES_WRITE_SAFE_ROOT=str(profile))
        env.setdefault('PATH', os.defpath)
        command = [sys.executable, '-I', str(Path(__file__).with_name('mcp_profile_worker.py')),
                   action, json.dumps({'name': name, 'body': body})]
        with native_hermes.profile_write_lock(profile) if mutation else nullcontext():
            result = subprocess.run(command, cwd=profile, env=env, capture_output=True,
                                    check=True, timeout=60 if action == 'test' else 15)
        response = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        if action == 'test':
            return {'ok': False, 'error': '연결 테스트 시간이 초과됐어요.', 'tools': [], 'elapsed_ms': 60000}
        raise HTTPException(503, 'MCP 프로필 응답 시간이 초과됐어요.') from None
    except (OSError, ValueError, subprocess.SubprocessError):
        raise HTTPException(503, 'MCP 프로필을 불러오지 못했어요.') from None
    if isinstance(response.get('error'), int):
        raise HTTPException(response['error'], response['detail'])
    return response


@router.get('/harness/mcp/servers')
def mcp_servers(request: Request):
    return profile_mcp(request, 'list')


@router.post('/harness/mcp/servers')
def add_mcp_server(body: dict, request: Request):
    return profile_mcp(request, 'add', body.get('name'), body)


@router.put('/harness/mcp/servers/{name}')
def edit_mcp_server(name: str, body: dict, request: Request):
    return profile_mcp(request, 'edit', name, body)


@router.delete('/harness/mcp/servers/{name}')
def delete_mcp_server(name: str, request: Request):
    return profile_mcp(request, 'delete', name)


@router.put('/harness/mcp/servers/{name}/enabled')
def toggle_mcp_server(name: str, body: SkillToggle, request: Request):
    return profile_mcp(request, 'enabled', name, body.model_dump())


@router.post('/harness/mcp/servers/{name}/test')
def test_mcp_server(name: str, request: Request, body: dict | None = None):
    return profile_mcp(request, 'test', name, body)


@router.get("/harness/sessions/{session_id}")
async def session_runs(session_id: str, request: Request, limit: int = Query(50, ge=1, le=100), cursor: str | None = None):
    service = controller(request)
    query = {"principal_id": principal(request), "session_id": session_id}
    if cursor:
        try:
            created, run_id = json.loads(base64.urlsafe_b64decode(cursor).decode())
            datetime.fromisoformat(created)
            if not isinstance(run_id, str):
                raise ValueError("Invalid run ID")
            query["$or"] = [{"created_at": {"$lt": created}}, {"created_at": created, "run_id": {"$lt": run_id}}]
        except (ValueError, TypeError, UnicodeError):
            raise HTTPException(422, "Invalid cursor") from None
    runs = await service.store.runs.find(query).sort([("created_at", -1), ("run_id", -1)]).to_list(length=limit + 1)
    page = runs[:limit]
    next_cursor = base64.urlsafe_b64encode(json.dumps([page[-1]["created_at"], page[-1]["run_id"]]).encode()).decode() if len(runs) > limit else None
    return {"session_id": session_id, "runs": [public_run(run) for run in page], "next_cursor": next_cursor}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request):
    await owned_run(request, run_id)
    return public_run(await controller(request).cancel(principal(request), run_id))


@router.post("/runs/{run_id}/input")
async def input_run(run_id: str, body: InputRequest, request: Request):
    await owned_run(request, run_id)
    try:
        return public_run(await controller(request).submit_input(principal(request), run_id, body.model_dump()))
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None


async def events(request, run_id, after=0):
    while True:
        run = await owned_run(request, run_id)
        for event in run["events"]:
            if event["sequence"] > after:
                after = event["sequence"]
                yield event
        if not run["active"] or run["status"] == "waiting_user":
            break
        if await request.is_disconnected():
            break
        await asyncio.sleep(.2)


@router.get("/runs/{run_id}/events")
async def stream_events(run_id: str, request: Request, after: int = 0):
    await owned_run(request, run_id)
    async def stream():
        async for event in events(request, run_id, after):
            yield "id: " + str(event["sequence"]) + "\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/harness/results/{result_id}")
async def read_result(result_id: str, session_id: str, request: Request, offset: int = 0, limit: int = 50, table_id: str | None = None):
    try:
        return await controller(request).store.read_result(principal(request), session_id, result_id, offset=offset, limit=limit, table_id=table_id)
    except PermissionError:
        raise HTTPException(404, "Result not found") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/harness/artifacts/{artifact_id}/matrix-panels")
async def matrix_panels(artifact_id: str, session_id: str, request: Request, offset: int, length: int):
    try:
        data = await controller(request).store.matrix_panel_range(principal(request), session_id, artifact_id, offset, length)
    except PermissionError:
        raise HTTPException(404, "Matrix not found") from None
    except ValueError:
        raise HTTPException(416, "Invalid matrix panel range") from None
    return StreamingResponse(data, media_type="application/octet-stream",
                             headers={"Content-Length": str(length), "Cache-Control": "private, no-store"})


@router.api_route("/harness/artifacts/{artifact_id}", methods=["GET", "HEAD"])
async def artifact(artifact_id: str, session_id: str, request: Request):
    try:
        ref, data = await controller(request).store.artifact(principal(request), session_id, artifact_id)
        is_html = ref["mime"].split(";", 1)[0].strip().lower() == "text/html"
        headers = {
            "Content-Disposition": "inline" if is_html else "attachment",
            "X-Content-Type-Options": "nosniff",
        }
        if ref["mime"] == "application/octet-stream":
            headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(ref["title"], safe="")
        if is_html:
            headers["Content-Security-Policy"] = (
                "sandbox allow-scripts; frame-ancestors 'self'; object-src 'none'; "
                "base-uri 'none'; form-action 'none'"
            )
        return Response(data, media_type=ref["mime"], headers=headers)
    except PermissionError:
        raise HTTPException(404, "Artifact not found") from None


async def compatibility_chat(body, request):
    service = controller(request)
    after = body.after_sequence
    if body.run_id and body.resume_value is None:
        run = await owned_run(request, body.run_id)
        if run["session_id"] != body.session_id:
            raise HTTPException(404, "Run not found")
    elif body.resume_value is not None:
        active = await service.store.runs.find_one({"principal_id": principal(request), "session_id": body.session_id, "active": True})
        if not active:
            raise HTTPException(409, "No pending input")
        after = len(active["events"])
        try:
            run = await service.submit_input(principal(request), active["run_id"], {"kind": "input", "request_id": body.request_id or str(uuid.uuid4()), "goal_revision": body.goal_revision or active["goal_revision"], "interrupt_id": body.interrupt_id or active.get("question", {}).get("interrupt_id"), "value": body.resume_value})
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
    else:
        try:
            run = await service.start(principal(request), body.session_id, getattr(body, "request_id", None) or str(uuid.uuid4()), body.query)
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
    async def stream():
        yield "data: " + json.dumps({"type": "stream_start", "session_id": body.session_id, "query": body.query, "run_id": run["run_id"], "goal_revision": run["goal_revision"], "after_sequence": after}, ensure_ascii=False) + "\n\n"
        async for event in events(request, run["run_id"], after):
            payload, kind = event["payload"], event["type"]
            outgoing = []
            if kind in ("progress", "tool_started", "tool_finished"):
                message = payload.get("summary") or payload.get("message")
                if kind == "tool_started":
                    message = "도구를 실행하고 있습니다."
                if message:
                    outgoing.append({"type": "status", "node": payload.get("name", "harness"), "message": message,
                        "invocation_id": payload.get("invocation_id"), "parent_invocation_id": payload.get("parent_invocation_id"),
                        "state": payload.get("state", payload.get("status", "running")), "elapsed": payload.get("elapsed_seconds", 0)})
                if kind == "tool_finished" and payload.get("result_id"):
                    outgoing.extend({"type": "artifact", **table, "step": event["sequence"]}
                        for table in await table_payloads(request, body.session_id, payload["result_id"]))
            elif kind == "commentary":
                outgoing.append({"type": "commentary", "agent": "harness", "content": payload["content"]})
            elif kind == "artifact":
                outgoing.append({"type": "artifact", **await artifact_payload(request, body.session_id, payload["artifact_id"]), "step": event["sequence"]})
            elif kind == "input_required":
                current = await owned_run(request, run["run_id"])
                if current["status"] == "waiting_user" and current.get("question", {}).get("interrupt_id") == payload.get("interrupt_id"):
                    outgoing.append({"type": "interrupt", "interrupt_type": "missing_param", "param": (payload.get("fields") or [{}])[0].get("slot", ""), "route": "harness", "options": [], **payload})
                else:
                    outgoing.append({"type": "message", "role": "assistant", "agent": "harness", "content": payload.get("message", payload.get("answer", "")), "step": event["sequence"]})
            elif kind == "input_received":
                outgoing.append({"type": "user_input", "content": input_text(payload.get("value", "")), "request_id": payload.get("request_id")})
            elif kind == "run_finished":
                outgoing.extend([{"type": "message", "role": "assistant", "agent": "harness", "content": payload.get("answer", ""), "step": event["sequence"]},
                    {"type": "stream_end", "total_steps": event["sequence"], "elapsed": 0, "status": payload["status"], "reason": payload["reason"]}])
            for index, item in enumerate(outgoing):
                item.update(event_id=event["event_id"] + ":" + str(index), sequence=event["sequence"], run_id=run["run_id"])
                yield "data: " + json.dumps(item, ensure_ascii=False) + "\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream")

"""Hermes web download transport, scoped to immutable Y-Agent output references."""
import asyncio
import secrets
import time
from urllib.parse import quote

from bson import ObjectId
from fastapi import HTTPException
from gridfs.errors import NoFile
from starlette.responses import JSONResponse, Response, StreamingResponse

from .session_store import MongoSessionStore


class DownloadTickets:
    """Loop-local tickets with Hermes' 60-second lifetime and two download retries."""

    def __init__(self):
        self.entries = {}

    def mint(self, scope):
        now = time.monotonic()
        self.entries = {k: v for k, v in self.entries.items() if v[1] > now}
        if len(self.entries) >= 1024:
            del self.entries[next(iter(self.entries))]
        ticket = secrets.token_urlsafe(32)
        self.entries[ticket] = (scope, now + 60, 3)
        return ticket

    def consume(self, ticket, scope):
        entry = self.entries.get(ticket)
        if not entry or entry[0] != scope or entry[1] <= time.monotonic():
            raise HTTPException(403, "Invalid download ticket")
        if entry[2] == 1:
            del self.entries[ticket]
        else:
            self.entries[ticket] = (*entry[:2], entry[2] - 1)


async def serve(services, request, principal):
    issue = request.method == "POST"
    if issue:
        try:
            query = await request.json()
        except ValueError:
            raise HTTPException(422, "Invalid ticket request") from None
        if not isinstance(query, dict) or query.pop("route", None) != "download":
            raise HTTPException(422, "Expected a download request")
    else:
        query = dict(request.query_params)
        if len(query) != len(request.query_params.multi_items()):
            raise HTTPException(422, "Ambiguous download query")
    ticket = None if issue else query.pop("ticket", None)
    if (query.keys() - {"path", "session_id", "profile"} or
            not all(isinstance(v, str) for v in query.values())):
        raise HTTPException(422, "Invalid download scope")
    sid = query.get("session_id")
    profile = services.profile_id_for(principal)
    path = query.get("path", "")
    if not sid or query.get("profile", profile) != profile or not path.startswith("/outputs/"):
        raise HTTPException(404, "Output unavailable")
    endpoint = request.url.path.rsplit("/api/", 1)[0] + "/api/fs/download"
    scope = (principal.user_id, profile, endpoint, tuple(sorted(query.items())))
    local = getattr(request.state, "file_ticket_required", False)
    if ticket is not None or (local and request.method == "GET"):
        services.download_tickets.consume(ticket, scope)

    store = services.store
    sessions = MongoSessionStore(store, principal.user_id)
    if not await sessions.get_session(sid):
        raise HTTPException(404, "Output unavailable")
    parts = path.split("/")
    if len(parts) != 5:
        raise HTTPException(404, "Output unavailable")
    _, _, run_id, result_id, _ = parts
    try:
        observation = await store.observation(principal.user_id, sid, result_id)
    except PermissionError:
        raise HTTPException(404, "Output unavailable") from None
    ref = next((r for r in observation.artifact_refs if r.get("output_path") == path), None)
    if observation.run_id != run_id or ref is None:
        raise HTTPException(404, "Output unavailable")
    try:
        blob = await store.blobs.open_download_stream(ObjectId(ref["artifact_id"]))
    except NoFile:
        raise HTTPException(404, "Output unavailable") from None
    title, mime, length = ref['title'], ref.get('mime'), blob.length
    if issue or request.method == "HEAD":
        await asyncio.to_thread(blob.close)
    if issue:
        return JSONResponse({"ticket": services.download_tickets.mint(scope)}, headers={"Cache-Control": "no-store"})
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(title, safe='')}",
               "Content-Length": str(length), "Cache-Control": "private, no-store",
               "X-Content-Type-Options": "nosniff"}
    mime = mime or "application/octet-stream"
    if request.method == "HEAD":
        if local:
            headers["X-Hermes-File-Ticket"] = "required"
        return Response(headers=headers, media_type=mime)

    async def chunks():
        try:
            while chunk := await blob.readchunk():
                yield chunk
        finally:
            await asyncio.to_thread(blob.close)

    return StreamingResponse(chunks(), headers=headers, media_type=mime)

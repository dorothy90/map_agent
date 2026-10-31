"""Manifest-only transport boundary. No engine, store, discovery or RPC dispatch.

The manifest is read once at router construction. Request handling only performs
injected authentication checks and returns rejection frames. `implemented` in
PR-1 means a future target, never permission to execute an upstream handler.
"""
import json
from pathlib import Path

from fastapi import APIRouter, Request, WebSocket
from starlette.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from .auth import (
    AuthenticationFailure, DenyOriginPolicy, DenyPrincipalResolver,
    DenyTicketVerifier, OriginPolicy, Principal, PrincipalResolver, TicketVerifier,
)
from .policy import UPSTREAM
from .unsupported import http_rejection, rpc_rejection, validate_mode


def _route_order(row):
    # Segment-by-segment specificity: literal > mixed (@{profile}) > parameter
    # > greedy :path. In particular, /sessions/search precedes /sessions/{id}.
    return tuple((3 if ':path}' in segment else 2 if segment.startswith('{')
                  else 1 if '{' in segment else 0, segment)
                 for segment in row['path'].split('/'))


def _unconnected(feature):
    return {'code': 'handler_not_connected', 'reason': 'handler_not_connected',
            'feature': feature, 'upstream': UPSTREAM}


def _rpc_error(request_id, code, message):
    return {'jsonrpc': '2.0', 'id': request_id,
            'error': {'code': code, 'message': message}}


def build_gateway_router(
    resolver: PrincipalResolver | None = None, *,
    origin_policy: OriginPolicy | None = None,
    ticket_verifier: TicketVerifier | None = None,
    rpc_mode: str = 'legacy',
    services=None,
) -> APIRouter:
    """Construct inert routes; RPC mode is server-selected, never client fallback."""
    validate_mode(rpc_mode)
    resolver = resolver if resolver is not None else DenyPrincipalResolver()
    origin_policy = origin_policy if origin_policy is not None else DenyOriginPolicy()
    ticket_verifier = ticket_verifier if ticket_verifier is not None else DenyTicketVerifier()
    manifest = json.loads(Path(__file__).with_name('manifest.json').read_text())
    router = APIRouter()

    async def authenticate(connection):
        principal = await resolver.resolve(connection)
        if not isinstance(principal, Principal):
            raise AuthenticationFailure(401)
        return principal

    def http_endpoint(row):
        async def endpoint(request: Request):
            try:
                if services is not None and row['classification'] == 'implemented':
                    principal = await authenticate(request)
                    return await services.serve_http(request, principal, row)
                await authenticate(request)
            except AuthenticationFailure as exc:
                return JSONResponse({'error': {'code': exc.code}}, status_code=exc.status_code)
            rejection = http_rejection(manifest, row['method'], row['path'])
            if rejection is None:
                return JSONResponse({'error': _unconnected(row['path'])}, status_code=503)
            status, body = rejection
            return JSONResponse(body, status_code=status)
        return endpoint

    def ws_endpoint(row):
        async def endpoint(websocket: WebSocket):
            try:
                principal = await authenticate(websocket)
                if row['path'] != '/api/ws' or row['classification'] != 'implemented':
                    raise AuthenticationFailure(403)
                if not await origin_policy.allows(websocket, principal):
                    raise AuthenticationFailure(403)
                if not await ticket_verifier.verify_and_consume(websocket, principal):
                    raise AuthenticationFailure(401)
            except AuthenticationFailure:
                # ASGI websocket.close before accept rejects the upgrade (HTTP
                # 403 on conforming servers). No denial-response extension is
                # needed. TestClient exposes close 1008 instead of HTTP status.
                await websocket.close(code=1008)
                return

            await websocket.accept()
            if services is not None:
                await services.serve_ws(websocket, principal, manifest, rpc_mode)
                return
            try:
                while True:
                    try:
                        request = await websocket.receive_json()
                    except (ValueError, UnicodeDecodeError):
                        await websocket.send_json(_rpc_error(None, -32700, 'Parse error'))
                        continue
                    if (not isinstance(request, dict) or request.get('jsonrpc') != '2.0'
                            or not isinstance(request.get('method'), str)
                            or ('id' in request and (isinstance(request['id'], (bool, dict, list))))):
                        await websocket.send_json(_rpc_error(None, -32600, 'Invalid Request'))
                        continue
                    if 'id' not in request:
                        continue  # JSON-RPC notifications never receive a response.
                    frame = rpc_rejection(manifest, request['method'], request['id'], mode=rpc_mode)
                    if frame is None:
                        frame = _rpc_error(request['id'], -32005, 'handler_not_connected')
                        frame['error']['data'] = _unconnected(request['method'])
                    await websocket.send_json(frame)
            except WebSocketDisconnect:
                return
        return endpoint

    seen = set()
    for row in sorted(manifest['routes'] + manifest['external_routes'], key=_route_order):
        identity = (row['method'], row['path'])
        if identity in seen:
            raise ValueError(f'Duplicate manifest route: {identity}')
        seen.add(identity)
        if row['method'] == 'WEBSOCKET':
            router.add_api_websocket_route(row['path'], ws_endpoint(row), name=row['name'])
        else:
            router.add_api_route(row['path'], http_endpoint(row), methods=[row['method']],
                                 name=row['name'], include_in_schema=False)
    return router

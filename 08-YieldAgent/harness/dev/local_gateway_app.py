"""Loopback-only browser gateway for the local harness controller.

Login is handled upstream by the company platform. This local gateway does no
login of its own: every local request runs as the fixed HARNESS_PRINCIPAL user,
which keeps per-user folders and isolation working.
"""
import logging
import re
import os
from pathlib import Path
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

# A worktree has no .env ancestor, so common.py's load_dotenv() walk can miss
# the main checkout file. Fill missing process variables from that existing
# dotenv without overriding launch.sh HARNESS_* values already in the
# environment. Do not log the path or contents.
from harness.dev.dotenv_load import load_existing_dotenv
load_existing_dotenv(PROJECT, override=False)

from harness.gateway.auth import AuthenticationFailure, Principal
from harness.gateway.routes import build_gateway_router
from harness.gateway.services import GatewayServices

WEB_HOST = f"127.0.0.1:{int(os.getenv('HARNESS_WEB_PORT', '61313'))}"
GATEWAY_HOST = f"127.0.0.1:{int(os.getenv('HARNESS_GATEWAY_PORT', '61302'))}"
ORIGIN = f'http://{WEB_HOST}'
HOSTS = frozenset((WEB_HOST, GATEWAY_HOST))


class TicketLogRedaction(logging.Filter):
    """Remove handshake ticket query values before Uvicorn formats or propagates a record."""
    ticket_query = re.compile(r"([?&]ticket=)[^&\s\"'#<>]+", re.IGNORECASE)

    def filter(self, record):
        message = record.getMessage()
        redacted = self.ticket_query.sub(r"\1[REDACTED]", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def install_ticket_log_redaction():
    for name in ('uvicorn', 'uvicorn.error', 'uvicorn.access'):
        logger = logging.getLogger(name)
        if not any(isinstance(item, TicketLogRedaction) for item in logger.filters):
            logger.addFilter(TicketLogRedaction())


install_ticket_log_redaction()


def local_peer(connection):
    return (connection.client is not None
            and connection.client.host in ('127.0.0.1', '::1')
            and connection.headers.get('host') in HOSTS)


class FixedLocalPrincipal:
    """PrincipalResolver for local use: every local connection is the configured user."""

    async def resolve(self, connection):
        if not local_peer(connection):
            raise AuthenticationFailure(403)
        connection.state.file_ticket_required = True
        return Principal(connection.app.state.principal_user)

    async def allows(self, websocket, principal):
        return websocket.headers.get('origin') == ORIGIN

    async def verify_and_consume(self, websocket, principal):
        # No login session to bind a ticket to; connecting is enough.
        return True


class BoundServices:
    async def serve_ws(self, websocket, principal, manifest, mode):
        return await websocket.app.state.gateway.serve_ws(websocket, principal, manifest, mode)

    async def serve_http(self, request, principal, row):
        return await request.app.state.gateway.serve_http(request, principal, row)


def create_app(*, base_app=None, user=None, services_factory=None):
    production = base_app is None
    if production:
        from harness.app import app as base_app
    services_factory = services_factory or (lambda ctrl: GatewayServices(ctrl.store, ctrl))

    @asynccontextmanager
    async def lifespan(app):
        async with base_app.router.lifespan_context(base_app):
            principal_user = user or os.environ.get('HARNESS_PRINCIPAL')
            if not principal_user or not principal_user.strip():
                raise RuntimeError('HARNESS_PRINCIPAL must be configured')
            app.state.principal_user = principal_user
            ctrl = getattr(base_app.state, 'harness', None)
            if production:
                from harness.config import LocalIsolationSettings, SeatbeltIsolationSettings
                if (ctrl is None or not ctrl.settings.no_yield_tools
                        or not isinstance(ctrl.settings.isolation, (LocalIsolationSettings, SeatbeltIsolationSettings))
                        or ctrl.settings.isolation.sandbox != 'required'
                        or ctrl.settings.principal != principal_user):
                    raise RuntimeError('Local gateway requires local isolation and the configured principal')
            app.state.harness = ctrl
            app.state.gateway = services_factory(ctrl)
            yield

    app = FastAPI(title='Yield local gateway', lifespan=lifespan, docs_url=None, redoc_url=None)
    resolver = FixedLocalPrincipal()

    @app.middleware('http')
    async def local_only(request, call_next):
        if not local_peer(request):
            return JSONResponse({'error': 'Local host only'}, status_code=403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get('origin') != ORIGIN:
            return JSONResponse({'error': 'Origin rejected'}, status_code=403)
        request.state.principal_id = request.app.state.principal_user
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        return response

    @app.post('/api/auth/ws-ticket')
    async def ws_ticket():
        # Kept only because the running 61313 proxy script still calls it before dialing.
        # The value is not checked; WebSocket connections need no ticket.
        return {'ticket': 'local', 'expires_in': 30}

    app.router.routes.extend(base_app.router.routes)
    services = BoundServices()
    app.include_router(build_gateway_router(resolver, origin_policy=resolver,
        ticket_verifier=resolver, rpc_mode='authority', services=services), prefix='/gateway-m1')
    app.include_router(build_gateway_router(resolver, origin_policy=resolver,
        ticket_verifier=resolver, rpc_mode='authority', services=services))
    return app


app = create_app()

"""Browser gateway for local harness development with LAN access.

Login is handled upstream by the company platform. This local gateway does no
login of its own: every request runs as the fixed HARNESS_PRINCIPAL user,
which keeps per-user folders and isolation working.
"""
import logging
import re
import os
from pathlib import Path
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

# A worktree has no .env ancestor, so common.py's load_dotenv() walk can miss
# the main checkout file. Fill missing process variables from that existing
# dotenv without overriding launch.sh HARNESS_* values already in the
# environment. Do not log the path or contents.
from harness.dev.dotenv_load import load_existing_dotenv
load_existing_dotenv(PROJECT, override=False)

from harness.gateway.auth import Principal
from harness.gateway.routes import build_gateway_router
from harness.gateway.services import GatewayServices
from harness.web_cron_dispatch import gateway_schedule_lifespan


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


class FixedLocalPrincipal:
    """PrincipalResolver for local use: every connection is the configured user."""

    async def resolve(self, connection):
        connection.state.file_ticket_required = True
        return Principal(connection.app.state.principal_user)

    async def allows(self, websocket, principal):
        return True

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
        principal_user = user or os.environ.get('HARNESS_PRINCIPAL')
        if not principal_user or not principal_user.strip():
            raise RuntimeError('HARNESS_PRINCIPAL must be configured')
        if production:
            base_app.state.map_job_principal = principal_user
        async with base_app.router.lifespan_context(base_app):
            app.state.principal_user = principal_user
            ctrl = getattr(base_app.state, 'harness', None)
            if production:
                from harness.config import LocalIsolationSettings, SeatbeltIsolationSettings, WindowsIsolationSettings
                if (ctrl is None or not ctrl.settings.no_yield_tools
                        or not isinstance(ctrl.settings.isolation, (
                            LocalIsolationSettings, SeatbeltIsolationSettings, WindowsIsolationSettings))
                        or ctrl.settings.isolation.sandbox != 'required'
                        or ctrl.settings.principal != principal_user):
                    raise RuntimeError('Local gateway requires local isolation and the configured principal')
            app.state.harness = ctrl
            app.state.gateway = services_factory(ctrl)
            yield

    app = FastAPI(title='Yield local gateway', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])
    resolver = FixedLocalPrincipal()

    @app.middleware('http')
    async def local_principal(request, call_next):
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
    def schedules(app):
        return gateway_schedule_lifespan(app, principal=app.state.principal_user)

    app.include_router(build_gateway_router(resolver, origin_policy=resolver,
        ticket_verifier=resolver, rpc_mode='authority', services=services,
        schedule_lifespan=schedules if production and sys.platform != 'win32' else None), prefix='/gateway-m1')
    app.include_router(build_gateway_router(resolver, origin_policy=resolver,
        ticket_verifier=resolver, rpc_mode='authority', services=services))
    return app


app = create_app()

"""Host injection for WS and future REST; no credentials, model or tool creation.

Mongo ownership: no upstream equivalent → PB:575,664. Construct on the app loop
(or in lifespan), including HarnessStore/Motor; never share a client across loops.
"""
import asyncio
from dataclasses import dataclass

from .execution import Execution
from .session_store import MongoSessionStore


def default_profile_id(principal):
    # H/hermes_cli/profiles.py:249-250: the built-in profile is named "default".
    # Company mapping is unspecified; this is a server-owned, injectable name.
    return 'default'


@dataclass
class GatewayScope:
    sessions: MongoSessionStore
    execution: Execution


class GatewayServices:
    def __init__(self, store, controller, *, profile_id_for=default_profile_id,
                 poll_interval=0.1, max_requests=32):
        if poll_interval <= 0 or max_requests < 1:
            raise ValueError('Positive polling interval and request limit required')
        if controller is not None and controller.store is not store:
            raise ValueError('Controller and gateway must share one store')
        self.store, self.controller = store, controller
        self.profile_id_for = profile_id_for
        self.poll_interval, self.max_requests = poll_interval, max_requests
        self._setup_lock = asyncio.Lock()
        self._ready = False
        self._legacy_readers = {}

    def claim_request(self, principal_id, sid, durable, alias):
        self._legacy_readers.setdefault((principal_id, sid, durable), set()).add(alias)

    def release_request(self, principal_id, sid, durable, alias):
        key = (principal_id, sid, durable)
        readers = self._legacy_readers.get(key, set())
        readers.discard(alias)
        if readers:
            return False
        self._legacy_readers.pop(key, None)
        return True

    async def serve_ws(self, websocket, principal, manifest, mode):
        from .connection import GatewayConnection
        await GatewayConnection(websocket, principal, self, manifest, mode).run()

    async def serve_http(self, request, principal, row):
        from .rest import serve_http
        return await serve_http(self, request, principal, row)

    async def open(self, principal):
        sessions = MongoSessionStore(self.store, principal.user_id)
        execution = Execution(self.controller, principal.user_id,
                              profile_id=self.profile_id_for(principal))
        async with self._setup_lock:
            if not self._ready:
                await sessions.setup()
                await execution.pending.setup()
                self._ready = True
        return GatewayScope(sessions, execution)

"""Injectable platform authentication boundary; no credential format assumptions."""
from dataclasses import dataclass
from typing import Protocol

from starlette.requests import Request
from starlette.websockets import WebSocket


@dataclass(frozen=True)
class Principal:
    """Server-verified user identity only; never inherits host/operator authority."""

    user_id: str

    def __post_init__(self):
        if not isinstance(self.user_id, str) or not self.user_id.strip():
            raise ValueError('A nonempty user_id is required')


class AuthenticationFailure(Exception):
    """401: absent/invalid credentials; 403: authenticated but not authorized."""

    def __init__(self, status_code: int = 401):
        if status_code not in (401, 403):
            raise ValueError('Authentication status must be 401 or 403')
        self.status_code = status_code
        self.code = 'unauthorized' if status_code == 401 else 'forbidden'
        super().__init__(self.code)


class PrincipalResolver(Protocol):
    async def resolve(self, connection: Request | WebSocket) -> Principal:
        """Verify identity and route access, or raise AuthenticationFailure.

        Implementations must use the agreed platform trust contract, not raw
        identity fields from clients. Resource ownership must come from trusted
        records. Future RPC handlers must separately authorize each resource;
        authenticating the upgrade does not authorize arbitrary RPC parameters.
        """
        ...


class DenyPrincipalResolver:
    async def resolve(self, connection: Request | WebSocket) -> Principal:
        # No platform verifier is installed. Even request.state is not a contract.
        # Never fall back to harness settings or a local/development identity.
        raise AuthenticationFailure(401)


def require_owner(principal: Principal, owner_id: str) -> None:
    """Compare against an owner from trusted storage, never a client owner field."""
    if principal.user_id != owner_id:
        raise AuthenticationFailure(403)


# Origin/ticket 검증 책임 위치(사내 플랫폼 vs 앱)는 미확정.
# These interfaces do not choose a ticket header, token format or signature.
class OriginPolicy(Protocol):
    async def allows(self, websocket: WebSocket, principal: Principal) -> bool:
        """Validate against explicit allowed origins; absent allowlist must deny."""
        ...


class TicketVerifier(Protocol):
    async def verify_and_consume(self, websocket: WebSocket, principal: Principal) -> bool:
        """Atomically validate/consume a one-use ticket bound to this principal.

        Missing, expired, replayed, or incorrectly bound tickets must be denied.
        """
        ...


class DenyOriginPolicy:
    async def allows(self, websocket: WebSocket, principal: Principal) -> bool:
        return False


class DenyTicketVerifier:
    async def verify_and_consume(self, websocket: WebSocket, principal: Principal) -> bool:
        return False

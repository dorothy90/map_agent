"""Authentication boundary tests; identities below are test fixtures only."""
import asyncio
from dataclasses import FrozenInstanceError

import pytest
from starlette.requests import Request
from starlette.websockets import WebSocket

from harness.gateway.auth import (
    AuthenticationFailure, DenyPrincipalResolver, DenyOriginPolicy,
    DenyTicketVerifier, Principal, require_owner,
)


def test_principal_is_required_immutable_and_has_no_host_authority():
    for invalid in ('', '   ', None, 1):
        with pytest.raises(ValueError):
            Principal(invalid)
    principal = Principal('alice')
    with pytest.raises(FrozenInstanceError):
        principal.user_id = 'bob'
    assert vars(principal) == {'user_id': 'alice'}


@pytest.mark.parametrize('kind', ['http', 'websocket'])
def test_default_resolver_ignores_unverified_state(kind):
    scope = {'type': kind, 'headers': [], 'state': {'principal_id': 'local'}}
    connection = Request(scope) if kind == 'http' else WebSocket(scope, None, None)
    with pytest.raises(AuthenticationFailure) as exc:
        asyncio.run(DenyPrincipalResolver().resolve(connection))
    assert exc.value.status_code == 401


def test_ownership_uses_verified_identity():
    alice, bob = Principal('alice'), Principal('bob')
    assert alice != bob
    require_owner(alice, 'alice')
    with pytest.raises(AuthenticationFailure) as exc:
        require_owner(bob, 'alice')
    assert exc.value.status_code == 403


def test_failure_status_is_restricted():
    with pytest.raises(ValueError):
        AuthenticationFailure(501)


def test_ws_defaults_deny_without_platform_contract():
    ws = WebSocket({'type': 'websocket', 'headers': []}, None, None)
    assert asyncio.run(DenyOriginPolicy().allows(ws, Principal('alice'))) is False
    assert asyncio.run(DenyTicketVerifier().verify_and_consume(ws, Principal('alice'))) is False

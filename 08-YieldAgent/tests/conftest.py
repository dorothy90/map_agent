"""pytest shared fixtures for the E2E regression suite.

The suite needs the live agent server (uvicorn agent_server:app --port 8001) AND the
LLM backend reachable. If the server is down we SKIP (not fail) so unit runs stay green;
start the server with:  uvicorn agent_server:app --port 8001

Server-independent unit tests opt out with `@pytest.mark.no_server` (e.g.
test_user_memory.py — local Mongo only).
"""

import pytest

from e2e_client import server_is_up

_server_up: bool | None = None


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "no_server: 서버 불필요 단위 테스트 — require_server 스킵에서 제외"
    )


@pytest.fixture(autouse=True)
def require_server(request):
    if request.node.get_closest_marker("no_server"):
        return
    global _server_up
    if _server_up is None:
        _server_up = server_is_up()
    if not _server_up:
        pytest.skip(
            "agent server not reachable at the E2E base URL "
            "(start: `uvicorn agent_server:app --port 8001`)"
        )

"""pytest shared fixtures for the E2E regression suite.

The suite needs the live agent server (uvicorn agent_server:app --port 8001) AND the
LLM backend reachable. If the server is down we SKIP (not fail) so unit runs stay green;
start the server with:  uvicorn agent_server:app --port 8001
"""

import pytest

from e2e_client import server_is_up


@pytest.fixture(scope="session", autouse=True)
def require_server():
    if not server_is_up():
        pytest.skip(
            "agent server not reachable at the E2E base URL "
            "(start: `uvicorn agent_server:app --port 8001`)",
            allow_module_level=True,
        )

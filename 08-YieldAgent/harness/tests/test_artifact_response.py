from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness.router import router


class Store:
    async def artifact(self, principal_id, session_id, artifact_id):
        if artifact_id == "missing" or session_id != "session-1":
            raise PermissionError
        mime = "text/html" if artifact_id == "html" else "text/csv"
        return {"mime": mime}, b"<script>parent.document</script>" if mime == "text/html" else b"a,b\n"


def client():
    app = FastAPI()
    app.include_router(router)
    app.state.harness = SimpleNamespace(
        store=Store(), settings=SimpleNamespace(principal="operator")
    )
    return TestClient(app)


def test_html_artifact_get_and_head_are_inline_and_sandboxed():
    with client() as http:
        get = http.get("/harness/artifacts/html?session_id=session-1")
        head = http.head("/harness/artifacts/html?session_id=session-1")

    expected_csp = (
        "sandbox allow-scripts; frame-ancestors 'self'; object-src 'none'; "
        "base-uri 'none'; form-action 'none'"
    )
    assert get.status_code == head.status_code == 200
    assert get.content == b"<script>parent.document</script>"
    assert head.content == b""
    for response in (get, head):
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["content-disposition"] == "inline"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-security-policy"] == expected_csp


def test_non_html_stays_attachment_and_access_failures_are_hidden():
    with client() as http:
        csv = http.get("/harness/artifacts/csv?session_id=session-1")
        missing = http.get("/harness/artifacts/missing?session_id=session-1")
        wrong_owner = http.get("/harness/artifacts/html?session_id=other")
        missing_session = http.get("/harness/artifacts/html")

    assert csv.headers["content-disposition"] == "attachment"
    assert "content-security-policy" not in csv.headers
    assert missing.status_code == wrong_owner.status_code == 404
    assert missing_session.status_code == 422

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from identity import PlatformIdentity, get_platform_identity
from settings import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def build_client():
    app = FastAPI()

    @app.get("/whoami")
    def whoami(identity: PlatformIdentity = Depends(get_platform_identity)):
        return identity.model_dump()

    return TestClient(app)


def test_missing_gateway_header_is_401(monkeypatch):
    monkeypatch.setenv("PLATFORM_USER_ID_HEADER", "X-Authenticated-User")
    monkeypatch.setenv("OWNER_HASH_KEY", "test-key")
    assert build_client().get("/whoami").status_code == 401


def test_identity_comes_from_header(monkeypatch):
    monkeypatch.setenv("PLATFORM_USER_ID_HEADER", "X-Authenticated-User")
    monkeypatch.setenv("OWNER_HASH_KEY", "test-key")
    response = build_client().get(
        "/whoami", headers={"X-Authenticated-User": "employee123"}
    )
    assert response.status_code == 200
    assert response.json()["owner_id"] == "employee123"
    assert response.json()["owner_hash"] != "employee123"

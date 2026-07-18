import importlib

import pytest
from pydantic import ValidationError

import common
from settings import Settings, reset_settings_cache


class FakePool:
    busy = 2
    opened = 3
    max = 4

    def __init__(self):
        self.closed = False

    def close(self, force=False):
        assert force is True
        self.closed = True

    def acquire(self):
        class Connection:
            def ping(self):
                return None

            def close(self):
                return None

        return Connection()


@pytest.fixture(autouse=True)
def reset_pool_and_settings(monkeypatch):
    common._pool = None
    reset_settings_cache()
    for key in (
        "ORACLE_USER",
        "ORACLE_PASSWORD",
        "ORACLE_DSN",
        "ORACLE_POOL_MIN",
        "ORACLE_POOL_MAX",
        "ORACLE_POOL_INCREMENT",
    ):
        monkeypatch.delenv(key, raising=False)
    yield
    common._pool = None
    reset_settings_cache()


def test_oracle_pool_uses_validated_environment_settings(monkeypatch):
    monkeypatch.setenv("ORACLE_USER", "yield-user")
    monkeypatch.setenv("ORACLE_PASSWORD", "yield-password")
    monkeypatch.setenv("ORACLE_DSN", "oracle.internal/service")
    monkeypatch.setenv("ORACLE_POOL_MIN", "1")
    monkeypatch.setenv("ORACLE_POOL_MAX", "4")
    monkeypatch.setenv("ORACLE_POOL_INCREMENT", "1")
    created = FakePool()
    calls = []
    monkeypatch.setattr(
        common.oracledb,
        "create_pool",
        lambda **kwargs: calls.append(kwargs) or created,
    )

    assert common._get_oracle_pool() is created
    assert calls == [
        {
            "user": "yield-user",
            "password": "yield-password",
            "dsn": "oracle.internal/service",
            "min": 1,
            "max": 4,
            "increment": 1,
        }
    ]


def test_oracle_pool_budget_is_validated():
    with pytest.raises(ValidationError):
        Settings(oracle_pool_min=5, oracle_pool_max=4, oracle_pool_increment=1)
    with pytest.raises(ValidationError):
        Settings(oracle_pool_min=1, oracle_pool_max=4, oracle_pool_increment=5)


def test_pool_metrics_do_not_create_pool_and_close_resets_it(monkeypatch):
    monkeypatch.setattr(
        common.oracledb,
        "create_pool",
        lambda **_kwargs: pytest.fail("must stay lazy"),
    )
    assert common.get_oracle_pool_metrics() == {"busy": 0, "open": 0, "max": 0}

    pool = FakePool()
    common._pool = pool
    assert common.get_oracle_pool_metrics() == {"busy": 2, "open": 3, "max": 4}
    common.close_oracle_pool()
    assert pool.closed is True
    assert common._pool is None


def test_worker_runtime_close_closes_oracle_pool():
    from worker_runtime import WorkerRuntime

    pool = FakePool()
    common._pool = pool

    WorkerRuntime(settings=Settings()).close()

    assert pool.closed is True
    assert common._pool is None


def test_agent_server_import_does_not_create_oracle_pool(monkeypatch):
    monkeypatch.setenv("ENABLE_LEGACY_CHAT", "false")
    monkeypatch.setattr(
        common.oracledb,
        "create_pool",
        lambda **_kwargs: pytest.fail("API import must stay lazy"),
    )
    import agent_server

    importlib.reload(agent_server)
    assert common._pool is None


def test_worker_dependency_probe_creates_oracle_pool(monkeypatch, tmp_path):
    from health_router import probe_worker_dependencies

    pool = FakePool()
    calls = []
    monkeypatch.setattr(
        common.oracledb,
        "create_pool",
        lambda **kwargs: calls.append(kwargs) or pool,
    )
    settings = Settings(
        environment="test",
        artifact_root=tmp_path,
        oracle_user="u",
        oracle_password="p",
        oracle_dsn="dsn",
        oracle_pool_min=1,
        oracle_pool_max=4,
        oracle_pool_increment=1,
    )

    result = probe_worker_dependencies(settings, llm_probe=lambda: True)

    assert result == {"nas": "ok", "oracle": "ok", "llm": "ok"}
    assert len(calls) == 1

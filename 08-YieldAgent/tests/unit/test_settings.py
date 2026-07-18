import pytest
from pydantic import ValidationError
from settings import Settings


def test_production_requires_shared_services(tmp_path):
    with pytest.raises(ValidationError):
        Settings(environment="production", artifact_root=tmp_path)


def test_limits_and_header_are_loaded(tmp_path):
    settings = Settings(
        environment="test",
        mongo_uri="mongodb://mongo:27017",
        redis_url="redis://redis:6379/0",
        artifact_root=tmp_path,
        platform_user_id_header="X-Authenticated-User",
        user_job_limit=2,
        global_job_limit=100,
    )
    assert settings.platform_user_id_header == "X-Authenticated-User"
    assert settings.user_job_limit == 2
    assert settings.global_job_limit == 100

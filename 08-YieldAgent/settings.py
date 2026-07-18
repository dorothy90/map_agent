from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "yield_agent"
    redis_url: str = "redis://localhost:6379/0"
    artifact_root: Path = Path("generated")
    platform_user_id_header: str = "user_id"
    owner_hash_key: SecretStr | None = None
    cors_origins: list[str] = Field(default_factory=list)
    user_job_limit: int = Field(default=2, ge=1)
    global_job_limit: int = Field(default=100, ge=1)
    enable_legacy_chat: bool = True
    enable_repl: bool = False
    enable_wiki: bool = False
    enable_local_trace: bool = False

    @model_validator(mode="after")
    def validate_production(self):
        if self.environment == "production":
            if "localhost" in self.mongo_uri or "localhost" in self.redis_url:
                raise ValueError("production requires non-local MongoDB and Redis")
            if not self.artifact_root.is_absolute():
                raise ValueError("production ARTIFACT_ROOT must be absolute")
            if not self.owner_hash_key:
                raise ValueError("production OWNER_HASH_KEY is required")
            if self.enable_legacy_chat or self.enable_repl or self.enable_wiki or self.enable_local_trace:
                raise ValueError("unsafe production routes or traces are enabled")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()

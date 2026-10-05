from __future__ import annotations

import os
from urllib.parse import urlparse
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = "z-ai/glm-5.3-flash"
    base_url: str = "https://openrouter.ai/api/v1"
    api_key: SecretStr = SecretStr("")
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "yield_agent"
    tool_limit: int = Field(default=24, ge=1)
    model_limit: int = Field(default=40, ge=3)
    token_limit: int = Field(default=120_000, ge=4096)
    context_tokens: int = Field(default=24_000, ge=4096)
    active_seconds: int = Field(default=300, ge=5)
    call_timeout: int = Field(default=90, ge=1)
    max_output_tokens: int = Field(default=8192, ge=128)
    temperature: float = Field(default=0, ge=0, le=2)
    reasoning_effort: str | None = None
    python_image: str = "yield-harness-python:2"
    principal: str = "local"
    enabled: bool = False

    @classmethod
    def from_env(cls):
        base_url = os.getenv("HARNESS_BASE_URL", cls.model_fields["base_url"].default)
        host = urlparse(base_url).hostname
        key = os.getenv("HARNESS_API_KEY", "")
        if not key and host == "api.groq.com":
            key = os.getenv("GROQ_API_KEY") or os.getenv("groq_api_key", "")
        elif not key and host == "openrouter.ai":
            key = os.getenv("OPENROUTER_API_KEY", "")
        return cls(
            model=os.getenv("HARNESS_MODEL", cls.model_fields["model"].default),
            base_url=base_url,
            api_key=key,
            mongo_uri=os.getenv("HARNESS_MONGO_URI", "mongodb://localhost:27017"),
            mongo_db=os.getenv("HARNESS_MONGO_DB", "yield_agent"),
            principal=os.getenv("HARNESS_PRINCIPAL", "local"),
            enabled=os.getenv("HARNESS_ENABLED", "false").lower() == "true",
            python_image=os.getenv("HARNESS_PYTHON_IMAGE", "yield-harness-python:2"),
            tool_limit=int(os.getenv("HARNESS_TOOL_LIMIT", "24")),
            model_limit=int(os.getenv("HARNESS_MODEL_LIMIT", "40")),
            token_limit=int(os.getenv("HARNESS_TOKEN_LIMIT", "120000")),
            context_tokens=int(os.getenv("HARNESS_CONTEXT_TOKENS", "24000")),
            active_seconds=int(os.getenv("HARNESS_ACTIVE_SECONDS", "300")),
            call_timeout=int(os.getenv("HARNESS_CALL_TIMEOUT", "90")),
            max_output_tokens=int(os.getenv("HARNESS_MAX_OUTPUT_TOKENS", "8192")),
            temperature=float(os.getenv("HARNESS_TEMPERATURE", "0")),
            reasoning_effort=os.getenv("HARNESS_REASONING_EFFORT") or None,
        )

from langchain_openai import ChatOpenAI

from .config import Settings


def build_model(settings: Settings, *, purpose: str = "reasoning"):
    if not settings.api_key.get_secret_value().strip():
        raise ValueError("LLM API key is required")
    return ChatOpenAI(
        model=settings.model,
        base_url=settings.base_url,
        api_key=settings.api_key,
        temperature=settings.temperature,
        max_tokens=settings.max_output_tokens,
        **({"reasoning_effort": settings.reasoning_effort} if settings.reasoning_effort else {}),
        timeout=settings.call_timeout,
        max_retries=0,
    )

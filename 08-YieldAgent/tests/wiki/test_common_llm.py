import pytest


pytestmark = pytest.mark.no_server


def test_get_llm_uses_explicit_model(monkeypatch):
    import common
    import langchain_openai

    captured = {}

    def fake_chat_openai(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", fake_chat_openai)
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    common.get_llm(model="google/gemma-4-26b-a4b-it:free")

    assert captured["model"] == "google/gemma-4-26b-a4b-it:free"


def test_get_llm_uses_configured_default_model(monkeypatch):
    import common
    import langchain_openai

    captured = {}

    def fake_chat_openai(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", fake_chat_openai)
    monkeypatch.setenv("RETRIEVE_CHAIN_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")

    common.get_llm()

    assert captured["model"] == "nvidia/nemotron-3-super-120b-a12b:free"

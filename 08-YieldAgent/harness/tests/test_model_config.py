def test_explicit_model_is_preserved(monkeypatch):
    from common import get_llm

    monkeypatch.setenv("HARNESS_API_KEY", "fixture-key")
    assert get_llm(model="fixture/model").model_name == "fixture/model"


def test_missing_key_fails_without_provider_fallback(monkeypatch):
    import pytest
    from harness.config import Settings
    from harness.model import build_model

    with pytest.raises(ValueError, match="API key"):
        build_model(Settings(api_key=""))


def test_groq_uses_lowercase_user_key_and_never_openrouter_key(monkeypatch):
    from harness.config import Settings
    monkeypatch.delenv("HARNESS_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("groq_api_key", "fixture-groq")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-other")
    monkeypatch.setenv("HARNESS_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("HARNESS_MODEL", "openai/gpt-oss-120b")
    settings = Settings.from_env()
    assert settings.api_key.get_secret_value() == "fixture-groq"
    monkeypatch.delenv("groq_api_key")
    assert not Settings.from_env().api_key.get_secret_value()


def test_legacy_factory_uses_same_provider_settings(monkeypatch):
    from common import get_llm
    monkeypatch.delenv("HARNESS_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "fixture-groq")
    monkeypatch.setenv("HARNESS_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("HARNESS_MODEL", "openai/gpt-oss-120b")
    model = get_llm()
    assert str(model.openai_api_base) == "https://api.groq.com/openai/v1"
    assert model.model_name == "openai/gpt-oss-120b"
    assert model.openai_api_key.get_secret_value() == "fixture-groq"


def test_configured_output_budget_and_effort_reach_compatible_client(monkeypatch):
    from harness.config import Settings
    from harness.model import build_model
    monkeypatch.setenv("HARNESS_API_KEY", "fixture-key")
    monkeypatch.setenv("HARNESS_MAX_OUTPUT_TOKENS", "768")
    monkeypatch.setenv("HARNESS_REASONING_EFFORT", "none")
    model = build_model(Settings.from_env())
    assert model.max_tokens == 768
    assert model.reasoning_effort == "none"


def test_openrouter_uses_its_own_key_and_larger_output_budget(monkeypatch):
    from harness.config import Settings
    from harness.model import build_model
    from common import get_llm
    monkeypatch.delenv("HARNESS_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-openrouter")
    monkeypatch.setenv("groq_api_key", "fixture-other-provider")
    monkeypatch.setenv("HARNESS_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("HARNESS_MODEL", "z-ai/glm-5.3-flash")
    monkeypatch.setenv("HARNESS_MAX_OUTPUT_TOKENS", "8192")
    monkeypatch.setenv("HARNESS_REASONING_EFFORT", "")
    settings = Settings.from_env()
    for model in (build_model(settings), get_llm()):
        assert model.openai_api_key.get_secret_value() == "fixture-openrouter"
        assert model.model_name == "z-ai/glm-5.3-flash"
        assert model.max_tokens == 8192
        assert model.reasoning_effort is None
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert not Settings.from_env().api_key.get_secret_value()


def test_sampling_setting_reaches_harness_and_legacy_without_overriding_explicit_value(monkeypatch):
    from harness.config import Settings
    from harness.model import build_model
    from common import get_llm
    monkeypatch.setenv("HARNESS_API_KEY", "fixture-key")
    monkeypatch.setenv("HARNESS_TEMPERATURE", "1.0")
    assert build_model(Settings.from_env()).temperature == 1.0
    assert get_llm().temperature == 1.0
    assert get_llm(temperature=0).temperature == 0


from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import pytest


@pytest.fixture
def native_result(monkeypatch):
    async def run_chat(context, *, history):
        await context.check()
        return {"status": "completed", "answer": "안녕하세요", "stop_reason": "completed",
                "native_messages": [], "native_usage": {}, "native_engine": {}}

    monkeypatch.setattr("harness.hermes_chat.run_chat", run_chat)
    return run_chat


@pytest.fixture
def isolated_settings_env(monkeypatch, tmp_path):
    from harness.tests.test_isolation_config import isolation_env

    return isolation_env(monkeypatch, tmp_path)

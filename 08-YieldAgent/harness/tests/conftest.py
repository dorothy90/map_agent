from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import pytest


@pytest.fixture
def native_result(monkeypatch):
    async def run_native(context, registry, *, history):
        await context.check()
        return {"status": "completed", "answer": "안녕하세요", "stop_reason": "completed",
                "native_messages": [], "native_usage": {}, "native_engine": {}}

    monkeypatch.setattr("harness.native_hermes.run_native", run_native)
    return run_native

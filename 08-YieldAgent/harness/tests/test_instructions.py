import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_load_native_instructions_includes_notes_once():
    from harness.instructions import load_native_instructions

    directory = Path(__file__).parents[1] / "instructions"
    native = (directory / "native.md").read_text(encoding="utf-8").strip()
    notes = (directory / "domain-notes.md").read_text(encoding="utf-8").strip()
    loaded = load_native_instructions()

    assert loaded == native + "\n\n" + notes
    assert loaded.count(notes) == 1
    assert "lot은 LOTID를 뜻하며, LOT_CD는 제품 코드다." in loaded
    assert "item PARAM(VTH, FMAX 등)은 테스트 측정값" in loaded


def test_load_native_instructions_is_cwd_independent(monkeypatch, tmp_path):
    from harness.instructions import load_native_instructions

    expected = load_native_instructions()
    monkeypatch.chdir(tmp_path)
    assert load_native_instructions() == expected


def test_missing_domain_notes_raises(monkeypatch):
    import harness.instructions as instructions

    original = Path.read_text

    def missing_notes(path, *args, **kwargs):
        if path.name == "domain-notes.md":
            raise FileNotFoundError(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(instructions.Path, "read_text", missing_notes)
    with pytest.raises(FileNotFoundError):
        instructions.load_native_instructions()


def test_native_startup_contains_domain_notes(monkeypatch, tmp_path):
    import harness.native_hermes as native
    from harness.config import Settings
    from harness.tools.registry import ToolRegistry

    profile = tmp_path / "profile"
    profile.mkdir()
    writes = []

    class Stdin:
        def write(self, data):
            writes.append(data)

        async def drain(self):
            pass

    class Stdout:
        def __init__(self):
            self.lines = [json.dumps({
                "type": "result", "result": {"completed": False, "messages": []},
            }).encode() + b"\n"]

        async def readline(self):
            return self.lines.pop(0) if self.lines else b""

    process = SimpleNamespace(stdin=Stdin(), stdout=Stdout(), returncode=0)

    async def create_process(*args, **kwargs):
        return process

    class Runs:
        async def update_one(self, *args, **kwargs):
            pass

    context = SimpleNamespace(
        settings=Settings(engine="hermes"),
        run={
            "run_id": "run-1", "session_id": "session-1", "principal_id": "alice",
            "epoch": 1, "query": "수율을 보여줘", "created_at": "2026-11-01T09:00:00+09:00",
        },
        store=SimpleNamespace(runs=Runs()),
        remaining_seconds=lambda: 30,
        check=lambda: asyncio.sleep(0),
    )

    monkeypatch.setattr(native, "ROOT", tmp_path)
    monkeypatch.setattr(native, "prepare_profile", lambda *args: profile)
    monkeypatch.setattr(native.asyncio, "create_subprocess_exec", create_process)
    asyncio.run(native.run_native(context, ToolRegistry()))

    startup = json.loads(writes[0])
    assert "검사 단계가 명시되지 않은 “수율”은 PT1H를 뜻한다." in startup["instructions"]
    assert "서버가 제공한 요청 기준정보(JSON)다." in startup["instructions"]
    assert startup["request_context"] == {
        "requested_at": "2026-11-01T09:00:00+09:00", "timezone": "Asia/Seoul",
    }

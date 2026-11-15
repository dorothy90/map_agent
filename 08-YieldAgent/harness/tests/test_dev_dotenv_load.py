"""Discover and load an existing dotenv without overriding process env or printing values."""
import os
from pathlib import Path

from harness.dev.dotenv_load import existing_dotenv_path, load_existing_dotenv


def test_existing_dotenv_path_prefers_project_then_sibling_checkout(tmp_path):
    work = tmp_path / "work"
    project = work / "yield-agent-ui-b" / "08-YieldAgent"
    sibling = work / "yield-agent"
    project.mkdir(parents=True)
    sibling.mkdir()
    (sibling / ".env").write_text("ORACLE_SAMPLE=from-sibling\n", encoding="utf-8")
    found = existing_dotenv_path(project, cwd=project, home=tmp_path / "no-home")
    assert found == (sibling / ".env").resolve()

    own = project / ".env"
    own.write_text("ORACLE_SAMPLE=from-project\n", encoding="utf-8")
    found = existing_dotenv_path(project, cwd=project, home=tmp_path / "no-home")
    assert found == own.resolve()


def test_load_existing_dotenv_fills_missing_keys_without_override(tmp_path, monkeypatch):
    project = tmp_path / "yield-agent" / "08-YieldAgent"
    project.mkdir(parents=True)
    env_path = tmp_path / "yield-agent" / ".env"
    kept = "kept-process-value"
    filled = "filled-from-file"
    env_path.write_text(
        f"DOTENV_LOAD_TEST_KEPT={filled}\nDOTENV_LOAD_TEST_FILLED={filled}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOTENV_LOAD_TEST_KEPT", kept)
    monkeypatch.delenv("DOTENV_LOAD_TEST_FILLED", raising=False)
    loaded = load_existing_dotenv(project, cwd=project, home=tmp_path / "no-home", override=False)
    assert loaded == env_path.resolve()
    assert os.environ["DOTENV_LOAD_TEST_KEPT"] == kept
    assert os.environ["DOTENV_LOAD_TEST_FILLED"] == filled
    monkeypatch.delenv("DOTENV_LOAD_TEST_FILLED", raising=False)


def test_existing_dotenv_path_returns_none_when_absent(tmp_path):
    project = tmp_path / "empty" / "08-YieldAgent"
    project.mkdir(parents=True)
    assert existing_dotenv_path(project, cwd=project, home=tmp_path / "no-home") is None

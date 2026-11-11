import os
from pathlib import Path

import pytest

from harness.config import Settings


def isolation_env(monkeypatch, tmp_path):
    key = tmp_path / "yieldrun_key"
    known_hosts = tmp_path / "known_hosts"
    key.write_text("test-key")
    known_hosts.write_text("localhost test-host-key")
    home = tmp_path / "yieldrun-home"
    work_root = home / "work"
    analysis_venv = tmp_path / "analysis-venv"
    launcher = tmp_path / "yieldrun-launcher"
    work_root.mkdir(parents=True)
    analysis_venv.mkdir()
    launcher.write_text("launcher")
    monkeypatch.delenv("HARNESS_ISOLATION_BACKEND", raising=False)
    values = {
        "HARNESS_ISOLATION_SSH_HOST": "127.0.0.1",
        "HARNESS_ISOLATION_SSH_USER": "yieldrun",
        "HARNESS_ISOLATION_SSH_PORT": "22",
        "HARNESS_ISOLATION_SSH_KEY": str(key),
        "HARNESS_ISOLATION_KNOWN_HOSTS": str(known_hosts),
        "HARNESS_ISOLATION_HOME": str(home),
        "HARNESS_ISOLATION_WORK_ROOT": str(work_root),
        "HARNESS_ISOLATION_ANALYSIS_VENV": str(analysis_venv),
        "HARNESS_ISOLATION_LAUNCHER": str(launcher),
        "HARNESS_ISOLATION_SANDBOX": "auto",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


def test_isolation_settings_are_always_loaded(monkeypatch, tmp_path):
    isolation_env(monkeypatch, tmp_path)

    settings = Settings.from_env()

    assert settings.no_yield_tools is True
    assert settings.isolation.ssh_host == "127.0.0.1"
    assert settings.isolation.ssh_user == "yieldrun"
    assert settings.isolation.work_root == Path(os.environ["HARNESS_ISOLATION_WORK_ROOT"])
    assert settings.isolation.sandbox == "auto"


def test_environment_settings_require_isolation_configuration(monkeypatch):
    for name in list(os.environ):
        if name.startswith("HARNESS_ISOLATION_"):
            monkeypatch.delenv(name)
    with pytest.raises(ValueError, match="missing isolation configuration"):
        Settings.from_env()


def test_explicit_native_settings_require_isolation():
    with pytest.raises(ValueError, match="native execution requires isolation configuration"):
        Settings(no_yield_tools=True)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("HARNESS_ISOLATION_SSH_HOST", "example.com", "localhost"),
        ("HARNESS_ISOLATION_SSH_USER", "root", "yieldrun"),
        ("HARNESS_ISOLATION_HOME", "relative/home", "absolute"),
        ("HARNESS_ISOLATION_SANDBOX", "maybe", "sandbox"),
    ],
)
def test_invalid_isolation_configuration_is_rejected(monkeypatch, tmp_path, name, value, message):
    isolation_env(monkeypatch, tmp_path)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        Settings.from_env()


def test_missing_identity_file_is_rejected(monkeypatch, tmp_path):
    isolation_env(monkeypatch, tmp_path)
    monkeypatch.setenv("HARNESS_ISOLATION_SSH_KEY", str(tmp_path / "missing"))

    with pytest.raises(ValueError, match="identity|key"):
        Settings.from_env()


def test_repository_paths_are_rejected(monkeypatch, tmp_path):
    isolation_env(monkeypatch, tmp_path)
    repo = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("HARNESS_ISOLATION_WORK_ROOT", str(repo / "harness"))

    with pytest.raises(ValueError, match="repository"):
        Settings.from_env()


def test_symlinked_isolation_path_is_rejected(monkeypatch, tmp_path):
    values = isolation_env(monkeypatch, tmp_path)
    linked_home = tmp_path / "linked-home"
    linked_home.symlink_to(Path(values["HARNESS_ISOLATION_HOME"]), target_is_directory=True)
    monkeypatch.setenv("HARNESS_ISOLATION_HOME", str(linked_home))

    with pytest.raises(ValueError, match="symlink"):
        Settings.from_env()

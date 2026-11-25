import sys

import pytest

from harness import config


def test_windows_selects_the_appcontainer_backend(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("HARNESS_ISOLATION_BACKEND", raising=False)
    def selected(cls):
        raise LookupError("Windows backend selected")
    monkeypatch.setattr(config.WindowsIsolationSettings, "from_env", classmethod(selected))
    with pytest.raises(LookupError, match="Windows backend selected"):
        config.Settings.from_env()


@pytest.mark.skipif(sys.platform == "win32", reason="Exercises the non-Windows host guard")
def test_windows_selection_cannot_run_on_a_posix_host(monkeypatch):
    monkeypatch.setenv("HARNESS_ISOLATION_BACKEND", "windows")
    with pytest.raises(ValueError, match="requires Windows"):
        config.Settings.from_env()

from pathlib import Path

import pytest

from harness.isolation import launcher


def config(tmp_path):
    home = tmp_path / "home"
    work = home / "work"
    venv = tmp_path / "venv"
    home.mkdir()
    work.mkdir()
    venv.mkdir()
    return {
        "home": str(home),
        "work_root": str(work),
        "analysis_venv": str(venv),
        "launcher": str(tmp_path / "launcher"),
        "sandbox": "auto",
    }


def request(principal="a" * 64, run="b" * 64, command="pwd"):
    return {"v": 1, "principal": principal, "run": run, "op": "command", "command": command}


def test_parse_request_rejects_missing_or_malformed_scope():
    for value in (None, "", "pwd", "yieldrun-v1 !!!"):
        with pytest.raises(SystemExit):
            launcher.parse_request(value)
    invalid = request(principal="alice")
    with pytest.raises(SystemExit):
        launcher.validate_request(invalid)


def test_scope_paths_are_derived_from_config_and_reject_symlinks(tmp_path):
    settings = config(tmp_path)
    scope = launcher.scope_paths(settings, request())
    assert scope.profile == Path(settings["work_root"]) / "profiles" / ("a" * 64)
    assert scope.workdir == Path(settings["work_root"]) / "runs" / ("b" * 64)

    outside = tmp_path / "outside"
    outside.mkdir()
    scope.profile.parent.mkdir()
    scope.profile.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SystemExit):
        launcher.ensure_scope(settings, scope)


def test_configured_path_rejects_symlinked_component(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(SystemExit):
        launcher.resolve_without_symlinks(link)


def test_linux_masks_parents_before_binding_only_current_scope(monkeypatch, tmp_path):
    settings = config(tmp_path)
    settings["sandbox_binary"] = "/usr/bin/bwrap"
    scope = launcher.scope_paths(settings, request())
    scope.profile.mkdir(parents=True)
    scope.workdir.mkdir(parents=True)
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setattr(Path, "is_file", lambda self: str(self) == "/usr/bin/bwrap")
    monkeypatch.setattr(launcher, "root_owned_readonly", lambda *args, **kwargs: None)

    clean_env = {}
    command = launcher.sandbox_command(settings, scope, "pwd", clean_env)

    home_index = command.index("--tmpfs", command.index("--tmpfs") + 1)
    assert command[home_index + 1] == settings["home"]
    assert command.index(str(scope.profile)) > home_index
    assert command.index(str(scope.workdir)) > home_index
    sources = [command[index + 1] for index, item in enumerate(command[:-1])
               if item in {"--bind", "--ro-bind"}]
    scope_sources = [source for source in sources if source.startswith("/proc/self/fd/")]
    assert len(scope_sources) == 2
    assert settings["home"] not in sources and settings["work_root"] not in sources
    for descriptor in clean_env["_YIELD_PASS_FDS"].split(","):
        launcher.os.close(int(descriptor))


def test_unavailable_sandbox_fails_closed_for_auto(monkeypatch, tmp_path):
    settings = config(tmp_path)
    settings["sandbox_binary"] = str(tmp_path / "missing")
    scope = launcher.scope_paths(settings, request())
    monkeypatch.setattr(launcher.sys, "platform", "linux")

    with pytest.raises(SystemExit):
        launcher.sandbox_command(settings, scope, "true", {})


def test_macos_uses_root_policy_with_exact_scope_parameters(monkeypatch, tmp_path):
    settings = config(tmp_path)
    settings.update(sandbox_binary="/usr/bin/sandbox-exec", sandbox_profile="/etc/yieldrun.sb")
    scope = launcher.scope_paths(settings, request())
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "is_file", lambda self: str(self) == "/usr/bin/sandbox-exec")
    monkeypatch.setattr(launcher, "root_owned_readonly", lambda *args, **kwargs: None)

    command = launcher.sandbox_command(settings, scope, "pwd", {})

    assert command[:8] == [
        "/usr/bin/sandbox-exec", "-D", f"PROFILE={scope.profile}",
        "-D", f"WORKDIR={scope.workdir}", "-f", "/etc/yieldrun.sb", "/bin/bash",
    ]

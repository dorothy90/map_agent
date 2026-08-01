import json
from types import SimpleNamespace

import pytest

import sync_wiki


pytestmark = pytest.mark.no_server


class FakeService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def check(self):
        self.calls.append(("check", None))
        return self.result

    def apply(self, limit):
        self.calls.append(("apply", limit))
        return self.result

    def resume(self, limit):
        self.calls.append(("resume", limit))
        return self.result


def _result(status="completed", errors=()):
    return SimpleNamespace(
        status=status,
        new=2,
        changed=1,
        source_removed=0,
        unchanged=4,
        enqueued=3,
        succeeded=3,
        recovered=0,
        failed=0,
        materialized=True,
        errors=errors,
        targets={"new": ("A|OP|EASY", "B|OP|EASY")},
    )


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--check", "--apply"],
        ["--check", "--limit", "3"],
        ["--apply", "--limit", "0"],
        ["--resume", "--limit", "-1"],
    ],
)
def test_parser_rejects_invalid_mode_or_limit_combinations(argv):
    with pytest.raises(SystemExit):
        sync_wiki._parse_args(argv)


@pytest.mark.parametrize(
    ("argv", "expected_call"),
    [
        (["--check"], ("check", None)),
        (["--apply"], ("apply", 10)),
        (["--apply", "--limit", "3"], ("apply", 3)),
        (["--resume", "--limit", "2"], ("resume", 2)),
    ],
)
def test_cli_dispatches_one_mode_and_prints_json(monkeypatch, capsys, argv, expected_call):
    service = FakeService(_result())
    monkeypatch.setattr(sync_wiki, "_build_service", lambda read_only: service)

    exit_code = sync_wiki.main(argv)

    assert exit_code == 0
    assert service.calls == [expected_call]
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed"
    assert payload["succeeded"] == 3


def test_check_builds_read_only_service(monkeypatch):
    observed = []
    service = FakeService(_result(status="checked"))
    monkeypatch.setattr(
        sync_wiki,
        "_build_service",
        lambda read_only: observed.append(read_only) or service,
    )

    assert sync_wiki.main(["--check"]) == 0
    assert observed == [True]


def test_cli_returns_nonzero_for_dependency_or_run_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        sync_wiki,
        "_build_service",
        lambda read_only: (_ for _ in ()).throw(RuntimeError("dependency unavailable")),
    )
    assert sync_wiki.main(["--apply"]) == 1
    assert "dependency unavailable" in capsys.readouterr().err

    service = FakeService(_result(status="completed_with_errors", errors=("graph failed",)))
    monkeypatch.setattr(sync_wiki, "_build_service", lambda read_only: service)
    assert sync_wiki.main(["--apply"]) == 1


def test_already_running_is_a_clean_no_op(monkeypatch):
    service = FakeService(_result(status="already_running"))
    monkeypatch.setattr(sync_wiki, "_build_service", lambda read_only: service)

    assert sync_wiki.main(["--apply"]) == 0

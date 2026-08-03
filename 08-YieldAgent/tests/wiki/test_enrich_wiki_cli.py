import json
from types import SimpleNamespace

import pytest

import enrich_wiki


pytestmark = pytest.mark.no_server


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--check", "--apply"],
        ["--apply", "--vault", "/tmp/vault"],
        [
            "--apply",
            "--allow-external-llm",
            "--vault",
            "/tmp/vault",
            "--limit",
            "0",
        ],
        ["--check", "--vault", "/tmp/vault", "--product", "4SS"],
        ["--check", "--vault", "/tmp/vault", "--source-index", "syld_*"],
    ],
)
def test_parser_rejects_unsafe_arguments(argv):
    with pytest.raises(SystemExit):
        enrich_wiki._parse_args(argv)


def test_cli_check_dispatches_without_external_opt_in(monkeypatch, capsys):
    calls = []
    service = SimpleNamespace(
        check=lambda selector: calls.append(("check", selector))
        or SimpleNamespace(status="checked", errors=()),
    )
    monkeypatch.setattr(enrich_wiki, "_build_service", lambda args, read_only: service)

    code = enrich_wiki.main(["--check", "--vault", "/tmp/vault"])

    assert code == 0
    assert calls == [("check", None)]
    assert json.loads(capsys.readouterr().out)["status"] == "checked"


def test_cli_apply_passes_exact_selector_and_limit(monkeypatch, capsys):
    calls = []
    service = SimpleNamespace(
        apply=lambda limit, selector: calls.append((limit, selector))
        or SimpleNamespace(status="completed", errors=()),
    )
    monkeypatch.setattr(enrich_wiki, "_build_service", lambda args, read_only: service)

    code = enrich_wiki.main(
        [
            "--apply",
            "--allow-external-llm",
            "--vault",
            "/tmp/vault",
            "--limit",
            "3",
            "--product",
            "4SS",
            "--fail-type",
            "EASY",
            "--cause-oper",
            "PRE METAL CLN",
        ]
    )

    assert code == 0
    limit, selector = calls[0]
    assert limit == 3
    assert (selector.product, selector.fail_type, selector.cause_oper) == (
        "4SS",
        "EASY",
        "PRE METAL CLN",
    )
    assert json.loads(capsys.readouterr().out)["status"] == "completed"


def test_cli_apply_without_limit_processes_all_concepts(monkeypatch, capsys):
    calls = []
    service = SimpleNamespace(
        apply=lambda limit, selector: calls.append((limit, selector))
        or SimpleNamespace(status="completed", errors=()),
    )
    monkeypatch.setattr(enrich_wiki, "_build_service", lambda args, read_only: service)

    code = enrich_wiki.main(
        ["--apply", "--allow-external-llm", "--vault", "/tmp/vault"]
    )

    assert code == 0
    assert calls == [(None, None)]

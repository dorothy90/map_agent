import asyncio
import json


def test_budget_partial_cannot_pass_normal_case():
    from harness.evals.assertions import evaluate_case

    result = evaluate_case(
        {"id": "normal", "expected_outcome": "success"},
        {"status": "partial", "stop_reason": "token_budget", "calls": [], "observations": []},
    )
    assert result["passed"] is False
    assert "status_not_completed" in result["reasons"]


def test_failed_required_tool_cannot_pass():
    from harness.evals.assertions import evaluate_case

    result = evaluate_case(
        {"id": "python", "expected_outcome": "success", "requires_tools": ["run_python"]},
        {"status": "completed", "calls": [{"tool_name": "run_python", "status": "failed"}], "observations": []},
    )
    assert result["passed"] is False
    assert "required_tool_failed:run_python" in result["reasons"]


def test_unrelated_evidence_cannot_approve_release(tmp_path):
    from harness.evals.assertions import validate_review_manifest

    unrelated = tmp_path / "README.md"
    unrelated.write_text("old evidence")
    current = {"code_hash": "code", "model_hash": "model"}
    review = {
        "numeric_match": {
            "passed": True,
            "evidence": [{"path": str(unrelated), "sha256": "wrong", "code_hash": "old", "model_hash": "model", "run_ids": ["r1"], "numeric_hashes": ["n1"]}],
        }
    }
    valid, reasons = validate_review_manifest(review, ("numeric_match",), current, {"r1"}, {"n1"}, set())
    assert valid is False
    assert reasons


def test_fault_injection_expected_failure_is_preserved():
    from harness.evals.assertions import evaluate_case

    result = evaluate_case(
        {"id": "provider_fault", "expected_outcome": "failure", "expected_statuses": ["failed"]},
        {"status": "failed", "stop_reason": "provider_429", "calls": [], "observations": []},
    )
    assert result["passed"] is True


def test_numeric_and_artifact_checks_require_bound_hashes():
    from harness.evals.assertions import evaluate_case

    case = {"id": "report", "expected_outcome": "success", "numeric_checks": [{"id": "weekly"}], "artifact_checks": [{"id": "deck"}]}
    run = {"status": "completed", "calls": [], "observations": [], "numeric_evidence": {"weekly": {"passed": True}}, "artifact_evidence": {"deck": {"passed": True}}}
    result = evaluate_case(case, run)
    assert result["passed"] is False
    assert "numeric_evidence_unbound:weekly" in result["reasons"]
    assert "artifact_evidence_unbound:deck" in result["reasons"]


def test_build_identity_excludes_env_secrets(monkeypatch, tmp_path):
    from harness.evals.run import capture_build_identity

    (tmp_path / ".env").write_text("SECRET=first")
    (tmp_path / "module.py").write_text("VALUE = 1")
    monkeypatch.setattr("harness.evals.run.PROJECT_ROOT", tmp_path)
    first = capture_build_identity(model={"model": "fixture", "temperature": 0})
    (tmp_path / ".env").write_text("SECRET=second")
    second = capture_build_identity(model={"model": "fixture", "temperature": 0})
    assert first["code_hash"] == second["code_hash"]
    assert first["model_hash"] == second["model_hash"]
    assert "SECRET" not in json.dumps(first)


def test_eval_reports_final_state_when_worker_exits_without_finishing(monkeypatch, tmp_path):
    from harness.config import Settings
    from harness.control import RunController
    from harness.evals.run_live import evaluate
    from harness.store import HarnessStore

    async def exit_early(self, run):
        return

    monkeypatch.setattr(RunController, "_run", exit_early)

    async def scenario():
        output = tmp_path / "report.json"
        report = await evaluate(["fixture"], output, no_domain_tools=True, data_origin="fixture")
        store = HarnessStore(Settings.from_env().mongo_uri, report["database"])
        try:
            case = report["cases"][0]
            saved = await store.get_run("live-eval", case["run_id"])
            assert case["status"] == saved["status"] == "cancelled"
            assert case["evaluation_error"] == "nonterminal_worker_exit"
            assert json.loads(output.read_text())["cases"][0] == case
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_release_and_live_reports_fingerprint_same_provider_settings(monkeypatch, tmp_path):
    import pytest
    from types import SimpleNamespace
    from harness.config import Settings
    from harness.evals import run as release, run_live
    class BoundaryReached(Exception):
        pass
    payloads = []
    def capture(model=None):
        payloads.append(model)
        return {'model_hash': release._hash(model)}
    async def ready():
        return {'fixture': {'ok': True}}
    async def stop(*args, **kwargs):
        raise BoundaryReached
    class Store:
        def __init__(self, *args):
            self.db = SimpleNamespace(name='fixture')
        setup = stop
    settings = Settings(model='fixture-model', base_url='https://fixture.invalid/api', temperature=0.5)
    monkeypatch.setattr(release, 'capture_build_identity', capture)
    monkeypatch.setattr(release, 'check', ready)
    monkeypatch.setattr(release, 'evaluate', stop)
    monkeypatch.setattr(run_live, 'HarnessStore', Store)
    monkeypatch.setattr(run_live.Settings, 'from_env', classmethod(lambda cls: settings))
    monkeypatch.setattr(run_live, 'load_dotenv', lambda: None)
    args = SimpleNamespace(output=tmp_path / 'release', settings=settings, repetitions=1, review_manifest=None)
    with pytest.raises(BoundaryReached):
        asyncio.run(release.run(args))
    with pytest.raises(BoundaryReached):
        asyncio.run(run_live.evaluate(['fixture'], tmp_path / 'live.json'))
    assert payloads[0] == payloads[1]
    assert payloads[0]['base_url'] == settings.base_url
    assert 'api_key' not in payloads[0]


def test_frontend_mjs_changes_build_identity(monkeypatch, tmp_path):
    from harness.evals.run import capture_build_identity
    monkeypatch.setattr('harness.evals.run.PROJECT_ROOT', tmp_path)
    source = tmp_path / 'frontend.mjs'
    source.write_text('export const version = 1;')
    before = capture_build_identity()
    source.write_text('export const version = 2;')
    assert capture_build_identity()['code_hash'] != before['code_hash']

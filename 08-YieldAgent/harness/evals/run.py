"""Release evaluation with build-bound evidence gates."""
from __future__ import annotations
import argparse, asyncio, hashlib, json, subprocess
from pathlib import Path
from .assertions import evaluate_case, validate_review_manifest
from .preflight import check
from .run_live import evaluate

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REVIEW_AREAS = ("numeric_match", "fault_suite", "mining_live", "browser_live", "memory_30_turns", "legacy_comparison", "independent_domain_review")

def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

def capture_build_identity(model=None):
    root, files = PROJECT_ROOT, []
    allowed = {".py", ".json", ".md", ".toml", ".yaml", ".yml", ".ts", ".tsx", ".js", ".mjs", ".css", ".html"}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if (not path.is_file() or path.suffix not in allowed or path.name.startswith(".env") or
                any(p in {".git", ".venv", "node_modules", "__pycache__", "outputs"} for p in rel.parts)):
            continue
        files.append((str(rel), hashlib.sha256(path.read_bytes()).hexdigest()))
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, capture_output=True, text=True).stdout)
    except (OSError, subprocess.CalledProcessError):
        sha, dirty = None, False
    return {"git_sha": sha, "dirty": dirty, "code_hash": _hash(files), "model_hash": _hash(model or {}),
            "config_hash": _hash([x for x in files if x[0].endswith((".json", ".toml", ".yaml", ".yml"))]),
            "tools_hash": _hash([x for x in files if "/tools/" in x[0]]),
            "skills_hash": _hash([x for x in files if "/skills/" in x[0]])}

async def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    checks = await check()
    manifest = {"schema_version": "harness-release/v2", "data_origin": "live", "checks": checks, "cases": [], "release_ready": False}
    try:
        if not all(c["ok"] for c in checks.values() if not c.get("optional")):
            manifest["blocked"] = "Preflight failed"
            return manifest
        cases = json.loads(Path(__file__).with_name("cases.json").read_text())
        settings = args.settings
        model = {"model": settings.model, "base_url": settings.base_url, "temperature": settings.temperature, "max_output_tokens": settings.max_output_tokens, "reasoning_effort": settings.reasoning_effort}
        manifest["build"] = capture_build_identity(model)
        for repeat in range(args.repetitions):
            for case in cases:
                if case.get("automated", True) is False:
                    manifest["cases"].append({"id": case["id"], "repeat": repeat + 1, "passed": False, "reasons": ["explicit_fault_injection_not_executed"]})
                    continue
                report = await evaluate(case.get("turns", case.get("queries", [])), args.output / f"{case['id']}-{repeat + 1}.json")
                results = report["cases"][-1:] if case.get("turns") else report["cases"]
                for result in results:
                    manifest["cases"].append({"id": case["id"], "repeat": repeat + 1, "run_id": result.get("run_id"), **evaluate_case(case, result)})
        scored = [c for c in manifest["cases"] if "explicit_fault_injection_not_executed" not in c.get("reasons", [])]
        manifest["scenario_pass_rate"] = sum(c["passed"] for c in scored) / len(scored) if scored else 0
        review = json.loads(args.review_manifest.read_text()) if args.review_manifest else {}
        manifest["review"] = review
        run_ids = {c["run_id"] for c in manifest["cases"] if c.get("run_id")}
        numeric_hashes, artifact_hashes = set(), set()
        for path in args.output.glob("*.json"):
            record = json.loads(path.read_text())
            for item in record.get("cases", []):
                numeric_hashes.update(v.get("sha256") for v in (item.get("numeric_evidence") or {}).values() if v.get("sha256"))
                artifact_hashes.update(v.get("sha256") for v in (item.get("artifact_evidence") or {}).values() if v.get("sha256"))
        manifest['unavailable_optional_services'] = [name for name, check in checks.items() if check.get('optional') and not check['ok']]
        areas = tuple(area for area in REVIEW_AREAS if area != 'mining_live' or checks.get('mining', {}).get('ok'))
        manifest['release_scope'] = 'connected_domains'
        covered, reasons = validate_review_manifest(review, areas, manifest["build"], run_ids, numeric_hashes, artifact_hashes)
        manifest["review_reasons"] = reasons
        automated = [case for case in cases if case.get("automated", True)]
        per_case = all(len([c for c in manifest["cases"] if c["id"] == case["id"]]) >= args.repetitions
            and all(c["passed"] for c in manifest["cases"] if c["id"] == case["id"]) for case in automated)
        manifest["release_ready"] = covered and per_case and manifest["scenario_pass_rate"] >= .9 and args.repetitions >= 3
        if not manifest["release_ready"]:
            manifest["blocked"] = "Scenario gate or build-bound independent review is incomplete"
    finally:
        (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest

if __name__ == "__main__":
    from harness.config import Settings
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-live", action="store_true", required=True)
    parser.add_argument("--allow-external-data", action="store_true")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-manifest", type=Path)
    args = parser.parse_args()
    if not args.allow_external_data: parser.error("Live DB data is sent to the configured model. Explicit --allow-external-data is required.")
    if args.repetitions < 1: parser.error("repetitions must be positive")
    args.settings = Settings.from_env()
    result = asyncio.run(run(args))
    print(json.dumps({"release_ready": result["release_ready"], "blocked": result.get("blocked"), "scenario_pass_rate": result.get("scenario_pass_rate")}, ensure_ascii=False))
    raise SystemExit(0 if result["release_ready"] else 1)

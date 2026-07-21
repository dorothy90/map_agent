from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

import frontmatter

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from e2e_client import Session, server_is_up
from control_knowledge_curator import AGENT_REQUIRED_SECTIONS
from control_knowledge_registry import AGENT_CONTROL_PROFILES
from control_knowledge_validator import scan_bundle


def validate_ledger_entries(entries: list[dict]) -> None:
    latest_by_fingerprint = {
        str(entry.get("fingerprint") or ""): entry for entry in entries
    }
    failed = [
        entry
        for entry in latest_by_fingerprint.values()
        if entry.get("action") in {"invalid_decision", "failed"}
    ]
    if failed:
        raise SystemExit(f"curation failures recorded: {len(failed)}")
    successful = {
        "created",
        "updated",
        "proposal",
        "no_change",
    }
    if not any(
        entry.get("action") in successful
        for entry in latest_by_fingerprint.values()
    ):
        raise SystemExit("no successful curation decision was recorded")


def ledger_covers_candidates(
    candidate_fingerprints: set[str], entries: list[dict]
) -> bool:
    processed = {str(entry.get("fingerprint") or "") for entry in entries}
    return bool(candidate_fingerprints) and candidate_fingerprints <= processed


def validate_operational_pages(root: Path) -> None:
    agents = [
        path
        for path in (root / "wiki/agents").glob("*.md")
        if path.name != "index.md"
    ]
    if len(agents) != 9:
        raise SystemExit(f"expected 9 Agent pages, found {len(agents)}")
    for path in agents:
        post = frontmatter.load(path)
        headings = {
            line[3:].strip()
            for line in post.content.splitlines()
            if line.startswith("## ")
        }
        missing = set(AGENT_REQUIRED_SECTIONS) - headings
        if missing:
            raise SystemExit(
                f"operational sections missing in {path.name}: {sorted(missing)}"
            )
        relations = post.metadata.get("relations") or {}
        if "[[workflows/orchestration-graph]]" not in relations.get(
            "participates_in", []
        ):
            raise SystemExit(f"workflow relation missing in {path.name}")
        agent_id = path.stem.replace("-", "_")
        expected_contracts = {
            f"[[{contract}]]"
            for contract in AGENT_CONTROL_PROFILES[agent_id].output_contracts
        }
        actual_contracts = set(relations.get("uses_contract", []))
        if actual_contracts != expected_contracts:
            raise SystemExit(
                f"contract relations differ in {path.name}: "
                f"expected={sorted(expected_contracts)} "
                f"actual={sorted(actual_contracts)}"
            )
        if relations.get("uses_hitl_contract") != [
            "[[contracts/hitl-contracts]]"
        ]:
            raise SystemExit(f"HITL contract relation missing in {path.name}")


def main() -> int:
    root_value = os.getenv("CONTROL_KNOWLEDGE_ROOT", "").strip()
    if not root_value:
        raise SystemExit("CONTROL_KNOWLEDGE_ROOT must point to the live test bundle")
    root = Path(root_value).resolve()
    if not server_is_up():
        raise SystemExit("live agent server is not reachable")

    domain_wiki = Path(__file__).resolve().parent.parent / "wiki"
    before = {
        path: path.read_bytes() for path in domain_wiki.rglob("*") if path.is_file()
    }
    result = Session().turn(
        "4SS 최근 3주 수율을 실제 데이터로 보여줘", timeout=300
    )
    if not any(event.get("type") == "stream_end" for event in result.sse_events):
        raise SystemExit("live turn did not reach stream_end")

    deadline = time.time() + 900
    ledger_entries: list[dict] = []
    while time.time() < deadline:
        candidates = list((root / "raw/candidates").glob("*.json"))
        ledger = root / "raw/curation-ledger.jsonl"
        if candidates and ledger.exists() and ledger.stat().st_size:
            ledger_entries = [
                json.loads(line)
                for line in ledger.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if ledger_covers_candidates(
                {path.stem for path in candidates}, ledger_entries
            ):
                break
        time.sleep(1)
    else:
        raise SystemExit("not every control candidate reached the ledger")

    forbidden_keys = {
        "rows",
        "query",
        "messages",
        "artifact_payload",
        "data",
        "html",
        "base64",
        "bytes",
        "content",
        "prompt",
        "sql",
    }

    def assert_redacted(value, path_name):
        if isinstance(value, dict):
            bad = forbidden_keys & {str(key).lower() for key in value}
            if bad:
                raise SystemExit(
                    f"forbidden payload keys {sorted(bad)} in {path_name}"
                )
            for item in value.values():
                assert_redacted(item, path_name)
        elif isinstance(value, list):
            for item in value:
                assert_redacted(item, path_name)
        elif isinstance(value, str) and "4SS" in value:
            raise SystemExit(f"controlled domain sentinel leaked into {path_name}")

    for path in candidates:
        assert_redacted(json.loads(path.read_text(encoding="utf-8")), path.name)
    issues = scan_bundle(root)
    if issues:
        raise SystemExit(
            "bundle lint failed: "
            + json.dumps([issue.__dict__ for issue in issues], ensure_ascii=False)
        )
    validate_operational_pages(root)
    compiled_types = {
        frontmatter.load(path).metadata.get("type")
        for path in (root / "wiki").rglob("*.md")
        if path.name != "index.md"
    }
    if not {"Agent", "Workflow", "Contract"}.issubset(compiled_types):
        raise SystemExit("compiled control wiki is missing Agent/Workflow/Contract pages")
    if any(
        "4SS" in path.read_text(encoding="utf-8")
        for path in (root / "wiki").rglob("*.md")
    ):
        raise SystemExit("controlled domain sentinel leaked into compiled control wiki")
    validate_ledger_entries(ledger_entries)
    version_state = root / "raw/live-verifier-state.json"
    current_versions = {
        str(post.metadata["page_id"]): int(post.metadata["version"])
        for path in (root / "wiki").rglob("*.md")
        if path.name != "index.md"
        for post in [frontmatter.load(path)]
        if post.metadata.get("source_status") == "code-backed"
    }
    if version_state.exists():
        previous_versions = json.loads(version_state.read_text(encoding="utf-8"))
        for page_id in set(previous_versions) & set(current_versions):
            if previous_versions[page_id] != current_versions[page_id]:
                raise SystemExit(
                    f"unchanged code-backed page version advanced: {page_id}"
                )
    version_state.write_text(
        json.dumps(current_versions, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    after = {
        path: path.read_bytes() for path in domain_wiki.rglob("*") if path.is_file()
    }
    if before != after:
        raise SystemExit("existing domain wiki changed during control-knowledge run")
    print(f"PASS candidates={len(candidates)} trace={result.trace_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

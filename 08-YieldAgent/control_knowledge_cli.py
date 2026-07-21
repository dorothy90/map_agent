from __future__ import annotations

import argparse
import os
from pathlib import Path

import frontmatter

from common import get_llm
from control_knowledge_collector import (
    current_system_snapshot,
    system_snapshot_candidates,
)
from control_knowledge_curator import ControlKnowledgeCurator
from control_knowledge_store import ControlKnowledgeStore
from control_knowledge_validator import scan_bundle


def _root(value: str | None) -> Path:
    return Path(
        value
        or os.getenv("CONTROL_KNOWLEDGE_ROOT")
        or Path(__file__).resolve().parent / "multiagent_knowledge"
    ).resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("lint")
    sub.add_parser("snapshot")
    sub.add_parser("curate-once")
    approve = sub.add_parser("approve")
    approve.add_argument("proposal_id")
    args = parser.parse_args(argv)
    root = _root(args.root)
    store = ControlKnowledgeStore(root)

    if args.command == "lint":
        issues = scan_bundle(root)
        for issue in issues:
            print(f"[{issue.code}] {issue.path}: {issue.message}")
        return 1 if issues else 0
    if args.command == "snapshot":
        paths = [
            store.save_candidate(item)
            for item in system_snapshot_candidates(current_system_snapshot())
        ]
        print(f"saved={len(paths)}")
        return 0
    if args.command == "curate-once":
        curator = ControlKnowledgeCurator(
            store, get_llm(os.getenv("CONTROL_KNOWLEDGE_MODEL") or None)
        )
        paths = store.pending_candidates()
        for path in paths:
            curator.curate(store.load_candidate(path))
        print(f"processed={len(paths)}")
        return 0
    if args.command == "approve":
        proposal_path = store.review_queue / f"{args.proposal_id}.md"
        if not proposal_path.exists():
            parser.error("proposal does not exist")
        fingerprint = str(
            frontmatter.load(proposal_path).metadata["candidate_fingerprint"]
        )
        candidate_path = store.candidates / f"{fingerprint}.json"
        store.approve_proposal(args.proposal_id, store.load_candidate(candidate_path))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

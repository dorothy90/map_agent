"""Cron-ready incremental synchronization from OpenSearch to the Wiki Vault."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, is_dataclass

from dotenv import load_dotenv


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incrementally synchronize the Wiki")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="read-only change preview")
    mode.add_argument("--apply", action="store_true", help="scan, enqueue, and process")
    mode.add_argument("--resume", action="store_true", help="resume queued jobs without scan")
    parser.add_argument("--limit", type=_positive_int, help="jobs processed in this run")
    args = parser.parse_args(argv)
    if args.check and args.limit is not None:
        parser.error("--limit is only valid with --apply or --resume")
    if not args.check and args.limit is None:
        args.limit = 10
    return args


def _build_service(*, read_only: bool):
    load_dotenv(override=False)
    from fail_history_tools import _get_opensearch_client
    from wiki_config import (
        initialize_wiki_vault,
        resolve_wiki_paths,
        validate_wiki_vault,
    )
    from wiki_job_store import WikiJobStore
    from wiki_sync import OpenSearchWikiScanner, WikiSyncService
    from wiki_summarizer import synthesize_concept_from_docs
    import wiki_store

    paths = resolve_wiki_paths()
    if read_only:
        if not paths.root.is_dir():
            raise RuntimeError(f"Wiki Vault does not exist: {paths.root}")
        job_store = None
    else:
        initialize_wiki_vault(paths)
        validate_wiki_vault(paths)
        job_store = WikiJobStore.from_env()
        job_store.ensure_indexes()
    index = os.getenv("OPENSEARCH_INDEX", "fail-history")
    scanner = OpenSearchWikiScanner(_get_opensearch_client(), index)
    return WikiSyncService(
        scanner=scanner,
        job_store=job_store,
        manifest_path=paths.manifest,
        index=index,
        synthesize=synthesize_concept_from_docs,
        wiki_store=wiki_store,
        materialize=wiki_store.materialize_obsidian_wiki,
    )


def _payload(result) -> dict:
    if is_dataclass(result):
        return asdict(result)
    return dict(vars(result))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        service = _build_service(read_only=args.check)
        if args.check:
            result = service.check()
        elif args.apply:
            result = service.apply(args.limit)
        else:
            result = service.resume(args.limit)
    except Exception as exc:
        print(f"error: {' '.join(str(exc).split())[:500]}", file=sys.stderr)
        return 1
    print(json.dumps(_payload(result), ensure_ascii=False, sort_keys=True))
    return 1 if result.status == "completed_with_errors" else 0


if __name__ == "__main__":
    sys.exit(main())

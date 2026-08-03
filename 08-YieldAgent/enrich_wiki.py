"""One-command content-only OpenSearch enrichment for the Obsidian Wiki."""
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
    parser = argparse.ArgumentParser(description="Enrich Wiki Concepts with related evidence")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="read-only preview")
    mode.add_argument("--apply", action="store_true", help="retrieve, judge, and attach")
    parser.add_argument("--allow-external-llm", action="store_true")
    parser.add_argument("--vault")
    parser.add_argument("--source-index", default="syld_gpt_2067627")
    parser.add_argument("--limit", type=_positive_int)
    parser.add_argument("--product")
    parser.add_argument("--fail-type")
    parser.add_argument("--cause-oper")
    args = parser.parse_args(argv)
    if args.apply and not args.allow_external_llm:
        parser.error("--apply requires --allow-external-llm")
    if args.check and args.limit is not None:
        parser.error("--limit is only valid with --apply")
    selected = (args.product, args.fail_type, args.cause_oper)
    if any(selected) and not all(selected):
        parser.error("--product, --fail-type, and --cause-oper must be supplied together")
    if any(token in args.source_index for token in ("*", "?", ",")):
        parser.error("--source-index must be one exact index name")
    if args.apply and args.limit is None:
        args.limit = 10
    return args


def _build_service(args: argparse.Namespace, *, read_only: bool):
    load_dotenv(override=False)
    if args.vault:
        os.environ["WIKI_VAULT_PATH"] = args.vault
    from common import get_llm
    from fail_history_tools import _get_embedding, _get_opensearch_client
    from wiki_config import (
        initialize_wiki_vault,
        resolve_wiki_paths,
        validate_wiki_vault,
    )
    from wiki_evidence_enrichment import (
        EvidenceManifestStore,
        OpenSearchEvidenceRetriever,
        StructuredEvidenceJudge,
        WikiEvidenceEnrichmentService,
        read_concept_snapshots,
    )

    paths = resolve_wiki_paths()
    if read_only:
        if not paths.root.is_dir():
            raise RuntimeError(f"Wiki Vault does not exist: {paths.root}")
    else:
        initialize_wiki_vault(paths)
        validate_wiki_vault(paths)
    client = _get_opensearch_client()
    retriever = OpenSearchEvidenceRetriever(
        client,
        args.source_index,
        (lambda _: (_ for _ in ()).throw(RuntimeError("embedding disabled in check")))
        if read_only
        else _get_embedding,
    )
    judge = None
    if not read_only:
        model_name = (
            os.getenv("WIKI_EVIDENCE_MODEL")
            or os.getenv("WIKI_SUMMARIZE_MODEL")
            or os.getenv("RETRIEVE_CHAIN_MODEL")
            or "openai/gpt-oss-120b"
        )
        judge = StructuredEvidenceJudge(get_llm(model_name), model_name)
    import wiki_store

    return WikiEvidenceEnrichmentService(
        source_index=args.source_index,
        retriever=retriever,
        judge=judge,
        manifest_store=EvidenceManifestStore(
            paths, paths.state_dir / "evidence-manifest.json"
        ),
        read_concepts=lambda selector: read_concept_snapshots(paths, selector),
        replace_related_evidence=wiki_store.replace_related_evidence,
        write_source=wiki_store.upsert_related_evidence_source,
        refresh_backlinks=wiki_store.refresh_related_evidence_backlinks,
        materialize=wiki_store.materialize_obsidian_wiki,
    )


def _payload(result) -> dict:
    if is_dataclass(result):
        return asdict(result)
    return dict(vars(result))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        from wiki_evidence_enrichment import EvidenceSelector

        selector = (
            EvidenceSelector(args.product, args.fail_type, args.cause_oper)
            if args.product
            else None
        )
        service = _build_service(args, read_only=args.check)
        result = (
            service.check(selector)
            if args.check
            else service.apply(args.limit, selector)
        )
    except Exception as exc:
        print(f"error: {' '.join(str(exc).split())[:500]}", file=sys.stderr)
        return 1
    print(json.dumps(_payload(result), ensure_ascii=False, sort_keys=True))
    return 1 if result.status == "completed_with_errors" else 0


if __name__ == "__main__":
    sys.exit(main())

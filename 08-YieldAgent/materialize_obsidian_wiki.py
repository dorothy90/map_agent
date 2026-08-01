"""Preview or apply deterministic Obsidian Wiki graph materialization."""
from __future__ import annotations

import argparse
import sys

from wiki_config import (
    initialize_wiki_vault,
    resolve_wiki_paths,
    validate_wiki_vault,
)
from wiki_materializer import materialize_wiki


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Materialize Obsidian Wiki links")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="preview changes")
    mode.add_argument("--apply", action="store_true", help="apply changes")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = resolve_wiki_paths()
    if args.apply:
        initialize_wiki_vault(paths)
        validate_wiki_vault(paths)
    elif not paths.root.is_dir():
        print(f"error: Wiki Vault does not exist: {paths.root}", file=sys.stderr)
        return 1

    report = materialize_wiki(paths, apply=args.apply)
    for status, values in (
        ("created", report.created),
        ("modified", report.modified),
        ("deleted", report.deleted),
        ("unchanged", report.unchanged),
        ("error", report.errors),
    ):
        for value in values:
            print(f"{status}: {value}")
    print(
        "summary: "
        f"created={len(report.created)} "
        f"modified={len(report.modified)} "
        f"deleted={len(report.deleted)} "
        f"errors={len(report.errors)}"
    )
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())

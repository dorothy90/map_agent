from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import frontmatter

from control_knowledge_models import PageType

REQUIRED = frozenset(
    {
        "type",
        "page_id",
        "title",
        "description",
        "routing_summary",
        "status",
        "owner",
        "source_status",
        "agent_use",
        "llmwiki_status",
        "llmwiki_owner",
        "llmwiki_source_status",
        "llmwiki_agent_use",
        "sensitivity",
        "last_reviewed",
        "review_cycle",
        "version",
        "relations",
        "evidence_refs",
    }
)
ALLOWED_STATUS = frozenset(
    {"draft", "reviewed", "current", "stale", "deprecated", "archived", "blocked"}
)
WIKILINK = re.compile(r"^\[\[([a-z0-9][a-z0-9_./-]*)\]\]$")


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str


def _issue(code: str, path: Path, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=str(path), message=message)


def _frontmatter(path: Path) -> tuple[dict[str, Any], str, list[ValidationIssue]]:
    try:
        post = frontmatter.load(path)
        return dict(post.metadata), post.content or "", []
    except Exception as exc:
        return {}, "", [_issue("invalid_frontmatter", path, str(exc))]


def validate_page(path: Path, wiki_root: Path) -> list[ValidationIssue]:
    metadata, body, issues = _frontmatter(path)
    if issues:
        return issues
    relative = path.relative_to(wiki_root)
    if relative == Path("index.md"):
        extra = set(metadata) - {"okf_version"}
        if extra:
            issues.append(
                _issue("root_index_extra_frontmatter", path, f"extra={sorted(extra)}")
            )
        if set(metadata) != {"okf_version"}:
            issues.append(
                _issue(
                    "root_index_missing_version",
                    path,
                    "root index needs okf_version only",
                )
            )
        return issues
    if relative == Path("log.md"):
        if metadata:
            issues.append(
                _issue("root_log_frontmatter", path, "root log must not have frontmatter")
            )
        return issues
    if path.name == "index.md":
        if metadata:
            issues.append(
                _issue(
                    "nested_index_frontmatter",
                    path,
                    "nested index must not have frontmatter",
                )
            )
        return issues

    missing = REQUIRED - set(metadata)
    if missing:
        issues.append(_issue("missing_governance", path, f"missing={sorted(missing)}"))
    expected_id = relative.with_suffix("").as_posix()
    if metadata.get("page_id") != expected_id:
        issues.append(_issue("page_id_path_mismatch", path, f"expected={expected_id}"))
    if metadata.get("type") not in {item.value for item in PageType}:
        issues.append(_issue("invalid_type", path, str(metadata.get("type"))))
    if metadata.get("status") not in ALLOWED_STATUS:
        issues.append(_issue("invalid_status", path, str(metadata.get("status"))))
    first_h1 = next(
        (line[2:].strip() for line in body.splitlines() if line.startswith("# ")), ""
    )
    if metadata.get("title") and first_h1 != metadata.get("title"):
        issues.append(_issue("title_h1_mismatch", path, f"h1={first_h1!r}"))
    return issues


def scan_bundle(bundle_root: Path) -> list[ValidationIssue]:
    wiki_root = bundle_root / "wiki"
    if not wiki_root.exists():
        return [_issue("missing_wiki_root", wiki_root, "wiki directory does not exist")]
    issues: list[ValidationIssue] = []
    page_ids: set[str] = set()
    relations: list[tuple[Path, str]] = []
    for path in sorted(wiki_root.rglob("*.md")):
        issues.extend(validate_page(path, wiki_root))
        if path.name == "index.md":
            continue
        metadata, _, parse_issues = _frontmatter(path)
        if parse_issues:
            continue
        page_id = str(metadata.get("page_id") or "")
        if page_id in page_ids:
            issues.append(_issue("duplicate_page_id", path, page_id))
        elif page_id:
            page_ids.add(page_id)
        for values in (metadata.get("relations") or {}).values():
            for value in values or []:
                match = WIKILINK.fullmatch(str(value))
                if not match:
                    issues.append(_issue("invalid_relation", path, str(value)))
                else:
                    relations.append((path, match.group(1)))
    for path, target in relations:
        if target not in page_ids:
            issues.append(_issue("broken_relation", path, target))
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)
    issues = scan_bundle(args.bundle)
    for issue in issues:
        print(f"[{issue.code}] {issue.path}: {issue.message}")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())

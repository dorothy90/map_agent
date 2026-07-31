"""Wiki vault v2 → v3 frontmatter 마이그레이션.

plan v3 §신규 파일 — 기존 wiki/* frontmatter에 신규 필드 추가:
  - lifecycle: status, last_active, stale_after_days
  - confidence/citations 기본값
  - body_versions, evidence_diversity_score, unique_doc_ids (concept만)
기존 값은 보존. 누락 필드만 추가.

CLI:
  python migrate_v2_to_v3.py --vault wiki/ [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import frontmatter
from wiki_config import resolve_wiki_paths

_STALE_DEFAULT = int(os.getenv("WIKI_STALE_AFTER_DAYS", "30"))


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def migrate_node(path: Path, dry_run: bool) -> dict:
    """단일 노드 마이그레이션. Returns {added: list[str], skipped: bool}."""
    try:
        post = frontmatter.load(path)
    except Exception as e:
        return {"error": f"parse fail: {e}", "added": [], "skipped": True}
    md = dict(post.metadata)
    ntype = md.get("type", "")
    added: list[str] = []
    now = _now_iso()

    # lifecycle (모든 type)
    if "status" not in md:
        md["status"] = "active"
        added.append("status")
    if "last_active" not in md:
        md["last_active"] = md.get("updated", now)
        added.append("last_active")
    if "stale_after_days" not in md:
        md["stale_after_days"] = _STALE_DEFAULT
        added.append("stale_after_days")

    # confidence/citations (모든 type)
    if "confidence" not in md:
        # episode 0.5 중립, concept 0.0 (아직 합성 안 됨), alias 1.0 확정
        defaults = {"episode": 0.5, "concept": 0.0, "alias": 1.0}
        md["confidence"] = defaults.get(ntype, 0.0)
        added.append("confidence")
    if "citations" not in md:
        md["citations"] = []
        added.append("citations")

    # concept만 — body_versions/evidence
    if ntype == "concept":
        if "body_versions" not in md:
            md["body_versions"] = []
            added.append("body_versions")
        if "evidence_diversity_score" not in md:
            md["evidence_diversity_score"] = 0.0
            added.append("evidence_diversity_score")
        if "unique_doc_ids" not in md:
            md["unique_doc_ids"] = 0
            added.append("unique_doc_ids")

    if not added:
        return {"added": [], "skipped": True}

    if not dry_run:
        post.metadata = md
        # atomic write (.tmp + replace)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(frontmatter.dumps(post), encoding="utf-8")
        os.replace(tmp, path)

    return {"added": added, "skipped": False, "type": ntype}


def main() -> int:
    p = argparse.ArgumentParser(description="wiki vault v2 → v3 frontmatter migration")
    default_vault = str(resolve_wiki_paths().root)
    p.add_argument("--vault", default=default_vault)
    p.add_argument("--dry-run", action="store_true", help="변경 없이 보고만")
    args = p.parse_args()

    vault = Path(args.vault).resolve()
    if not vault.exists():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2

    summary = {"episode": {"changed": 0, "skipped": 0},
               "concept": {"changed": 0, "skipped": 0},
               "alias": {"changed": 0, "skipped": 0},
               "errors": []}

    for kind in ("episodes", "concepts", "aliases"):
        d = vault / kind
        if not d.exists():
            continue
        for path in sorted(d.glob("*.md")):
            r = migrate_node(path, args.dry_run)
            if "error" in r:
                summary["errors"].append({"path": str(path), "error": r["error"]})
                continue
            t = r.get("type", kind[:-1])  # episodes → episode
            if r["skipped"]:
                summary.setdefault(t, {"changed": 0, "skipped": 0})["skipped"] += 1
            else:
                summary.setdefault(t, {"changed": 0, "skipped": 0})["changed"] += 1
                if args.dry_run:
                    print(f"[would add] {path.name}: {r['added']}")
                else:
                    print(f"[migrated] {path.name}: +{r['added']}")

    print()
    print("=== summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("\n(dry-run — 실제 변경 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

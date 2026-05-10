"""Wiki vault minimal lint (Day 4 추가, plan v3 §wiki_lint.py).

검사:
  - orphan: 어떤 concept도 source_episode_ids/links로 참조하지 않는 episode
  - broken_link: frontmatter `links`가 가리키는 id가 vault에 없음
  - invalid_frontmatter: YAML 파싱 실패 또는 필수 필드(id, type) 누락
  - alias_asymmetry: A→B alias는 있는데 B→A alias가 없음
  - duplicate_concept: 같은 트리플 키가 두 파일에 (filename 변환 충돌)

CLI: `python -m wiki_lint --vault wiki/`
exit 0 = 위반 없음, 1 = 위반 발견, 2 = vault 경로 오류
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import frontmatter


def scan(vault: Path) -> dict[str, list[Any]]:
    """vault 전체 스캔 → 위반 카테고리별 list."""
    issues: dict[str, list[Any]] = {
        "orphan": [],
        "broken_link": [],
        "invalid_frontmatter": [],
        "alias_asymmetry": [],
        "duplicate_concept": [],
    }

    nodes: dict[str, tuple[Path, dict]] = {}  # id -> (path, frontmatter)

    # 1) frontmatter 파싱 + id 등록
    for kind in ("episodes", "concepts", "aliases"):
        d = vault / kind
        if not d.exists():
            continue
        for p in sorted(d.glob("*.md")):
            try:
                post = frontmatter.load(p)
            except Exception as e:
                issues["invalid_frontmatter"].append({"path": str(p), "error": str(e)})
                continue
            md = dict(post.metadata)
            nid = md.get("id")
            if not nid:
                issues["invalid_frontmatter"].append({"path": str(p), "error": "missing id"})
                continue
            if not md.get("type"):
                issues["invalid_frontmatter"].append({"path": str(p), "error": "missing type"})
                continue
            if nid in nodes:
                issues["duplicate_concept"].append({
                    "key": nid,
                    "files": [str(nodes[nid][0]), str(p)],
                })
                continue
            nodes[nid] = (p, md)

    # 2) broken_link
    for nid, (_, md) in nodes.items():
        for link in (md.get("links") or []):
            if link not in nodes:
                issues["broken_link"].append({"from": nid, "to": link})

    # 3) alias_asymmetry
    for nid, (_, md) in nodes.items():
        if not nid.startswith("alias:"):
            continue
        c = md.get("canonical", "")
        v = md.get("variant", "")
        if not c or not v:
            continue
        reverse_id = f"alias:{v}|{c}"
        if reverse_id not in nodes:
            issues["alias_asymmetry"].append({"id": nid, "missing_reverse": reverse_id})

    # 4) duplicate_concept (트리플 키 중복 — 동일 (product, oper, fail)이 두 노드)
    concept_keys: dict[tuple[str, str, str], str] = {}
    for nid, (_, md) in nodes.items():
        if not nid.startswith("concept:"):
            continue
        triple = (md.get("product", ""), md.get("cause_oper", ""), md.get("fail_type", ""))
        if triple in concept_keys and concept_keys[triple] != nid:
            issues["duplicate_concept"].append({
                "triple": "|".join(triple),
                "ids": [concept_keys[triple], nid],
            })
        else:
            concept_keys[triple] = nid

    # 5) orphan: episode가 어떤 concept의 source_episode_ids/links에도 없음
    referenced_eps: set[str] = set()
    for nid, (_, md) in nodes.items():
        if nid.startswith("concept:"):
            referenced_eps.update(md.get("source_episode_ids") or [])
            for link in (md.get("links") or []):
                if link.startswith("episode:"):
                    referenced_eps.add(link)
    for nid in nodes:
        if nid.startswith("episode:") and nid not in referenced_eps:
            issues["orphan"].append({"id": nid})

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="wiki vault lint")
    default_vault = str(Path(__file__).parent / "wiki")
    parser.add_argument("--vault", default=default_vault, help="vault directory")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    parser.add_argument("--quiet", action="store_true", help="exit code only")
    args = parser.parse_args()

    vault = Path(args.vault).resolve()
    if not vault.exists():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2

    issues = scan(vault)
    total = sum(len(v) for v in issues.values())

    if args.json:
        print(json.dumps({"total": total, "issues": issues}, ensure_ascii=False, indent=2))
    elif not args.quiet:
        for kind, items in issues.items():
            if items:
                print(f"[{kind}] {len(items)}")
                for it in items[:10]:
                    print(f"  - {it}")
                if len(items) > 10:
                    print(f"  ... and {len(items) - 10} more")
        print(f"\nTOTAL ISSUES: {total} (vault={vault})")

    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

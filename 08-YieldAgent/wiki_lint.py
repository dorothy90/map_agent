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
import datetime
import json
import sys
from pathlib import Path
from typing import Any

import frontmatter
import yaml
from wiki_config import resolve_wiki_paths
from wiki_graph_models import RelationPredicate


_GENERATED_BY = "yield-wiki-materializer"


def scan(vault: Path) -> dict[str, list[Any]]:
    """vault 전체 스캔 → 위반 카테고리별 list.

    Karpathy 회귀(임계 완화)로 wiki-first 게이트가 풀린 만큼 lint 룰을 강화한다.
    추가: gap(foundations.yaml priority=high 누락), low_confidence, stale_episode.
    """
    issues: dict[str, list[Any]] = {
        "orphan": [],
        "broken_link": [],
        "invalid_frontmatter": [],
        "alias_asymmetry": [],
        "duplicate_concept": [],
        "gap": [],
        "low_confidence": [],
        "stale_episode": [],
        "invalid_relation": [],
        "stale_node": [],
    }

    nodes: dict[str, tuple[Path, dict]] = {}  # id -> (path, frontmatter)

    # 1) frontmatter 파싱 + id 등록
    for kind in (
        "episodes",
        "concepts",
        "aliases",
        "entities",
        "relations",
        "sources",
    ):
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
    now = datetime.datetime.now()
    for nid, (_, md) in nodes.items():
        if not nid.startswith("episode:") or nid in referenced_eps:
            continue
        age_days = _age_days(md, now)
        entry: dict[str, Any] = {"id": nid}
        if age_days is not None:
            entry["age_days"] = age_days
        issues["orphan"].append(entry)
        if age_days is not None and age_days > 30:
            issues["stale_episode"].append({"id": nid, "age_days": age_days})

    # 6) gap: foundations.yaml priority=high 트리플 중 vault 미존재
    fpath = vault / "foundations.yaml"
    if fpath.exists():
        try:
            cfg = yaml.safe_load(fpath.read_text(encoding="utf-8")) or {}
        except Exception as e:
            issues["invalid_frontmatter"].append({"path": str(fpath), "error": str(e)})
            cfg = {}
        for entry in cfg.get("foundations", []) or []:
            if entry.get("priority") != "high":
                continue
            triple = (
                entry.get("product", ""),
                entry.get("cause_oper", ""),
                entry.get("fail_type", ""),
            )
            if all(triple) and triple not in concept_keys:
                issues["gap"].append({
                    "triple": "|".join(triple),
                    "priority": entry.get("priority"),
                })

    # 7) low_confidence: concept confidence < 0.5 → 운영자 검수 대상
    for nid, (_, md) in nodes.items():
        if not nid.startswith("concept:"):
            continue
        try:
            conf = float(md.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        if conf < 0.5:
            issues["low_confidence"].append({"id": nid, "confidence": conf})

    valid_predicates = {predicate.value for predicate in RelationPredicate}
    source_doc_ids = {
        str(md.get("doc_id") or "").strip()
        for _, md in nodes.values()
        if md.get("type") == "source" and str(md.get("doc_id") or "").strip()
    }
    for nid, (path, md) in nodes.items():
        if (
            md.get("type") not in {"entity", "relation"}
            or md.get("generated_by") != _GENERATED_BY
        ):
            continue
        if md.get("status") == "stale":
            issues["stale_node"].append({"id": nid, "path": str(path)})
            continue
        if md.get("type") != "relation" or md.get("status") != "active":
            continue
        reasons: list[str] = []
        for field in ("subject_entity_id", "object_entity_id"):
            endpoint_id = str(md.get(field) or "").strip()
            endpoint = nodes.get(endpoint_id)
            if endpoint is None or not (
                endpoint[1].get("type") == "entity"
                and endpoint[1].get("status") == "active"
                and endpoint[1].get("generated_by") == _GENERATED_BY
            ):
                reasons.append(field)
        predicate = str(md.get("predicate") or "").strip()
        if predicate not in valid_predicates:
            reasons.append("predicate")
        relation_source_doc_ids = md.get("source_doc_ids")
        if not isinstance(relation_source_doc_ids, list) or not relation_source_doc_ids:
            reasons.append("source_doc_ids")
        elif any(
            str(doc_id).strip() not in source_doc_ids
            for doc_id in relation_source_doc_ids
        ):
            reasons.append("source_doc_ids")
        if reasons:
            issues["invalid_relation"].append(
                {"id": nid, "path": str(path), "invalid": sorted(set(reasons))}
            )

    return issues


def _age_days(md: dict, now: datetime.datetime) -> int | None:
    last = md.get("last_active") or md.get("updated") or md.get("created")
    if not last:
        return None
    try:
        return (now - datetime.datetime.fromisoformat(str(last))).days
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="wiki vault lint")
    default_vault = str(resolve_wiki_paths().root)
    parser.add_argument("--vault", default=default_vault, help="vault directory")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    parser.add_argument("--quiet", action="store_true", help="exit code only")
    parser.add_argument("--log", action="store_true",
                        help="결과를 wiki/lint_logs/YYYY-MM-DD.md 에도 저장")
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

    if args.log:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        log_dir = vault / "lint_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{today}.md"
        lines = [f"# Wiki lint — {today}", "",
                 f"TOTAL ISSUES: **{total}** (vault=`{vault}`)", ""]
        for kind, items in issues.items():
            if not items:
                continue
            lines.append(f"## {kind} ({len(items)})")
            lines.append("")
            for it in items:
                lines.append(f"- {it}")
            lines.append("")
        log_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"logged to {log_path}")

    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

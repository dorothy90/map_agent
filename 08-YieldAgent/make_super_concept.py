"""
make_super_concept.py — Day 5 cross-concept 추상화 1회성 CLI (PoC, 참고용 한정)

vault 안 concept을 한 축 기준으로 그룹화 → 같은 그룹 concept ≥ min_concepts개면
LLM 메타 합성으로 super_concept 생성 → super_concepts/ 폴더에 저장.

자동 답변 근거 사용 금지 (status: reference_only). 운영자 수동 검수용.

사용:
  # 어떤 축으로 그룹 가능한지 후보 조회만
  python make_super_concept.py --dry-run

  # fail_type=EASY 축으로 super 생성 (fail_type prefix "EASY" 모두 묶음)
  python make_super_concept.py --axis fail_type --value EASY --apply

  # cause_oper="PRE METAL CLN" 축
  python make_super_concept.py --axis cause_oper --value "PRE METAL CLN" --apply

  # 전체 자동 — 그룹별 ≥2 concept인 axis_value 모두 처리
  python make_super_concept.py --axis fail_type --apply --all
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(override=True)
sys.path.insert(0, str(Path(__file__).parent))

import wiki_store
from wiki_summarizer import synthesize_super_concept

ALLOWED_AXES = ("fail_type", "cause_oper", "product")


def load_concepts() -> list[dict[str, Any]]:
    """vault 안 모든 concept 로드 (frontmatter + body)."""
    out: list[dict[str, Any]] = []
    for cpath in sorted(wiki_store._CONCEPTS.glob("*.md")):
        post = wiki_store._read(cpath)
        if post is None:
            continue
        out.append({
            "id": post.metadata.get("id", ""),
            "frontmatter": dict(post.metadata),
            "body": post.content or "",
            "path": str(cpath),
        })
    return out


def group_by_axis(concepts: list[dict], axis: str) -> dict[str, list[dict]]:
    """axis (예: 'fail_type') 값으로 concept 그룹화. fail_type은 alias 정규화 위해
    괄호 앞 부분만 키로 사용 (예: 'EASY(W)' → 'EASY')."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in concepts:
        v = (c["frontmatter"].get(axis) or "").strip()
        if not v:
            continue
        # fail_type alias 정규화: "EASY(W)" -> "EASY"
        if axis == "fail_type" and "(" in v:
            v = v.split("(", 1)[0].strip()
        groups[v].append(c)
    return dict(groups)


def summarize_groups(groups: dict[str, list[dict]], min_concepts: int) -> None:
    qualifying = sorted(
        ((k, v) for k, v in groups.items() if len(v) >= min_concepts),
        key=lambda kv: -len(kv[1]),
    )
    print(f"  (≥{min_concepts} concepts 그룹: {len(qualifying)})")
    for k, v in qualifying:
        print(f"    {k!r}: {len(v)}개  ({', '.join(c['id'] for c in v[:5])}{'...' if len(v) > 5 else ''})")
    skipped = [(k, len(v)) for k, v in groups.items() if len(v) < min_concepts]
    if skipped:
        print(f"  (미달 {len(skipped)} 그룹: {', '.join(f'{k}={n}' for k, n in skipped[:8])}"
              f"{'...' if len(skipped) > 8 else ''})")


def process_one(axis: str, axis_value: str, concepts_in_group: list[dict]) -> bool:
    print(f"\n--- super: {axis}={axis_value!r} (sources={len(concepts_in_group)}) ---")
    for c in concepts_in_group:
        print(f"    + {c['id']}  confidence={c['frontmatter'].get('confidence', 0)}")
    result = synthesize_super_concept(axis, axis_value, concepts_in_group)
    if result is None:
        print("    ✗ LLM 합성 실패 (skip)")
        return False
    src_ids = [c["id"] for c in concepts_in_group]
    super_id, path = wiki_store.upsert_super_concept(
        axis=axis,
        axis_value=axis_value,
        source_concept_ids=src_ids,
        synthesized_body=result.body_markdown,
        confidence=result.confidence,
    )
    print(f"    ✓ saved {super_id}  confidence={result.confidence:.2f}  →  {path}")
    return True


def main() -> int:
    p = argparse.ArgumentParser(description="Cross-concept 추상화 super_concept 생성 (참고용)")
    p.add_argument("--axis", choices=ALLOWED_AXES,
                   help="와일드카드 축 — 와일드카드가 아닌 축 값을 --value로 고정")
    p.add_argument("--value", default="",
                   help="고정된 축 값 (--all 지정 시 무시)")
    p.add_argument("--all", action="store_true",
                   help="해당 축의 모든 그룹(≥min-concepts) 자동 처리")
    p.add_argument("--min-concepts", type=int, default=2,
                   help="그룹당 concept 최소 수 (default 2)")
    p.add_argument("--dry-run", action="store_true", help="그룹 후보만 표시, LLM 호출 X")
    p.add_argument("--apply", action="store_true", help="실 실행")
    args = p.parse_args()

    if args.dry_run and args.apply:
        p.error("--dry-run 과 --apply 동시 지정 불가")
    if not (args.dry_run or args.apply):
        p.error("--dry-run 또는 --apply 둘 중 하나 필수")

    concepts = load_concepts()
    print(f"loaded concepts: {len(concepts)}")

    if args.axis:
        axes = [args.axis]
    else:
        axes = list(ALLOWED_AXES)

    if args.dry_run:
        for ax in axes:
            groups = group_by_axis(concepts, ax)
            print(f"\naxis={ax} groups: {len(groups)}")
            summarize_groups(groups, args.min_concepts)
        return 0

    # apply
    if not args.axis:
        p.error("--apply 시 --axis 필수")
    groups = group_by_axis(concepts, args.axis)
    if args.all:
        targets = [(k, v) for k, v in groups.items() if len(v) >= args.min_concepts]
    else:
        if not args.value:
            p.error("--apply 시 --value 또는 --all 둘 중 하나 필수")
        if args.value not in groups or len(groups[args.value]) < args.min_concepts:
            print(f"  ✗ {args.axis}={args.value!r} 미달 (concepts={len(groups.get(args.value, []))})")
            return 1
        targets = [(args.value, groups[args.value])]

    print(f"\nprocessing {len(targets)} group(s)…")
    success = 0
    for axis_value, group_concepts in targets:
        if process_one(args.axis, axis_value, group_concepts):
            success += 1

    counts = wiki_store.counts()
    print(f"\ndone — success {success}/{len(targets)}.  vault super_concepts: {counts.get('super_concept', 0)}")
    return 0 if success > 0 else 1


if __name__ == "__main__":
    sys.exit(main())

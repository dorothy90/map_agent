"""fresh 골든셋 + 인덱스에서 어려운 카테고리(v2) 자동 합성.

카테고리:
  - alias_variant: fail_type의 "(X)" alias 제거한 쿼리/필터 (~15)
  - distractor: 같은 (fail_type, cause_oper)인데 다른 product의 doc을 forbidden_doc_ids로 (~10)
  - ambiguous: filter 비우고 cause_oper만 자연어 쿼리 → 다수 expected (~10)
  - typo: 공백/대소문자 변형 (~5)

실행:
  python -m eval.build_goldset_v2 --out datasets/fail_history_goldset_v2.json
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(override=True)

from eval.build_goldset_from_index import _fetch_all_docs  # noqa: E402

_ALIAS_SUFFIX = re.compile(r"\([A-Za-z0-9]+\)$")


def _strip_alias(fail_type: str) -> str:
    return _ALIAS_SUFFIX.sub("", fail_type or "").strip()


def build_alias_variants(fresh_cases: list[dict], n: int = 15, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    cands = [
        c for c in fresh_cases
        if _strip_alias(c["expected_filters"]["fail_type"]) != c["expected_filters"]["fail_type"]
    ]
    rng.shuffle(cands)
    out: list[dict] = []
    for i, c in enumerate(cands[:n], 1):
        ft_full = c["expected_filters"]["fail_type"]
        ft_alias = _strip_alias(ft_full)
        new_filters = dict(c["expected_filters"])
        new_filters["fail_type"] = ft_alias
        out.append({
            "id": f"fh_alias_{i:03d}",
            "category": "alias_variant",
            "anchor_doc_id": c["anchor_doc_id"],
            "query": c["query"].replace(ft_full, ft_alias),
            "expected_doc_ids": c["expected_doc_ids"],
            "expected_filters": new_filters,
            "ideal_summary": c.get("ideal_summary", ""),
            "must_mention": [ft_alias, c["expected_filters"]["cause_oper"]],
        })
    return out


def build_distractors(docs: list[dict], n: int = 10, seed: int = 42) -> list[dict]:
    by_pair: dict[tuple, list[dict]] = defaultdict(list)
    for d in docs:
        key = (d.get("fail_type", ""), d.get("cause_oper", ""))
        if all(key):
            by_pair[key].append(d)
    cands: list[tuple] = []
    for pair, recs in by_pair.items():
        products = sorted({r["product"] for r in recs if r.get("product")})
        if len(products) >= 2:
            cands.append((pair, recs, products))
    rng = random.Random(seed)
    rng.shuffle(cands)
    out: list[dict] = []
    for i, (pair, recs, products) in enumerate(cands[:n], 1):
        target_p, wrong_p = products[0], products[1]
        target_recs = sorted([r for r in recs if r["product"] == target_p], key=lambda r: r["doc_id"])
        wrong_recs = sorted([r for r in recs if r["product"] == wrong_p], key=lambda r: r["doc_id"])
        fail_type, cause_oper = pair
        out.append({
            "id": f"fh_distract_{i:03d}",
            "category": "distractor",
            "anchor_doc_id": target_recs[0]["doc_id"],
            "query": f"{cause_oper} 공정에서 발생하는 {fail_type} 불량의 원인과 대처 방법은 무엇인가요?",
            "expected_doc_ids": [r["doc_id"] for r in target_recs],
            "forbidden_doc_ids": [r["doc_id"] for r in wrong_recs],
            "expected_filters": {
                "product": target_p,
                "fail_type": fail_type,
                "cause_oper": cause_oper,
            },
            "ideal_summary": (target_recs[0].get("cause") or "")[:300],
            "must_mention": [fail_type, cause_oper],
        })
    return out


def build_ambiguous(docs: list[dict], n: int = 10, seed: int = 42) -> list[dict]:
    by_cause: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        if d.get("cause_oper"):
            by_cause[d["cause_oper"]].append(d)
    cands = [(c, recs) for c, recs in by_cause.items() if len(recs) >= 3]
    rng = random.Random(seed)
    rng.shuffle(cands)
    out: list[dict] = []
    for i, (cause_oper, recs) in enumerate(cands[:n], 1):
        recs_sorted = sorted(recs, key=lambda r: r["doc_id"])
        expected = [r["doc_id"] for r in recs_sorted[:5]]
        out.append({
            "id": f"fh_ambig_{i:03d}",
            "category": "ambiguous",
            "anchor_doc_id": recs_sorted[0]["doc_id"],
            "query": f"{cause_oper} 공정에서 일어나는 결함 사례를 알려주세요",
            "expected_doc_ids": expected,
            "expected_cause_oper": cause_oper,
            "expected_filters": {"product": "", "fail_type": "", "cause_oper": ""},
            "ideal_summary": "",
            "must_mention": [cause_oper],
        })
    return out


def build_typos(fresh_cases: list[dict], n: int = 5, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    sample = list(fresh_cases)
    rng.shuffle(sample)
    out: list[dict] = []
    for i, c in enumerate(sample[:n], 1):
        co = c["expected_filters"]["cause_oper"]
        ft = c["expected_filters"]["fail_type"]
        co_typo = co.replace(" ", "  ") if " " in co else co.lower()
        q_typo = c["query"].replace(co, co_typo).lower()
        out.append({
            "id": f"fh_typo_{i:03d}",
            "category": "typo",
            "anchor_doc_id": c["anchor_doc_id"],
            "query": q_typo,
            "expected_doc_ids": c["expected_doc_ids"],
            "expected_filters": c["expected_filters"],
            "ideal_summary": c.get("ideal_summary", ""),
            "must_mention": [ft, co],
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", default="datasets/fail_history_goldset_fresh.json")
    parser.add_argument("--out", default="datasets/fail_history_goldset_v2.json")
    args = parser.parse_args()

    fresh_path = Path(__file__).parent / args.fresh
    fresh_cases = json.loads(fresh_path.read_text(encoding="utf-8"))
    docs = _fetch_all_docs()

    cases = (
        build_alias_variants(fresh_cases)
        + build_distractors(docs)
        + build_ambiguous(docs)
        + build_typos(fresh_cases)
    )
    out_path = Path(__file__).parent / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(cases)} cases → {out_path}")
    for cat, cnt in sorted(Counter(c["category"] for c in cases).items()):
        print(f"  {cat}: {cnt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

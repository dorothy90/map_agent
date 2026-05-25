"""인덱스 record에서 deterministic하게 fail_history 골든셋을 빌드.

기존 fail_history_goldset.json은 인덱스 reseed 시 stale 됨
(generate_fail_history_data.py가 random.seed 고정 없이 LLM으로 매번 다른 record 합성).
이 스크립트는 현 인덱스에서 (product, fail_type, cause_oper) triple 그룹을 직접 추출해
새 골든셋을 합성한다. seed 고정으로 재현 가능.

실행:
  python -m eval.build_goldset_from_index --out datasets/fail_history_goldset_fresh.json --n 50
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(override=True)

from fail_history_tools import _OPENSEARCH_INDEX, _get_opensearch_client  # noqa: E402


def _fetch_all_docs() -> list[dict]:
    """fail-history 인덱스의 모든 record를 가져온다 (size=10000)."""
    client = _get_opensearch_client()
    body = {
        "size": 10000,
        "query": {"match_all": {}},
        "_source": [
            "doc_id", "product", "fail_type", "cause_oper",
            "cause", "action", "comment",
        ],
    }
    resp = client.search(index=_OPENSEARCH_INDEX, body=body)
    return [h["_source"] for h in resp["hits"]["hits"]]


def build_goldset(n: int = 50, seed: int = 42) -> list[dict]:
    docs = _fetch_all_docs()

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for d in docs:
        key = (d.get("product", ""), d.get("fail_type", ""), d.get("cause_oper", ""))
        if all(key):
            groups[key].append(d)

    by_product: dict[str, list[tuple]] = defaultdict(list)
    for key in groups:
        by_product[key[0]].append(key)

    rng = random.Random(seed)
    products = sorted(by_product.keys())
    per_product = max(1, n // len(products))

    selected: list[tuple] = []
    for p in products:
        keys = sorted(by_product[p])
        rng.shuffle(keys)
        selected.extend(keys[:per_product])

    rng.shuffle(selected)
    if len(selected) > n:
        selected = selected[:n]

    cases: list[dict] = []
    for idx, key in enumerate(selected, 1):
        product, fail_type, cause_oper = key
        recs = sorted(groups[key], key=lambda r: r.get("doc_id", ""))
        anchor = recs[0]
        expected_doc_ids = [r["doc_id"] for r in recs]
        cases.append({
            "id": f"fh_gold_{idx:03d}",
            "anchor_doc_id": anchor["doc_id"],
            "query": f"{cause_oper} 공정에서 발생하는 {fail_type} 불량의 원인과 대처 방법은 무엇인가요?",
            "expected_doc_ids": expected_doc_ids,
            "expected_filters": {
                "product": product,
                "cause_oper": cause_oper,
                "fail_type": fail_type,
            },
            "ideal_summary": (anchor.get("cause") or "")[:300],
            "must_mention": [fail_type, cause_oper],
        })
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="datasets/fail_history_goldset_fresh.json")
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    cases = build_goldset(n=args.n, seed=args.seed)
    out_arg = Path(args.out)
    out_path = out_arg if out_arg.is_absolute() else Path(__file__).parent / out_arg
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(cases)} cases → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

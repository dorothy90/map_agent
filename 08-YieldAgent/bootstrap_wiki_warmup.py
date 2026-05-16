"""
Bootstrap wiki vault eager warm-up — 1회성 (재실행 안전).

사내 배포 직전에 vault를 사전 채워서 첫 사용자 검색부터 wiki-first 발동 가능하게 만든다.

흐름:
  1) foundations.yaml 우선 seed
  2) OpenSearch aggregation top-N으로 보완 (min_docs 임계 통과만)
  3) 트리플별 query 변형 N회로 do_search() 호출 → 비동기 ingest 누적
  4) 큐 drain 대기
  5) vault 요약 + lint 실행

재실행 안전성 (idempotent):
  - episode upsert는 (query + filters + doc_ids) hash 기반 id 사용 → 같은 검색은
    같은 episode_id로 덮어쓰기 (중복 누적 X)
  - concept synthesis는 evidence_diversity_score < 0.3 (동일 raw 반복) 시 skip,
    in-flight 가드 (_synth_in_flight)도 중복 트리거 차단
  - 같은 트리플 재실행은 안전. 단 LLM 호출이 다시 발생할 수는 있음

사용:
  python bootstrap_wiki_warmup.py --dry-run
  python bootstrap_wiki_warmup.py --apply
  python bootstrap_wiki_warmup.py --apply --top 30 --queries-per-triple 4
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv(override=True)

sys.path.insert(0, str(Path(__file__).parent))

from fail_history_tools import _get_opensearch_client, do_search
from wiki_queue import wiki_queue
from wiki_summarizer import summarize as wiki_summarize_fn
import wiki_store

_OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "fail-history")
_VAULT_PATH = Path(os.getenv("WIKI_VAULT_PATH", str(Path(__file__).parent / "wiki")))


def load_foundations(vault: Path) -> list[dict[str, Any]]:
    fpath = vault / "foundations.yaml"
    if not fpath.exists():
        return []
    cfg = yaml.safe_load(fpath.read_text(encoding="utf-8")) or {}
    out: list[dict[str, Any]] = []
    for e in cfg.get("foundations", []) or []:
        if all(e.get(k) for k in ("product", "fail_type", "cause_oper")):
            out.append({
                "product": e["product"],
                "fail_type": e["fail_type"],
                "cause_oper": e["cause_oper"],
                "priority": e.get("priority", "low"),
                "source": "foundations",
            })
    return out


def fetch_opensearch_triples(top: int, min_docs: int) -> list[dict[str, Any]]:
    client = _get_opensearch_client()
    body = {
        "size": 0,
        "aggs": {
            "by_product": {
                "terms": {"field": "product.keyword", "size": 10},
                "aggs": {
                    "by_fail": {
                        "terms": {"field": "fail_type.keyword", "size": 20},
                        "aggs": {
                            "by_oper": {"terms": {"field": "cause_oper", "size": 20}}
                        }
                    }
                }
            }
        }
    }
    resp = client.search(index=_OPENSEARCH_INDEX, body=body)
    triples: list[dict[str, Any]] = []
    for pb in resp["aggregations"]["by_product"]["buckets"]:
        product = pb["key"]
        for fb in pb["by_fail"]["buckets"]:
            fail_type = fb["key"]
            for ob in fb["by_oper"]["buckets"]:
                triples.append({
                    "product": product,
                    "fail_type": fail_type,
                    "cause_oper": ob["key"],
                    "doc_count": ob["doc_count"],
                })
    triples = sorted([t for t in triples if t["doc_count"] >= min_docs],
                     key=lambda t: -t["doc_count"])[:top]
    for t in triples:
        t["priority"] = "auto"
        t["source"] = "opensearch"
    return triples


def merge_seeds(foundations: list[dict], aggregated: list[dict]) -> list[dict]:
    seen = {(s["product"], s["fail_type"], s["cause_oper"]) for s in foundations}
    out = list(foundations)
    for t in aggregated:
        key = (t["product"], t["fail_type"], t["cause_oper"])
        if key not in seen:
            out.append(t)
            seen.add(key)
    return out


def query_variants(product: str, fail_type: str, cause_oper: str) -> list[str]:
    """자연어 다양화 — 같은 raw 반복 가드(evidence_diversity)를 우회."""
    return [
        f"{fail_type} 불량 원인",
        f"{cause_oper}에서 발생한 {fail_type}",
        f"{product} {fail_type} 패턴 정리",
        f"{product} {cause_oper} 불량 이력",
    ]


async def warmup(seeds: list[dict], queries_per_triple: int) -> None:
    for i, s in enumerate(seeds, 1):
        product = s["product"]
        fail_type = s["fail_type"]
        cause_oper = s["cause_oper"]
        print(f"  [{i}/{len(seeds)}] {product}|{fail_type}|{cause_oper}  (source={s['source']})")
        for q in query_variants(product, fail_type, cause_oper)[:queries_per_triple]:
            try:
                do_search(query=q, product=product, fail_type=fail_type,
                          cause_oper=cause_oper, top_k=5)
            except Exception as e:
                print(f"    ✗ query={q!r} 실패: {e}")
                continue


def summarize_vault() -> None:
    counts = wiki_store.counts()
    print(f"\nvault counts: episodes={counts.get('episodes', 0)} "
          f"concepts={counts.get('concepts', 0)} "
          f"aliases={counts.get('aliases', 0)}")
    high = mid = low = 0
    for cpath in wiki_store._CONCEPTS.glob("*.md"):
        post = wiki_store._read(cpath)
        if post is None:
            continue
        try:
            conf = float(post.metadata.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        if conf >= 0.7:
            high += 1
        elif conf >= 0.5:
            mid += 1
        else:
            low += 1
    print(f"  concept confidence: ≥0.7={high}  0.5~0.7={mid}  <0.5={low}")


async def amain(args: argparse.Namespace) -> int:
    vault = _VAULT_PATH
    print(f"vault: {vault}")
    print(f"opensearch index: {_OPENSEARCH_INDEX}")

    foundations = load_foundations(vault)
    print(f"\n1) foundations.yaml seed: {len(foundations)}")
    for s in foundations:
        print(f"  - {s['product']}|{s['fail_type']}|{s['cause_oper']} (priority={s['priority']})")

    aggregated: list[dict[str, Any]] = []
    try:
        aggregated = fetch_opensearch_triples(top=args.top, min_docs=args.min_docs)
    except Exception as e:
        print(f"\n  ⚠️ OpenSearch aggregation 실패: {e}")
        if not foundations:
            print("  foundations도 없음 → 종료")
            return 2
    print(f"\n2) OpenSearch aggregation top-{args.top} (min_docs={args.min_docs}): {len(aggregated)}")
    for t in aggregated[:10]:
        print(f"  - {t['product']}|{t['fail_type']}|{t['cause_oper']} (doc={t['doc_count']})")
    if len(aggregated) > 10:
        print(f"  ... and {len(aggregated) - 10} more")

    seeds = merge_seeds(foundations, aggregated)
    print(f"\n3) merged seeds: {len(seeds)}")

    if args.dry_run:
        print("\n--dry-run — 실 실행 X")
        return 0

    print("\n4) wiki_queue start + warmup 시작…")
    wiki_queue.set_summarizer(wiki_summarize_fn)
    await wiki_queue.start()
    try:
        await warmup(seeds, queries_per_triple=args.queries_per_triple)
        print(f"\n5) 큐 drain 대기 (timeout={args.drain_timeout}s)…")
        await wiki_queue.stop(timeout=args.drain_timeout)
    except Exception as e:
        print(f"warmup 중 오류: {e}")
        await wiki_queue.stop(timeout=10)
        return 1

    summarize_vault()

    if not args.no_lint:
        print("\n6) lint 실행…")
        import wiki_lint
        issues = wiki_lint.scan(vault)
        total = sum(len(v) for v in issues.values())
        print(f"  lint total: {total}")
        for kind, items in issues.items():
            if items:
                print(f"    {kind}: {len(items)}")

    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Bootstrap wiki vault eager warm-up")
    p.add_argument("--dry-run", action="store_true", help="seed 후보만 출력")
    p.add_argument("--apply", action="store_true", help="실 실행")
    p.add_argument("--top", type=int, default=20, help="OpenSearch top-N 트리플 (default 20)")
    p.add_argument("--min-docs", type=int, default=2, help="트리플 채택 최소 doc 수 (default 2)")
    p.add_argument("--queries-per-triple", type=int, default=3, help="트리플당 query 변형 수 (default 3)")
    p.add_argument("--drain-timeout", type=int, default=600, help="큐 drain timeout 초 (default 600)")
    p.add_argument("--no-lint", action="store_true", help="끝에 lint 실행 안 함")
    args = p.parse_args()

    if not (args.dry_run or args.apply):
        p.error("--dry-run 또는 --apply 둘 중 하나 필수")
    if args.dry_run and args.apply:
        p.error("--dry-run 과 --apply 동시 지정 불가")

    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())

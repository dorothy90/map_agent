"""
Bootstrap wiki vault — 직접 합성 (raw docs → concept body, episode 단계 생략).

Phase 11: 사용자 검색 시뮬레이션·큐·episode 단계 모두 제거. 트리플별 OpenSearch에서
raw docs 직접 fetch → LLM 합성 1회 → wiki_store.upsert_concept 즉시 저장.

장점:
  - LLM 호출 ~트리플 수 (검색 트리거형 대비 1/3)
  - 큐·worker 없음 → 디버깅 단순, 직렬 진행
  - 신규 doc 인덱싱 시 idempotent 재실행 (같은 트리플은 최신 raw로 재합성)

흐름:
  1) foundations.yaml seed + OpenSearch aggregation top-N → 트리플 list
  2) 트리플별 OpenSearch raw docs fetch (terms+prefix filter)
  3) synthesize_concept_from_docs (LLM 1회) → ConceptSynthesis
  4) wiki_store.upsert_concept → vault에 즉시 저장
  5) (선택) wiki_lint 실행

사용:
  python bootstrap_wiki_warmup.py --dry-run                  # seed 후보만 출력
  python bootstrap_wiki_warmup.py --apply                    # 실 실행
  python bootstrap_wiki_warmup.py --apply --top 50 --max-docs 20
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv(override=True)
sys.path.insert(0, str(Path(__file__).parent))

from fail_history_tools import _get_opensearch_client  # noqa: E402
import wiki_store  # noqa: E402
from wiki_config import resolve_wiki_paths  # noqa: E402
from wiki_manifest import load_manifest, record_success, save_manifest  # noqa: E402
from wiki_summarizer import (  # noqa: E402
    restrict_concept_synthesis_sources,
    synthesize_concept_from_docs,
)
from wiki_sync import build_triple_snapshot, make_triple_key, normalize_fail_type  # noqa: E402

_OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "fail-history")
_VAULT_PATH = resolve_wiki_paths().root
_MANIFEST_PATH = resolve_wiki_paths().manifest


# ── seed 수집 ────────────────────────────────────────────
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
                "terms": {"field": "product.keyword", "size": 20},
                "aggs": {
                    "by_fail": {
                        "terms": {"field": "fail_type.keyword", "size": 50},
                        "aggs": {
                            "by_oper": {"terms": {"field": "cause_oper", "size": 50}}
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
    triples = sorted(
        [t for t in triples if t["doc_count"] >= min_docs],
        key=lambda t: -t["doc_count"],
    )[:top]
    for t in triples:
        t["priority"] = "auto"
        t["source"] = "opensearch"
    return triples


def merge_seeds(foundations: list[dict], aggregated: list[dict]) -> list[dict]:
    """foundations 우선 + aggregation에 있는 트리플 추가.
    fail_type alias 정규화 키 ("EASY(W)" → "EASY") 로 dedup."""
    def _norm_key(t: dict) -> tuple[str, str, str]:
        return (
            t["product"],
            normalize_fail_type(t["fail_type"]),
            t["cause_oper"],
        )

    seen = {_norm_key(s) for s in foundations}
    out = list(foundations)
    for t in aggregated:
        if _norm_key(t) not in seen:
            out.append(t)
            seen.add(_norm_key(t))
    return out


# ── raw docs fetch + 합성 ────────────────────────────────
def fetch_docs_for_triple(
    product: str,
    fail_type: str,
    cause_oper: str,
    max_docs: int,
) -> list[dict[str, Any]]:
    """트리플 docs를 OpenSearch에서 직접 fetch (검색 점수 계산 X)."""
    client = _get_opensearch_client()
    fail_norm = fail_type.split("(", 1)[0].strip() if "(" in fail_type else fail_type
    body = {
        "size": max_docs,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"product.keyword": product}},
                    {"prefix": {"fail_type.keyword": fail_norm}},
                    {"term": {"cause_oper": cause_oper}},
                ]
            }
        },
    }
    resp = client.search(index=_OPENSEARCH_INDEX, body=body)
    return [hit.get("_source", {}) for hit in resp.get("hits", {}).get("hits", [])]


def process_triple(t: dict[str, Any], max_docs: int) -> tuple[str, str]:
    """트리플 1개 처리 → (status, message)."""
    p, f, o = t["product"], t["fail_type"], t["cause_oper"]
    key = make_triple_key(p, f, o)
    cid = f"concept:{key.canonical}"
    try:
        docs = fetch_docs_for_triple(p, f, o, max_docs=10_000)
    except Exception as e:
        return ("fetch_fail", f"  ✗ docs fetch: {e}")
    if not docs:
        return ("no_docs", "  ⚠ docs 0건 (skip)")
    snapshot = build_triple_snapshot(key, docs)
    try:
        manifest = load_manifest(_MANIFEST_PATH, _OPENSEARCH_INDEX)
    except Exception as e:
        return ("manifest_fail", f"  ✗ manifest: {e}")
    try:
        result = synthesize_concept_from_docs(cid, docs[:max_docs])
    except Exception as e:
        return ("synth_fail", f"  ✗ synth: {e}")
    if result is None:
        return ("synth_none", "  ✗ synth None (LLM 실패 또는 응답 빈 채)")
    result = restrict_concept_synthesis_sources(result, snapshot.source_doc_ids)
    try:
        stored = wiki_store.upsert_concept(
            filters={
                "product": key.product,
                "fail_type": key.fail_type,
                "cause_oper": key.cause_oper,
            },
            source_episode_id=None,  # 직접 합성 — episode 단계 생략
            synthesized_body=result.body_markdown,
            confidence=result.confidence,
            citations=[c.model_dump() for c in result.citations],
            entities=[
                candidate.model_dump(mode="json")
                for candidate in getattr(result, "entities", [])
            ],
            relations=[
                candidate.model_dump(mode="json")
                for candidate in getattr(result, "relations", [])
            ],
            evidence={
                "score": 1.0 if len(docs) >= 5 else len(docs) / 5.0,
                "unique_doc_ids": len({d.get("doc_id") for d in docs if d.get("doc_id")}),
                "n_episodes": 0,
                "n_dates": len({d.get("date") for d in docs if d.get("date")}),
            },
            sync_metadata={
                "source_fingerprint": snapshot.source_fingerprint,
                "source_doc_ids": list(snapshot.source_doc_ids),
                "evidence_count": snapshot.evidence_count,
                "evidence_scope": snapshot.evidence_scope,
                "sync_job_id": f"bootstrap:{snapshot.source_fingerprint}",
            },
            materialize=False,
        )
    except Exception as e:
        return ("save_fail", f"  ✗ upsert: {e}")
    if isinstance(stored, tuple) and stored:
        stored_id = stored[0]
        full_id = stored_id if stored_id.startswith("concept:") else f"concept:{stored_id}"
        node = wiki_store.read_node(full_id)
        if node is not None:
            try:
                success_at = datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat(timespec="seconds")
                record_success(
                    manifest,
                    snapshot,
                    concept_id=full_id,
                    concept_version=int(node["frontmatter"].get("version", 1)),
                    success_at=success_at,
                )
                save_manifest(_MANIFEST_PATH, manifest)
            except Exception as e:
                return ("save_fail", f"  ✗ manifest save: {e}")
    return ("ok", f"  ✓ conf={result.confidence:.2f}  docs={len(docs)}  cits={len(result.citations)}")


# ── main ────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description="Wiki vault 직접 합성 (raw docs → concept body)")
    p.add_argument("--dry-run", action="store_true", help="seed 후보만 출력")
    p.add_argument("--apply", action="store_true", help="실 실행")
    p.add_argument("--top", type=int, default=200, help="OpenSearch aggregation top-N 트리플 (default 200)")
    p.add_argument("--min-docs", type=int, default=1, help="트리플 채택 최소 doc 수 (default 1)")
    p.add_argument("--max-docs", type=int, default=15, help="트리플당 LLM에 전달할 max docs (default 15)")
    p.add_argument("--skip-existing", action="store_true",
                   help="vault에 이미 합성된 concept(confidence>0)은 skip — 신규 트리플만")
    p.add_argument("--no-lint", action="store_true", help="끝에 lint 실행 안 함")
    p.add_argument("--product", help="exact triple product")
    p.add_argument("--fail-type", help="exact triple fail type")
    p.add_argument("--cause-oper", help="exact triple cause operation")
    args = p.parse_args()

    if args.dry_run and args.apply:
        p.error("--dry-run 과 --apply 동시 지정 불가")
    if not (args.dry_run or args.apply):
        p.error("--dry-run 또는 --apply 둘 중 하나 필수")
    exact_values = (args.product, args.fail_type, args.cause_oper)
    if any(exact_values) and not all(exact_values):
        p.error("--product, --fail-type, --cause-oper must be provided together")

    vault = _VAULT_PATH
    print(f"vault: {vault}")
    print(f"opensearch index: {_OPENSEARCH_INDEX}")

    exact = all(exact_values)
    foundations = [] if exact else load_foundations(vault)
    print(f"\n1) foundations.yaml seed: {len(foundations)}")
    for s in foundations:
        print(f"  - {s['product']}|{s['fail_type']}|{s['cause_oper']} (priority={s['priority']})")

    if exact:
        aggregated = []
    else:
        try:
            aggregated = fetch_opensearch_triples(top=args.top, min_docs=args.min_docs)
        except Exception as e:
            print(f"\n  ⚠️ OpenSearch aggregation 실패: {e}")
            aggregated = []
    print(f"\n2) OpenSearch aggregation top-{args.top} (min_docs={args.min_docs}): {len(aggregated)}")
    for t in aggregated[:10]:
        print(f"  - {t['product']}|{t['fail_type']}|{t['cause_oper']} (doc={t['doc_count']})")
    if len(aggregated) > 10:
        print(f"  ... and {len(aggregated) - 10} more")

    if exact:
        seeds = [{
            "product": args.product,
            "fail_type": normalize_fail_type(args.fail_type),
            "cause_oper": args.cause_oper,
            "priority": "exact",
            "source": "operator",
        }]
    else:
        seeds = merge_seeds(foundations, aggregated)
    print(f"\n3) merged seeds: {len(seeds)}")

    if args.skip_existing:
        before = len(seeds)
        filtered = []
        for s in seeds:
            cid = f"concept:{s['product']}|{s['cause_oper']}|{s['fail_type']}"
            existing = wiki_store.read_node(cid)
            try:
                conf = float(existing.get("frontmatter", {}).get("confidence", 0) if existing else 0)
            except (TypeError, ValueError):
                conf = 0.0
            if conf <= 0:
                filtered.append(s)
        seeds = filtered
        print(f"  --skip-existing: {before} → {len(seeds)} (이미 합성된 concept 제외)")

    if args.dry_run:
        print("\n--dry-run — 실 실행 X")
        return 0

    # ── 4) 트리플별 직접 합성 ─────────────────────────
    print(f"\n4) 트리플별 직접 합성 시작 — {len(seeds)}개")
    t0 = time.time()
    counts = {"ok": 0, "no_docs": 0, "fetch_fail": 0, "manifest_fail": 0, "synth_fail": 0, "synth_none": 0, "save_fail": 0}
    for i, t in enumerate(seeds, 1):
        print(f"  [{i}/{len(seeds)}] {t['product']}|{t['fail_type']}|{t['cause_oper']}  (src={t['source']})")
        status, msg = process_triple(t, max_docs=args.max_docs)
        counts[status] = counts.get(status, 0) + 1
        print(msg)
    elapsed = time.time() - t0
    print(f"\n  완료: {elapsed:.1f}s ({elapsed/60:.1f}분)")
    print(f"  결과: {counts}")

    materialization = wiki_store.materialize_obsidian_wiki()
    if materialization.errors:
        print("\n  ✗ Obsidian graph materialization 실패:")
        for error in materialization.errors:
            print(f"    - {error}")
        return 1

    # ── 5) vault 요약 + lint ───────────────────────────
    vc = wiki_store.counts()
    print(f"\nvault counts: episodes={vc.get('episode', 0)}  concepts={vc.get('concept', 0)}  super={vc.get('super_concept', 0)}")
    high = mid = low = 0
    for cpath in wiki_store._CONCEPTS.glob("*.md"):
        post = wiki_store._read(cpath)
        if post is None:
            continue
        try:
            conf = float(post.metadata.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            conf = 0.0
        if conf >= 0.7:
            high += 1
        elif conf >= 0.5:
            mid += 1
        else:
            low += 1
    print(f"  concept confidence: ≥0.7={high}  0.5~0.7={mid}  <0.5={low}")

    if not args.no_lint:
        print("\n6) lint 실행…")
        import wiki_lint
        issues = wiki_lint.scan(vault)
        total = sum(len(v) for v in issues.values())
        print(f"  lint total: {total}")
        for kind, items in issues.items():
            if items:
                print(f"    {kind}: {len(items)}")

    return 0 if counts["ok"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())

"""Wiki PoC evaluator (Day 6, plan v3 §eval).

baseline (wiki off) vs wiki-on (자연 분기: wiki-first / wiki-assisted / baseline) 비교.
실 OpenSearch + 실 도구 호출. ReAct 없음 (B2 함수형 노드 기준).

CLI:
  uv run python -m eval.run_wiki_eval --bench main --limit 5
  uv run python -m eval.run_wiki_eval --bench wiki_micro --mode both --save
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wiki_queue  # noqa: E402
import wiki_store  # noqa: E402
from fail_history_tools import do_search  # noqa: E402


# ── plan v3 §D 신규 메트릭 함수들 ─────────────────────
def compute_rouge_l(reference: str, candidate: str) -> float:
    """ROUGE-L F1 (LCS 기반, 단순 whitespace 토큰)."""
    ref = (reference or "").lower().split()
    cand = (candidate or "").lower().split()
    if not ref or not cand:
        return 0.0
    m, n = len(ref), len(cand)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if ref[i - 1] == cand[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = dp[i - 1][j] if dp[i - 1][j] >= dp[i][j - 1] else dp[i][j - 1]
    lcs = dp[m][n]
    if lcs == 0:
        return 0.0
    p = lcs / n
    r = lcs / m
    return round(2 * p * r / (p + r), 4) if (p + r) > 0 else 0.0


def compute_citation_match(body: str, citations: list[dict]) -> float:
    """body 안 [ep:xxx] 인용 vs citations list 매칭 비율."""
    import re as _re
    inline = set(_re.findall(r"\[ep:([a-zA-Z0-9_-]+)\]", body or ""))
    cited = {c.get("episode_id", "").replace("episode:", "") for c in (citations or [])}
    if not cited:
        return 0.0
    if not inline:
        return 0.5
    matched = inline & cited
    return round(len(matched) / max(len(cited), 1), 4)


def compute_freshness_regression(node_meta: dict, max_days: int = 30) -> bool:
    """30일 초과 stale 노드가 응답에 쓰였는지. True면 회귀."""
    import datetime as _dt
    la = node_meta.get("last_active") or node_meta.get("updated", "")
    if not la:
        return False
    try:
        last_dt = _dt.datetime.fromisoformat(str(la))
        return (_dt.datetime.now() - last_dt).days > max_days
    except Exception:
        return False


_DATASETS = Path(__file__).parent / "datasets"
_RESULTS = Path(__file__).parent / "results"


@contextmanager
def wiki_disabled():
    """baseline 모드: wiki lookup/enqueue/gate 무력화."""
    orig_lookup = wiki_store.lookup
    orig_enq = wiki_queue.wiki_queue.summarize_enqueue
    orig_gate = wiki_store.lookup_concept_body
    wiki_store.lookup = lambda *a, **k: {"concepts": [], "aliases": [], "recent_episodes": []}
    wiki_queue.wiki_queue.summarize_enqueue = lambda *a, **k: "skipped"
    wiki_store.lookup_concept_body = lambda *a, **k: {
        "gate": "none", "body": None, "confidence": 0.0,
        "citations": [], "fail_reasons": ["wiki-disabled"], "concept_id": None,
    }
    try:
        yield
    finally:
        wiki_store.lookup = orig_lookup
        wiki_queue.wiki_queue.summarize_enqueue = orig_enq
        wiki_store.lookup_concept_body = orig_gate


def _expected_concept_id(filters: dict) -> str | None:
    if all(filters.get(k) for k in ("product", "cause_oper", "fail_type")):
        return f"concept:{filters['product']}|{filters['cause_oper']}|{filters['fail_type']}"
    return None


def _is_rich_concept(expected_cid: str | None) -> bool:
    """vault에 해당 concept이 있고 confidence >= 0.5 (현 wiki-first 임계)면 '풍부' 분류."""
    if not expected_cid:
        return False
    node = wiki_store.read_node(expected_cid)
    if not node:
        return False
    try:
        return float(node.get("frontmatter", {}).get("confidence", 0)) >= 0.5
    except (TypeError, ValueError):
        return False


def run_case(case: dict, force_baseline: bool) -> dict:
    f = dict(case.get("expected_filters", {}) or {})
    is_exact_triple = all(f.get(k) for k in ("product", "fail_type", "cause_oper"))
    expected_cid = case.get("expected_wiki_concept_id") or _expected_concept_id(f)
    is_rich = _is_rich_concept(expected_cid)

    cm = wiki_disabled() if force_baseline else nullcontext()
    t0 = time.perf_counter()
    with cm:
        data = do_search(
            query=case["query"],
            product=f.get("product", "") or "",
            fail_type=f.get("fail_type", "") or "",
            cause_oper=f.get("cause_oper", "") or "",
            top_k=5,
        )
    elapsed = time.perf_counter() - t0

    results = data.get("results", []) or []
    wiki_mem = data.get("wiki_memory", {}) or {}
    retrieval_mode = data.get("retrieval_mode", "baseline")
    wiki_body = data.get("wiki_concept_body", "") or ""
    wiki_confidence = float(data.get("wiki_concept_confidence", 0.0) or 0.0)
    wiki_citations = data.get("wiki_citations", []) or []

    expected_docs = set(case.get("expected_doc_ids", []) or [])
    top_doc_ids = [r.get("doc_id") for r in results[:5] if r.get("doc_id")]
    recall = len(set(top_doc_ids) & expected_docs) / max(len(expected_docs), 1)
    mrr = 0.0
    for i, d in enumerate(top_doc_ids, 1):
        if d in expected_docs:
            mrr = 1.0 / i
            break

    raw_text = " ".join(
        (r.get("cause", "") or "") + " " + (r.get("action", "") or "") + " " + (r.get("comment", "") or "")
        for r in results
    )
    must = case.get("must_mention", []) or []
    must_hits = sum(1 for m in must if m in raw_text)
    must_rate = must_hits / max(len(must), 1)

    wiki_concept_hit = 0
    if expected_cid and wiki_mem.get("concepts"):
        wiki_concept_hit = int(any(c.get("id") == expected_cid for c in wiki_mem["concepts"]))

    # plan v3 §D 신규 메트릭
    rouge_l = compute_rouge_l(raw_text, wiki_body) if wiki_body else 0.0
    cit_match = compute_citation_match(wiki_body, wiki_citations) if wiki_body else 0.0
    freshness_regression = False
    if expected_cid and retrieval_mode in ("wiki-first", "wiki-assisted"):
        node = wiki_store.read_node(expected_cid)
        if node:
            freshness_regression = compute_freshness_regression(node.get("frontmatter", {}), max_days=30)

    return {
        "id": case["id"],
        "is_exact_triple": is_exact_triple,
        "is_rich_concept": is_rich,
        "retrieval_mode": retrieval_mode,
        "recall@5": round(recall, 3),
        "mrr": round(mrr, 3),
        "must_mention_rate": round(must_rate, 3),
        "wiki_concept_hit": wiki_concept_hit,
        "wiki_alias_count": len(wiki_mem.get("aliases") or []),
        "wiki_recent_episodes": len(wiki_mem.get("recent_episodes") or []),
        "wiki_confidence": round(wiki_confidence, 2),
        "rouge_l": round(rouge_l, 3),
        "citation_match": round(cit_match, 3),
        "freshness_regression": int(freshness_regression),
        "latency_s": round(elapsed, 3),
        "raw_results_count": len(results),
        "top_doc_ids": top_doc_ids,
    }


def _load_cases(bench: str, limit: int | None) -> list[dict]:
    if bench == "main":
        path = _DATASETS / "fail_history_goldset.json"
        cases = json.loads(path.read_text(encoding="utf-8"))
    elif bench == "wiki_micro":
        path = _DATASETS / "wiki_micro.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        cases = (raw.get("repeat_triple") or []) + (raw.get("alias_variant") or [])
    else:
        raise ValueError(f"unknown bench: {bench}")
    if limit:
        cases = cases[:limit]
    return cases


def _avg(rs: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rs if key in r and isinstance(r[key], (int, float))]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _format_row(metric: str, *vals: float | None, width: int = 12) -> str:
    cells = []
    for v in vals:
        if v is None:
            cells.append(f"{'-':>{width}}")
        else:
            cells.append(f"{v:>{width}.3f}")
    return f"{metric:<26} | " + " | ".join(cells)


def _summary_table(by_mode: dict[str, list[dict]]) -> str:
    """4-way 비교 + 분모 분리 출력."""
    metrics = [
        "recall@5", "mrr", "must_mention_rate",
        "rouge_l", "citation_match", "freshness_regression",
        "wiki_confidence", "wiki_concept_hit",
        "raw_results_count", "latency_s",
    ]

    lines: list[str] = []
    lines.append("=" * 92)
    lines.append("Wiki PoC 4-way 비교 (baseline / wiki-on 자연 분기로 분류)")
    lines.append("=" * 92)

    # 1) 모드별 retrieval_mode 분포
    lines.append("\n[Retrieval mode 분포]")
    for mode, rs in by_mode.items():
        if not rs:
            continue
        dist: dict[str, int] = {}
        for r in rs:
            rm = r.get("retrieval_mode", "baseline")
            dist[rm] = dist.get(rm, 0) + 1
        dist_str = ", ".join(f"{k}={v}" for k, v in sorted(dist.items()))
        lines.append(f"  {mode}: total={len(rs)} ({dist_str})")

    # 2) 분모: 전체
    lines.append("\n[전체 평균]")
    lines.append("-" * 92)
    head = f"{'metric':<26} | " + " | ".join(f"{m:>12}" for m in by_mode.keys())
    lines.append(head)
    lines.append("-" * 92)
    for k in metrics:
        vals = [_avg(by_mode[m], k) for m in by_mode.keys()]
        lines.append(_format_row(k, *vals))

    # 3) 분모: exact-triple만
    lines.append("\n[exact-triple 한정 평균]")
    lines.append("-" * 92)
    lines.append(head)
    lines.append("-" * 92)
    for k in metrics:
        vals = [_avg([r for r in by_mode[m] if r.get("is_exact_triple")], k) for m in by_mode.keys()]
        lines.append(_format_row(k, *vals))

    # 4) 분모: 풍부-concept (vault에 confidence>=0.5 concept 있음)
    lines.append("\n[풍부-concept 한정 평균 (vault에 임계 통과 concept 존재)]")
    lines.append("-" * 92)
    lines.append(head)
    lines.append("-" * 92)
    for k in metrics:
        vals = [_avg([r for r in by_mode[m] if r.get("is_rich_concept")], k) for m in by_mode.keys()]
        lines.append(_format_row(k, *vals))

    # 5) 분모: retrieval_mode가 wiki-first / wiki-assisted인 case만
    for target_mode in ("wiki-first", "wiki-assisted"):
        rs_target = [r for m in by_mode.values() for r in m if r.get("retrieval_mode") == target_mode]
        if not rs_target:
            continue
        lines.append(f"\n[retrieval_mode={target_mode} 한정 평균 (mode 무관 합산, n={len(rs_target)})]")
        lines.append("-" * 92)
        lines.append(f"{'metric':<26} | {'value':>12}")
        for k in metrics:
            v = _avg(rs_target, k)
            lines.append(_format_row(k, v))

    lines.append("\n" + "=" * 92)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Wiki PoC evaluator (4-way)")
    parser.add_argument("--mode", choices=["baseline", "wiki-on", "both"], default="both",
                        help="baseline=wiki off / wiki-on=wiki 자연 분기 / both=둘 다")
    parser.add_argument("--bench", choices=["main", "wiki_micro"], default="main")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--save", action="store_true", help="results/ 에 JSON 저장")
    args = parser.parse_args()

    cases = _load_cases(args.bench, args.limit)
    print(f"[bench={args.bench} mode={args.mode} cases={len(cases)}]")

    modes = ["baseline", "wiki-on"] if args.mode == "both" else [args.mode]
    by_mode: dict[str, list[dict]] = {m: [] for m in modes}

    for case in cases:
        for m in modes:
            force_baseline = (m == "baseline")
            try:
                r = run_case(case, force_baseline)
            except Exception as e:
                r = {"id": case["id"], "error": str(e)}
                print(f"  [{m}] {case['id']} ERROR: {e}")
                by_mode[m].append(r)
                continue
            by_mode[m].append(r)
            print(
                f"  [{m}] {case['id']:<24} mode={r['retrieval_mode']:<14} "
                f"recall={r['recall@5']:.2f} mrr={r['mrr']:.2f} "
                f"must={r['must_mention_rate']:.2f} rouge={r['rouge_l']:.2f} "
                f"cit_m={r['citation_match']:.2f} fresh_reg={r['freshness_regression']} "
                f"raw={r['raw_results_count']} t={r['latency_s']}s"
            )

    print()
    print(_summary_table(by_mode))

    if args.save:
        _RESULTS.mkdir(parents=True, exist_ok=True)
        out = _RESULTS / f"wiki_eval_{args.bench}_{int(time.time())}.json"
        out.write_text(json.dumps({"by_mode": by_mode}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nsaved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

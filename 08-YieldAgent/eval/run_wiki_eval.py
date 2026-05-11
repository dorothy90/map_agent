"""Wiki PoC evaluator (Day 6, plan v3 §eval).

baseline (wiki off) vs wiki-on 비교.
실 OpenSearch + 실 도구 호출. ReAct 루프는 안 돌리고 search_fail_history 직접 호출.

CLI:
  uv run python -m eval.run_wiki_eval --bench main --limit 5
  uv run python -m eval.run_wiki_eval --bench wiki_micro
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

# parent (08-YieldAgent) sys.path 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wiki_queue  # noqa: E402
import wiki_store  # noqa: E402
from fail_history_tools import search_fail_history  # noqa: E402


# ── plan v3 §D 신규 메트릭 함수들 ─────────────────────
def compute_rouge_l(reference: str, candidate: str) -> float:
    """ROUGE-L F1 (LCS 기반, 단순 whitespace 토큰).

    plan v3 §D: concept_improvement_score 측정용.
    baseline answer와 synthesized answer의 ROUGE-L 차이로 합성 효과 정량화.
    """
    ref = (reference or "").lower().split()
    cand = (candidate or "").lower().split()
    if not ref or not cand:
        return 0.0
    m, n = len(ref), len(cand)
    # DP LCS
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
    """plan v3 §D: citation_match_rate.

    body 안의 [ep:xxx] 인용 표시 수 vs citations list 매칭 비율.
    citations가 충분히 등장하면 1.0, 빠지면 비율로.
    """
    import re as _re
    inline = set(_re.findall(r"\[ep:([a-zA-Z0-9_-]+)\]", body or ""))
    cited = {c.get("episode_id", "").replace("episode:", "") for c in (citations or [])}
    if not cited:
        return 0.0
    if not inline:
        # citations만 있고 본문에 인용 없음 → 부분 점수
        return 0.5
    matched = inline & cited
    return round(len(matched) / max(len(cited), 1), 4)


def compute_freshness_regression(node_meta: dict, max_days: int = 30) -> bool:
    """plan v3 §D: 30일 초과 stale 노드가 응답에 쓰였는지. True면 회귀."""
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
    """baseline 모드: wiki lookup/enqueue 무력화."""
    orig_lookup = wiki_store.lookup
    orig_enq = wiki_queue.wiki_queue.summarize_enqueue
    wiki_store.lookup = lambda *a, **k: {"concepts": [], "aliases": [], "recent_episodes": []}
    wiki_queue.wiki_queue.summarize_enqueue = lambda *a, **k: "skipped"
    try:
        yield
    finally:
        wiki_store.lookup = orig_lookup
        wiki_queue.wiki_queue.summarize_enqueue = orig_enq


def _expected_concept_id(filters: dict) -> str | None:
    if all(filters.get(k) for k in ("product", "cause_oper", "fail_type")):
        return f"concept:{filters['product']}|{filters['cause_oper']}|{filters['fail_type']}"
    return None


def run_case(case: dict, wiki_on: bool) -> dict:
    f = dict(case.get("expected_filters", {}) or {})
    cm = nullcontext() if wiki_on else wiki_disabled()
    t0 = time.perf_counter()
    with cm:
        result_str = search_fail_history.invoke({
            "query": case["query"],
            "product": f.get("product", "") or "",
            "fail_type": f.get("fail_type", "") or "",
            "cause_oper": f.get("cause_oper", "") or "",
            "top_k": 5,
        })
    elapsed = time.perf_counter() - t0

    try:
        data = json.loads(result_str)
    except Exception:
        data = {"results": [], "wiki_memory": {}}

    results = data.get("results", []) or []
    wiki_mem = data.get("wiki_memory", {}) or {}

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

    expected_cid = case.get("expected_wiki_concept_id") or _expected_concept_id(f)
    wiki_concept_hit = 0
    if expected_cid and wiki_mem.get("concepts"):
        wiki_concept_hit = int(any(c.get("id") == expected_cid for c in wiki_mem["concepts"]))

    return {
        "id": case["id"],
        "recall@5": round(recall, 3),
        "mrr": round(mrr, 3),
        "must_mention_rate": round(must_rate, 3),
        "wiki_concept_hit": wiki_concept_hit,
        "wiki_alias_count": len(wiki_mem.get("aliases") or []),
        "wiki_recent_episodes": len(wiki_mem.get("recent_episodes") or []),
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


def _summary_table(by_mode: dict[str, list[dict]]) -> str:
    keys = [
        "recall@5", "mrr", "must_mention_rate",
        "wiki_concept_hit", "wiki_alias_count", "wiki_recent_episodes",
        "latency_s",
    ]
    lines = []
    lines.append("=" * 78)
    lines.append(f"{'metric':<24} | {'baseline':>12} | {'wiki-on':>12} | {'delta':>12}")
    lines.append("-" * 78)
    for k in keys:
        bs = by_mode.get("baseline", [])
        ws = by_mode.get("wiki-on", [])
        b = sum(r[k] for r in bs) / max(len(bs), 1) if bs else None
        w = sum(r[k] for r in ws) / max(len(ws), 1) if ws else None
        if b is not None and w is not None:
            d = w - b
            sign = "+" if d >= 0 else ""
            lines.append(f"{k:<24} | {b:>12.3f} | {w:>12.3f} | {sign}{d:>11.3f}")
        elif b is not None:
            lines.append(f"{k:<24} | {b:>12.3f} | {'-':>12} | {'-':>12}")
        elif w is not None:
            lines.append(f"{k:<24} | {'-':>12} | {w:>12.3f} | {'-':>12}")
    lines.append("=" * 78)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Wiki PoC evaluator")
    parser.add_argument("--mode", choices=["baseline", "wiki-on", "both"], default="both")
    parser.add_argument("--bench", choices=["main", "wiki_micro"], default="main")
    parser.add_argument("--limit", type=int, default=5, help="첫 N케이스만")
    parser.add_argument("--save", action="store_true", help="results/ 에 JSON 저장")
    args = parser.parse_args()

    cases = _load_cases(args.bench, args.limit)
    print(f"[bench={args.bench} mode={args.mode} cases={len(cases)}]")

    modes = ["baseline", "wiki-on"] if args.mode == "both" else [args.mode]
    by_mode: dict[str, list[dict]] = {m: [] for m in modes}

    for case in cases:
        for m in modes:
            wiki_on = (m == "wiki-on")
            try:
                r = run_case(case, wiki_on)
            except Exception as e:
                r = {"id": case["id"], "error": str(e)}
                print(f"  [{m}] {case['id']} ERROR: {e}")
                by_mode[m].append(r)
                continue
            by_mode[m].append(r)
            print(
                f"  [{m}] {case['id']:<24} "
                f"recall={r['recall@5']:.2f} mrr={r['mrr']:.2f} "
                f"must={r['must_mention_rate']:.2f} "
                f"c_hit={r['wiki_concept_hit']} alias={r['wiki_alias_count']} "
                f"recent={r['wiki_recent_episodes']} "
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

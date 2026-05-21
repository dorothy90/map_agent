"""합성 1건 테스트 — raw docs → concept 합성 결과의 인용 형식 / 조회 가능 여부 검증.

FH-xxx 날조 없이 [doc:<_id>]로 인용되는지, 그 doc_id가 get_doc 로직으로
404 없이 조회되는지 확인한다. 기본은 vault에 쓰지 않는 순수 테스트.

사용법 (08-YieldAgent 디렉터리에서):
    python _test_synth_one.py                                  # OpenSearch top-1 트리플 자동 선택
    python _test_synth_one.py --product 4SS --fail-type EASY --cause-oper "STI CMP"
    python _test_synth_one.py --apply                          # 검증 후 vault에도 저장
"""
from __future__ import annotations

import argparse
import re

import bootstrap_wiki_warmup as bw
import wiki_store
from fail_history_tools import _OPENSEARCH_INDEX, _get_opensearch_client
from wiki_summarizer import synthesize_concept_from_docs


def _resolve_doc(doc_id: str) -> int:
    """get_doc과 동일한 쿼리(_id + doc_id 필드)로 조회 → hit 수 반환."""
    client = _get_opensearch_client()
    resp = client.search(
        index=_OPENSEARCH_INDEX,
        body={
            "size": 1,
            "_source": {"excludes": ["embedding"]},
            "query": {
                "bool": {
                    "should": [
                        {"ids": {"values": [doc_id]}},
                        {"term": {"doc_id": doc_id}},
                    ],
                    "minimum_should_match": 1,
                }
            },
        },
    )
    return len(resp.get("hits", {}).get("hits", []))


def main() -> int:
    ap = argparse.ArgumentParser(description="합성 1건 테스트")
    ap.add_argument("--product")
    ap.add_argument("--fail-type")
    ap.add_argument("--cause-oper")
    ap.add_argument("--max-docs", type=int, default=15)
    ap.add_argument("--apply", action="store_true", help="검증 후 vault에도 저장")
    args = ap.parse_args()

    if args.product and args.fail_type and args.cause_oper:
        t = {"product": args.product, "fail_type": args.fail_type, "cause_oper": args.cause_oper}
    else:
        triples = bw.fetch_opensearch_triples(top=1, min_docs=1)
        if not triples:
            print("OpenSearch에서 트리플을 못 찾음. --product/--fail-type/--cause-oper로 직접 지정하세요.")
            return 1
        t = triples[0]

    p, f, o = t["product"], t["fail_type"], t["cause_oper"]
    cid = f"concept:{p}|{o}|{f}"
    print(f"트리플: product={p} | fail_type={f} | cause_oper={o}")
    print(f"concept_id: {cid}\n")

    docs = bw.fetch_docs_for_triple(p, f, o, max_docs=args.max_docs)
    print(f"raw docs: {len(docs)}건")
    if not docs:
        print("docs 0건 — 다른 트리플로 시도하세요.")
        return 1
    print("doc_id 샘플:", [d.get("doc_id") for d in docs[:5]], "\n")

    result = synthesize_concept_from_docs(cid, docs)
    if result is None:
        print("합성 실패 (LLM None) — WIKI_SUMMARIZE_MODEL / API 키 등 환경변수 확인")
        return 1

    print("=" * 60, "\n[body_markdown]\n", "=" * 60, sep="")
    print(result.body_markdown)
    print("\n", "=" * 60, "\n[citations]\n", "=" * 60, sep="")
    for c in result.citations:
        print(f"  doc_id={c.doc_id!r}  source_file={c.source_file!r}  date={c.date!r}")

    print("\n", "=" * 60, "\n[검증]\n", "=" * 60, sep="")
    fh_body = re.findall(r"\[FH-[^\]]*\]", result.body_markdown)
    fh_cits = [c.doc_id for c in result.citations if c.doc_id.startswith("FH-")]
    print(f"  body 내 [FH-...] 날조 : {fh_body or '없음 OK'}")
    print(f"  citation 내 FH- ID   : {fh_cits or '없음 OK'}")

    ok = bad = 0
    for c in result.citations:
        if not c.doc_id:
            continue
        if _resolve_doc(c.doc_id) > 0:
            ok += 1
        else:
            bad += 1
            print(f"  ✗ 조회 실패(404 예상) citation doc_id: {c.doc_id!r}")
    print(f"  citation 조회        : {ok}건 OK / {bad}건 실패")

    doc_refs = set(re.findall(r"\[doc:([^\]]+)\]", result.body_markdown))
    bok = bbad = 0
    for ref in doc_refs:
        if _resolve_doc(ref) > 0:
            bok += 1
        else:
            bbad += 1
            print(f"  ✗ 본문 인용 미해결: [doc:{ref}]")
    print(f"  body [doc:...] 인용  : {bok}건 OK / {bbad}건 미해결")

    passed = not fh_body and not fh_cits and bad == 0 and bbad == 0
    print(f"\n  결과: {'PASS ✓' if passed else 'FAIL ✗'}")

    if args.apply:
        wiki_store.upsert_concept(
            filters={"product": p, "fail_type": f, "cause_oper": o},
            source_episode_id=None,
            synthesized_body=result.body_markdown,
            confidence=result.confidence,
            citations=[c.model_dump() for c in result.citations],
            evidence={
                "score": 1.0 if len(docs) >= 5 else len(docs) / 5.0,
                "unique_doc_ids": len({d.get("doc_id") for d in docs if d.get("doc_id")}),
                "n_episodes": 0,
                "n_dates": len({d.get("date") for d in docs if d.get("date")}),
            },
        )
        print(f"\n  vault 저장 완료: {cid}")

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

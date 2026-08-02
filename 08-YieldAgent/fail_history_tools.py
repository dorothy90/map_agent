"""
Fail History @tool 함수 모듈
============================
OpenSearch 하이브리드 검색(BM25 + kNN) + HTML 리포트 렌더링.
fail_history_agent.py에서 사용.
"""
from __future__ import annotations

import contextvars
import functools
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests as http_requests
from jinja2 import Environment, FileSystemLoader
from langfuse import observe

from wiki_config import resolve_wiki_paths
from wiki_graph_models import GraphContext
from wiki_graph_projection import build_graph_projection
from wiki_sync import make_triple_key
from lf_utils import lf_capture_disabled

# ── Jinja2 템플릿 환경 ──────────────────────────────────────
_TEMPLATE_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=True,
)

from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger("yield_agent.fail_history_tools")

# ── 환경변수 ────────────────────────────────────────────────
_OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
_OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
_OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "fail-history")
_OPENSEARCH_USER = os.getenv("OPENSEARCH_USER", "admin")
_OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "admin")
_OPENSEARCH_USE_SSL = os.getenv("OPENSEARCH_USE_SSL", "false").lower() in ("true", "1", "yes")

_EMBEDDING_MODEL = os.getenv("EMBEDDINGS_MODEL_NAME", "qwen/qwen3-embedding-8b")
_EMBEDDING_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
_EMBEDDING_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
_EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "4096"))

_BM25_WEIGHT = float(os.getenv("BM25_WEIGHT", "0.4"))
_VECTOR_WEIGHT = float(os.getenv("VECTOR_WEIGHT", "0.6"))

# ── 약어 사전 ────────────────────────────────────────────────
ACRONYM_MAP: Dict[str, str] = {
    "WTM": "Wafer Test Module",
    "PCB": "Printed Circuit Board",
    "EDS": "Electrical Die Sorting",
    "PKG": "Package",
    "OSAT": "Outsourced Semiconductor Assembly and Test",
    "FAB": "Fabrication",
    "SLT": "System Level Test",
    "FT": "Final Test",
    "CP": "Circuit Probing",
}

# ── ContextVar (요청별 격리) ──────────────────────────────────
_tool_payload_var: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "_fh_tool_payload"
)
# Day 3: wiki ContextVar 분리 (plan v3 §ContextVar 분리). reports lifecycle과 분리.
_wiki_payload_var: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "_fh_wiki_payload"
)

# Day 4 옵션 C: supervisor parsed state를 도구가 직접 읽을 수 있게 노출.
# ReAct LLM이 search_fail_history 호출 시 filter 빈 채로 보내면 fallback으로 보강.
_supervisor_parsed_var: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "_fh_supervisor_parsed"
)


def _get_tool_payload() -> Dict[str, Any]:
    """현재 컨텍스트의 tool payload storage 반환 (없으면 초기화)"""
    try:
        return _tool_payload_var.get()
    except LookupError:
        storage: Dict[str, Any] = {"reports": []}
        _tool_payload_var.set(storage)
        return storage


def _get_wiki_payload() -> Dict[str, Any]:
    """현재 컨텍스트의 wiki payload storage (hit_ids/last_status 등). plan v3 §ContextVar 분리."""
    try:
        return _wiki_payload_var.get()
    except LookupError:
        storage: Dict[str, Any] = {"hit_ids": [], "last_status": "skipped", "queries": []}
        _wiki_payload_var.set(storage)
        return storage


def _get_supervisor_parsed() -> Dict[str, Any]:
    """plan v3 옵션 C: supervisor가 추출한 product/fail_type/cause_oper.

    fail_history_agent_node가 시작 시 set. ReAct LLM이 filter 빠뜨린 호출에 fallback.
    """
    try:
        return _supervisor_parsed_var.get()
    except LookupError:
        return {}


# ── OpenSearch 클라이언트 (싱글턴) ───────────────────────────
_os_client = None
_os_lock = threading.Lock()


def _get_opensearch_client():
    """OpenSearch 클라이언트 싱글턴 (lazy init, thread-safe)"""
    global _os_client
    if _os_client is None:
        with _os_lock:
            if _os_client is None:
                from opensearchpy import OpenSearch

                _os_client = OpenSearch(
                    hosts=[{"host": _OPENSEARCH_HOST, "port": _OPENSEARCH_PORT}],
                    http_auth=(_OPENSEARCH_USER, _OPENSEARCH_PASSWORD),
                    use_ssl=_OPENSEARCH_USE_SSL,
                    verify_certs=False,
                    ssl_show_warn=False,
                    timeout=30,
                )
                logger.info("OpenSearch 클라이언트 생성 (%s:%s)", _OPENSEARCH_HOST, _OPENSEARCH_PORT)
    return _os_client


# ── 임베딩 ───────────────────────────────────────────────────
@functools.lru_cache(maxsize=128)
def _get_embedding_cached(text: str) -> tuple:
    """임베딩 API 호출 (캐시 + 재시도). 캐시 호환을 위해 tuple 반환."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = http_requests.post(
                f"{_EMBEDDING_BASE_URL}/embeddings",
                headers={
                    "Authorization": f"Bearer {_EMBEDDING_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"model": _EMBEDDING_MODEL, "input": text},
                timeout=30,
            )
            resp.raise_for_status()
            return tuple(resp.json()["data"][0]["embedding"])
        except http_requests.exceptions.RequestException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if attempt < max_retries - 1 and (status is None or status >= 429):
                import time
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("임베딩 API 호출 실패 (최대 재시도 초과)")


@observe(name="fh_get_embedding", capture_input=False, capture_output=False)
def _get_embedding(text: str) -> List[float]:
    """OpenRouter API로 텍스트 임베딩 생성 (캐시 + 재시도 적용)"""
    return list(_get_embedding_cached(text))


def _expand_acronyms(query: str) -> str:
    """쿼리 내 약어를 풀네임으로 확장 (BM25 recall 향상)"""
    expanded = query
    for abbr, full in ACRONYM_MAP.items():
        if re.search(rf'\b{abbr}\b', query, re.IGNORECASE):
            expanded += f" {full}"
    return expanded


# fail_type 입력에서 PT1H_TEST_ / PT1C_TEST_ 접두를 제거 (대소문자 무시).
# 인덱스의 fail_type 값은 stage 접두 없이 저장되므로, 외부에서 stage 접두가 붙어
# 들어와도 prefix filter가 0-hit 나지 않도록 정규화한다.
_FAIL_TYPE_STAGE_PREFIX_RE = re.compile(r"^(?:PT1[HC]_TEST_)+", re.IGNORECASE)


def _normalize_fail_type(fail_type: str) -> str:
    if not fail_type:
        return fail_type
    return _FAIL_TYPE_STAGE_PREFIX_RE.sub("", fail_type)


# ── OpenSearch 하이브리드 검색 ────────────────────────────────
def _build_bm25_query(
    query: str,
    product: str = "",
    fail_type: str = "",
    cause_oper: str = "",
) -> tuple[str, list[Dict[str, Any]], Dict[str, Any]]:
    fail_type = _normalize_fail_type(fail_type)
    expanded_query = _expand_acronyms(query)
    filters = []
    if product:
        filters.append({"term": {"product.keyword": product}})
    if fail_type:
        filters.append({
            "wildcard": {
                "fail_type.keyword": {
                    "value": f"*{fail_type}*",
                    "case_insensitive": True,
                }
            }
        })
    if cause_oper:
        filters.append({"term": {"cause_oper": cause_oper}})

    # BM25 쿼리
    bm25_query: Dict[str, Any] = {
        "multi_match": {
            "query": expanded_query,
            "fields": ["content", "cause", "action", "comment", "product", "fail_type"],
        }
    }
    if filters:
        bm25_query = {"bool": {"must": [bm25_query], "filter": filters}}
    return expanded_query, filters, bm25_query


def _format_search_results(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = []
    for hit in response.get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        score = hit.get("_score", 0)
        results.append({
            "product": src.get("product", ""),
            "cause_oper": src.get("cause_oper", ""),
            "fail_type": src.get("fail_type", ""),
            "cause": src.get("cause", ""),
            "action": src.get("action", ""),
            "comment": src.get("comment", ""),
            "date": src.get("date", ""),
            "source_file": src.get("source_file", ""),
            "page_num": src.get("page_num", 0),
            "doc_id": src.get("doc_id", ""),
            "filenm": src.get("filenm", ""),
            "download_url": src.get("download_url", ""),
            "score": round(min(score * 100, 100.0), 1),
            "content": src.get("content", "")[:200],
        })
    return results


def _search_bm25(
    query: str,
    product: str = "",
    fail_type: str = "",
    cause_oper: str = "",
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Run the existing BM25 query without an embedding request."""
    _, _, bm25_query = _build_bm25_query(query, product, fail_type, cause_oper)
    response = _get_opensearch_client().search(
        index=_OPENSEARCH_INDEX,
        body={"size": top_k, "query": bm25_query},
    )
    return _format_search_results(response)


def _search_opensearch_with_embedding(
    query: str,
    embedding: List[float],
    product: str = "",
    fail_type: str = "",
    cause_oper: str = "",
    top_k: int = 5,
) -> tuple[List[Dict[str, Any]], str]:
    """Run hybrid search, using BM25 only for known hybrid search-phase failures."""
    _, filters, bm25_query = _build_bm25_query(
        query, product, fail_type, cause_oper
    )
    client = _get_opensearch_client()

    # kNN 쿼리 (메타데이터 필터 포함)
    # ※ kNN + filter 조합에서 OpenSearch가 exact-kNN 경로로 떨어지면,
    #   필터된 후보군에 embedding=null인 문서가 섞일 때
    #   "cannot read field 'point' because this.point is null" 오류 발생.
    #   filter에 항상 `exists: embedding`을 함께 걸어 null 후보를 배제.
    knn_body: Dict[str, Any] = {"vector": embedding, "k": top_k}
    knn_filters = filters + [{"exists": {"field": "embedding"}}]
    knn_body["filter"] = {"bool": {"filter": knn_filters}}
    knn_query: Dict[str, Any] = {
        "knn": {
            "embedding": knn_body
        }
    }

    # 하이브리드 검색 (인라인 search_pipeline)
    search_body = {
        "size": top_k,
        "query": {
            "hybrid": {
                "queries": [bm25_query, knn_query]
            }
        },
        "search_pipeline": {
            "phase_results_processors": [
                {
                    "normalization-processor": {
                        "normalization": {"technique": "min_max"},
                        "combination": {
                            "technique": "arithmetic_mean",
                            "parameters": {"weights": [_BM25_WEIGHT, _VECTOR_WEIGHT]},
                        },
                    }
                }
            ]
        },
    }

    try:
        response = client.search(
            index=_OPENSEARCH_INDEX,
            body=search_body,
        )
    except Exception as e:
        # OpenSearch hybrid search + normalization-processor 는 sub-query 결과 수가
        # 어긋나면(특히 kNN 후보가 0건일 때) `index -X out of bounds for length Y`
        # 같은 search_phase_execution_exception 을 던지는 알려진 버그가 있다.
        # BM25-only 로 폴백해 사용자에게 빈 화면 대신 결과를 돌려준다.
        msg = str(e)
        is_hybrid_bug = (
            "search_phase_execution_exception" in msg
            or "out of bounds" in msg
            or "this.point is null" in msg
        )
        if not is_hybrid_bug:
            logger.error("OpenSearch 검색 실패: %s", e, exc_info=True)
            raise
        logger.warning("[_search_opensearch] hybrid 실패 — BM25-only 폴백: %s", e)
        try:
            return _search_bm25(
                query=query,
                product=product,
                fail_type=fail_type,
                cause_oper=cause_oper,
                top_k=top_k,
            ), "bm25_fallback"
        except Exception as e2:
            logger.error("OpenSearch BM25 폴백도 실패: %s", e2, exc_info=True)
            raise

    return _format_search_results(response), "hybrid"


def search_opensearch_with_mode(
    query: str,
    product: str = "",
    fail_type: str = "",
    cause_oper: str = "",
    top_k: int = 5,
    *,
    allow_embedding_fallback: bool = False,
) -> tuple[List[Dict[str, Any]], str]:
    """Run OpenSearch and explicitly report hybrid or BM25 fallback retrieval."""
    try:
        embedding = _get_embedding(_expand_acronyms(query))
    except Exception:
        if not allow_embedding_fallback:
            raise
        return _search_bm25(
            query=query,
            product=product,
            fail_type=fail_type,
            cause_oper=cause_oper,
            top_k=top_k,
        ), "bm25_fallback"
    return _search_opensearch_with_embedding(
        query=query,
        embedding=embedding,
        product=product,
        fail_type=fail_type,
        cause_oper=cause_oper,
        top_k=top_k,
    )


@observe(name="fh_search_opensearch", capture_input=False, capture_output=False)
def _search_opensearch(
    query: str,
    product: str = "",
    fail_type: str = "",
    cause_oper: str = "",
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """BM25 + kNN hybrid search preserving the legacy list-only contract."""
    results, _ = search_opensearch_with_mode(
        query,
        product,
        fail_type,
        cause_oper,
        top_k,
        allow_embedding_fallback=False,
    )
    return results


# ── super_concept "참고용" 보조 섹션 (env로 토글) ────
def _lookup_super_reference(product: str, fail_type: str, cause_oper: str) -> str:
    """WIKI_SUPER_REFERENCE_ENABLED=true 시 super_concept 본문 합쳐서 반환.

    Karpathy + plan v3 §B 정신: super는 답변 근거 아닌 "참고용" 보조 섹션.
    fail_type 우선, 그 다음 cause_oper 축. alias 정규화: "EASY(W)" → "EASY".
    """
    if os.getenv("WIKI_SUPER_REFERENCE_ENABLED", "false").lower() not in ("true", "1", "yes"):
        return ""
    try:
        import wiki_store
    except Exception:
        return ""
    parts: List[str] = []
    if fail_type:
        ft_norm = fail_type.split("(", 1)[0].strip()
        s = wiki_store.lookup_super_concept("fail_type", ft_norm)
        if s and s.get("body"):
            parts.append(f"### fail_type={ft_norm} 관련\n{s['body'].strip()}")
    if cause_oper:
        s = wiki_store.lookup_super_concept("cause_oper", cause_oper)
        if s and s.get("body"):
            parts.append(f"### cause_oper={cause_oper} 관련\n{s['body'].strip()}")
    return "\n\n".join(parts)


# ── wiki-first 카드 보조: citations doc_id로 raw 단순 조회 ────
def _fetch_results_by_doc_ids(doc_ids: List[str]) -> List[Dict[str, Any]]:
    """citations의 doc_id로 OpenSearch에서 단순 조회 (BM25/kNN 없음).

    wiki-first 응답일 때 HTML 카드 렌더용 raw 결과를 채우기 위해 사용.
    점수는 의미 없음(0.0). LLM 합성은 안 거치므로 wiki-first 가치(LLM 0회)는 유지.
    """
    doc_ids = [d for d in doc_ids if d]
    if not doc_ids:
        return []
    client = _get_opensearch_client()
    # doc_id는 인덱스에서 type=keyword 단일 매핑 — `.keyword` 서브필드 없음
    body = {
        "size": min(len(doc_ids), 20),
        "query": {"terms": {"doc_id": doc_ids}},
    }
    resp = client.search(index=_OPENSEARCH_INDEX, body=body)
    results: List[Dict[str, Any]] = []
    for hit in resp.get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        results.append({
            "product": src.get("product", ""),
            "cause_oper": src.get("cause_oper", ""),
            "fail_type": src.get("fail_type", ""),
            "cause": src.get("cause", ""),
            "action": src.get("action", ""),
            "comment": src.get("comment", ""),
            "date": src.get("date", ""),
            "source_file": src.get("source_file", ""),
            "page_num": src.get("page_num", 0),
            "doc_id": src.get("doc_id", ""),
            "filenm": src.get("filenm", ""),
            "score": 0.0,
            "content": src.get("content", "")[:200],
        })
    return results


def _seed_concept_ids(
    product: str,
    fail_type: str,
    cause_oper: str,
    results: List[Dict[str, Any]],
) -> List[str]:
    seeds: List[str] = []
    if product and fail_type and cause_oper:
        seeds.append(
            f"concept:{make_triple_key(product, fail_type, cause_oper).canonical}"
        )
    for result in results:
        triple = make_triple_key(
            str(result.get("product") or ""),
            str(result.get("fail_type") or ""),
            str(result.get("cause_oper") or ""),
        )
        if triple.product and triple.fail_type and triple.cause_oper:
            seeds.append(f"concept:{triple.canonical}")
    return list(dict.fromkeys(seeds))


def _merge_evidence(
    results: List[Dict[str, Any]], graph_results: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    merged = {
        str(item.get("doc_id") or ""): item
        for item in results
        if item.get("doc_id")
    }
    for item in graph_results:
        merged.setdefault(str(item.get("doc_id") or ""), item)
    return list(merged.values())


def _expand_graph_context(concept_ids: List[str]) -> GraphContext | None:
    if not concept_ids:
        return None
    try:
        projection = build_graph_projection(resolve_wiki_paths())
        return projection.expand_concepts(concept_ids)
    except Exception as exc:
        logger.warning("[do_search] graph projection unavailable: %s", exc)
        return None


def _ground_graph_context(
    graph_context: GraphContext,
    results: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    resolved_doc_ids = {
        str(result.get("doc_id") or "")
        for result in results
        if result.get("doc_id")
    }
    relations = []
    for relation in graph_context.relations:
        source_doc_ids = [
            doc_id
            for doc_id in relation.source_doc_ids
            if doc_id in resolved_doc_ids
        ]
        if source_doc_ids:
            relations.append(
                relation.model_copy(update={"source_doc_ids": source_doc_ids})
            )
    if not relations:
        return None
    grounded = graph_context.model_copy(
        update={
            "relations": relations,
            "source_doc_ids": [
                doc_id
                for doc_id in graph_context.source_doc_ids
                if doc_id in resolved_doc_ids
            ],
        }
    )
    return grounded.model_dump(mode="json")


# ── 함수형 노드 API (B2: ReAct 제거, 코드가 직접 호출) ────
def do_search(
    query: str,
    product: str = "",
    fail_type: str = "",
    cause_oper: str = "",
    top_k: int = 5,
) -> Dict[str, Any]:
    """OpenSearch 하이브리드 검색 (BM25 + kNN)으로 불량이력 조회. dict 반환.

    retrieval_mode: "wiki-first" | "wiki-assisted" | "baseline"
    wiki-first 시 rendered_answer(markdown) 포함 → 추가 합성 불요.
    """
    if not query or not query.strip():
        return {
            "total": 0,
            "results": [],
            "retrieval_mode": "empty_query",
            "evidence_sensitive": False,
        }
    top_k = max(1, min(top_k, 20))

    if not product and not fail_type and not cause_oper:
        sp = _get_supervisor_parsed()
        if sp:
            product = sp.get("product", "")
            fail_type = sp.get("fail_type", "")
            cause_oper = sp.get("cause_oper", "")
            if any([product, fail_type, cause_oper]):
                logger.info(
                    "[do_search] supervisor fallback applied: product=%s fail_type=%s cause_oper=%s",
                    product, fail_type, cause_oper,
                )

    logger.info(
        "[do_search] query=%s, product=%s, fail_type=%s, cause_oper=%s, top_k=%d",
        query, product, fail_type, cause_oper, top_k,
    )

    gate_result: Optional[Dict[str, Any]] = None
    wiki_first_enabled = os.getenv("WIKI_FIRST_ENABLED", "true").lower() in ("true", "1", "yes")
    if wiki_first_enabled and product and fail_type and cause_oper:
        try:
            import wiki_store
            gate_result = wiki_store.lookup_concept_body({
                "product": product,
                "fail_type": fail_type,
                "cause_oper": cause_oper,
            })
        except Exception as e:
            logger.warning("[do_search] wiki gate lookup 실패: %s", e)

    exact_seed_ids = _seed_concept_ids(product, fail_type, cause_oper, [])
    graph_context = (
        _expand_graph_context(exact_seed_ids) if exact_seed_ids else None
    )

    if gate_result and gate_result.get("gate") == "wiki-first":
        logger.info("[do_search] WIKI-FIRST mode (confidence=%.2f, %s)",
                    gate_result["confidence"], gate_result["concept_id"])
        ws = _get_wiki_payload()
        ws["last_status"] = "wiki-first"
        ws.setdefault("queries", []).append(query)
        ws.setdefault("hit_ids", []).append(gate_result["concept_id"])
        body = gate_result.get("body", "")
        confidence = gate_result.get("confidence", 0.0)
        citations = _format_user_citations(gate_result.get("citations", []))
        cit_lines: List[str] = []
        for c in citations:
            label = c["natural_label"] or c["doc_id"] or c["source_file"]
            url = c["download_url"]
            if url:
                cit_lines.append(f"- {label} [원본 PPT 다운로드]({url})")
            else:
                cit_lines.append(f"- {label}")
        citations_md = "\n".join(cit_lines) if cit_lines else "(없음)"
        # Karpathy 회귀로 임계 완화 → 저신뢰 concept도 wiki-first 발동.
        # 사용자 판단을 위한 confidence 배지 (≥0.7 무배지 / 0.5~0.7 참고용 / <0.5 근거 약함)
        evidence_n = len(cit_lines)
        if confidence < 0.5:
            badge = (f"> ⚠️ **근거 약함** — evidence {evidence_n}건, "
                     f"confidence={confidence:.2f}. 운영자 검수 권장.\n\n")
        elif confidence < 0.7:
            badge = (f"> ℹ️ **참고용** — evidence {evidence_n}건, "
                     f"confidence={confidence:.2f}.\n\n")
        else:
            badge = ""
        # 옵션 4: citations doc_id로 OpenSearch 단순 조회 → HTML 카드용 raw 채움.
        # LLM 합성은 안 거치므로 wiki-first 본질(LLM 0회)은 유지. 점수 의미 없음.
        doc_ids = [c.get("doc_id", "") for c in citations if c.get("doc_id")]
        if graph_context is not None:
            doc_ids = list(dict.fromkeys(doc_ids + graph_context.source_doc_ids))
        card_results = _fetch_results_by_doc_ids(doc_ids)
        grounded_graph_context = (
            _ground_graph_context(graph_context, card_results)
            if graph_context is not None
            else None
        )
        rendered_answer = (
            f"{badge}{body}\n\n"
            f"**참고 자료 ({len(cit_lines)}건)**:\n{citations_md}\n\n"
            f"_wiki-first 응답 · confidence={confidence:.2f} · OpenSearch lookup {len(card_results)}건 (카드용, LLM 호출 0회)_"
        )
        output = {
            "total": len(card_results),
            "results": card_results,
            "wiki_memory": {"concepts": [], "aliases": [], "recent_episodes": []},
            "retrieval_mode": "wiki-first",
            "wiki_concept_body": body,
            "wiki_concept_confidence": confidence,
            "wiki_concept_id": gate_result.get("concept_id", ""),
            "wiki_citations": citations,
            "rendered_answer": rendered_answer,
            "super_reference_body": _lookup_super_reference(product, fail_type, cause_oper),
            "evidence_sensitive": True,
        }
        if grounded_graph_context is not None:
            output["graph_context"] = grounded_graph_context
        return output

    try:
        results = _search_opensearch(
            query=query,
            product=product,
            fail_type=fail_type,
            cause_oper=cause_oper,
            top_k=top_k,
        )
    except Exception as e:
        logger.error("[do_search] 검색 오류: %s", e, exc_info=True)
        raise

    # 0-hit fallback: fail_type 필터로 인해 결과가 비었을 가능성이 있으면
    # fail_type 필터를 빼고 1회 재검색 (BM25/kNN이 fail_type을 텍스트로 매칭).
    # 예: 사용자 "snc_no" / 인덱스값 "pteidx_snc_n/o"처럼 wildcard도 빗나가는 케이스.
    fail_type_filter_dropped = False
    if not results and fail_type:
        logger.info(
            "[do_search] 0-hit with fail_type=%r — fail_type 필터 제거 후 재시도",
            fail_type,
        )
        try:
            results = _search_opensearch(
                query=query,
                product=product,
                fail_type="",
                cause_oper=cause_oper,
                top_k=top_k,
            )
            fail_type_filter_dropped = True
        except Exception as e:
            logger.error("[do_search] fallback 검색 오류: %s", e, exc_info=True)
            raise

    opensearch_results = results
    if graph_context is None and not exact_seed_ids:
        graph_context = _expand_graph_context(
            _seed_concept_ids(product, fail_type, cause_oper, results)
        )

    if graph_context is not None:
        existing_doc_ids = {
            str(result.get("doc_id") or "")
            for result in results
            if result.get("doc_id")
        }
        graph_only_doc_ids = [
            doc_id
            for doc_id in graph_context.source_doc_ids
            if doc_id not in existing_doc_ids
        ]
        graph_results = (
            _fetch_results_by_doc_ids(graph_only_doc_ids)
            if graph_only_doc_ids
            else []
        )
        results = _merge_evidence(results, graph_results)
        grounded_graph_context = _ground_graph_context(graph_context, results)
    else:
        grounded_graph_context = None

    if not results:
        super_reference_body = _lookup_super_reference(
            product, fail_type, cause_oper
        )
        return {
            "total": 0,
            "results": [],
            "retrieval_mode": "baseline",
            "super_reference_body": super_reference_body,
            "evidence_sensitive": bool(
                grounded_graph_context is not None or super_reference_body
            ),
        }

    wiki_mem: Dict[str, Any] = {"concepts": [], "aliases": [], "recent_episodes": []}
    try:
        import wiki_store
        wiki_mem = wiki_store.lookup(
            query=query,
            filters={"product": product, "fail_type": fail_type, "cause_oper": cause_oper},
            max_episodes=3,
        )
        ws = _get_wiki_payload()
        ws.setdefault("hit_ids", []).extend(
            [c["id"] for c in wiki_mem.get("concepts", [])]
            + [e["id"] for e in wiki_mem.get("recent_episodes", []) if e.get("id")]
        )
    except Exception as e:
        logger.warning("[do_search] wiki lookup 실패: %s", e)

    super_reference_body = _lookup_super_reference(product, fail_type, cause_oper)
    evidence_sensitive = bool(
        grounded_graph_context is not None
        or any(wiki_mem.get(key) for key in ("concepts", "aliases", "recent_episodes"))
        or (gate_result and gate_result.get("body"))
        or super_reference_body
    )

    enqueue_status = "skipped"
    try:
        from wiki_queue import wiki_queue
        enqueue_status = wiki_queue.summarize_enqueue(
            {
                "query": query,
                "filters": {
                    "product": product,
                    "fail_type": fail_type,
                    "cause_oper": cause_oper,
                },
                "raw_results": opensearch_results,
            },
            private=lf_capture_disabled() or evidence_sensitive,
        )
    except Exception as e:
        logger.warning("[do_search] wiki enqueue 실패: %s", e)
    ws = _get_wiki_payload()
    ws["last_status"] = enqueue_status
    ws.setdefault("queries", []).append(query)

    output: Dict[str, Any] = {
        "total": len(results),
        "results": results,
        "wiki_memory": wiki_mem,
        "retrieval_mode": "baseline",
        "fail_type_filter_dropped": fail_type_filter_dropped,
        "evidence_sensitive": evidence_sensitive,
    }

    if gate_result and gate_result.get("gate") == "wiki-assisted":
        output["retrieval_mode"] = "wiki-assisted"
        output["wiki_concept_body"] = gate_result.get("body", "")
        output["wiki_concept_confidence"] = gate_result.get("confidence", 0.0)
        output["wiki_concept_id"] = gate_result.get("concept_id", "")
        output["wiki_citations"] = _format_user_citations(gate_result.get("citations", []))
        logger.info("[do_search] WIKI-ASSISTED mode (confidence=%.2f)",
                    gate_result["confidence"])

    if grounded_graph_context is not None:
        output["retrieval_mode"] = "graph-assisted"
        output["graph_context"] = grounded_graph_context

    output["super_reference_body"] = super_reference_body
    return output


# ── plan v3 §C 헬퍼 ─────────────────────────────────────
def _format_user_citations(citations: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """citation에 download_url + natural_label 보강. doc_id 기반 placeholder."""
    download_base = os.getenv("DOWNLOAD_BASE_URL", "https://internal-api.example.com/docs")
    out: List[Dict[str, str]] = []
    for c in citations[:8]:
        doc_id = str(c.get("doc_id", "") or "")
        date = str(c.get("date", "") or "")
        source_file = str(c.get("source_file", "") or "")
        existing_url = str(c.get("download_url", "") or "")
        existing_label = str(c.get("natural_label", "") or "")
        # source_file 없으면 doc_id 기반 placeholder
        if not source_file and doc_id:
            source_file = f"{doc_id}.pptx"
        url = existing_url or (f"{download_base}/{source_file}" if source_file else "")
        label = existing_label or (f"{date} {doc_id}".strip() if (date or doc_id) else source_file)
        out.append({
            "episode_id": str(c.get("episode_id", "") or ""),
            "doc_id": doc_id,
            "date": date,
            "source_file": source_file,
            "natural_label": label,
            "download_url": url,
        })
    return out


def do_render_report(query: str, results: List[Dict[str, Any]], summary: str = "") -> str:
    """검색 결과 → Fail History HTML 카드. storage에 저장 + HTML 반환.

    Args:
        query: Query Card에 표시될 검색 쿼리
        results: do_search()의 results list (raw 검색 결과)
        summary: RAG Summary 카드 본문 (LLM 합성 답변 그대로 가능)
    """
    if not results:
        return ""
    logger.info("[do_render_report] query=%s, results=%d, summary_len=%d",
                query, len(results), len(summary))
    storage = _get_tool_payload()
    html = _render_fail_history_html(results, query, summary)
    storage.setdefault("reports", []).append({
        "html": html,
        "query": query,
        "total": len(results),
    })
    return html


# ── HTML 렌더링 ───────────────────────────────────────────────
def _render_fail_history_html(
    results: List[Dict[str, Any]],
    query: str,
    summary: str,
) -> str:
    """Fail History 결과를 번호 표 HTML로 렌더 (Jinja2 템플릿)."""

    # TODO: 사내 API 실제 URL로 교체
    download_base_url = os.getenv("DOWNLOAD_BASE_URL", "https://internal-api.example.com/docs")

    template = _jinja_env.get_template("fail_history_report.html")
    return template.render(
        results=results,
        query=query,
        summary=summary,
        total=len(results),
        download_base_url=download_base_url,
    )

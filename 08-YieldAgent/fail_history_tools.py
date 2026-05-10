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
from langchain_core.tools import tool
from langfuse import observe

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


@observe(name="fh_get_embedding")
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


# ── OpenSearch 하이브리드 검색 ────────────────────────────────
@observe(name="fh_search_opensearch")
def _search_opensearch(
    query: str,
    product: str = "",
    fail_type: str = "",
    cause_oper: str = "",
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """BM25 + kNN 하이브리드 검색 실행"""
    client = _get_opensearch_client()

    # 약어 확장 후 임베딩 생성
    expanded_query = _expand_acronyms(query)
    embedding = _get_embedding(expanded_query)

    # 메타데이터 필터 구성
    # product / fail_type은 text + .keyword 매핑 → 정확매칭 위해 .keyword 사용
    # cause_oper는 keyword 단일 매핑 → 그대로 사용
    # fail_type 값은 인덱스에 "EASY(W)"처럼 alias 포함으로 저장 → prefix 매칭으로
    #   사용자 입력("EASY")과 alias 포함값 양쪽 모두 수용 가능
    filters = []
    if product:
        filters.append({"term": {"product.keyword": product}})
    if fail_type:
        filters.append({"prefix": {"fail_type.keyword": fail_type}})
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

    # kNN 쿼리 (메타데이터 필터 포함)
    knn_body: Dict[str, Any] = {"vector": embedding, "k": top_k}
    if filters:
        knn_body["filter"] = {"bool": {"filter": filters}}
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
        logger.error("OpenSearch 검색 실패: %s", e, exc_info=True)
        raise

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
            "score": round(min(score * 100, 100.0), 1),
            "content": src.get("content", "")[:200],
        })

    return results


# ── @tool 함수 ────────────────────────────────────────────────
@tool
def search_fail_history(
    query: str,
    product: str = "",
    fail_type: str = "",
    cause_oper: str = "",
    top_k: int = 5,
) -> str:
    """OpenSearch 하이브리드 검색 (BM25 + kNN)으로 불량이력을 조회합니다.

    Args:
        query: 검색 쿼리 (자유 텍스트, 예: "TWT 불량", "M0C ETCH RF TIME 원인")
        product: 제품 필터 (예: "4SS", "4SA", "6E2", "5QQ"). 미입력시 전체 조회.
        fail_type: 불량 유형 필터 (예: "TWT", "IOFF(F)", "VTH"). 미입력시 전체 조회.
        cause_oper: 원인 공정 필터 (예: "M0C ETCH", "ISO ETCH"). 미입력시 전체 조회.
        top_k: 검색 결과 수 (기본 5)

    Returns:
        검색 결과 JSON 문자열 (건수 + 각 결과의 메타정보)
    """
    if not query or not query.strip():
        return "검색어를 입력해주세요."
    top_k = max(1, min(top_k, 20))

    logger.info(
        "[search_fail_history] query=%s, product=%s, fail_type=%s, cause_oper=%s, top_k=%d",
        query, product, fail_type, cause_oper, top_k,
    )

    try:
        results = _search_opensearch(
            query=query,
            product=product,
            fail_type=fail_type,
            cause_oper=cause_oper,
            top_k=top_k,
        )
    except Exception as e:
        logger.error("[search_fail_history] 검색 오류: %s", e, exc_info=True)
        return "오류: 불량이력 검색에 실패했습니다. 잠시 후 다시 시도해주세요."

    if not results:
        return "조건에 맞는 불량이력이 없습니다."

    # ── wiki_memory lookup (Day 3) — 동기 디스크 read만 (ms) ───
    wiki_mem: Dict[str, Any] = {"concepts": [], "aliases": [], "recent_episodes": []}
    try:
        import wiki_store
        wiki_mem = wiki_store.lookup(
            query=query,
            filters={"product": product, "fail_type": fail_type, "cause_oper": cause_oper},
            max_episodes=3,
        )
        wiki_storage = _get_wiki_payload()
        wiki_storage.setdefault("hit_ids", []).extend(
            [c["id"] for c in wiki_mem.get("concepts", [])]
            + [e["id"] for e in wiki_mem.get("recent_episodes", []) if e.get("id")]
        )
    except Exception as e:
        logger.warning("[search_fail_history] wiki lookup 실패: %s", e)

    # ── wiki ingest enqueue (비동기, 사용자 응답에 무영향) ────
    enqueue_status = "skipped"
    try:
        from wiki_queue import wiki_queue
        enqueue_status = wiki_queue.summarize_enqueue({
            "query": query,
            "filters": {"product": product, "fail_type": fail_type, "cause_oper": cause_oper},
            "raw_results": results,
        })
    except Exception as e:
        logger.warning("[search_fail_history] wiki enqueue 실패: %s", e)
    wiki_storage = _get_wiki_payload()
    wiki_storage["last_status"] = enqueue_status
    wiki_storage.setdefault("queries", []).append(query)

    output = {
        "total": len(results),
        "results": results,           # 기존 호환 — render_fail_report가 사용
        "wiki_memory": wiki_mem,      # Day 3 신규 — ReAct LLM이 보조 컨텍스트로 사용
    }
    return json.dumps(output, ensure_ascii=False, indent=2)


@tool
def render_fail_report(
    query: str,
    results_json: str,
    summary: str = "",
) -> str:
    """검색 결과를 Fail History HTML 리포트로 렌더링합니다.
    search_fail_history의 반환값을 그대로 results_json에 전달하세요.

    Args:
        query: 검색에 사용된 쿼리 (Query Card에 표시)
        results_json: search_fail_history의 반환값 (JSON 문자열)
        summary: 종합 요약 (RAG Summary 카드에 표시). 2-3문장으로 작성.

    Returns:
        렌더링 완료 메시지 (HTML은 화면에 별도 표시)
    """
    logger.info("[render_fail_report] query=%s, summary_len=%d", query, len(summary))
    storage = _get_tool_payload()

    try:
        data = json.loads(results_json)
        results = data.get("results", [])
    except (json.JSONDecodeError, AttributeError):
        return "오류: results_json 파싱에 실패했습니다. search_fail_history의 반환값을 그대로 전달하세요."

    if not results:
        return "렌더링할 검색 결과가 없습니다."

    html = _render_fail_history_html(results, query, summary)

    storage.setdefault("reports", []).append({
        "html": html,
        "query": query,
        "total": len(results),
    })

    return f"Fail History HTML 리포트 렌더링 완료: {len(results)}건 (리포트는 화면에 별도 표시됩니다)"


# ── HTML 렌더링 ───────────────────────────────────────────────
def _render_fail_history_html(
    results: List[Dict[str, Any]],
    query: str,
    summary: str,
) -> str:
    """gui_v2.html 포맷의 Fail History HTML 리포트 동적 생성 (Jinja2 템플릿)"""

    products = list(dict.fromkeys(r["product"] for r in results if r.get("product")))
    opers = list(dict.fromkeys(r["cause_oper"] for r in results if r.get("cause_oper")))

    # TODO: 사내 API 실제 URL로 교체
    download_base_url = os.getenv("DOWNLOAD_BASE_URL", "https://internal-api.example.com/docs")

    # Day 5: wiki graph deep link — Streamlit dev viewer로 (PoC 시각화)
    # 운영 React repo 도입 후엔 STREAMLIT_BASE_URL → REACT_BASE_URL로 교체 (plan v3 §부록)
    _viewer_base = os.getenv("STREAMLIT_BASE_URL", "http://localhost:8501")
    wiki_graph_url = f"{_viewer_base}/wiki_graph"
    if results:
        from urllib.parse import quote
        r = results[0]
        triple = (r.get("product", ""), r.get("cause_oper", ""), r.get("fail_type", ""))
        if all(triple):
            cid = "concept:" + "|".join(triple)
            wiki_graph_url = f"{_viewer_base}/wiki_graph?focus={quote(cid, safe='|():')}"

    template = _jinja_env.get_template("fail_history_report.html")
    return template.render(
        results=results,
        query=query,
        summary=summary,
        total=len(results),
        products=products,
        opers=opers,
        index_name=_OPENSEARCH_INDEX,
        embedding_dim=_EMBEDDING_DIM,
        download_base_url=download_base_url,
        wiki_graph_url=wiki_graph_url,
    )


# ── 도구 리스트 ──────────────────────────────────────────────
FAIL_HISTORY_TOOLS = [search_fail_history, render_fail_report]

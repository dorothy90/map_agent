"""
Fail History @tool 함수 모듈
============================
OpenSearch 하이브리드 검색(BM25 + kNN) + HTML 리포트 렌더링.
fail_history_agent.py에서 사용.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

import requests as http_requests
from langchain_core.tools import tool
from langfuse import observe

from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger("yield_agent.fail_history_tools")

# ── 환경변수 ────────────────────────────────────────────────
_OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
_OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
_OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "defect-history")
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


def _get_tool_payload() -> Dict[str, Any]:
    """현재 컨텍스트의 tool payload storage 반환 (없으면 초기화)"""
    try:
        return _tool_payload_var.get()
    except LookupError:
        storage: Dict[str, Any] = {"reports": []}
        _tool_payload_var.set(storage)
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
@observe(name="fh_get_embedding")
def _get_embedding(text: str) -> List[float]:
    """OpenRouter API로 텍스트 임베딩 생성 (OpenAI 호환)"""
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
    return resp.json()["data"][0]["embedding"]


def _expand_acronyms(query: str) -> str:
    """쿼리 내 약어를 풀네임으로 확장 (BM25 recall 향상)"""
    expanded = query
    for abbr, full in ACRONYM_MAP.items():
        if abbr in query.upper():
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
    filters = []
    if product:
        filters.append({"term": {"product": product}})
    if fail_type:
        filters.append({"term": {"defect_type": fail_type}})
    if cause_oper:
        filters.append({"term": {"cause_oper": cause_oper}})

    # BM25 쿼리
    bm25_query: Dict[str, Any] = {
        "multi_match": {
            "query": expanded_query,
            "fields": ["content", "cause", "action", "comment", "product", "defect_type"],
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
            "fail_type": src.get("defect_type", ""),
            "cause": src.get("cause", ""),
            "action": src.get("action", ""),
            "comment": src.get("comment", ""),
            "date": src.get("date", ""),
            "source_file": src.get("source_file", ""),
            "page_num": src.get("page_num", 0),
            "score": round(score * 100, 1) if score <= 1 else round(score, 1),
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
        return f"오류: OpenSearch 검색에 실패했습니다. ({e})"

    if not results:
        return "조건에 맞는 불량이력이 없습니다."

    # content 필드는 검색 결과 확인용으로만 포함, render에는 불필요
    output = {
        "total": len(results),
        "results": results,
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
def _html_escape(s: Any) -> str:
    txt = "" if s is None else str(s)
    return (
        txt.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _render_fail_history_html(
    results: List[Dict[str, Any]],
    query: str,
    summary: str,
) -> str:
    """gui_v2.html 포맷의 Fail History HTML 리포트 동적 생성"""

    top3 = results[:3]
    total = len(results)

    # 제품/공정 칩 수집 (중복 제거)
    products = list(dict.fromkeys(r["product"] for r in results if r.get("product")))
    opers = list(dict.fromkeys(r["cause_oper"] for r in results if r.get("cause_oper")))

    # 칩 HTML
    chips_html = ""
    for p in products:
        chips_html += f'<span class="chip chip-product">{_html_escape(p)}</span>\n'
    for o in opers:
        chips_html += f'<span class="chip chip-oper">{_html_escape(o)}</span>\n'

    # Match Cards HTML
    accent_classes = ["accent-1", "accent-2", "accent-3"]
    rank_classes = ["rank-1", "rank-2", "rank-3"]
    progress_classes = ["progress-1", "progress-2", "progress-3"]
    details_classes = ["details-1", "details-2", "details-3"]

    match_cards_html = ""
    for idx, r in enumerate(top3):
        score = r.get("score", 0)
        offset = round(188.5 * (1 - score / 100), 1)
        title = f'{_html_escape(r.get("product", ""))} / {_html_escape(r.get("cause_oper", ""))} / {_html_escape(r.get("fail_type", ""))}'

        match_cards_html += f"""
        <div class="card match-card">
            <div class="match-accent {accent_classes[idx]}"></div>
            <div class="match-body">
                <a class="file-icon" title="다운로드"><svg width="20" height="24" viewBox="0 0 20 24" fill="none"><path d="M12 1H3a2 2 0 0 0-2 2v18a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8l-7-7z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M12 1v7h7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M10 13l-3 3 3 3M10 13l3 3-3 3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" opacity="0"/></svg></a>
                <div class="match-content">
                    <div class="match-header">
                        <div class="match-title-group">
                            <div class="match-rank-row">
                                <span class="match-rank {rank_classes[idx]}">RANK #{idx+1:02d}</span>
                                <span class="match-date">{_html_escape(r.get("date", ""))}</span>
                            </div>
                            <span class="match-title">{title}</span>
                        </div>
                        <div class="score-ring-wrapper">
                            <div class="score-ring">
                                <svg viewBox="0 0 64 64">
                                    <circle class="bg" cx="32" cy="32" r="30" />
                                    <circle class="progress {progress_classes[idx]}" cx="32" cy="32" r="30"
                                        style="stroke-dashoffset: {offset};" />
                                </svg>
                                <span class="score-number" data-target="{score}">0</span>
                            </div>
                            <span class="score-label">RRF Score</span>
                        </div>
                    </div>
                    <div class="match-details {details_classes[idx]}">
                        <div class="detail-row">
                            <span class="detail-label label-cause">Root Cause</span>
                            <span class="detail-text">{_html_escape(r.get("cause", ""))}</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label label-action">Verified Action</span>
                            <span class="detail-text text-action">{_html_escape(r.get("action", ""))}</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>"""

    # Summary Card HTML
    summary_html = ""
    if summary:
        summary_html = f"""
        <div class="summary-card">
            <div class="summary-content">
                <div class="summary-header">
                    <div class="summary-icon">
                        <div class="pulse-dot" style="width:8px;height:8px;animation-delay:1s;"></div>
                    </div>
                    <span class="summary-title">INTEGRATED RAG SUMMARY</span>
                </div>
                <p class="summary-text">{_html_escape(summary)}</p>
                <div class="summary-stats">
                    <div class="stat-item">
                        <span class="stat-label">Index</span>
                        <span class="stat-value">{_html_escape(_OPENSEARCH_INDEX)}</span>
                    </div>
                    <div class="stat-item">
                        <span class="stat-label">Documents</span>
                        <span class="stat-value">{total}</span>
                    </div>
                    <div class="stat-item">
                        <span class="stat-label">Embedding</span>
                        <span class="stat-value">{_EMBEDDING_DIM}-dim</span>
                    </div>
                    <div class="stat-item">
                        <span class="stat-label">Method</span>
                        <span class="stat-value">Hybrid RRF</span>
                    </div>
                </div>
                <div class="summary-footer">
                    <span>Powered by <em>BM25 + kNN Vector</em> Reciprocal Rank Fusion</span>
                </div>
            </div>
        </div>"""

    # 전체 HTML 조립
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Fail History Intelligence Report</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet" />
    <style>
        :root {{
            --bg-body: #f0f4f8; --bg-card: #ffffff;
            --text-main: #1a1f26; --text-muted: #73808c; --text-dim: #a0aab4;
            --primary: #00ccd9; --primary-glow: rgba(0,204,217,0.3); --primary-dim: rgba(0,204,217,0.08);
            --blue: #4d8eff; --purple: #a855f7;
            --danger: #e53333; --danger-bg: rgba(229,51,51,0.08);
            --success: #1ab280; --success-bg: rgba(26,178,128,0.08);
            --border: #e2e8f0; --border-hover: rgba(0,204,217,0.35);
            --shadow-sm: 0 2px 8px rgba(0,0,0,0.04); --shadow-lg: 0 16px 32px rgba(0,0,0,0.1);
            --radius-md: 14px; --radius-lg: 18px;
            --transition: all 0.35s cubic-bezier(0.25,0.8,0.25,1);
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: linear-gradient(160deg,#f0f4f8 0%,#e4eaf0 50%,#f0f4f8 100%);
            min-height: 100vh; display: flex; justify-content: center; padding: 48px 20px;
            font-family: 'Inter', sans-serif; color: var(--text-main); position: relative; overflow-x: hidden;
        }}
        body::before {{
            content: ''; position: fixed; inset: 0;
            background-image: radial-gradient(circle,rgba(0,0,0,0.04) 1px,transparent 1px);
            background-size: 32px 32px; z-index: 0; pointer-events: none;
        }}
        .container {{ width: 100%; max-width: 740px; display: flex; flex-direction: column; gap: 24px; position: relative; z-index: 1; }}
        .header {{ display: flex; align-items: center; justify-content: space-between; padding: 0 4px; animation: fadeSlideDown 0.6s ease-out; }}
        .header-left {{ display: flex; align-items: center; gap: 14px; }}
        .pulse-dot {{ width: 11px; height: 11px; border-radius: 50%; background: var(--primary); box-shadow: 0 0 0 0 var(--primary-glow); animation: pulse 2s infinite; }}
        @keyframes pulse {{ 0% {{ transform: scale(0.95); box-shadow: 0 0 0 0 var(--primary-glow); }} 70% {{ transform: scale(1); box-shadow: 0 0 0 10px rgba(0,204,217,0); }} 100% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(0,204,217,0); }} }}
        .header-title {{ font-family: 'Outfit', sans-serif; font-size: 24px; font-weight: 700; letter-spacing: 1px; background: linear-gradient(135deg,#1a1f26,#3b4252); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
        .header-time {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-dim); letter-spacing: 0.5px; }}
        .section-header {{ display: flex; justify-content: space-between; align-items: flex-end; padding-bottom: 14px; border-bottom: 2px solid rgba(0,0,0,0.04); animation: fadeSlideDown 0.6s ease-out 0.1s both; }}
        .section-title {{ font-family: 'Outfit', sans-serif; font-size: 18px; font-weight: 700; color: var(--text-main); letter-spacing: 0.5px; }}
        .badge-count {{ background: var(--danger-bg); border: 1px solid rgba(229,51,51,0.2); border-radius: 20px; padding: 6px 14px; font-size: 11px; font-weight: 700; color: var(--danger); letter-spacing: 0.5px; }}
        .card {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 26px; box-shadow: var(--shadow-sm); transition: var(--transition); position: relative; overflow: hidden; }}
        .card:hover {{ transform: translateY(-3px); box-shadow: var(--shadow-lg); border-color: var(--border-hover); }}
        .query-card {{ animation: fadeSlideDown 0.6s ease-out 0.2s both; }}
        .query-card::before {{ content: ''; position: absolute; top: 0; left: 0; width: 100%; height: 4px; background: linear-gradient(90deg,var(--danger),#ff6b6b); }}
        .query-label {{ font-size: 11px; font-weight: 700; color: var(--danger); letter-spacing: 1px; text-transform: uppercase; margin-bottom: 6px; display: block; }}
        .query-value {{ font-family: 'Outfit', sans-serif; font-size: 28px; font-weight: 800; color: var(--text-main); margin-bottom: 18px; }}
        .chip-group {{ display: flex; gap: 8px; flex-wrap: wrap; }}
        .chip {{ border-radius: 20px; padding: 5px 14px; font-size: 11px; font-weight: 600; transition: var(--transition); cursor: default; letter-spacing: 0.3px; }}
        .chip:hover {{ transform: scale(1.05); }}
        .chip-product {{ background: rgba(0,204,217,0.08); border: 1px solid rgba(0,204,217,0.2); color: #009da8; }}
        .chip-oper {{ background: rgba(77,142,255,0.08); border: 1px solid rgba(77,142,255,0.2); color: #3366cc; }}
        .match-card {{ display: flex; gap: 0; padding: 0; overflow: hidden; }}
        .match-accent {{ width: 5px; flex-shrink: 0; transition: width 0.3s ease; }}
        .match-card:hover .match-accent {{ width: 7px; }}
        .accent-1 {{ background: linear-gradient(180deg,var(--primary),var(--blue)); }}
        .accent-2 {{ background: linear-gradient(180deg,var(--blue),var(--purple)); }}
        .accent-3 {{ background: linear-gradient(180deg,var(--purple),#ec4899); }}
        .match-body {{ display: flex; gap: 20px; padding: 24px; flex: 1; align-items: stretch; }}
        @media (max-width: 640px) {{ .match-body {{ flex-direction: column; }} }}
        .file-icon {{ width: 36px; flex-shrink: 0; display: flex; align-items: center; justify-content: center; cursor: pointer; text-decoration: none; transition: var(--transition); border-radius: 8px; color: var(--text-dim); }}
        .file-icon:hover {{ color: var(--primary); transform: scale(1.1); }}
        .match-rank-row {{ display: flex; align-items: center; gap: 8px; }}
        .match-date {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-dim); font-weight: 600; letter-spacing: 0.3px; }}
        .match-content {{ flex: 1; display: flex; flex-direction: column; gap: 16px; justify-content: center; }}
        .match-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }}
        .match-title-group {{ display: flex; flex-direction: column; gap: 8px; }}
        .match-rank {{ font-size: 10px; font-weight: 800; letter-spacing: 1px; text-transform: uppercase; padding: 4px 12px; border-radius: 20px; display: inline-block; align-self: flex-start; }}
        .rank-1 {{ background: rgba(0,204,217,0.1); color: #009da8; }}
        .rank-2 {{ background: rgba(77,142,255,0.1); color: #3366cc; }}
        .rank-3 {{ background: rgba(168,85,247,0.1); color: #8b3fd9; }}
        .match-title {{ font-family: 'Outfit', sans-serif; font-size: 18px; font-weight: 700; color: var(--text-main); }}
        .score-ring-wrapper {{ display: flex; flex-direction: column; align-items: center; gap: 4px; flex-shrink: 0; }}
        .score-ring {{ position: relative; width: 72px; height: 72px; }}
        .score-ring svg {{ transform: rotate(-90deg); width: 72px; height: 72px; }}
        .score-ring .bg {{ fill: none; stroke: #e8ecf1; stroke-width: 4; }}
        .score-ring .progress {{ fill: none; stroke-width: 4; stroke-linecap: round; stroke-dasharray: 188.5; animation: scoreRing 1.2s ease-out forwards; }}
        .progress-1 {{ stroke: url(#grad1); }} .progress-2 {{ stroke: url(#grad2); }} .progress-3 {{ stroke: url(#grad3); }}
        @keyframes scoreRing {{ from {{ stroke-dashoffset: 188.5; }} }}
        .score-number {{ position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-family: 'Outfit', sans-serif; font-size: 18px; font-weight: 800; color: var(--text-main); }}
        .score-label {{ font-size: 9px; font-weight: 700; color: var(--text-dim); letter-spacing: 1px; text-transform: uppercase; }}
        .match-details {{ display: flex; flex-direction: column; gap: 12px; background: #f8fafc; padding: 16px 20px; border-radius: 10px; border-left: 3px solid #cbd5e1; }}
        .details-1 {{ border-left-color: var(--primary); }} .details-2 {{ border-left-color: var(--blue); }} .details-3 {{ border-left-color: var(--purple); }}
        .detail-row {{ display: flex; flex-direction: column; gap: 5px; }}
        .detail-label {{ font-size: 10px; font-weight: 800; letter-spacing: 0.8px; text-transform: uppercase; }}
        .label-cause {{ color: var(--text-muted); }} .label-action {{ color: var(--success); }}
        .detail-text {{ font-size: 13px; line-height: 1.6; color: var(--text-main); }}
        .text-action {{ font-weight: 600; color: var(--primary); }}
        .summary-card {{ background: linear-gradient(145deg,#161b22,#1f2937); border-radius: var(--radius-lg); padding: 34px; position: relative; overflow: hidden; border: 1px solid #374151; box-shadow: 0 20px 25px -5px rgba(0,0,0,0.4),0 10px 10px -5px rgba(0,0,0,0.15); margin-top: 8px; animation: fadeSlideDown 0.6s ease-out 0.8s both; }}
        .summary-card::before {{ content: ''; position: absolute; top: -50%; left: -50%; width: 200%; height: 200%; background: radial-gradient(circle,rgba(0,204,217,0.08) 0%,transparent 50%); z-index: 0; pointer-events: none; }}
        .summary-content {{ position: relative; z-index: 1; display: flex; flex-direction: column; gap: 22px; }}
        .summary-header {{ display: flex; align-items: center; gap: 12px; }}
        .summary-icon {{ width: 30px; height: 30px; border-radius: 8px; background: rgba(0,204,217,0.12); display: flex; align-items: center; justify-content: center; }}
        .summary-title {{ font-family: 'Outfit', sans-serif; font-size: 16px; font-weight: 700; color: var(--primary); letter-spacing: 1px; }}
        .summary-text {{ font-size: 14px; color: #cbd5e1; line-height: 1.8; font-weight: 400; }}
        .summary-stats {{ display: flex; gap: 0; border-top: 1px solid rgba(255,255,255,0.08); padding-top: 18px; flex-wrap: wrap; }}
        .stat-item {{ flex: 1; display: flex; flex-direction: column; gap: 5px; padding: 0 16px; border-right: 1px solid rgba(255,255,255,0.06); }}
        .stat-item:first-child {{ padding-left: 0; }} .stat-item:last-child {{ border-right: none; padding-right: 0; }}
        .stat-label {{ font-size: 9px; color: #64748b; letter-spacing: 0.8px; text-transform: uppercase; font-weight: 700; }}
        .stat-value {{ font-family: 'Outfit', sans-serif; font-size: 14px; color: #e2e8f0; font-weight: 700; }}
        .summary-footer {{ text-align: center; padding-top: 14px; border-top: 1px solid rgba(255,255,255,0.05); }}
        .summary-footer span {{ font-size: 10px; color: #4a5568; letter-spacing: 1px; font-weight: 500; }}
        .summary-footer span em {{ color: var(--primary); font-style: normal; font-weight: 700; }}
        @keyframes fadeSlideDown {{ from {{ opacity: 0; transform: translateY(16px); }} to {{ opacity: 1; transform: translateY(0); }} }}
        @media (max-width: 500px) {{ .header {{ flex-direction: column; align-items: flex-start; gap: 8px; }} .query-value {{ font-size: 22px; }} .match-title {{ font-size: 15px; }} .summary-stats {{ flex-wrap: wrap; gap: 12px; }} .stat-item {{ border-right: none; padding: 0; min-width: 45%; }} }}
    </style>
</head>
<body>
    <svg width="0" height="0" style="position:absolute;">
        <defs>
            <linearGradient id="grad1" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#00ccd9"/><stop offset="100%" stop-color="#4d8eff"/></linearGradient>
            <linearGradient id="grad2" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#4d8eff"/><stop offset="100%" stop-color="#a855f7"/></linearGradient>
            <linearGradient id="grad3" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#a855f7"/><stop offset="100%" stop-color="#ec4899"/></linearGradient>
        </defs>
    </svg>

    <div class="container">
        <div class="header">
            <div class="header-left">
                <div class="pulse-dot"></div>
                <span class="header-title">FAIL HISTORY AGENT</span>
            </div>
            <span class="header-time" id="headerTime"></span>
        </div>

        <div class="section-header">
            <span class="section-title">AGENT RESULTS</span>
            <span class="badge-count">Results {total} History</span>
        </div>

        <div class="card query-card">
            <span class="query-label">Search Query</span>
            <div class="query-value">{_html_escape(query)}</div>
            <div class="chip-group">
                {chips_html}
            </div>
        </div>

        {match_cards_html}

        {summary_html}
    </div>

    <script>
        function updateTime() {{
            var now = new Date();
            var fmt = now.toLocaleString('ko-KR', {{
                year: 'numeric', month: '2-digit', day: '2-digit',
                hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
            }});
            document.getElementById('headerTime').textContent = fmt;
        }}
        updateTime(); setInterval(updateTime, 1000);

        var observer = new IntersectionObserver(function(entries) {{
            entries.forEach(function(entry) {{
                if (entry.isIntersecting) {{
                    var el = entry.target;
                    var target = parseFloat(el.dataset.target);
                    var duration = 1200; var start = performance.now();
                    function animate(now) {{
                        var elapsed = now - start;
                        var progress = Math.min(elapsed / duration, 1);
                        var eased = 1 - Math.pow(1 - progress, 3);
                        el.textContent = (target * eased).toFixed(1);
                        if (progress < 1) requestAnimationFrame(animate);
                    }}
                    requestAnimationFrame(animate);
                    observer.unobserve(el);
                }}
            }});
        }}, {{ threshold: 0.5 }});
        document.querySelectorAll('.score-number').forEach(function(el) {{ observer.observe(el); }});
    </script>
</body>
</html>"""


# ── 도구 리스트 ──────────────────────────────────────────────
FAIL_HISTORY_TOOLS = [search_fail_history, render_fail_report]

"""Wiki vault I/O — episode immutable snapshot, concept rollup, alias symmetry.

PoC Day 1: 동기 함수만 (워커가 호출). 파일 락 없음 — 워커 1개가 single-writer.
"""
from __future__ import annotations

import datetime
import hashlib
import logging
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import frontmatter
from wiki_config import initialize_wiki_vault, resolve_wiki_paths

logger = logging.getLogger("yield_agent.wiki_store")

# ── 경로 (env로 사내 vault 위치 변경 가능) ────────────────
_PATHS = resolve_wiki_paths()
_VAULT = _PATHS.root
_EPISODES = _PATHS.episodes
_CONCEPTS = _PATHS.concepts
_ALIASES = _PATHS.aliases
_SUPER_CONCEPTS = _PATHS.super_concepts
_LOG = _PATHS.log
_INDEX = _PATHS.index

# ── wiki-first 게이트 임계 (env로 운영 중 조정 가능, 재기동 후 반영) ──
# 데이터 누적 후 보수적으로 올리려면 .env에 WIKI_FIRST_MIN_* 추가.
WIKI_FIRST_MIN_LEN = int(os.getenv("WIKI_FIRST_MIN_LEN", "200"))
WIKI_FIRST_MIN_SOURCE_EPISODES = int(os.getenv("WIKI_FIRST_MIN_SOURCE_EPISODES", "1"))
WIKI_FIRST_MIN_CONFIDENCE = float(os.getenv("WIKI_FIRST_MIN_CONFIDENCE", "0.5"))
WIKI_FIRST_MIN_CITATION_COVERAGE = float(os.getenv("WIKI_FIRST_MIN_CITATION_COVERAGE", "0.0"))
WIKI_FIRST_MAX_AGE_DAYS = int(os.getenv("WIKI_FIRST_MAX_AGE_DAYS", "30"))


# ── ACRONYM_MAP 재사용 (fail_history_tools에 정의) ───────
def _acronym_map() -> dict[str, str]:
    try:
        from fail_history_tools import ACRONYM_MAP
        return ACRONYM_MAP
    except Exception:
        return {}


# ── 유틸 ─────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _safe_filename(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", key)


def _normalize_query(query: str) -> str:
    """lowercase + 공백 정규화 + ACRONYM_MAP 확장."""
    q = re.sub(r"\s+", " ", (query or "").strip().lower())
    for abbr, full in _acronym_map().items():
        if re.search(rf"\b{abbr.lower()}\b", q):
            q += f" {full.lower()}"
    return q


def _episode_key(query: str, filters: dict, doc_ids: list[str]) -> str:
    qn = _normalize_query(query)
    fc = "|".join([
        filters.get("product", "") or "",
        filters.get("fail_type", "") or "",
        filters.get("cause_oper", "") or "",
    ])
    di = "|".join(sorted(doc_ids or []))
    raw = f"{qn}|{fc}|{di}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _concept_key(filters: dict) -> str:
    return f"{filters.get('product','')}|{filters.get('cause_oper','')}|{filters.get('fail_type','')}"


def _alias_key(canonical: str, variant: str) -> str:
    return f"{canonical}|{variant}"


def _ensure_dirs() -> None:
    initialize_wiki_vault(_PATHS)


def _log(event: str, key: str, hits: int = 0) -> None:
    _ensure_dirs()
    line = f"{_now_iso()} {event} {key} hits={hits}\n"
    with _LOG.open("a", encoding="utf-8") as f:
        f.write(line)


def _read(path: Path) -> frontmatter.Post | None:
    if not path.exists():
        return None
    return frontmatter.load(path)


def _write(path: Path, post: frontmatter.Post) -> None:
    """Atomic write: .tmp → os.rename. plan v3 §위험·동시 write 충돌 가드."""
    import os as _os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(frontmatter.dumps(post), encoding="utf-8")
    _os.replace(tmp, path)


def materialize_obsidian_wiki() -> Any:
    """Refresh deterministic Obsidian links after the primary Wiki write."""
    from wiki_materializer import materialize_wiki

    return materialize_wiki(_PATHS, apply=True)


# ── lifecycle 기본값 ───────────────────────────────────
_STALE_AFTER_DAYS = int(os.getenv("WIKI_STALE_AFTER_DAYS", "30"))


def _lifecycle_defaults() -> dict[str, Any]:
    """plan v3 §신규 필수 4기능 (3) Lifecycle minimal TTL."""
    return {
        "status": "active",
        "last_active": _now_iso(),
        "stale_after_days": _STALE_AFTER_DAYS,
    }


def compute_evidence_diversity(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """plan v3 §A — evidence diversity 기반 트리거.

    Returns {score: 0~1, unique_doc_ids: int, unique_dates: int, total_episodes: int}.
    raw doc_id 다양성 + 시점 다양성 + episode 수로 0~1 점수 산출.
    같은 raw set만 반복되면 score 낮음 (사용자 비판 정면 해결).
    """
    if not episodes:
        return {"score": 0.0, "unique_doc_ids": 0, "unique_dates": 0, "total_episodes": 0}
    all_doc_ids: set[str] = set()
    all_dates: set[str] = set()
    for ep in episodes:
        # support flat dict (ep["doc_ids"]) and nested ({"frontmatter": {...}})
        fm = ep.get("frontmatter") if isinstance(ep.get("frontmatter"), dict) else None
        doc_ids = ep.get("doc_ids") or (fm.get("doc_ids") if fm else None) or []
        for d in doc_ids:
            all_doc_ids.add(d)
        created = ep.get("created") or (fm.get("created") if fm else None) or ""
        if created:
            all_dates.add(str(created)[:10])  # date only
    n = len(episodes)
    unique_doc = len(all_doc_ids)
    unique_dates = len(all_dates)
    # 0~1 점수: doc 다양성(0.6) + 날짜 다양성(0.3) + episode 수(0.1)
    score = min(1.0, (
        0.6 * min(1.0, unique_doc / max(n, 1) / 1.5)  # doc/ep 비율, 1.5 이상이면 max
        + 0.3 * min(1.0, unique_dates / max(n, 1))
        + 0.1 * min(1.0, n / 5.0)
    ))
    return {
        "score": round(score, 3),
        "unique_doc_ids": unique_doc,
        "unique_dates": unique_dates,
        "total_episodes": n,
    }


# ── upsert: episode (immutable snapshot) ─────────────────
def upsert_episode(payload: dict) -> tuple[str, str]:
    """Immutable episode snapshot.

    payload keys: query (필수), filters {product,fail_type,cause_oper},
                  doc_ids (list[str]), body (markdown), summary (1줄), links (list[str]),
                  private (bool; legacy 누락은 public).
    Returns (episode_id, status) — status in {"created", "skipped"}.
    같은 키 재호출은 skipped (정보 손실 방지). plan v3 §Vault & Schema.
    """
    _ensure_dirs()
    eid = _episode_key(payload["query"], payload.get("filters", {}), payload.get("doc_ids", []))
    path = _EPISODES / f"{eid}.md"
    if path.exists():
        # Privacy provenance is monotonic even though episode content is immutable.
        if bool(payload.get("private", False)):
            existing = _read(path)
            if existing is not None and not bool(existing.metadata.get("private", False)):
                metadata = dict(existing.metadata)
                metadata["private"] = True
                _write(path, frontmatter.Post(content=existing.content or "", **metadata))
        _log("episode_skip", eid)
        return eid, "skipped"
    now = _now_iso()
    fm = {
        "id": f"episode:{eid}",
        "type": "episode",
        "created": now,
        "updated": now,
        "version": 1,
        "links": list(payload.get("links", []) or []),
        "query": payload["query"],
        "query_normalized": _normalize_query(payload["query"]),
        "doc_ids": list(payload.get("doc_ids", []) or []),
        "source_files": list(payload.get("source_files", []) or []),
        "filters": dict(payload.get("filters", {}) or {}),
        "summary": payload.get("summary", ""),
        "private": bool(payload.get("private", False)),
        # plan v3 신규 필수 4기능
        **_lifecycle_defaults(),
        "confidence": float(payload.get("confidence", 0.5)),  # episode 단일이라 중립 0.5
        "citations": list(payload.get("citations", []) or []),
    }
    post = frontmatter.Post(content=payload.get("body", ""), **fm)
    _write(path, post)
    _log("episode_create", eid, hits=len(payload.get("doc_ids", []) or []))
    return eid, "created"


# ── upsert: concept (mutable rollup) ─────────────────────
def upsert_concept(
    filters: dict,
    source_episode_id: str | None = None,
    links: list[str] | None = None,
    *,
    synthesized_body: str | None = None,
    confidence: float | None = None,
    citations: list[dict] | None = None,
    entities: list[dict] | None = None,
    relations: list[dict] | None = None,
    evidence: dict | None = None,
    sync_metadata: dict[str, Any] | None = None,
    materialize: bool = True,
) -> tuple[str, str]:
    """Concept rollup. 같은 (product, fail_type, cause_oper)는 누적.

    Base rollup (seen_count++, source append, version++, lifecycle 갱신)은 항상.
    synthesized_body가 주어지면 body_versions에 누적 + content 갱신 (plan v3 §A 합성).
    confidence/citations/entities/relations/evidence는 합성 호출에서만 전달.

    Returns (concept_id, status) — status in {"created", "updated"}.
    """
    _ensure_dirs()
    cid = _concept_key(filters)
    path = _CONCEPTS / f"{_safe_filename(cid)}.md"
    existing = _read(path)
    now = _now_iso()

    if existing is None:
        fm = {
            "id": f"concept:{cid}",
            "type": "concept",
            "created": now,
            "updated": now,
            "version": 1,
            "links": sorted(set(links or [])),
            "product": filters.get("product", ""),
            "fail_type": filters.get("fail_type", ""),
            "cause_oper": filters.get("cause_oper", ""),
            "seen_count": 1,
            "source_episode_ids": [source_episode_id] if source_episode_id else [],
            # plan v3 신규 필수 4기능
            **_lifecycle_defaults(),
            "confidence": float(confidence) if confidence is not None else 0.0,
            "citations": list(citations or []),
            "entities": [],
            "relations": [],
            "evidence_diversity_score": float((evidence or {}).get("score", 0.0)),
            "unique_doc_ids": int((evidence or {}).get("unique_doc_ids", 0)),
            "body_versions": [],
        }
        fm.update(_approved_sync_metadata(sync_metadata))
        if sync_metadata:
            fm["status"] = "active"
        body = ""
        if synthesized_body:
            fm["entities"] = deepcopy(entities or [])
            fm["relations"] = deepcopy(relations or [])
            fm["body_versions"].append({
                "version": 1,
                "created": now,
                "expires_at": _ttl_iso(_STALE_AFTER_DAYS),
                "source_episode_ids": fm["source_episode_ids"],
                "body_markdown": synthesized_body,
                "confidence": fm["confidence"],
                "entities": deepcopy(fm["entities"]),
                "relations": deepcopy(fm["relations"]),
            })
            body = synthesized_body
        post = frontmatter.Post(content=body, **fm)
        _write(path, post)
        _log("concept_create", cid)
        if materialize:
            materialize_obsidian_wiki()
        return cid, "created"

    md = dict(existing.metadata)
    md["updated"] = now
    md["version"] = int(md.get("version", 1)) + 1
    md["seen_count"] = int(md.get("seen_count", 1)) + 1
    se = list(md.get("source_episode_ids", []) or [])
    if source_episode_id and source_episode_id not in se:
        se.append(source_episode_id)
    md["source_episode_ids"] = se
    md["links"] = sorted(set(list(md.get("links", []) or []) + list(links or [])))
    # plan v3 lifecycle 갱신
    md["last_active"] = now
    md.setdefault("status", "active")
    md.setdefault("stale_after_days", _STALE_AFTER_DAYS)
    md.setdefault("citations", [])
    md.setdefault("entities", [])
    md.setdefault("relations", [])
    md.setdefault("body_versions", [])
    md.setdefault("evidence_diversity_score", 0.0)
    md.setdefault("unique_doc_ids", 0)
    md.setdefault("confidence", 0.0)
    md.update(_approved_sync_metadata(sync_metadata))
    if sync_metadata:
        md["status"] = "active"

    body_content = existing.content or ""
    if synthesized_body:
        md["entities"] = deepcopy(entities or [])
        md["relations"] = deepcopy(relations or [])
        new_ver = len(md["body_versions"]) + 1
        md["body_versions"].append({
            "version": new_ver,
            "created": now,
            "expires_at": _ttl_iso(_STALE_AFTER_DAYS),
            "source_episode_ids": list(se),
            "body_markdown": synthesized_body,
            "confidence": float(confidence) if confidence is not None else md["confidence"],
            "entities": deepcopy(md["entities"]),
            "relations": deepcopy(md["relations"]),
        })
        # cap 5 (plan §위험: body_versions 누적 cap)
        if len(md["body_versions"]) > 5:
            md["body_versions"] = md["body_versions"][-5:]
        if confidence is not None:
            md["confidence"] = float(confidence)
        if citations:
            md["citations"] = list(citations)
        if evidence:
            md["evidence_diversity_score"] = float(evidence.get("score", md["evidence_diversity_score"]))
            md["unique_doc_ids"] = int(evidence.get("unique_doc_ids", md["unique_doc_ids"]))
        body_content = synthesized_body

    _write(path, frontmatter.Post(content=body_content, **md))
    _log("concept_update", cid)
    if materialize:
        materialize_obsidian_wiki()
    return cid, "updated"


def _approved_sync_metadata(values: dict[str, Any] | None) -> dict[str, Any]:
    if not values:
        return {}
    approved = {
        "source_fingerprint",
        "source_doc_ids",
        "evidence_count",
        "evidence_scope",
        "sync_job_id",
    }
    return {key: values[key] for key in approved if key in values}


def mark_concept_stale(
    filters: dict[str, Any],
    missing_doc_ids: list[str] | tuple[str, ...],
    detected_at: str,
) -> tuple[str, bool]:
    """Mark an existing Concept stale without deleting or re-synthesizing it."""
    _ensure_dirs()
    cid = _concept_key(filters)
    path = _CONCEPTS / f"{_safe_filename(cid)}.md"
    post = _read(path)
    if post is None:
        return cid, False
    missing = sorted({str(value) for value in missing_doc_ids if value})
    metadata = dict(post.metadata)
    if (
        metadata.get("status") == "stale"
        and list(metadata.get("missing_source_doc_ids", []) or []) == missing
    ):
        return cid, False
    metadata["status"] = "stale"
    metadata["missing_source_doc_ids"] = missing
    metadata["stale_detected_at"] = detected_at
    metadata["updated"] = detected_at
    _write(path, frontmatter.Post(content=post.content or "", **metadata))
    _log("concept_stale", cid, hits=len(missing))
    return cid, True


def create_source_removal_review(
    filters: dict[str, Any],
    previous_doc_ids: list[str] | tuple[str, ...],
    current_doc_ids: list[str] | tuple[str, ...],
    detected_at: str,
) -> tuple[str, bool]:
    """Create a deterministic operator-owned source-removal Review once."""
    _ensure_dirs()
    cid = _concept_key(filters)
    previous = sorted({str(value) for value in previous_doc_ids if value})
    current = sorted({str(value) for value in current_doc_ids if value})
    missing = sorted(set(previous) - set(current))
    identity = "|".join((cid, ",".join(previous), ",".join(current)))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    review_id = f"review:source-removal:{digest}"
    path = _PATHS.reviews / f"source_removal_{digest}.md"
    if path.exists():
        return review_id, False
    metadata = {
        "id": review_id,
        "type": "review",
        "review_type": "source_removal",
        "status": "pending",
        "target_concept_id": f"concept:{cid}",
        "previous_doc_ids": previous,
        "current_doc_ids": current,
        "missing_doc_ids": missing,
        "detected_at": detected_at,
        "created": detected_at,
        "updated": detected_at,
    }
    body = (
        "# Source Removal Review\n\n"
        "근거 문서가 제거되어 자동 재합성을 보류했습니다. "
        "운영자 검토 후 exact bootstrap 복구 여부를 결정하세요.\n"
    )
    _write(path, frontmatter.Post(content=body, **metadata))
    _log("source_removal_review", review_id, hits=len(missing))
    return review_id, True


def _ttl_iso(days: int) -> str:
    return (datetime.datetime.now() + datetime.timedelta(days=days)).isoformat(timespec="seconds")


def update_concept_evidence(concept_id: str, evidence: dict[str, Any]) -> bool:
    """진단 가시성: evidence 계산 결과를 concept frontmatter에 매번 갱신.

    합성 발동 여부 무관하게 score/unique_doc_ids/last_evidence_check 저장.
    "왜 합성 안 됐는지"를 vault 파일만 보고도 파악 가능.
    """
    cid_key = concept_id.replace("concept:", "")
    cpath = _CONCEPTS / f"{_safe_filename(cid_key)}.md"
    post = _read(cpath)
    if post is None:
        return False
    md = dict(post.metadata)
    md["evidence_diversity_score"] = float(evidence.get("score", 0.0))
    md["unique_doc_ids"] = int(evidence.get("unique_doc_ids", 0))
    md["last_evidence_check"] = _now_iso()
    _write(cpath, frontmatter.Post(content=post.content or "", **md))
    return True


# ── lookup_concept_body: wiki-first/assisted 게이트 ──────
def lookup_concept_body(
    filters: dict,
    *,
    min_len: int = WIKI_FIRST_MIN_LEN,
    min_source_episodes: int = WIKI_FIRST_MIN_SOURCE_EPISODES,
    min_confidence: float = WIKI_FIRST_MIN_CONFIDENCE,
    min_citation_coverage: float = WIKI_FIRST_MIN_CITATION_COVERAGE,
    max_last_active_days: int = WIKI_FIRST_MAX_AGE_DAYS,
) -> dict[str, Any]:
    """plan v3 §C wiki-first/wiki-assisted 게이트.

    Returns:
      {
        "gate": "wiki-first" | "wiki-assisted" | "none",
        "body": str | None,
        "confidence": float,
        "citations": list,
        "fail_reasons": list[str],   # 미통과 시 이유
        "concept_id": str | None,
      }
    """
    _ensure_dirs()
    out: dict[str, Any] = {"gate": "none", "body": None, "confidence": 0.0,
                            "citations": [], "fail_reasons": [], "concept_id": None}
    if not all(filters.get(k) for k in ("product", "fail_type", "cause_oper")):
        out["fail_reasons"].append("filter_not_exact")
        return out
    cid = _concept_key(filters)
    out["concept_id"] = f"concept:{cid}"
    post = _read(_CONCEPTS / f"{_safe_filename(cid)}.md")
    if post is None:
        # plan v3 §위험 "concept 키 정규화 미흡" 보완 — alias로 fallback
        # 예: filters.fail_type="EASY(W)"인데 vault엔 "EASY"만 있으면
        #     alias 노드(EASY↔EASY(W)) 통해 정규형 변환 시도
        ft = filters.get("fail_type", "")
        for apath in _ALIASES.glob("*.md"):
            apost = _read(apath)
            if apost is None:
                continue
            am = apost.metadata
            c = am.get("canonical", "")
            v = am.get("variant", "")
            other = None
            if ft == v and c:
                other = c
            elif ft == c and v:
                other = v
            if other:
                alt_filters = {**filters, "fail_type": other}
                alt_cid = _concept_key(alt_filters)
                alt_path = _CONCEPTS / f"{_safe_filename(alt_cid)}.md"
                alt_post = _read(alt_path)
                if alt_post is not None:
                    post = alt_post
                    cid = alt_cid
                    out["concept_id"] = f"concept:{alt_cid}"
                    logger.info("[lookup_concept_body] alias fallback %s → %s", ft, other)
                    break
        if post is None:
            out["fail_reasons"].append("concept_missing")
            return out
    md = post.metadata
    body = post.content or ""
    confidence = float(md.get("confidence", 0.0))
    citations = list(md.get("citations") or [])
    src_ep = list(md.get("source_episode_ids") or [])
    out["confidence"] = confidence
    out["citations"] = citations

    # Karpathy 회귀: unique_doc_ids·evidence_diversity 게이트 제거.
    # 합성 호출 가드(같은 raw 반복 source 제외)는 wiki_queue 쪽에 남김.
    if len(body) < min_len:
        out["fail_reasons"].append(f"body_len<{min_len}")
    # source count = episode source + citation source. 직접 합성 모드(episode 단계 생략)에선
    # source_episode_ids가 비고 citations만 채워짐 → 둘 다 합산해서 임계 체크.
    source_count = len(src_ep) + len(citations)
    if source_count < min_source_episodes:
        out["fail_reasons"].append(f"source_count<{min_source_episodes}")
    if confidence < min_confidence:
        out["fail_reasons"].append(f"confidence<{min_confidence}")
    # citation coverage: citations 수 / body의 [ep:xxx] 등장 수 (단순화: citations 충분하면 OK)
    citation_coverage = 1.0 if citations else 0.0
    if citation_coverage < min_citation_coverage:
        out["fail_reasons"].append(f"citation_coverage<{min_citation_coverage}")
    # freshness
    last_active = md.get("last_active", md.get("updated", ""))
    if last_active:
        try:
            last_dt = datetime.datetime.fromisoformat(str(last_active))
            age_days = (datetime.datetime.now() - last_dt).days
            if age_days > max_last_active_days:
                out["fail_reasons"].append(f"stale_>{max_last_active_days}d")
        except Exception:
            out["fail_reasons"].append("last_active_unparseable")
    if md.get("status", "active") != "active":
        out["fail_reasons"].append(f"status={md.get('status')}")

    # 게이트 결정
    if not out["fail_reasons"]:
        out["gate"] = "wiki-first"
        out["body"] = body
    elif body and len(body) >= 200 and confidence >= 0.4:
        # 일부 임계 통과 — wiki-assisted
        out["gate"] = "wiki-assisted"
        out["body"] = body
    return out


# ── upsert: alias (symmetry 보장) ────────────────────────
def upsert_alias(canonical: str, variant: str) -> list[tuple[str, str]]:
    """Alias node 양방향 생성: canonical→variant + variant→canonical.

    Returns list of (alias_id, status). symmetry 위반은 lint가 잡지만 여기서 미리 차단.
    """
    _ensure_dirs()
    if not canonical or not variant or canonical == variant:
        return []
    results: list[tuple[str, str]] = []
    for c, v in ((canonical, variant), (variant, canonical)):
        aid = _alias_key(c, v)
        path = _ALIASES / f"{_safe_filename(aid)}.md"
        if path.exists():
            results.append((aid, "skipped"))
            continue
        now = _now_iso()
        fm = {
            "id": f"alias:{aid}",
            "type": "alias",
            "created": now,
            "updated": now,
            "version": 1,
            "links": [],
            "canonical": c,
            "variant": v,
            **_lifecycle_defaults(),
            "confidence": 1.0,  # alias는 LLM 확정 발견이라 100%
            "citations": [],
        }
        post = frontmatter.Post(content="", **fm)
        _write(path, post)
        _log("alias_create", aid)
        results.append((aid, "created"))
    return results


# ── lookup: 검색 시 wiki_memory 응답용 ───────────────────
def lookup(query: str, filters: dict | None = None, max_episodes: int = 3) -> dict[str, Any]:
    """search_fail_history가 ms 단위로 호출. concept + alias + recent_episodes.

    plan v3 §3 search_fail_history 확장 인터페이스 wiki_memory 구조.
    """
    _ensure_dirs()
    filters = filters or {}
    res: dict[str, Any] = {"concepts": [], "aliases": [], "recent_episodes": []}

    # concept 정확 매칭 (모든 필터 채워진 경우만)
    if all(filters.get(k) for k in ("product", "fail_type", "cause_oper")):
        cid = _concept_key(filters)
        cpost = _read(_CONCEPTS / f"{_safe_filename(cid)}.md")
        if cpost is not None:
            md = cpost.metadata
            res["concepts"].append({
                "id": f"concept:{cid}",
                "summary_1line": (cpost.content or "").strip()[:200],
                "seen_count": md.get("seen_count", 1),
            })

    # alias: query 토큰이 canonical/variant와 매칭
    qtok = set(re.findall(r"[A-Za-z0-9가-힣()]+", query or ""))
    seen_alias: set[tuple[str, str]] = set()
    for apath in sorted(_ALIASES.glob("*.md")):
        apost = _read(apath)
        if apost is None:
            continue
        c = apost.metadata.get("canonical", "")
        v = apost.metadata.get("variant", "")
        if (c in qtok or v in qtok) and (c, v) not in seen_alias:
            res["aliases"].append({"variant": v, "canonical": c})
            seen_alias.add((c, v))

    # 최근 episode (filter 일부 매칭, mtime 내림차순)
    epaths = sorted(_EPISODES.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    for epath in epaths:
        epost = _read(epath)
        if epost is None:
            continue
        ef = epost.metadata.get("filters", {}) or {}
        if filters.get("product") and ef.get("product") != filters["product"]:
            continue
        res["recent_episodes"].append({
            "id": epost.metadata.get("id", ""),
            "query": epost.metadata.get("query", ""),
            "doc_ids": epost.metadata.get("doc_ids", []),
        })
        if len(res["recent_episodes"]) >= max_episodes:
            break
    return res


# ── read by node_id (API용) ──────────────────────────────
def read_node(node_id: str) -> dict[str, Any] | None:
    """node_id e.g. 'episode:abc123', 'concept:4SS|STI CMP|EASY(W)', 'alias:EASY|EASY(W)'."""
    if ":" not in node_id:
        return None
    kind, key = node_id.split(":", 1)
    if kind == "episode":
        path = _EPISODES / f"{key}.md"
    elif kind == "concept":
        path = _CONCEPTS / f"{_safe_filename(key)}.md"
    elif kind == "alias":
        path = _ALIASES / f"{_safe_filename(key)}.md"
    else:
        return None
    post = _read(path)
    if post is None:
        return None
    return {"frontmatter": dict(post.metadata), "body": post.content or ""}


def lookup_super_concept(axis: str, axis_value: str) -> dict[str, Any] | None:
    """super_concept 노드 읽기. status=reference_only가 아니면 None.

    Args:
        axis: "fail_type" | "cause_oper" | "product"
        axis_value: 고정된 축 값
    """
    _ensure_dirs()
    sid_key = f"{axis}={axis_value}"
    path = _SUPER_CONCEPTS / f"{_safe_filename(sid_key)}.md"
    post = _read(path)
    if post is None:
        return None
    md = dict(post.metadata)
    if md.get("status") != "reference_only":
        return None
    return {"frontmatter": md, "body": post.content or ""}


# ── super_concept upsert (Day 5: cross-concept 추상화, 참고용 한정) ────
def upsert_super_concept(
    axis: str,
    axis_value: str,
    source_concept_ids: list[str],
    synthesized_body: str,
    confidence: float,
    *,
    materialize: bool = True,
) -> tuple[str, Path]:
    """super_concept 노드 생성/갱신. 자동 답변 근거 사용 금지 (status: reference_only).

    Args:
        axis: 와일드카드 축 — "fail_type" | "cause_oper" | "product"
        axis_value: 고정된 다른 축 값 (예: "EASY"면 fail_type=EASY 고정, 나머지 *)
        source_concept_ids: 합성 source가 된 concept id list
        synthesized_body: LLM이 메타 합성한 markdown 본문
        confidence: 0~1 self-rated (concept보다 낮게 권장)

    Returns:
        (super_id, file_path)
    """
    _ensure_dirs()
    sid_key = f"{axis}={axis_value}"
    super_id = f"super:{sid_key}"
    path = _SUPER_CONCEPTS / f"{_safe_filename(sid_key)}.md"
    now = _now_iso()
    existing = _read(path)
    if existing is not None:
        md = dict(existing.metadata)
    else:
        md = {
            "id": super_id,
            "type": "super_concept",
            "axis": axis,
            "axis_value": axis_value,
            "status": "reference_only",
            "created": now,
            **_lifecycle_defaults(),
        }
    md["updated"] = now
    md["last_active"] = now
    md["source_concept_ids"] = list(source_concept_ids)
    md["confidence"] = float(confidence)
    md["version"] = int(md.get("version", 0)) + 1
    md["status"] = "reference_only"  # 항상 강제
    post = frontmatter.Post(content=synthesized_body, **md)
    _write(path, post)
    _log("super_upsert", super_id, hits=len(source_concept_ids))
    if materialize:
        materialize_obsidian_wiki()
    return super_id, path


# ── 카운트 (디버그) ─────────────────────────────────
def counts() -> dict[str, int]:
    _ensure_dirs()
    return {
        "episode": len(list(_EPISODES.glob("*.md"))),
        "concept": len(list(_CONCEPTS.glob("*.md"))),
        "alias": len(list(_ALIASES.glob("*.md"))),
        "super_concept": len(list(_SUPER_CONCEPTS.glob("*.md"))),
    }

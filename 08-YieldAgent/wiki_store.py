"""Wiki vault I/O — episode immutable snapshot, concept rollup, alias symmetry.

PoC Day 1: 동기 함수만 (워커가 호출). 파일 락 없음 — 워커 1개가 single-writer.
"""
from __future__ import annotations

import datetime
import hashlib
import re
from pathlib import Path
from typing import Any

import frontmatter

# ── 경로 ─────────────────────────────────────────────────
_VAULT = Path(__file__).parent / "wiki"
_EPISODES = _VAULT / "episodes"
_CONCEPTS = _VAULT / "concepts"
_ALIASES = _VAULT / "aliases"
_LOG = _VAULT / "log.md"
_INDEX = _VAULT / "index.md"


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
    for d in (_EPISODES, _CONCEPTS, _ALIASES):
        d.mkdir(parents=True, exist_ok=True)
    if not _LOG.exists():
        _LOG.write_text("# Wiki Operation Log\n\n", encoding="utf-8")
    if not _INDEX.exists():
        _INDEX.write_text("# Wiki Index\n\n", encoding="utf-8")


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(frontmatter.dumps(post), encoding="utf-8")


# ── upsert: episode (immutable snapshot) ─────────────────
def upsert_episode(payload: dict) -> tuple[str, str]:
    """Immutable episode snapshot.

    payload keys: query (필수), filters {product,fail_type,cause_oper},
                  doc_ids (list[str]), body (markdown), summary (1줄), links (list[str]).
    Returns (episode_id, status) — status in {"created", "skipped"}.
    같은 키 재호출은 skipped (정보 손실 방지). plan v3 §Vault & Schema.
    """
    _ensure_dirs()
    eid = _episode_key(payload["query"], payload.get("filters", {}), payload.get("doc_ids", []))
    path = _EPISODES / f"{eid}.md"
    if path.exists():
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
        "filters": dict(payload.get("filters", {}) or {}),
        "summary": payload.get("summary", ""),
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
) -> tuple[str, str]:
    """Concept rollup. 같은 (product, fail_type, cause_oper)는 누적.

    seen_count++, source_episode_ids append (dedup), links union, version++, updated 갱신.
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
        }
        post = frontmatter.Post(content="", **fm)
        _write(path, post)
        _log("concept_create", cid)
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
    _write(path, frontmatter.Post(content=existing.content or "", **md))
    _log("concept_update", cid)
    return cid, "updated"


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


# ── 카운트 (eval/디버그) ─────────────────────────────────
def counts() -> dict[str, int]:
    _ensure_dirs()
    return {
        "episode": len(list(_EPISODES.glob("*.md"))),
        "concept": len(list(_CONCEPTS.glob("*.md"))),
        "alias": len(list(_ALIASES.glob("*.md"))),
    }

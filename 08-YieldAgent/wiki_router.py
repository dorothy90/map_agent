"""Wiki vault graph endpoint (Day 5, plan v3 §wiki_router.py).

graphology v0.25 호환 JSON 반환:
- multi: true + edge key에 kind 포함 → 같은 source-target 쌍에 mentions / alias_of 동시 표현
- 5초 TTL 메모리 캐시 (vault 풀스캔 비용 완화)
- since 파라미터 미구현 (plan v3 변경: delta index 없음)
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

import frontmatter
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
logger = logging.getLogger("yield_agent.wiki_router")

_VAULT = Path(__file__).parent / "wiki"
_CACHE_TTL_SEC = 5.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _safe_filename(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", key)


def _scan_nodes() -> dict[str, dict[str, Any]]:
    """vault 전체 frontmatter 스캔 → {id: metadata + _body}."""
    nodes: dict[str, dict[str, Any]] = {}
    for kind in ("episodes", "concepts", "aliases"):
        d = _VAULT / kind
        if not d.exists():
            continue
        for p in sorted(d.glob("*.md")):
            try:
                post = frontmatter.load(p)
            except Exception as e:
                logger.warning("[wiki_router] frontmatter parse fail %s: %s", p.name, e)
                continue
            md = dict(post.metadata)
            nid = md.get("id")
            if not nid:
                continue
            md["_body"] = post.content or ""
            nodes[nid] = md
    return nodes


def _label(md: dict[str, Any], ntype: str) -> str:
    if ntype == "concept":
        return f"{md.get('fail_type', '')} @ {md.get('product', '')} {md.get('cause_oper', '')}"
    if ntype == "episode":
        q = (md.get("query", "") or "").strip()[:30]
        return f"📄 {q}" if q else f"📄 {md.get('id', '')[:24]}"
    if ntype == "alias":
        return f"{md.get('canonical', '')} ↔ {md.get('variant', '')}"
    return md.get("id", "")


def _node_attrs(md: dict[str, Any], ntype: str) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "label": _label(md, ntype),
        "type": ntype,
        "updated": str(md.get("updated", "")),
    }
    if ntype == "concept":
        attrs.update({
            "product": md.get("product", ""),
            "fail_type": md.get("fail_type", ""),
            "cause_oper": md.get("cause_oper", ""),
            "seen_count": int(md.get("seen_count", 1)),
        })
    elif ntype == "episode":
        attrs.update({
            "query": md.get("query", ""),
            "doc_count": len(md.get("doc_ids", []) or []),
        })
    elif ntype == "alias":
        attrs.update({
            "canonical": md.get("canonical", ""),
            "variant": md.get("variant", ""),
        })
    return attrs


def _build_graph(filter_type: str | None, filter_product: str | None, limit: int) -> dict[str, Any]:
    nodes_raw = _scan_nodes()
    nodes_out: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for nid, md in nodes_raw.items():
        ntype = md.get("type", "")
        if filter_type and ntype != filter_type:
            continue
        if filter_product:
            np = md.get("product", "")
            if not np and isinstance(md.get("filters"), dict):
                np = md["filters"].get("product", "")
            if np != filter_product:
                continue
        nodes_out.append({"key": nid, "attributes": _node_attrs(md, ntype)})
        seen_keys.add(nid)
        if len(nodes_out) >= limit:
            break

    edges_out: list[dict[str, Any]] = []
    edge_seen: set[str] = set()
    for nid, md in nodes_raw.items():
        if nid not in seen_keys:
            continue
        if md.get("type") != "concept":
            continue
        # concept → alias (alias_of)
        for link in (md.get("links") or []):
            if link.startswith("alias:") and link in seen_keys:
                ek = f"{nid}__{link}__alias_of"
                if ek in edge_seen:
                    continue
                edges_out.append({
                    "key": ek,
                    "source": nid,
                    "target": link,
                    "attributes": {"kind": "alias_of", "weight": 1.0},
                })
                edge_seen.add(ek)
        # episode → concept (mentions)
        for ep_id in (md.get("source_episode_ids") or []):
            if ep_id in seen_keys:
                ek = f"{ep_id}__{nid}__mentions"
                if ek in edge_seen:
                    continue
                edges_out.append({
                    "key": ek,
                    "source": ep_id,
                    "target": nid,
                    "attributes": {"kind": "mentions", "weight": 3.0},
                })
                edge_seen.add(ek)

    return {
        "attributes": {"name": "fail_history_wiki"},
        "options": {"type": "directed", "multi": True, "allowSelfLoops": True},
        "nodes": nodes_out,
        "edges": edges_out,
    }


@router.get("/graph")
def get_graph(
    type: str | None = Query(None, description="filter by node type: episode|concept|alias"),
    product: str | None = Query(None, description="filter by product"),
    limit: int = Query(300, ge=1, le=1000),
) -> dict[str, Any]:
    """graphology v0.25 호환 JSON. 5초 TTL 캐시."""
    key = f"{type or ''}|{product or ''}|{limit}"
    now = time.time()
    cached = _cache.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL_SEC:
        return cached[1]
    g = _build_graph(type, product, limit)
    _cache[key] = (now, g)
    return g


@router.get("/node/{node_id:path}")
def get_node(node_id: str) -> dict[str, Any]:
    """노드 상세 + backlinks (incoming edges). node_id 예: 'concept:4SS|STI CMP|EASY(W)'."""
    nodes = _scan_nodes()
    if node_id not in nodes:
        raise HTTPException(status_code=404, detail=f"node not found: {node_id}")
    md = dict(nodes[node_id])
    body = md.pop("_body", "")
    ntype = md.get("type", "")

    # backlinks = incoming edges (그래프 방향: episode→concept→alias)
    backlinks: list[str] = []
    if ntype == "concept":
        # concept ← episode (source_episode_ids에 등록된 episode들이 incoming)
        backlinks = [ep for ep in (md.get("source_episode_ids") or []) if ep in nodes]
    elif ntype == "episode":
        # episode ← (없음, episode는 leaf source). 다만 다른 노드의 links에 등록됐으면 잡음.
        for nid, m in nodes.items():
            if nid == node_id:
                continue
            if node_id in (m.get("links") or []):
                backlinks.append(nid)
    elif ntype == "alias":
        # alias ← concept (concept.links에 alias_id가 있는 경우)
        for nid, m in nodes.items():
            if m.get("type") == "concept" and node_id in (m.get("links") or []):
                backlinks.append(nid)
    return {"frontmatter": md, "body_markdown": body, "backlinks": backlinks}

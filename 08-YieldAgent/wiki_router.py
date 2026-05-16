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
    for kind in ("episodes", "concepts", "aliases", "super_concepts"):
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
    if ntype == "super_concept":
        return f"★ {md.get('axis', '')}={md.get('axis_value', '')}"
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
    elif ntype == "super_concept":
        attrs.update({
            "axis": md.get("axis", ""),
            "axis_value": md.get("axis_value", ""),
            "confidence": float(md.get("confidence", 0.0) or 0.0),
        })
    return attrs


def _axis_node_id(axis: str, value: str) -> str:
    return f"axis_{axis}:{value}"


def _axis_label(axis: str, value: str) -> str:
    tag = {"product": "🟦 P", "fail_type": "🟥 F", "cause_oper": "🟩 O"}.get(axis, axis)
    return f"{tag} · {value}"


def _build_product_tree(filter_product: str | None, limit: int) -> dict[str, Any]:
    """Phase 8: product 중심 3-tier 트리 view.

    Levels:
      product (root) → prod_fail (product+fail_type, alias 정규화) → concept (leaf)
    leaf label은 cause_oper만 — product/fail_type은 부모 노드에서 이미 보임.
    episode / alias / axis_* / super_concept 노드 제외.
    """
    nodes_raw = _scan_nodes()
    nodes_out: list[dict[str, Any]] = []
    edges_out: list[dict[str, Any]] = []
    products_added: set[str] = set()
    prod_fails_added: set[str] = set()
    edge_seen: set[str] = set()

    for nid, md in nodes_raw.items():
        if md.get("type") != "concept":
            continue
        product = (md.get("product") or "").strip()
        fail_type = (md.get("fail_type") or "").strip()
        cause_oper = (md.get("cause_oper") or "").strip()
        if not (product and fail_type and cause_oper):
            continue
        if filter_product and product != filter_product:
            continue
        fail_norm = fail_type.split("(", 1)[0].strip() if "(" in fail_type else fail_type

        pid = f"product:{product}"
        if pid not in products_added:
            products_added.add(pid)
            nodes_out.append({
                "key": pid,
                "attributes": {
                    "label": product,
                    "type": "product",
                    "size": 28,
                    "color": "#3b82f6",
                    "product": product,
                },
            })

        pf_id = f"prod_fail:{product}|{fail_norm}"
        if pf_id not in prod_fails_added:
            prod_fails_added.add(pf_id)
            nodes_out.append({
                "key": pf_id,
                "attributes": {
                    "label": fail_norm,
                    "type": "prod_fail",
                    "size": 18,
                    "color": "#ef4444",
                    "product": product,
                    "fail_type": fail_norm,
                },
            })
            ek = f"{pid}__{pf_id}__has_fail"
            if ek not in edge_seen:
                edges_out.append({
                    "key": ek, "source": pid, "target": pf_id,
                    "attributes": {"kind": "has_fail", "weight": 1.0},
                })
                edge_seen.add(ek)

        nodes_out.append({
            "key": nid,
            "attributes": {
                "label": cause_oper,
                "type": "concept",
                "size": 12,
                "color": "#22c55e",
                "product": product,
                "fail_type": fail_type,
                "cause_oper": cause_oper,
                "confidence": float(md.get("confidence", 0.0) or 0.0),
            },
        })
        ek = f"{pf_id}__{nid}__has_oper"
        if ek not in edge_seen:
            edges_out.append({
                "key": ek, "source": pf_id, "target": nid,
                "attributes": {"kind": "has_oper", "weight": 1.0},
            })
            edge_seen.add(ek)

        if len(nodes_out) >= limit:
            break

    return {
        "attributes": {"name": "fail_history_wiki", "view": "product_tree"},
        "options": {"type": "directed", "multi": True, "allowSelfLoops": True},
        "nodes": nodes_out,
        "edges": edges_out,
    }


def _build_graph(
    filter_type: str | None,
    filter_product: str | None,
    limit: int,
    view: str = "default",
) -> dict[str, Any]:
    if view == "product_tree":
        return _build_product_tree(filter_product, limit)
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

    # axis 노드 (product / fail_type / cause_oper) 자동 생성.
    # concept이 어느 축에 속하는지 그래프에서 직접 시각화 + 같은 axis 가진 concept이
    # 자연 클러스터됨. super_concept은 axis 1개와만 연결 ("abstracts").
    axis_nodes_added: dict[str, dict[str, Any]] = {}

    def _add_axis_node(axis: str, value: str) -> str | None:
        v = (value or "").strip()
        if not v:
            return None
        # fail_type alias normalize: "EASY(W)" → "EASY"
        if axis == "fail_type" and "(" in v:
            v = v.split("(", 1)[0].strip()
        aid = _axis_node_id(axis, v)
        if aid in seen_keys or aid in axis_nodes_added:
            return aid
        axis_nodes_added[aid] = {
            "label": _axis_label(axis, v),
            "type": f"axis_{axis}",
            "axis": axis,
            "axis_value": v,
        }
        return aid

    for nid, md in nodes_raw.items():
        if nid not in seen_keys:
            continue
        ntype = md.get("type", "")
        if ntype == "concept":
            for ax in ("product", "fail_type", "cause_oper"):
                aid = _add_axis_node(ax, md.get(ax, ""))
                if aid is None:
                    continue
                ek = f"{nid}__{aid}__has_{ax}"
                if ek in edge_seen:
                    continue
                edges_out.append({
                    "key": ek,
                    "source": nid,
                    "target": aid,
                    "attributes": {"kind": f"has_{ax}", "weight": 0.8},
                })
                edge_seen.add(ek)
        elif ntype == "super_concept":
            aid = _add_axis_node(md.get("axis", ""), md.get("axis_value", ""))
            if aid is not None:
                ek = f"{nid}__{aid}__abstracts"
                if ek not in edge_seen:
                    edges_out.append({
                        "key": ek,
                        "source": nid,
                        "target": aid,
                        "attributes": {"kind": "abstracts", "weight": 2.0},
                    })
                    edge_seen.add(ek)

    for aid, attrs in axis_nodes_added.items():
        nodes_out.append({"key": aid, "attributes": attrs})

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
    view: str = Query("default", description="default | product_tree"),
) -> dict[str, Any]:
    """graphology v0.25 호환 JSON. 5초 TTL 캐시.

    view='product_tree': product → fail_type → cause_oper 3-tier (Phase 8).
    """
    key = f"{type or ''}|{product or ''}|{limit}|{view}"
    now = time.time()
    cached = _cache.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL_SEC:
        return cached[1]
    g = _build_graph(type, product, limit, view)
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

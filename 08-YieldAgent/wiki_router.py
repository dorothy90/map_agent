"""Wiki vault graph endpoint (Day 5, plan v3 §wiki_router.py).

graphology v0.25 호환 JSON 반환:
- multi: true + edge key에 kind 포함 → 같은 source-target 쌍에 mentions / alias_of 동시 표현
- 5초 TTL 메모리 캐시 (vault 풀스캔 비용 완화)
- since 파라미터 미구현 (plan v3 변경: delta index 없음)
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

import frontmatter
from fastapi import APIRouter, HTTPException, Query

from wiki_config import resolve_wiki_paths

router = APIRouter()
logger = logging.getLogger("yield_agent.wiki_router")

_VAULT = resolve_wiki_paths().root
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


def _fetch_triple_aggregation(filter_product: str | None) -> dict[tuple[str, str, str], int]:
    """OpenSearch aggregation으로 (product, fail_norm, cause_oper) 트리플별 doc 수 집계.

    실패 시 빈 dict 반환 → product_tree는 vault concept만으로 자연 fallback.
    """
    try:
        from fail_history_tools import _get_opensearch_client, _OPENSEARCH_INDEX
        client = _get_opensearch_client()
        body = {
            "size": 0,
            "aggs": {
                "by_product": {
                    "terms": {"field": "product.keyword", "size": 50},
                    "aggs": {
                        "by_fail": {
                            "terms": {"field": "fail_type.keyword", "size": 50},
                            "aggs": {
                                "by_oper": {"terms": {"field": "cause_oper", "size": 50}}
                            }
                        }
                    }
                }
            }
        }
        resp = client.search(index=_OPENSEARCH_INDEX, body=body)
    except Exception as e:
        logger.warning("[product_tree] OpenSearch aggregation 실패 — vault만 사용: %s", e)
        return {}
    out: dict[tuple[str, str, str], int] = {}
    for pb in resp.get("aggregations", {}).get("by_product", {}).get("buckets", []):
        p = pb["key"]
        if filter_product and p != filter_product:
            continue
        for fb in pb.get("by_fail", {}).get("buckets", []):
            f = fb["key"]
            fnorm = f.split("(", 1)[0].strip() if "(" in f else f
            for ob in fb.get("by_oper", {}).get("buckets", []):
                key = (p, fnorm, ob["key"])
                out[key] = out.get(key, 0) + int(ob.get("doc_count", 0))
    return out


def _build_product_tree(filter_product: str | None, limit: int) -> dict[str, Any]:
    """Phase 8 + 9: product 중심 3-tier 트리 view.

    Leaf 채우는 출처 두 가지:
      - vault concept (합성됨) → 진한 초록 + confidence 표시 (has_wiki=True)
      - OpenSearch aggregation only (vault 없음) → 옅은 초록 + doc_count만 (has_wiki=False)

    OpenSearch 실패 시 vault concept만 표시 (Phase 8-A 동작).
    """
    # 1) vault concept 수집 — key: (product, fail_norm, cause_oper)
    vault_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for nid, md in _scan_nodes().items():
        if md.get("type") != "concept":
            continue
        p = (md.get("product") or "").strip()
        f = (md.get("fail_type") or "").strip()
        o = (md.get("cause_oper") or "").strip()
        if not (p and f and o):
            continue
        if filter_product and p != filter_product:
            continue
        fnorm = f.split("(", 1)[0].strip() if "(" in f else f
        vault_by_key.setdefault((p, fnorm, o), {"id": nid, "md": md, "fail_raw": f})

    # 2) OpenSearch aggregation 트리플 + doc_count
    aggregated = _fetch_triple_aggregation(filter_product)

    # 3) merge — 두 출처의 모든 트리플
    all_keys = sorted(set(vault_by_key.keys()) | set(aggregated.keys()))

    nodes_out: list[dict[str, Any]] = []
    edges_out: list[dict[str, Any]] = []
    products_added: set[str] = set()
    prod_fails_added: set[str] = set()
    edge_seen: set[str] = set()

    for (p, fnorm, o) in all_keys:
        # product 노드
        pid = f"product:{p}"
        if pid not in products_added:
            products_added.add(pid)
            nodes_out.append({
                "key": pid,
                "attributes": {
                    "label": p, "type": "product", "size": 28,
                    "color": "#3b82f6", "product": p,
                },
            })

        # prod_fail 노드
        pf_id = f"prod_fail:{p}|{fnorm}"
        if pf_id not in prod_fails_added:
            prod_fails_added.add(pf_id)
            nodes_out.append({
                "key": pf_id,
                "attributes": {
                    "label": fnorm, "type": "prod_fail", "size": 18,
                    "color": "#ef4444", "product": p, "fail_type": fnorm,
                },
            })
            ek = f"{pid}__{pf_id}__has_fail"
            if ek not in edge_seen:
                edges_out.append({
                    "key": ek, "source": pid, "target": pf_id,
                    "attributes": {"kind": "has_fail", "weight": 1.0},
                })
                edge_seen.add(ek)

        # leaf 노드 — vault 있으면 진한 초록(has_wiki=True), 없으면 옅은 초록
        vault_info = vault_by_key.get((p, fnorm, o))
        doc_count = aggregated.get((p, fnorm, o), 0)
        if vault_info:
            nid = vault_info["id"]
            md = vault_info["md"]
            leaf_attrs = {
                "label": o, "type": "concept", "size": 14,
                "color": "#16a34a",   # 진한 초록 = vault 합성됨
                "product": p, "fail_type": vault_info["fail_raw"], "cause_oper": o,
                "confidence": float(md.get("confidence", 0.0) or 0.0),
                "has_wiki": True,
                "doc_count": doc_count,
            }
        else:
            nid = f"trip:{p}|{fnorm}|{o}"
            leaf_attrs = {
                "label": o, "type": "concept", "size": 10,
                "color": "#bbf7d0",   # 옅은 초록 = OpenSearch only (vault 미합성)
                "product": p, "fail_type": fnorm, "cause_oper": o,
                "confidence": 0.0,
                "has_wiki": False,
                "doc_count": doc_count,
            }
        nodes_out.append({"key": nid, "attributes": leaf_attrs})
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


@router.get("/trip-docs")
def get_trip_docs(
    product: str = Query(...),
    fail_type: str = Query(...),
    cause_oper: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """(product, fail_type, cause_oper) 트리플로 OpenSearch fail-history 원본 docs.

    vault 합성이 안 된 ⚪ 트리플의 원본 사례를 프론트에 보여주기 위함.
    fail_type은 alias 포함 저장이라 prefix 매칭(예: 입력 EASY → 인덱스 EASY/EASY(W) 모두).
    실패 시 빈 docs + error 반환 (graceful — vault concept 페이지는 정상 동작).
    """
    try:
        from fail_history_tools import _OPENSEARCH_INDEX, _get_opensearch_client
        client = _get_opensearch_client()
        body = {
            "size": limit,
            "_source": {"excludes": ["embedding"]},
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"product.keyword": product}},
                        {"prefix": {"fail_type.keyword": fail_type}},
                        {"term": {"cause_oper": cause_oper}},
                    ]
                }
            },
        }
        resp = client.search(index=_OPENSEARCH_INDEX, body=body)
    except Exception as e:
        logger.warning("[trip-docs] OpenSearch 호출 실패: %s", e)
        return {"docs": [], "total": 0, "error": str(e)}

    docs: list[dict[str, Any]] = []
    for hit in resp.get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        docs.append({
            "doc_id": src.get("doc_id") or hit.get("_id", ""),
            "product": src.get("product", ""),
            "fail_type": src.get("fail_type", ""),
            "cause_oper": src.get("cause_oper", ""),
            "cause": src.get("cause", ""),
            "action": src.get("action", ""),
            "comment": src.get("comment", ""),
            "content": src.get("content", ""),
            "date": src.get("date", ""),
            "source_file": src.get("source_file", ""),
            "download_url": src.get("download_url", ""),
            "score": hit.get("_score"),
        })
    total_obj = resp.get("hits", {}).get("total", {})
    total = total_obj.get("value", len(docs)) if isinstance(total_obj, dict) else int(total_obj or len(docs))
    return {"docs": docs, "total": total}


@router.get("/doc/{doc_id}")
def get_doc(doc_id: str) -> dict[str, Any]:
    """doc_id로 OpenSearch 원본 doc 1건 조회 (FH-xxx 인용 클릭 시 사용)."""
    try:
        from fail_history_tools import _OPENSEARCH_INDEX, _get_opensearch_client
        client = _get_opensearch_client()
        resp = client.search(
            index=_OPENSEARCH_INDEX,
            body={
                "size": 1,
                "_source": {"excludes": ["embedding"]},
                "query": {"ids": {"values": [doc_id]}},
            },
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OpenSearch 호출 실패: {e}")
    hits = resp.get("hits", {}).get("hits", [])
    if not hits:
        raise HTTPException(status_code=404, detail=f"doc not found: {doc_id}")
    src = hits[0].get("_source", {})
    source_file = src.get("source_file", "")
    download_url = src.get("download_url", "")
    if not download_url and source_file:
        base = os.getenv("DOWNLOAD_BASE_URL", "https://downloadendpoint-estress/download")
        download_url = f"{base.rstrip('/')}/{source_file}"
    return {
        "doc_id": src.get("doc_id", doc_id),
        "product": src.get("product", ""),
        "fail_type": src.get("fail_type", ""),
        "cause_oper": src.get("cause_oper", ""),
        "date": src.get("date", ""),
        "source_file": source_file,
        "download_url": download_url,
        "cause": src.get("cause", ""),
        "action": src.get("action", ""),
        "comment": src.get("comment", ""),
    }


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

"""Wiki Docs (wikidocs.net 스타일) — Phase 10.

좌측 카테고리 트리: product → fail_type → cause_oper
우측: 선택된 트리플의 vault concept 본문 (markdown 렌더)
상단: substring 검색 → tree 필터링
데이터: 기존 /api/wiki/graph?view=product_tree endpoint 재활용.
"""
from __future__ import annotations

import os
from collections import defaultdict

import requests
import streamlit as st

API_BASE = os.getenv("AGENT_API_BASE", "http://127.0.0.1:8001")

st.set_page_config(page_title="Wiki Docs", layout="wide", page_icon="📚")
st.title("📚 Wiki Docs")
st.caption("vault에 누적된 wiki 본문을 카테고리별로 탐색 — product → fail → oper")

# ── tree 데이터 fetch ───────────────────────────────────
@st.cache_data(ttl=10)
def _fetch_tree():
    r = requests.get(
        f"{API_BASE}/api/wiki/graph",
        params={"view": "product_tree", "limit": 1000},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


try:
    g = _fetch_tree()
except Exception as e:
    st.error(f"백엔드 호출 실패 ({API_BASE}): {e}")
    st.stop()

# ── tree 구조 빌드: {product: {fail_norm: [(oper, has_wiki, doc_count, node_id), ...]}} ───
tree: dict[str, dict[str, list[tuple[str, bool, int, str]]]] = defaultdict(
    lambda: defaultdict(list)
)
leaf_meta: dict[tuple[str, str, str], dict] = {}  # (p, fnorm, o) → attrs
for n in g.get("nodes", []):
    attrs = n["attributes"]
    if attrs.get("type") != "concept":
        continue
    p = (attrs.get("product") or "").strip()
    f = (attrs.get("fail_type") or "").strip()
    o = (attrs.get("cause_oper") or "").strip()
    if not (p and f and o):
        continue
    fnorm = f.split("(", 1)[0].strip() if "(" in f else f
    has_wiki = bool(attrs.get("has_wiki", False))
    doc_count = int(attrs.get("doc_count", 0) or 0)
    tree[p][fnorm].append((o, has_wiki, doc_count, n["key"]))
    leaf_meta[(p, fnorm, o)] = {**attrs, "_id": n["key"]}

# ── 통계 ────────────────────────────────────────────────
total_triples = sum(len(opers) for fails in tree.values() for opers in fails.values())
total_wiki = sum(1 for v in leaf_meta.values() if v.get("has_wiki"))
c1, c2, c3 = st.columns(3)
c1.metric("Products", len(tree))
c2.metric("Triples", total_triples)
c3.metric("Wiki 합성", f"{total_wiki} / {total_triples}")

# ── 검색 ────────────────────────────────────────────────
q = st.text_input("검색", placeholder="product / fail / oper 부분 일치 (예: EASY)")
q_lower = q.lower().strip() if q else ""


def _match(p: str, f: str, o: str) -> bool:
    if not q_lower:
        return True
    return q_lower in f"{p} {f} {o}".lower()


# ── 사이드바 트리 ────────────────────────────────────────
if "wiki_docs_selected" not in st.session_state:
    st.session_state["wiki_docs_selected"] = None

with st.sidebar:
    st.header("카테고리")
    for product in sorted(tree.keys()):
        fail_groups = tree[product]
        # 검색 필터 후 visible 트리플 수
        visible_count = sum(
            1 for fail, opers in fail_groups.items()
            for (o, _, _, _) in opers if _match(product, fail, o)
        )
        if visible_count == 0:
            continue
        with st.expander(f"📁 {product} ({visible_count})", expanded=bool(q_lower)):
            for fail in sorted(fail_groups.keys()):
                visible_in_fail = [
                    (o, hw, dc, nid)
                    for (o, hw, dc, nid) in sorted(fail_groups[fail])
                    if _match(product, fail, o)
                ]
                if not visible_in_fail:
                    continue
                st.markdown(f"**📂 {fail}**")
                for (oper, has_wiki, doc_count, nid) in visible_in_fail:
                    icon = "✅" if has_wiki else "⚪"
                    label = f"{icon} {oper}"
                    if st.button(label, key=f"btn_{product}_{fail}_{oper}", use_container_width=True):
                        st.session_state["wiki_docs_selected"] = (product, fail, oper)
                        st.rerun()

# ── main: 선택된 트리플 본문 ─────────────────────────────
sel = st.session_state.get("wiki_docs_selected")
if not sel:
    st.info("← 좌측 카테고리에서 트리플을 선택하세요.")
    st.markdown(
        """
        **범례**
        ✅ vault 합성됨 — wiki 본문 (누적 패턴 / 검증된 조치 / 미해결) 표시
        ⚪ vault 없음 — 사용자 검색으로 자동 누적되면 본문 생성
        """
    )
    st.stop()

p, f, o = sel
leaf = leaf_meta.get((p, f, o), {})
st.markdown(f"## {p} / {f} / {o}")

if leaf.get("has_wiki") and leaf.get("_id", "").startswith("concept:"):
    # vault concept 본문 fetch
    nid = leaf["_id"]
    try:
        nr = requests.get(f"{API_BASE}/api/wiki/node/{nid}", timeout=10)
        nr.raise_for_status()
        node_data = nr.json()
    except Exception as e:
        st.warning(f"본문 fetch 실패: {e}")
        st.stop()
    if "error" in node_data:
        st.warning(f"노드 없음: {nid}")
        st.stop()
    md = node_data.get("frontmatter", node_data.get("metadata", {}))
    body = (
        node_data.get("body_markdown")  # wiki_router get_node 실 키
        or node_data.get("body")
        or node_data.get("_body")
        or ""
    )

    # frontmatter 요약 배지
    conf = float(md.get("confidence", 0) or 0)
    src_eps = len(md.get("source_episode_ids", []) or [])
    last_active = md.get("last_active", "?")
    badge_cols = st.columns(4)
    badge_cols[0].metric("Confidence", f"{conf:.2f}")
    badge_cols[1].metric("Source episodes", src_eps)
    badge_cols[2].metric("Last active", str(last_active)[:10] if last_active else "?")
    badge_cols[3].metric("Doc count", leaf.get("doc_count", 0))

    st.divider()
    if body:
        st.markdown(body)
    else:
        st.info("본문이 비어 있습니다.")

    # citations (있으면)
    cits = md.get("citations") or []
    if cits:
        st.divider()
        st.subheader("📎 Citations")
        for c in cits:
            label = c.get("natural_label") or c.get("doc_id") or c.get("source_file") or c.get("episode_id")
            url = c.get("download_url", "")
            if url:
                st.markdown(f"- {label} — [원본]({url})")
            else:
                st.markdown(f"- {label}")
else:
    st.info("📭 이 트리플은 아직 wiki에 합성된 본문이 없습니다.")
    st.caption(
        f"OpenSearch에 {leaf.get('doc_count', 0)} docs 인덱싱됨. "
        "사용자가 이 트리플로 검색하면 episode가 누적되고, 2건 이상이면 자동 합성됩니다."
    )
    deep_url = (
        f"{API_BASE.replace(':8001', ':8501')}/?q="
        f"{p}+{f}+{o}+%EC%A0%95%EB%A6%AC"
    )
    st.markdown(f"👉 [메인 검색에서 이 트리플 조회하기]({deep_url})")

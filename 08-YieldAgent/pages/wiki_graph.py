"""Wiki vault graph viewer (PoC dev UI, Streamlit multipage).

본격적인 운영 시각화는 별도 React+Vite repo의 react-force-graph-2d (plan v3 §부록).
이 페이지는 PoC 검증용 임시 viewer — 같은 백엔드 endpoint(/api/wiki/graph)를 사용한다.
"""
from __future__ import annotations

import os
from urllib.parse import quote

import requests
import streamlit as st
from streamlit_agraph import Config, Edge, Node, agraph

st.set_page_config(page_title="Wiki Graph", page_icon="🕸", layout="wide")

API_BASE = os.getenv("AGENT_BASE_URL", "http://127.0.0.1:8001")

st.title("🕸 Wiki Knowledge Graph")
st.caption("vault에 누적된 episode/concept/alias 시각화 (PoC dev viewer)")

# ── URL query params ────────────────────────────────────
qp = st.query_params
focus = qp.get("focus", "")
default_type = qp.get("type", "")
default_product = qp.get("product", "")

# ── 사이드바 필터 ───────────────────────────────────────
with st.sidebar:
    st.header("필터")
    type_options = ["(전체)", "episode", "concept", "alias"]
    type_idx = type_options.index(default_type) if default_type in type_options else 0
    type_filter = st.selectbox("노드 유형", type_options, index=type_idx)

    product_filter = st.text_input("Product", value=default_product, placeholder="4SS / 6E2 / 4SA / 5QQ")
    limit = st.number_input("Max nodes", min_value=10, max_value=1000, value=300, step=50)
    st.divider()
    st.markdown(
        f"""
        **사용법**
        - 카드 리포트의 [그래프에서 보기] → focus 노드 강조
        - URL 파라미터: `?focus=concept:4SS|...&product=4SS`
        - 백엔드 endpoint: `{API_BASE}/api/wiki/graph`
        """
    )

# ── 백엔드 fetch ────────────────────────────────────────
params = {"limit": int(limit)}
if type_filter != "(전체)":
    params["type"] = type_filter
if product_filter.strip():
    params["product"] = product_filter.strip()

try:
    r = requests.get(f"{API_BASE}/api/wiki/graph", params=params, timeout=10)
    r.raise_for_status()
    g = r.json()
except Exception as e:
    st.error(f"백엔드 호출 실패 ({API_BASE}): {e}")
    st.stop()

c1, c2, c3 = st.columns(3)
c1.metric("Nodes", len(g.get("nodes", [])))
c2.metric("Edges", len(g.get("edges", [])))
c3.metric("Multi", str(g.get("options", {}).get("multi", "?")))

if focus:
    st.info(f"🎯 Focus: `{focus}`")

# ── agraph 노드/엣지 ────────────────────────────────────
type_color = {"episode": "#3498db", "concept": "#9b59b6", "alias": "#95a5a6"}
type_size = {"episode": 12, "concept": 22, "alias": 14}

nodes = []
for n in g.get("nodes", []):
    nid = n["key"]
    attrs = n["attributes"]
    nt = attrs.get("type", "")
    is_focus = (nid == focus)
    seen = int(attrs.get("seen_count", 1)) if nt == "concept" else 1
    size = type_size.get(nt, 12) + (12 if is_focus else 0) + min(seen, 12)
    # title은 hover tooltip — newline/특수문자 제거 (vis-network가 일부 텍스트를 URL로 해석하는 케이스 회피)
    safe_title = f"{nid} | updated {attrs.get('updated', '')}".replace("\n", " ").replace("\r", " ")
    nodes.append(Node(
        id=nid,
        label=(attrs.get("label", nid) or nid)[:42],
        size=size,
        color="#e74c3c" if is_focus else type_color.get(nt, "#bbb"),
        title=safe_title,
    ))

edges = []
for e in g.get("edges", []):
    edges.append(Edge(
        source=e["source"],
        target=e["target"],
        label=e["attributes"].get("kind", ""),
        color="#aaa",
    ))

config = Config(width=1200, height=720, directed=True, physics=True, hierarchical=False)
agraph(nodes=nodes, edges=edges, config=config)

# ── focus node 상세 ─────────────────────────────────────
if focus:
    st.divider()
    st.subheader("Focus Node Detail")
    try:
        nr = requests.get(f"{API_BASE}/api/wiki/node/{quote(focus, safe=':|()')}", timeout=10)
        if nr.status_code == 200:
            nd = nr.json()
            with st.expander("frontmatter", expanded=True):
                st.json(nd["frontmatter"])
            if nd.get("body_markdown"):
                with st.expander("body", expanded=True):
                    st.markdown(nd["body_markdown"])
            if nd.get("backlinks"):
                with st.expander(f"backlinks ({len(nd['backlinks'])})", expanded=False):
                    st.write(nd["backlinks"][:30])
        else:
            st.warning(f"Node not found: {focus} (status={nr.status_code})")
    except Exception as e:
        st.warning(f"Node fetch 실패: {e}")

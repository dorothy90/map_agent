import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import mermaid from "mermaid";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

let _mermaidReady = false;
function initMermaid() {
  if (_mermaidReady) return;
  mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "loose" });
  _mermaidReady = true;
}

function MermaidDiagram({ code }) {
  const ref = useRef(null);
  useEffect(() => {
    initMermaid();
    const id = `mmd-${Math.random().toString(36).slice(2)}`;
    mermaid.render(id, code)
      .then(({ svg }) => { if (ref.current) ref.current.innerHTML = svg; })
      .catch(() => { if (ref.current) ref.current.textContent = code; });
  }, [code]);
  return <div ref={ref} className="mermaid-wrap" />;
}

function EpModal({ epId, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    fetchNode(`episode:${epId}`)
      .then(setData)
      .finally(() => setLoading(false));
  }, [epId]);
  return (
    <div className="ep-modal-overlay" onClick={onClose}>
      <div className="ep-modal" onClick={(e) => e.stopPropagation()}>
        <div className="ep-modal-header">
          <span className="ep-modal-title">ep:{epId.slice(0, 8)}</span>
          <button className="ep-modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="ep-modal-body">
          {loading && <div className="loading-text">로딩…</div>}
          {data && (
            <>
              <div className="ep-modal-meta">
                {data.frontmatter?.query && <div><b>쿼리</b>: {data.frontmatter.query}</div>}
                {data.frontmatter?.created && <div><b>생성</b>: {String(data.frontmatter.created).slice(0, 10)}</div>}
                {(data.frontmatter?.doc_ids || []).length > 0 && (
                  <div><b>docs</b>: {data.frontmatter.doc_ids.join(", ")}</div>
                )}
              </div>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.body_markdown || "(본문 없음)"}</ReactMarkdown>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function FhModal({ docId, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  useEffect(() => {
    fetchDoc(docId)
      .then(setData)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [docId]);
  return (
    <div className="ep-modal-overlay" onClick={onClose}>
      <div className="ep-modal" onClick={(e) => e.stopPropagation()}>
        <div className="ep-modal-header">
          <span className="ep-modal-title">{docId}</span>
          <button className="ep-modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="ep-modal-body">
          {loading && <div className="loading-text">로딩…</div>}
          {err && <div style={{ color: "var(--red)" }}>{err}</div>}
          {data && (
            <>
              <div className="ep-modal-meta">
                {data.date && <div><b>날짜</b>: {data.date}</div>}
                {data.product && <div><b>제품</b>: {data.product} / {data.fail_type} / {data.cause_oper}</div>}
                {data.source_file && (
                  <div><b>파일</b>:{" "}
                    {data.download_url
                      ? <a href={data.download_url} target="_blank" rel="noreferrer">📎 {data.source_file}</a>
                      : data.source_file}
                  </div>
                )}
              </div>
              {data.cause && <div style={{ marginTop: 8 }}><b>원인</b><br />{data.cause}</div>}
              {data.action && <div style={{ marginTop: 8 }}><b>조치</b><br />{data.action}</div>}
              {data.comment && <div style={{ marginTop: 8 }}><b>코멘트</b><br />{data.comment}</div>}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function addEpLinks(md) {
  return md
    .replace(/\[ep:([a-f0-9]+)\]/g, "[ep:$1](#ep:$1)")
    .replace(/\[(FH-[A-Z0-9]+)\]/g, "[$1](#fh:$1)");
}

function makeMdComponents(onEpClick, onFhClick) {
  return {
    code({ node, inline, className, children, ...props }) {
      const lang = (className || "").replace("language-", "");
      if (!inline && lang === "mermaid") {
        return <MermaidDiagram code={String(children).trim()} />;
      }
      return <code className={className} {...props}>{children}</code>;
    },
    a({ href, children }) {
      if (href?.startsWith("#ep:")) {
        const epId = href.slice(4);
        return (
          <button className="ep-ref-btn" onClick={() => onEpClick(epId)}>
            {children}
          </button>
        );
      }
      if (href?.startsWith("#fh:")) {
        const docId = href.slice(4);
        return (
          <button className="ep-ref-btn" onClick={() => onFhClick(docId)}>
            {children}
          </button>
        );
      }
      return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
    },
  };
}

import { fetchProductTree, fetchNode, fetchTripDocs, fetchDoc } from "../api/wiki";

function buildTree(graphJson) {
  const tree = {};
  const leafMeta = {};
  for (const n of graphJson?.nodes || []) {
    const a = n.attributes || {};
    if (a.type !== "concept") continue;
    const p = (a.product || "").trim();
    const f = (a.fail_type || "").trim();
    const o = (a.cause_oper || "").trim();
    if (!p || !f || !o) continue;
    const fnorm = f.includes("(") ? f.split("(")[0].trim() : f;
    const hasWiki = Boolean(a.has_wiki);
    const docCount = Number(a.doc_count || 0);
    if (!tree[p]) tree[p] = {};
    if (!tree[p][fnorm]) tree[p][fnorm] = [];
    tree[p][fnorm].push({ oper: o, hasWiki, docCount, nid: n.key });
    leafMeta[n.key] = { ...a, _id: n.key, fnorm };
  }
  for (const p of Object.keys(tree)) {
    for (const f of Object.keys(tree[p])) {
      tree[p][f].sort((a, b) => a.oper.localeCompare(b.oper));
    }
  }
  return { tree, leafMeta };
}

function matchQ(q, p, f, o) {
  if (!q) return true;
  return `${p} ${f} ${o}`.toLowerCase().includes(q.toLowerCase());
}

export default function WikiDocs() {
  const [searchParams, setSearchParams] = useSearchParams();
  const conceptParam = searchParams.get("concept") || "";

  const [graph, setGraph] = useState(null);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");
  const [selectedNid, setSelectedNid] = useState(conceptParam || null);
  const [openProducts, setOpenProducts] = useState({});
  const [nodeData, setNodeData] = useState(null);
  const [nodeLoading, setNodeLoading] = useState(false);
  const [nodeError, setNodeError] = useState(null);
  const [tripDocs, setTripDocs] = useState(null);
  const [tripLoading, setTripLoading] = useState(false);
  const [tripError, setTripError] = useState(null);
  const [epModal, setEpModal] = useState(null);
  const [fhModal, setFhModal] = useState(null);

  useEffect(() => {
    let cancel = false;
    fetchProductTree({ limit: 1000 })
      .then((g) => !cancel && setGraph(g))
      .catch((e) => !cancel && setError(String(e)));
    return () => { cancel = true; };
  }, []);

  const { tree, leafMeta } = useMemo(
    () => (graph ? buildTree(graph) : { tree: {}, leafMeta: {} }),
    [graph],
  );

  // URL ?concept= 동기화
  useEffect(() => {
    if (!selectedNid) return;
    if (conceptParam !== selectedNid) {
      const next = new URLSearchParams(searchParams);
      next.set("concept", selectedNid);
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNid]);

  useEffect(() => {
    if (conceptParam && conceptParam !== selectedNid) setSelectedNid(conceptParam);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conceptParam]);

  // 선택 변경 → 본문 fetch (vault concept이면 fetchNode, ⚪ 트리플이면 fetchTripDocs)
  const leafForFetch = selectedNid ? leafMeta[selectedNid] : null;
  useEffect(() => {
    setNodeData(null); setNodeError(null);
    setTripDocs(null); setTripError(null);
    if (!selectedNid || !leafForFetch) return;

    let cancel = false;
    if (leafForFetch.has_wiki && selectedNid.startsWith("concept:")) {
      setNodeLoading(true);
      fetchNode(selectedNid)
        .then((d) => !cancel && setNodeData(d))
        .catch((e) => !cancel && setNodeError(String(e)))
        .finally(() => !cancel && setNodeLoading(false));
    } else {
      setTripLoading(true);
      fetchTripDocs({
        product: leafForFetch.product,
        fail_type: leafForFetch.fnorm || leafForFetch.fail_type,
        cause_oper: leafForFetch.cause_oper,
      })
        .then((d) => !cancel && setTripDocs(d))
        .catch((e) => !cancel && setTripError(String(e)))
        .finally(() => !cancel && setTripLoading(false));
    }
    return () => { cancel = true; };
  }, [selectedNid, leafForFetch]);

  const stats = useMemo(() => {
    let triples = 0, wiki = 0;
    for (const p of Object.keys(tree))
      for (const f of Object.keys(tree[p]))
        for (const l of tree[p][f]) { triples += 1; if (l.hasWiki) wiki += 1; }
    return { products: Object.keys(tree).length, triples, wiki };
  }, [tree]);

  if (error) return <div className="error-box">백엔드 호출 실패: {error}</div>;
  if (!graph) return <div className="loading-text">vault 트리 로딩…</div>;

  const selectedLeaf = selectedNid ? leafMeta[selectedNid] : null;
  const productKeys = Object.keys(tree).sort();
  const queryActive = Boolean(query.trim());

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-header">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <div style={{ display: "flex", gap: 12 }}>
              <Link to="/" style={{ fontSize: 12, color: "var(--text-muted)", textDecoration: "none" }} onMouseOver={(e) => e.currentTarget.style.color = "var(--text)"} onMouseOut={(e) => e.currentTarget.style.color = "var(--text-muted)"}>← Home</Link>
              <Link to="/wiki/graph" style={{ fontSize: 12, color: "var(--text-muted)", textDecoration: "none" }} onMouseOver={(e) => e.currentTarget.style.color = "var(--text)"} onMouseOut={(e) => e.currentTarget.style.color = "var(--text-muted)"}>🕸 Graph</Link>
            </div>
            <button 
              onClick={() => { setSearchParams(new URLSearchParams(), { replace: true }); setSelectedNid(null); setQuery(""); setOpenProducts({}); }}
              style={{ fontSize: 11, color: "var(--text-muted)", cursor: "pointer", background: "none", border: "none" }}
              onMouseOver={(e) => e.currentTarget.style.color = "var(--text)"}
              onMouseOut={(e) => e.currentTarget.style.color = "var(--text-muted)"}
            >
              ↺ 초기화
            </button>
          </div>
          <h1 className="sidebar-title" style={{ marginTop: 8 }}>Wiki Docs</h1>
          <div className="sidebar-stat">
            {stats.products} products · {stats.triples} triples · {stats.wiki} wiki
          </div>
        </div>
        <input
          className="sidebar-search"
          placeholder="검색 — product / fail / oper"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="sidebar-body">
          {productKeys.map((product) => {
            const failGroups = tree[product];
            const visible = Object.entries(failGroups).flatMap(([fail, leaves]) =>
              leaves.filter((l) => matchQ(query, product, fail, l.oper)),
            );
            if (visible.length === 0) return null;
            const open = queryActive ? true : openProducts[product] ?? false;
            return (
              <div key={product} className="tree-group">
                <div
                  className="tree-row"
                  onClick={() => setOpenProducts((s) => ({ ...s, [product]: !open }))}
                >
                  <span className={`tri ${open ? "open" : "closed"}`}>▼</span>
                  <span className="icon">▸</span>
                  <span className="label">{product}</span>
                  <span className="count">{visible.length}</span>
                </div>
                {open && (
                  <div className="tree-children">
                    {Object.keys(failGroups).sort().map((fail) => {
                      const inFail = failGroups[fail].filter((l) =>
                        matchQ(query, product, fail, l.oper),
                      );
                      if (inFail.length === 0) return null;
                      return (
                        <div key={fail}>
                          <div className="tree-row" style={{ paddingLeft: 8 }}>
                            <span className="tri open">▾</span>
                            <span className="icon" style={{ color: "var(--red)" }}>◉</span>
                            <span className="label">{fail}</span>
                          </div>
                          {inFail.map((leaf) => (
                            <button
                              key={leaf.nid}
                              className={`tree-leaf-button ${selectedNid === leaf.nid ? "active" : ""}`}
                              onClick={() => setSelectedNid(leaf.nid)}
                              title={`${product} / ${fail} / ${leaf.oper}`}
                            >
                              <span className={`dot ${leaf.hasWiki ? "on" : "off"}`} />
                              <span className="leaf-label">{leaf.oper}</span>
                              {leaf.docCount > 0 && (
                                <span className="leaf-meta">{leaf.docCount}</span>
                              )}
                            </button>
                          ))}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </aside>

      <main className="main-pane">
        {!selectedLeaf && (
          <div className="reader-empty">
            <div style={{ fontSize: 22, color: "var(--text-muted)" }}>← 좌측 카테고리에서 선택</div>
            <div className="legend">
              <div className="legend-row">
                <span className="legend-dot" style={{ background: "var(--green)" }} /> vault 합성됨 — wiki 본문 표시
              </div>
              <div className="legend-row">
                <span className="legend-dot" style={{ background: "var(--text-dim)" }} /> vault 없음 — OpenSearch만 존재
              </div>
            </div>
          </div>
        )}

        {selectedLeaf && (
          <article className="reader">
            <header className="reader-header">
              <div className="reader-crumbs">
                <span>{selectedLeaf.product}</span>
                <span className="sep">/</span>
                <span>{selectedLeaf.fail_type || selectedLeaf.fnorm}</span>
                <span className="sep">/</span>
                <span>{selectedLeaf.cause_oper}</span>
              </div>
              <h1 className="reader-title">{selectedLeaf.cause_oper}</h1>
              <div className="reader-actions">
                <Link
                  to={`/wiki/graph?view=default&focus=${encodeURIComponent(selectedNid)}`}
                >
                  🕸 그래프에서 보기
                </Link>
              </div>
            </header>

            {(nodeLoading || tripLoading) && <div className="loading-text">본문 로딩…</div>}
            {nodeError && <div className="error-box">vault 본문 fetch 실패: {nodeError}</div>}
            {tripError && <div className="error-box">OpenSearch 호출 실패: {tripError}</div>}

            {selectedLeaf.has_wiki && nodeData && (
              <div className="reader-body">
                {nodeData.body_markdown ? (
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={makeMdComponents(setEpModal, setFhModal)}
                  >
                    {addEpLinks(nodeData.body_markdown)}
                  </ReactMarkdown>
                ) : (
                  <p style={{ color: "var(--text-muted)" }}>본문이 비어 있습니다.</p>
                )}
              </div>
            )}
            {!selectedLeaf.has_wiki && tripDocs && (
              <TripDocsList tripDocs={tripDocs} leaf={selectedLeaf} />
            )}
          </article>
        )}
      </main>

      {selectedLeaf && (
        <aside className="meta-pane">
          <MetaPane
            data={nodeData}
            tripDocs={tripDocs}
            leaf={selectedLeaf}
            loading={nodeLoading || tripLoading}
            onFhClick={setFhModal}
          />
        </aside>
      )}

      {epModal && <EpModal epId={epModal} onClose={() => setEpModal(null)} />}
      {fhModal && <FhModal docId={fhModal} onClose={() => setFhModal(null)} />}
    </div>
  );
}

function MetaPane({ data, tripDocs, leaf, loading, onFhClick }) {
  const md = data?.frontmatter || {};
  const cits = md.citations || [];
  const backlinks = data?.backlinks || [];
  const conf = Number(md.confidence || 0);
  const srcEps = (md.source_episode_ids || []).length;
  const lastActive = String(md.last_active || "").slice(0, 10) || "—";

  return (
    <>
      <div className="meta-section">
        <h2 className="meta-section-title">Metrics</h2>
        <div className="metrics-grid">
          <Metric label="Confidence" value={leaf.has_wiki ? conf.toFixed(2) : "—"} />
          <Metric label="Source eps" value={leaf.has_wiki ? srcEps : "—"} />
          <Metric label="Last active" value={leaf.has_wiki ? lastActive : "—"} />
          <Metric label="Doc count" value={leaf.doc_count || 0} />
        </div>
      </div>

      {leaf.has_wiki && data && (
        <>
          <div className="meta-section">
            <h2 className="meta-section-title">Frontmatter</h2>
            {Object.entries(md)
              .filter(([k]) =>
                ["product", "fail_type", "cause_oper", "status", "version", "seen_count", "stale_after_days"].includes(k),
              )
              .map(([k, v]) => (
                <div className="field-row" key={k}>
                  <div className="field-key">{k}</div>
                  <div className="field-val">{String(v)}</div>
                </div>
              ))}
          </div>

          {cits.length > 0 && (
            <div className="meta-section">
              <h2 className="meta-section-title">Citations · {cits.length}</h2>
              {cits.map((c, i) => {
                const label = c.natural_label || c.doc_id || c.source_file || c.episode_id || `cit-${i}`;
                const docId = c.doc_id || (c.episode_id?.startsWith("FH-") ? c.episode_id : null);
                return (
                  <div className="citation-item" key={i}>
                    {c.download_url ? (
                      <a href={c.download_url} target="_blank" rel="noreferrer">📎 {label}</a>
                    ) : docId ? (
                      <button className="ep-ref-btn" onClick={() => onFhClick(docId)}>📎 {label}</button>
                    ) : (
                      <span>📎 {label}</span>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {backlinks.length > 0 && (
            <div className="meta-section">
              <h2 className="meta-section-title">Backlinks · {backlinks.length}</h2>
              {backlinks.slice(0, 30).map((b) => (
                <div className="backlink-item" key={b}>↩ {b}</div>
              ))}
            </div>
          )}
        </>
      )}

      {!leaf.has_wiki && (
        <div className="meta-section">
          <h2 className="meta-section-title">OpenSearch</h2>
          {loading && <div className="empty-meta">로딩…</div>}
          {!loading && tripDocs && (
            <>
              <div className="field-row">
                <div className="field-key">total docs</div>
                <div className="field-val">{tripDocs.total ?? 0}</div>
              </div>
              <div className="field-row">
                <div className="field-key">표시 건수</div>
                <div className="field-val">{(tripDocs.docs || []).length}</div>
              </div>
              {tripDocs.error && (
                <div className="empty-meta" style={{ color: "var(--red)" }}>
                  {tripDocs.error}
                </div>
              )}
              <div className="empty-meta" style={{ marginTop: 10 }}>
                vault 합성 전 — 사용자 검색이 2건 이상 누적되면 자동 합성됩니다.
              </div>
            </>
          )}
        </div>
      )}
    </>
  );
}

function Metric({ label, value }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
    </div>
  );
}

export function TripDocsList({ tripDocs, leaf }) {
  const docs = tripDocs?.docs || [];
  const total = tripDocs?.total ?? docs.length;
  if (docs.length === 0) {
    return (
      <div className="badge-warn">
        OpenSearch에 매칭 docs가 없습니다. (product={leaf.product}, fail={leaf.fnorm || leaf.fail_type}, oper={leaf.cause_oper})
      </div>
    );
  }
  return (
    <div className="reader-body">
      <p style={{ color: "var(--text-muted)", marginTop: 0 }}>
        OpenSearch 원본 docs — {docs.length}건 표시 / 총 {total}건. (vault 합성 아직 안 됨)
      </p>
      {docs.map((d, i) => (
        <TripDocCard key={d.doc_id || i} doc={d} index={i} />
      ))}
    </div>
  );
}

export function TripDocCard({ doc, index }) {
  const title = doc.doc_id || `doc-${index + 1}`;
  return (
    <section className="trip-doc-card">
      <header className="trip-doc-head">
        <div className="trip-doc-id">{title}</div>
        <div className="trip-doc-meta">
          {doc.date && <span>{doc.date}</span>}
          {doc.fail_type && <span>· {doc.fail_type}</span>}
          {doc.cause_oper && <span>· {doc.cause_oper}</span>}
          {doc.download_url && (
            <a href={doc.download_url} target="_blank" rel="noreferrer">📎 원본</a>
          )}
        </div>
      </header>
      {doc.cause && <DocField label="원인" value={doc.cause} />}
      {doc.action && <DocField label="조치" value={doc.action} />}
      {doc.comment && <DocField label="코멘트" value={doc.comment} />}
      {doc.content && <DocField label="본문" value={doc.content} long />}
    </section>
  );
}

export function DocField({ label, value, long }) {
  return (
    <div className={`trip-doc-field ${long ? "long" : ""}`}>
      <div className="trip-doc-field-label">{label}</div>
      <div className="trip-doc-field-value">{value}</div>
    </div>
  );
}

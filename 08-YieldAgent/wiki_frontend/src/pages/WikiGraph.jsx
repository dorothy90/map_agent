import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import ForceGraph2D from "react-force-graph-2d";
import {
  forceSimulation,
  forceManyBody,
  forceLink,
  forceCenter,
  forceCollide,
} from "d3-force-3d";

import { fetchGraph, fetchNode, fetchTripDocs } from "../api/wiki";
import { TripDocsList } from "./WikiDocs.jsx";

const DEFAULT_COLORS = {
  concept: "#a78bfa",
  episode: "#60a5fa",
  alias: "#9ca3af",
  super_concept: "#fb923c",
  axis_product: "#facc15",
  axis_fail_type: "#facc15",
  axis_cause_oper: "#facc15",
  product: "#60a5fa",
  prod_fail: "#f87171",
};

const DEFAULT_SIZES = {
  concept: 5,
  episode: 3,
  alias: 2.5,
  super_concept: 6,
  axis_product: 6,
  axis_fail_type: 6,
  axis_cause_oper: 6,
  product: 7,
  prod_fail: 5,
};

const TYPE_LABELS = {
  concept: "concept",
  episode: "episode",
  alias: "alias",
  super_concept: "super",
  axis_product: "axis:product",
  axis_fail_type: "axis:fail",
  axis_cause_oper: "axis:oper",
  product: "product",
  prod_fail: "prod_fail",
};

const REAL_VAULT_PREFIXES = ["concept:", "episode:", "alias:", "super_concept:"];
function hasVaultFile(id) {
  return REAL_VAULT_PREFIXES.some((p) => id.startsWith(p));
}

const HIDDEN_FIELDS = new Set([
  "_body", "citations", "source_episode_ids", "links", "doc_ids", "body_versions",
]);

function fmtVal(v) {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) {
    if (v.length === 0) return "[]";
    return v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ");
  }
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

// 사전 layout: d3-force를 React 밖에서 동기적으로 N틱 돌려서 안정 좌표 계산 + fx/fy로 박음.
// 결과: 노드 위치 정적. ForceGraph2D는 그저 캔버스 렌더만 함.
function preLayout(nodes, links) {
  // d3-force는 nodes/links를 mutate함 → 사본 사용.
  const ns = nodes.map((n) => ({ ...n }));
  const ls = links.map((l) => ({ ...l }));
  const sim = forceSimulation(ns, 2)
    .force("charge", forceManyBody().strength(-90))
    .force(
      "link",
      forceLink(ls)
        .id((d) => d.id)
        .distance(40)
        .strength(0.6),
    )
    .force("center", forceCenter(0, 0))
    .force("collide", forceCollide().radius((d) => (d.val || 3) + 2))
    .stop();
  for (let i = 0; i < 300; i++) sim.tick();
  ns.forEach((n) => { n.fx = n.x; n.fy = n.y; });
  return { nodes: ns, links: ls };
}

function toForceGraphData(graphJson) {
  const nodes = (graphJson?.nodes || []).map((n) => {
    const a = n.attributes || {};
    return {
      id: n.key,
      name: a.label || n.key,
      type: a.type || "",
      color: a.color || DEFAULT_COLORS[a.type] || "#94a3b8",
      val: a.size ? Math.max(2, a.size / 3) : DEFAULT_SIZES[a.type] || 3,
      raw: a,
    };
  });
  const links = (graphJson?.edges || []).map((e) => ({
    source: e.source,
    target: e.target,
    kind: e.attributes?.kind || "",
  }));
  return { nodes, links };
}

function getProduct(n) { return (n.raw?.product || "").trim(); }
function normalizeFail(f) {
  if (!f) return "";
  return f.includes("(") ? f.split("(")[0].trim() : f.trim();
}
function getFail(n) { return normalizeFail(n.raw?.fail_type); }
function getOper(n) { return (n.raw?.cause_oper || "").trim(); }

function withAlpha(hex, alpha) {
  if (typeof hex !== "string" || !hex.startsWith("#")) return hex;
  let h = hex.slice(1);
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function buildAdjacency(data) {
  const adj = new Map();
  for (const n of data.nodes) adj.set(n.id, new Set());
  for (const l of data.links) {
    const s = typeof l.source === "object" ? l.source.id : l.source;
    const t = typeof l.target === "object" ? l.target.id : l.target;
    if (adj.has(s)) adj.get(s).add(t);
    if (adj.has(t)) adj.get(t).add(s);
  }
  return adj;
}

function bfsHops(adj, start, depth) {
  const visited = new Set([start]);
  let frontier = new Set([start]);
  for (let d = 0; d < depth; d++) {
    const next = new Set();
    for (const x of frontier) {
      for (const y of (adj.get(x) || [])) {
        if (!visited.has(y)) { visited.add(y); next.add(y); }
      }
    }
    frontier = next;
  }
  return visited;
}

export default function WikiGraph() {
  const [searchParams, setSearchParams] = useSearchParams();
  const view = searchParams.get("view") || "product_tree";
  const limit = Number(searchParams.get("limit") || "300");
  const focus = searchParams.get("focus") || "";
  const types = (searchParams.get("types") || "").split(",").filter(Boolean);
  const products = (searchParams.get("products") || "").split(",").filter(Boolean);
  const fails = (searchParams.get("fails") || "").split(",").filter(Boolean);
  const opers = (searchParams.get("opers") || "").split(",").filter(Boolean);
  const hasWikiOnly = searchParams.get("hasWikiOnly") === "1";
  const localOn = searchParams.get("local") === "1";
  const depth = Math.max(1, Math.min(3, Number(searchParams.get("depth") || "1")));
  const search = searchParams.get("q") || "";

  // local (URL 안 박는) display 상태
  const [labels, setLabels] = useState("hover");
  const [arrows, setArrows] = useState(true);
  const [nodeScale, setNodeScale] = useState(1.0);
  const [linkDist, setLinkDist] = useState(60);
  const [openSec, setOpenSec] = useState({
    filters: true, local: true, display: false, groups: true,
  });

  const [graph, setGraph] = useState(null);
  const [error, setError] = useState(null);
  const [selectedId, setSelectedId] = useState(focus || null);
  const [hoverId, setHoverId] = useState(null);

  const [nodeData, setNodeData] = useState(null);
  const [nodeError, setNodeError] = useState(null);
  const [tripDocs, setTripDocs] = useState(null);
  const [tripError, setTripError] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const fgRef = useRef(null);
  const containerRef = useRef(null);
  const [dims, setDims] = useState({ w: 800, h: 600 });

  // 1) graph fetch — 서버 필터는 view + limit 만, 나머지는 클라이언트
  useEffect(() => {
    let cancel = false;
    setGraph(null);
    setError(null);
    fetchGraph({ view, limit })
      .then((g) => !cancel && setGraph(g))
      .catch((e) => !cancel && setError(String(e)));
    return () => { cancel = true; };
  }, [view, limit]);

  // 2) container resize
  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setDims({ w: e.contentRect.width, h: e.contentRect.height });
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  // 3) focus URL → selectedId
  useEffect(() => {
    if (focus && focus !== selectedId) setSelectedId(focus);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus]);

  const selectedRaw = useMemo(() => {
    if (!selectedId || !graph) return null;
    const n = (graph.nodes || []).find((x) => x.key === selectedId);
    return n ? n.attributes || {} : null;
  }, [selectedId, graph]);

  // 4) selected → 상세 fetch (분기: vault concept / trip / 가상)
  useEffect(() => {
    setNodeData(null); setNodeError(null);
    setTripDocs(null); setTripError(null);
    if (!selectedId || !selectedRaw) return;

    let cancel = false;
    const t = selectedRaw.type;
    const isConceptWithFile = t === "concept" && hasVaultFile(selectedId) && selectedRaw.has_wiki !== false;
    const isOtherVaultFile = (t === "episode" || t === "alias" || t === "super_concept") && hasVaultFile(selectedId);
    const hasTriple = selectedRaw.product && selectedRaw.fail_type && selectedRaw.cause_oper;

    if (isConceptWithFile || isOtherVaultFile) {
      setDetailLoading(true);
      fetchNode(selectedId)
        .then((d) => !cancel && setNodeData(d))
        .catch((e) => !cancel && setNodeError(String(e)))
        .finally(() => !cancel && setDetailLoading(false));
    } else if (hasTriple) {
      setDetailLoading(true);
      fetchTripDocs({
        product: selectedRaw.product,
        fail_type: normalizeFail(selectedRaw.fail_type),
        cause_oper: selectedRaw.cause_oper,
      })
        .then((d) => !cancel && setTripDocs(d))
        .catch((e) => !cancel && setTripError(String(e)))
        .finally(() => !cancel && setDetailLoading(false));
    }
    return () => { cancel = true; };
  }, [selectedId, selectedRaw]);

  const update = (key, value) => {
    const next = new URLSearchParams(searchParams);
    if (value === "" || value == null || (Array.isArray(value) && value.length === 0)) {
      next.delete(key);
    } else {
      next.set(key, Array.isArray(value) ? value.join(",") : String(value));
    }
    setSearchParams(next, { replace: true });
  };
  const toggleCsv = (key, val, list) => {
    const s = new Set(list);
    if (s.has(val)) s.delete(val); else s.add(val);
    update(key, [...s]);
  };

  const baseData = useMemo(() => toForceGraphData(graph), [graph]);

  const uniqueTypes = useMemo(
    () => [...new Set(baseData.nodes.map((n) => n.type).filter(Boolean))].sort(),
    [baseData],
  );
  const uniqueProducts = useMemo(
    () => [...new Set(baseData.nodes.map(getProduct).filter(Boolean))].sort(),
    [baseData],
  );
  const uniqueFails = useMemo(
    () => [...new Set(baseData.nodes.map(getFail).filter(Boolean))].sort(),
    [baseData],
  );
  const uniqueOpers = useMemo(
    () => [...new Set(baseData.nodes.map(getOper).filter(Boolean))].sort(),
    [baseData],
  );

  const filteredData = useMemo(() => {
    const passes = (n) => {
      if (types.length && !types.includes(n.type)) return false;
      if (products.length) {
        const p = getProduct(n);
        if (!p || !products.includes(p)) return false;
      }
      if (fails.length) {
        const f = getFail(n);
        if (!f || !fails.includes(f)) return false;
      }
      if (opers.length) {
        const o = getOper(n);
        if (!o || !opers.includes(o)) return false;
      }
      if (hasWikiOnly && !(n.type === "concept" && n.raw?.has_wiki)) return false;
      return true;
    };
    const nodes = baseData.nodes.filter(passes);
    const ids = new Set(nodes.map((n) => n.id));
    const links = baseData.links.filter((l) => {
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      return ids.has(s) && ids.has(t);
    });
    return { nodes, links };
  }, [baseData, types, products, fails, opers, hasWikiOnly]);

  const finalData = useMemo(() => {
    if (!localOn || !selectedId) return filteredData;
    const inFiltered = filteredData.nodes.some((n) => n.id === selectedId);
    if (!inFiltered) return filteredData;
    const adj = buildAdjacency(filteredData);
    const visited = bfsHops(adj, selectedId, depth);
    const nodes = filteredData.nodes.filter((n) => visited.has(n.id));
    const ids = new Set(nodes.map((n) => n.id));
    const links = filteredData.links.filter((l) => {
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      return ids.has(s) && ids.has(t);
    });
    return { nodes, links };
  }, [filteredData, localOn, selectedId, depth]);

  const searchMatch = useMemo(() => {
    if (!search.trim()) return null;
    const q = search.toLowerCase();
    return new Set(finalData.nodes.filter((n) => n.name.toLowerCase().includes(q)).map((n) => n.id));
  }, [finalData, search]);

  const hoverAdj = useMemo(() => {
    if (!hoverId) return null;
    const adj = new Set([hoverId]);
    for (const l of finalData.links) {
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      if (s === hoverId) adj.add(t);
      if (t === hoverId) adj.add(s);
    }
    return adj;
  }, [hoverId, finalData]);

  // link distance d3 force 적용 — distance만 갱신, reheat 안 함 (이미 식은 시뮬레이션 깨우지 않음).
  useEffect(() => {
    if (!fgRef.current) return;
    const f = fgRef.current.d3Force?.("link");
    if (f) f.distance(() => linkDist);
  }, [linkDist]);

  // 사전 layout: finalData에 d3-force 동기 N틱 → fx/fy 박힌 정적 데이터.
  // hover/select 어떤 trigger에도 노드 위치 안 움직임.
  const laidOutData = useMemo(() => {
    if (finalData.nodes.length === 0) return finalData;
    return preLayout(finalData.nodes, finalData.links);
  }, [finalData]);

  // 데이터 변경/첫 마운트 시 카메라 fit. cooldownTicks=0이라 시뮬 안 돌고, fit만 즉시.
  useEffect(() => {
    const t = setTimeout(() => {
      if (fgRef.current && laidOutData.nodes.length > 0) {
        fgRef.current.zoomToFit?.(400, 60);
      }
    }, 100);
    return () => clearTimeout(t);
  }, [laidOutData]);

  const isDimmed = useCallback(
    (n) => {
      if (hoverAdj && !hoverAdj.has(n.id)) return true;
      if (searchMatch && !searchMatch.has(n.id)) return true;
      return false;
    },
    [hoverAdj, searchMatch],
  );

  const nodeColor = useCallback(
    (n) => {
      if (n.id === selectedId || n.id === hoverId) return "#ffffff";
      return isDimmed(n) ? withAlpha(n.color, 0.18) : n.color;
    },
    [selectedId, hoverId, isDimmed],
  );

  const linkColor = useCallback(
    (l) => {
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      const focused = s === selectedId || t === selectedId || s === hoverId || t === hoverId;
      return focused ? "rgba(167, 139, 250, 0.65)" : "rgba(140,140,140,0.15)";
    },
    [selectedId, hoverId],
  );

  const linkWidth = useCallback(
    (l) => {
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      return s === selectedId || t === selectedId || s === hoverId || t === hoverId ? 1.4 : 0.4;
    },
    [selectedId, hoverId],
  );

  const drawLabel = useCallback(
    (n) => {
      if (labels === "off") return false;
      if (labels === "always") return true;
      return n.id === selectedId || n.id === hoverId;
    },
    [labels, selectedId, hoverId],
  );

  const nodeCanvasObject = useCallback(
    (n, ctx, globalScale) => {
      if (n.id === selectedId || n.id === hoverId) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, (n.val || 3) * nodeScale + 4, 0, 2 * Math.PI);
        ctx.strokeStyle = "rgba(167, 139, 250, 0.7)";
        ctx.lineWidth = 1.2;
        ctx.stroke();
      }
      if (!drawLabel(n)) return;
      ctx.fillStyle = isDimmed(n) ? "rgba(220,220,220,0.25)" : "#f0f0f0";
      ctx.font = `${11 / globalScale}px Inter, sans-serif`;
      ctx.textAlign = "left";
      ctx.fillText(n.name, n.x + (n.val || 3) * nodeScale + 5, n.y + 3 / globalScale);
    },
    [selectedId, hoverId, nodeScale, drawLabel, isDimmed],
  );

  const nodeCanvasObjectMode = useCallback(
    (n) => (drawLabel(n) || n.id === selectedId || n.id === hoverId ? "after" : undefined),
    [drawLabel, selectedId, hoverId],
  );

  return (
    <div className="app-shell">
      <aside className="filter-panel">
        <div className="filter-panel-header">
          <Link to="/" style={{ fontSize: 12, color: "var(--text-muted)" }}>← Home</Link>
          <h1 className="sidebar-title" style={{ marginTop: 8 }}>Wiki Graph</h1>
          <div className="sidebar-stat">
            {finalData.nodes.length} / {baseData.nodes.length} nodes
          </div>
        </div>

        <FilterSection
          title="Filters"
          badge={types.length + products.length + fails.length + opers.length + (hasWikiOnly ? 1 : 0) || ""}
          open={openSec.filters}
          onToggle={() => setOpenSec((s) => ({ ...s, filters: !s.filters }))}
        >
          <div className="filter-row">
            <span className="filter-row-label">검색</span>
            <input
              value={search}
              onChange={(e) => update("q", e.target.value)}
              placeholder="label substring"
              style={{ width: "100%" }}
            />
          </div>
          <div className="filter-row">
            <span className="filter-row-label">View</span>
            <select
              value={view}
              onChange={(e) => update("view", e.target.value)}
              style={{ width: "100%" }}
            >
              <option value="product_tree">product_tree</option>
              <option value="default">default</option>
            </select>
          </div>
          <div className="filter-row">
            <span className="filter-row-label">Node type ({types.length || "all"})</span>
            <div className="chip-group">
              {uniqueTypes.map((t) => (
                <span
                  key={t}
                  className={`chip ${types.includes(t) ? "active" : ""}`}
                  onClick={() => toggleCsv("types", t, types)}
                >
                  <span className="swatch" style={{ background: DEFAULT_COLORS[t] || "#888" }} />
                  {TYPE_LABELS[t] || t}
                </span>
              ))}
            </div>
          </div>
          <MultiChip
            label="Product"
            values={uniqueProducts}
            selected={products}
            onToggle={(v) => toggleCsv("products", v, products)}
          />
          <MultiChip
            label="Fail type"
            values={uniqueFails}
            selected={fails}
            onToggle={(v) => toggleCsv("fails", v, fails)}
          />
          <MultiChip
            label="Cause oper"
            values={uniqueOpers}
            selected={opers}
            onToggle={(v) => toggleCsv("opers", v, opers)}
          />
          <label className="toggle-row">
            <span>has_wiki only (✅)</span>
            <input
              type="checkbox"
              checked={hasWikiOnly}
              onChange={(e) => update("hasWikiOnly", e.target.checked ? "1" : "")}
            />
          </label>
        </FilterSection>

        <FilterSection
          title="Local Graph"
          badge={localOn ? `depth ${depth}` : ""}
          open={openSec.local}
          onToggle={() => setOpenSec((s) => ({ ...s, local: !s.local }))}
        >
          <label className="toggle-row">
            <span>Focus on selected</span>
            <input
              type="checkbox"
              checked={localOn}
              onChange={(e) => update("local", e.target.checked ? "1" : "")}
            />
          </label>
          <div className="slider-row" style={{ marginTop: 8 }}>
            <span>Depth</span>
            <input
              type="range"
              min={1}
              max={3}
              value={depth}
              disabled={!localOn}
              onChange={(e) => update("depth", e.target.value)}
            />
            <span className="value">{depth}</span>
          </div>
          {!selectedId && localOn && (
            <div className="empty-meta" style={{ marginTop: 8 }}>
              노드를 선택해야 동작.
            </div>
          )}
        </FilterSection>

        <FilterSection
          title="Display"
          open={openSec.display}
          onToggle={() => setOpenSec((s) => ({ ...s, display: !s.display }))}
        >
          <div className="filter-row">
            <span className="filter-row-label">Labels</span>
            <div className="radio-row">
              {["always", "hover", "off"].map((opt) => (
                <label key={opt}>
                  <input
                    type="radio"
                    name="labels"
                    checked={labels === opt}
                    onChange={() => setLabels(opt)}
                  />
                  <span>{opt}</span>
                </label>
              ))}
            </div>
          </div>
          <label className="toggle-row">
            <span>Show arrows</span>
            <input type="checkbox" checked={arrows} onChange={(e) => setArrows(e.target.checked)} />
          </label>
          <div className="slider-row">
            <span>Node size</span>
            <input
              type="range"
              min={0.5}
              max={2}
              step={0.1}
              value={nodeScale}
              onChange={(e) => setNodeScale(Number(e.target.value))}
            />
            <span className="value">{nodeScale.toFixed(1)}</span>
          </div>
          <div className="slider-row">
            <span>Link dist</span>
            <input
              type="range"
              min={20}
              max={150}
              value={linkDist}
              onChange={(e) => setLinkDist(Number(e.target.value))}
            />
            <span className="value">{linkDist}</span>
          </div>
          <div className="filter-row" style={{ marginTop: 10 }}>
            <span className="filter-row-label">Limit (서버)</span>
            <input
              type="number"
              value={limit}
              min={1}
              max={1000}
              onChange={(e) => update("limit", e.target.value)}
              style={{ width: "100%" }}
            />
          </div>
        </FilterSection>

        <FilterSection
          title="Groups (Legend)"
          open={openSec.groups}
          onToggle={() => setOpenSec((s) => ({ ...s, groups: !s.groups }))}
        >
          {uniqueTypes.map((t) => (
            <div className="legend-row" key={t}>
              <span className="legend-dot" style={{ background: DEFAULT_COLORS[t] || "#888" }} />
              {TYPE_LABELS[t] || t}
            </div>
          ))}
          {uniqueTypes.length === 0 && (
            <div className="empty-meta">표시할 type 없음.</div>
          )}
        </FilterSection>
      </aside>

      <div className="graph-shell">
        <div className="topbar">
          <h1 className="topbar-title">{view}</h1>
          {selectedId && (
            <span className="topbar-stat" style={{ marginLeft: 12, wordBreak: "break-all" }}>
              selected: {selectedId}
            </span>
          )}
          <span className="topbar-spacer" />
          <span className="topbar-stat">
            {finalData.nodes.length} nodes · {finalData.links.length} edges
          </span>
        </div>
        <div ref={containerRef} className="graph-canvas">
          {error && <div className="error-box">백엔드 호출 실패: {error}</div>}
          {!graph && !error && <div className="graph-empty">그래프 로딩…</div>}
          {graph && finalData.nodes.length === 0 && (
            <div className="graph-empty">필터 결과 노드 없음.</div>
          )}
          {graph && finalData.nodes.length > 0 && (
            <ForceGraph2D
              ref={fgRef}
              width={dims.w}
              height={dims.h}
              backgroundColor="#161616"
              graphData={laidOutData}
              nodeRelSize={4 * nodeScale}
              nodeVal={(n) => n.val}
              nodeColor={nodeColor}
              nodeLabel={(n) => `${n.name}  ·  ${n.type}`}
              linkColor={linkColor}
              linkWidth={linkWidth}
              linkDirectionalArrowLength={arrows ? 2 : 0}
              linkDirectionalArrowRelPos={1}
              onNodeClick={(n) => setSelectedId(n.id)}
              onNodeHover={(n) => setHoverId(n ? n.id : null)}
              cooldownTicks={0}
              cooldownTime={0}
              warmupTicks={0}
              enableNodeDrag={false}
              nodeCanvasObjectMode={nodeCanvasObjectMode}
              nodeCanvasObject={nodeCanvasObject}
            />
          )}
        </div>
      </div>

      <aside className="meta-pane">
        {!selectedId && <div className="empty-meta">노드를 클릭하면 상세가 표시됩니다.</div>}
        {selectedId && (
          <>
            <div className="meta-section">
              <h2 className="meta-section-title">Selected</h2>
              <div className="backlink-item" style={{ wordBreak: "break-all" }}>{selectedId}</div>
              {selectedRaw?.type && (
                <div className="field-row" style={{ marginTop: 6 }}>
                  <div className="field-key">type</div>
                  <div className="field-val">{selectedRaw.type}</div>
                </div>
              )}
            </div>

            {detailLoading && <div className="empty-meta">상세 로딩…</div>}
            {nodeError && <div className="error-box">vault 본문 fetch 실패: {nodeError}</div>}
            {tripError && <div className="error-box">OpenSearch 호출 실패: {tripError}</div>}

            {nodeData && <NodeSummary data={nodeData} nodeId={selectedId} />}

            {tripDocs && selectedRaw && (
              <TripDocsList
                tripDocs={tripDocs}
                leaf={{
                  product: selectedRaw.product,
                  fail_type: selectedRaw.fail_type,
                  fnorm: normalizeFail(selectedRaw.fail_type),
                  cause_oper: selectedRaw.cause_oper,
                }}
              />
            )}

            {!detailLoading &&
              !nodeData &&
              !tripDocs &&
              !nodeError &&
              !tripError &&
              selectedRaw && <VirtualNodeSummary raw={selectedRaw} />}
          </>
        )}
      </aside>
    </div>
  );
}

function FilterSection({ title, badge, open, onToggle, children }) {
  return (
    <div className="filter-section">
      <div className="filter-section-head" onClick={onToggle}>
        <span className={`tri ${open ? "open" : "closed"}`}>▼</span>
        <span>{title}</span>
        {badge ? <span className="badge">{badge}</span> : null}
      </div>
      {open && <div className="filter-section-body">{children}</div>}
    </div>
  );
}

function MultiChip({ label, values, selected, onToggle }) {
  if (!values || values.length === 0) return null;
  return (
    <div className="filter-row">
      <span className="filter-row-label">{label} ({selected.length || "all"})</span>
      <div className="chip-group">
        {values.map((v) => (
          <span
            key={v}
            className={`chip ${selected.includes(v) ? "active" : ""}`}
            onClick={() => onToggle(v)}
          >
            {v}
          </span>
        ))}
      </div>
    </div>
  );
}

function VirtualNodeSummary({ raw }) {
  const fields = Object.entries(raw).filter(([k]) => !HIDDEN_FIELDS.has(k) && k !== "label");
  return (
    <div className="meta-section">
      <h2 className="meta-section-title">Attributes</h2>
      {fields.length === 0 ? (
        <div className="empty-meta">표시할 속성이 없습니다.</div>
      ) : (
        fields.map(([k, v]) => (
          <div className="field-row" key={k}>
            <div className="field-key">{k}</div>
            <div className="field-val">{fmtVal(v)}</div>
          </div>
        ))
      )}
      <div className="empty-meta" style={{ marginTop: 8 }}>
        합성 노드 — vault 파일이 없어 본문/citations 없음.
      </div>
    </div>
  );
}

function NodeSummary({ data, nodeId }) {
  const md = data.frontmatter || {};
  const body = data.body_markdown || "";
  const backlinks = data.backlinks || [];
  const preview = body.split("\n").slice(0, 14).join("\n");
  const fields = Object.entries(md)
    .filter(([k]) => !HIDDEN_FIELDS.has(k))
    .slice(0, 12);

  return (
    <>
      <div className="meta-section">
        <h2 className="meta-section-title">Frontmatter</h2>
        {fields.map(([k, v]) => (
          <div className="field-row" key={k}>
            <div className="field-key">{k}</div>
            <div className="field-val">{fmtVal(v)}</div>
          </div>
        ))}
      </div>

      {nodeId.startsWith("concept:") && (
        <div className="meta-section">
          <Link to={`/wiki/docs?concept=${encodeURIComponent(nodeId)}`}>📖 Docs에서 열기 →</Link>
        </div>
      )}

      {preview && (
        <div className="meta-section">
          <h2 className="meta-section-title">Preview</h2>
          <pre
            style={{
              whiteSpace: "pre-wrap",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              background: "var(--bg-2)",
              color: "var(--text)",
              padding: 8,
              borderRadius: "var(--radius-sm)",
              margin: 0,
              maxHeight: 220,
              overflow: "auto",
            }}
          >
            {preview}
          </pre>
        </div>
      )}

      {backlinks.length > 0 && (
        <div className="meta-section">
          <h2 className="meta-section-title">Backlinks · {backlinks.length}</h2>
          {backlinks.slice(0, 20).map((b) => (
            <div className="backlink-item" key={b}>↩ {b}</div>
          ))}
        </div>
      )}
    </>
  );
}

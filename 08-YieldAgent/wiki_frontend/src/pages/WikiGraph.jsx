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

function getProduct(n) { return (n.raw?.product || (n.type === "product" ? n.name : "")).trim(); }
function normalizeFail(f) {
  if (!f) return "";
  return f.includes("(") ? f.split("(")[0].trim() : f.trim();
}
function getFail(n) { return normalizeFail(n.raw?.fail_type || (n.type === "prod_fail" ? n.name : "")); }
function getOper(n) { return (n.raw?.cause_oper || "").trim(); }

const _alphaCache = new Map();
function withAlpha(hex, alpha) {
  if (typeof hex !== "string" || !hex.startsWith("#")) return hex;
  const key = `${hex}_${alpha}`;
  if (_alphaCache.has(key)) return _alphaCache.get(key);
  let h = hex.slice(1);
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  const val = `rgba(${r},${g},${b},${alpha})`;
  _alphaCache.set(key, val);
  return val;
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
  const [labels, setLabels] = useState("smart");
  const [arrows, setArrows] = useState(true);
  const [nodeScale, setNodeScale] = useState(1.0);
  const [linkDist, setLinkDist] = useState(60);
  const [openSec, setOpenSec] = useState({
    filters: true, local: true, display: false, groups: true,
  });

  const [graph, setGraph] = useState(null);
  const [error, setError] = useState(null);
  const [selectedId, setSelectedId] = useState(focus || null);
  const [metaMinimized, setMetaMinimized] = useState(false);

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
    // 트리 구조에서 상위 노드(product, prod_fail)가 하위 필터 조건(opers, hasWiki)에 의해
    // 불필요하게 남아있는 것(고아 노드)을 방지하기 위해 연결 정보를 확인합니다.
    const baseAdj = new Map();
    for (const l of baseData.links) {
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      if (!baseAdj.has(s)) baseAdj.set(s, []);
      if (!baseAdj.has(t)) baseAdj.set(t, []);
      baseAdj.get(s).push(t);
      baseAdj.get(t).push(s);
    }
    const nodeMap = new Map(baseData.nodes.map((n) => [n.id, n]));

    const passes = (n) => {
      // 1. Type
      if (types.length && !types.includes(n.type)) return false;

      // 2. Product
      if (products.length) {
        const p = getProduct(n);
        if (!p || !products.includes(p)) return false;
      }

      // 3. Fails
      if (fails.length) {
        if (n.type === "product") {
          const neighbors = baseAdj.get(n.id) || [];
          const hasValidFail = neighbors.some((nid) => {
            const nb = nodeMap.get(nid);
            if (!nb) return false;
            const f = getFail(nb);
            return f && fails.includes(f);
          });
          if (!hasValidFail) return false;
        } else {
          const f = getFail(n);
          if (!f || !fails.includes(f)) return false;
        }
      }

      // 4. Opers
      if (opers.length) {
        if (n.type === "prod_fail") {
          const neighbors = baseAdj.get(n.id) || [];
          const hasValidOper = neighbors.some((nid) => {
            const nb = nodeMap.get(nid);
            if (!nb) return false;
            const o = getOper(nb);
            return o && opers.includes(o);
          });
          if (!hasValidOper) return false;
        } else if (n.type === "product") {
          const neighbors = baseAdj.get(n.id) || [];
          let hasValidOperDescendant = false;
          for (const nid of neighbors) {
            const nb = nodeMap.get(nid);
            if (!nb || nb.type !== "prod_fail") continue;
            const failNeighbors = baseAdj.get(nb.id) || [];
            const valid = failNeighbors.some((nnid) => {
              const nnb = nodeMap.get(nnid);
              if (!nnb) return false;
              const o = getOper(nnb);
              return o && opers.includes(o);
            });
            if (valid) {
              hasValidOperDescendant = true;
              break;
            }
          }
          if (!hasValidOperDescendant) return false;
        } else {
          const o = getOper(n);
          if (!o || !opers.includes(o)) return false;
        }
      }

      // 5. hasWikiOnly
      if (hasWikiOnly) {
        if (n.type === "prod_fail") {
          const neighbors = baseAdj.get(n.id) || [];
          const hasWiki = neighbors.some((nid) => {
            const nb = nodeMap.get(nid);
            return nb && nb.type === "concept" && nb.raw?.has_wiki;
          });
          if (!hasWiki) return false;
        } else if (n.type === "product") {
          const neighbors = baseAdj.get(n.id) || [];
          let hasWikiDescendant = false;
          for (const nid of neighbors) {
            const nb = nodeMap.get(nid);
            if (!nb || nb.type !== "prod_fail") continue;
            const failNeighbors = baseAdj.get(nb.id) || [];
            const valid = failNeighbors.some((nnid) => {
              const nnb = nodeMap.get(nnid);
              return nnb && nnb.type === "concept" && nnb.raw?.has_wiki;
            });
            if (valid) {
              hasWikiDescendant = true;
              break;
            }
          }
          if (!hasWikiDescendant) return false;
        } else if (n.type === "concept") {
          if (!n.raw?.has_wiki) return false;
        } else {
          return false;
        }
      }

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

  const selectedAdj = useMemo(() => {
    if (!selectedId) return null;
    const adj = new Set([selectedId]);
    for (const l of finalData.links) {
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      if (s === selectedId) adj.add(t);
      if (t === selectedId) adj.add(s);
    }
    return adj;
  }, [selectedId, finalData]);

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

  const fitGraphRef = useRef(null);

  // 데이터 변경/첫 마운트 시 카메라 fit. (graph 데이터가 새로 fetch 되었을 때만 1회 실행)
  useEffect(() => {
    const t = setTimeout(() => {
      if (fgRef.current && laidOutData.nodes.length > 0) {
        if (fitGraphRef.current !== graph) {
          fgRef.current.zoomToFit?.(400, 60);
          fitGraphRef.current = graph;
        }
      }
    }, 100);
    return () => clearTimeout(t);
  }, [laidOutData, graph]);

  const isDimmed = useCallback(
    (n) => {
      if (selectedAdj && !selectedAdj.has(n.id)) return true;
      if (searchMatch && !searchMatch.has(n.id)) return true;
      return false;
    },
    [selectedAdj, searchMatch],
  );

  const nodeColor = useCallback(
    (n) => {
      if (n.id === selectedId) return "#ffffff";
      return isDimmed(n) ? withAlpha(n.color, 0.18) : n.color;
    },
    [selectedId, isDimmed],
  );

  const linkColor = useCallback(
    (l) => {
      const s = l.source.id || l.source;
      const t = l.target.id || l.target;
      const hasFocus = !!selectedId;
      const isFocusedEdge = s === selectedId || t === selectedId;
      
      if (hasFocus) {
        return isFocusedEdge ? "rgba(167, 139, 250, 0.9)" : "rgba(120, 120, 120, 0.15)";
      }
      return "rgba(180, 180, 180, 0.35)"; // 기본 상태일 때 선을 더 밝게 표시
    },
    [selectedId],
  );

  const linkWidth = useCallback(
    (l) => {
      const s = l.source.id || l.source;
      const t = l.target.id || l.target;
      const hasFocus = !!selectedId;
      const isFocusedEdge = s === selectedId || t === selectedId;
      
      if (hasFocus) {
        return isFocusedEdge ? 2.0 : 0.4;
      }
      return 1.0; // 기본 상태일 때 굵기를 더 두껍게 표시
    },
    [selectedId],
  );

  const drawLabel = useCallback(
    (n, globalScale) => {
      if (labels === "off") return false;
      if (labels === "always") return true;
      if (n.id === selectedId) return true;
      if (labels === "hover") return false;

      // labels === "smart"
      // product, fail 계열은 줌아웃 상태에서도 우선 표시
      const isMajor = ["product", "prod_fail", "axis_product", "axis_fail_type"].includes(n.type);
      if (isMajor) return true;
      
      // 나머지(oper 등 concept)는 일정 수준 줌인(> 0.8) 했을 때 표시
      if (globalScale > 0.8) return true;
      
      return false;
    },
    [labels, selectedId],
  );

  const nodeCanvasObject = useCallback(
    (n, ctx, globalScale) => {
      if (n.id === selectedId) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, (n.val || 3) * nodeScale + 4, 0, 2 * Math.PI);
        ctx.strokeStyle = "rgba(167, 139, 250, 0.7)";
        ctx.lineWidth = 1.2;
        ctx.stroke();
      }
      if (!drawLabel(n, globalScale)) return;
      ctx.fillStyle = isDimmed(n) ? "rgba(220,220,220,0.25)" : "#f0f0f0";
      ctx.font = `${11 / globalScale}px Inter, sans-serif`;
      ctx.textAlign = "left";
      ctx.fillText(n.name, n.x + (n.val || 3) * nodeScale + 5, n.y + 3 / globalScale);
    },
    [selectedId, nodeScale, drawLabel, isDimmed],
  );

  const nodeCanvasObjectMode = useCallback(() => "after", []);

  const handleReset = useCallback(() => {
    setSearchParams(new URLSearchParams(), { replace: true });
    setLabels("smart");
    setArrows(true);
    setNodeScale(1.0);
    setLinkDist(60);
    setOpenSec({ filters: true, local: true, display: false, groups: true });
    setSelectedId(null);
    if (fgRef.current && laidOutData.nodes.length > 0) {
      setTimeout(() => fgRef.current.zoomToFit?.(400, 60), 100);
    }
  }, [setSearchParams, laidOutData]);

  return (
    <div className="app-shell">
      <aside className="filter-panel">
        <div className="filter-panel-header">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <div style={{ display: "flex", gap: 12 }}>
              <Link to="/" style={{ fontSize: 12, color: "var(--text-muted)", textDecoration: "none" }} onMouseOver={(e) => e.currentTarget.style.color = "var(--text)"} onMouseOut={(e) => e.currentTarget.style.color = "var(--text-muted)"}>← Home</Link>
              <Link to="/wiki/docs" style={{ fontSize: 12, color: "var(--text-muted)", textDecoration: "none" }} onMouseOver={(e) => e.currentTarget.style.color = "var(--text)"} onMouseOut={(e) => e.currentTarget.style.color = "var(--text-muted)"}>📖 Docs</Link>
            </div>
            <button 
              onClick={handleReset}
              style={{ fontSize: 11, color: "var(--text-muted)", cursor: "pointer", background: "none", border: "none" }}
              onMouseOver={(e) => e.currentTarget.style.color = "var(--text)"}
              onMouseOut={(e) => e.currentTarget.style.color = "var(--text-muted)"}
            >
              ↺ 초기화
            </button>
          </div>
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
              {["smart", "always", "hover", "off"].map((opt) => (
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
          <button 
            onClick={() => fgRef.current?.zoomToFit(400, 60)} 
            style={{
              marginRight: 16, 
              padding: "4px 8px", 
              background: "var(--bg-2)", 
              border: "1px solid var(--border)", 
              borderRadius: "var(--radius-sm)", 
              color: "var(--text-muted)",
              fontSize: 12,
              cursor: "pointer"
            }}
            onMouseOver={(e) => { e.currentTarget.style.color = "var(--text)"; e.currentTarget.style.borderColor = "var(--accent)"; }}
            onMouseOut={(e) => { e.currentTarget.style.color = "var(--text-muted)"; e.currentTarget.style.borderColor = "var(--border)"; }}
          >
            ⛶ 화면 맞춤
          </button>
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
              onBackgroundClick={() => setSelectedId(null)}
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

      <aside
        className="meta-pane"
        style={{
          width: metaMinimized ? 40 : 320,
          padding: metaMinimized ? "14px 4px" : "14px 16px",
          transition: "all 0.2s ease",
          position: "relative",
          overflowX: "hidden"
        }}
      >
        {!selectedId && !metaMinimized && <div className="empty-meta">노드를 클릭하면 상세가 표시됩니다.</div>}
        {selectedId && metaMinimized && (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 16 }}>
            <button onClick={() => setMetaMinimized(false)} title="상세 패널 최대화" style={{ padding: 4, color: "var(--text-muted)" }}>
              ◀
            </button>
            <div style={{ writingMode: "vertical-rl", color: "var(--text-muted)", fontSize: 11, letterSpacing: 2 }}>
              {selectedId.length > 20 ? selectedId.slice(0, 20) + "..." : selectedId}
            </div>
          </div>
        )}
        {selectedId && !metaMinimized && (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16, alignItems: "center" }}>
              <h2 className="meta-section-title" style={{ margin: 0 }}>Selected Node</h2>
              <div style={{ display: "flex", gap: 8 }}>
                <button onClick={() => setMetaMinimized(true)} title="패널 최소화" style={{ color: "var(--text-muted)", fontSize: 12 }}>
                  ▶ 접기
                </button>
                <button onClick={() => setSelectedId(null)} title="선택 해제" style={{ color: "var(--text-muted)", fontSize: 12 }}>
                  ✕ 닫기
                </button>
              </div>
            </div>

            <div className="meta-section">
              <div className="backlink-item" style={{ wordBreak: "break-all", color: "var(--accent-hover)", fontWeight: 600 }}>{selectedId}</div>
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

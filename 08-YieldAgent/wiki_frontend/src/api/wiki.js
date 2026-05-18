// Wiki API client — yield-agent /api/wiki/* endpoints.

const API_BASE = import.meta.env.VITE_WIKI_API_BASE || "http://localhost:8001";

async function getJson(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`GET ${path} → ${res.status}: ${detail}`);
  }
  return res.json();
}

export function fetchProductTree({ limit = 1000, product } = {}) {
  const params = new URLSearchParams({ view: "product_tree", limit: String(limit) });
  if (product) params.set("product", product);
  return getJson(`/api/wiki/graph?${params.toString()}`);
}

export function fetchGraph({ view = "default", type, product, limit = 300 } = {}) {
  const params = new URLSearchParams({ view, limit: String(limit) });
  if (type) params.set("type", type);
  if (product) params.set("product", product);
  return getJson(`/api/wiki/graph?${params.toString()}`);
}

export function fetchNode(nodeId) {
  return getJson(`/api/wiki/node/${encodeURIComponent(nodeId)}`);
}

export function fetchTripDocs({ product, fail_type, cause_oper, limit = 20 }) {
  const params = new URLSearchParams({
    product,
    fail_type,
    cause_oper,
    limit: String(limit),
  });
  return getJson(`/api/wiki/trip-docs?${params.toString()}`);
}

export { API_BASE };

const nodes = [
  { id: '4SS', type: 'product', name: '4SS' },
  { id: 'IDDQ', type: 'prod_fail', name: 'IDDQ' },
  { id: 'c1', type: 'concept', raw: { cause_oper: 'PRE METAL CLN', has_wiki: true } },
  { id: 'c2', type: 'concept', raw: { cause_oper: 'GATE OX', has_wiki: false } },
  { id: 'ep1', type: 'episode' }
];
const links = [
  { source: '4SS', target: 'IDDQ' },
  { source: 'IDDQ', target: 'c1' },
  { source: 'IDDQ', target: 'c2' },
  { source: 'c1', target: 'ep1' }
];

const baseAdj = new Map();
links.forEach(l => {
  if (!baseAdj.has(l.source)) baseAdj.set(l.source, []);
  if (!baseAdj.has(l.target)) baseAdj.set(l.target, []);
  baseAdj.get(l.source).push(l.target);
  baseAdj.get(l.target).push(l.source);
});
const nodeMap = new Map(nodes.map(n => [n.id, n]));

function getOper(n) { return (n.raw?.cause_oper || "").trim(); }

const opers = ['PRE METAL CLN'];
const hasWikiOnly = true;
const fails = [];

const filtered = nodes.filter(n => {
  if (opers.length) {
    if (n.type === "prod_fail") {
      const neighbors = baseAdj.get(n.id) || [];
      const hasValidOper = neighbors.some(nid => {
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
      return false; // other types pruned
    }
  }

  return true;
});
console.log(filtered.map(n => n.id));

const nodes = [
  { id: '4SS', type: 'product', name: '4SS' },
  { id: 'IDDQ', type: 'prod_fail', name: 'IDDQ' },
  { id: 'VMIN', type: 'prod_fail', name: 'VMIN' },
  { id: 'c1', type: 'concept', raw: { cause_oper: 'PRE METAL CLN' } },
  { id: 'c2', type: 'concept', raw: { cause_oper: 'GATE OX' } }
];
const links = [
  { source: '4SS', target: 'IDDQ' },
  { source: '4SS', target: 'VMIN' },
  { source: 'IDDQ', target: 'c1' },
  { source: 'VMIN', target: 'c2' }
];

// simulate baseAdj
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
const filtered = nodes.filter(n => {
  if (opers.length) {
    if (n.type === "prod_fail") {
      const neighbors = baseAdj.get(n.id) || [];
      const hasOperNeighbor = neighbors.some(nid => {
         const nb = nodeMap.get(nid);
         const o = getOper(nb);
         return o && opers.includes(o);
      });
      if (!hasOperNeighbor) return false;
    } else if (n.type !== "product") {
      const o = getOper(n);
      if (!o || !opers.includes(o)) return false;
    }
  }
  return true;
});
console.log(filtered.map(n => n.id));

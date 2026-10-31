import type { ArtifactRef, Observation, StoredEvent } from './types';

function normalizeMime(mime?:string):string {
  return (mime || '').split(';')[0].trim().toLowerCase();
}
export function artifactMime(eventMime?:string,responseMime?:string):string {
  const event = normalizeMime(eventMime), response = normalizeMime(responseMime);
  if (event === 'text/html' || response === 'text/html') return 'text/html';
  return response && response !== 'application/octet-stream' ? response : event || response;
}
export function artifactKind(eventMime?:string,responseMime?:string) {
  const mime = artifactMime(eventMime,responseMime);
  if (mime === 'text/html') return 'html';
  if (mime === 'text/plain') return 'code';
  if (mime === 'text/markdown' || mime === 'text/x-markdown') return 'markdown';
  if (mime.startsWith('image/')) return 'image';
  return 'download';
}
export function collectArtifacts(observations:Observation[],events:StoredEvent[]):ArtifactRef[] {
  const refs = new Map<string,ArtifactRef>();
  for (const obs of observations) for (const ref of obs.artifact_refs || []) refs.set(ref.artifact_id,{...refs.get(ref.artifact_id),...ref});
  for (const event of events) if (event.type === 'artifact' && typeof event.payload.artifact_id === 'string') {
    const ref = event.payload as unknown as ArtifactRef;
    refs.set(ref.artifact_id,{...refs.get(ref.artifact_id),...ref});
  }
  return [...refs.values()];
}
// Ported from yield_frontend: mechanical display sizing, not semantic routing.
export function iframeHeight(title: string, data: string): number {
  const t = (title || "").toLowerCase();
  const trCount = data ? (data.match(/<tr\b/g) || []).length : 0;
  if (t.includes("yield_table")) {
    return trCount > 0 ? Math.max(240, 270 + (trCount - 7) * 26) : 270;
  }
  if (t.includes("lot_compare")) {
    return trCount > 0 ? Math.max(260, 310 + (trCount - 5) * 26) : 310;
  }
  if (t.includes("cummap")) return 480;
  if (t.includes("scatter") || t.includes("trend")) return 1140;
  if (t.includes("fail_history") || t.includes("wads_report")) {
    if (!data) return 420;
    const hasSummary = data.includes("details") || data.includes("summary-block");
    const base = hasSummary ? 420 : 280;
    return trCount > 0 ? Math.max(220, base + (trCount - 4) * 35) : 420;
  }
  if (t.includes("relation_tree")) return 450;
  return 600;
}

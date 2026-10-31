import type { StoredEvent, Observation, Tool } from './types';
export const active = (status: string) => ['created', 'running', 'cancelling'].includes(status);
export const text = (value: unknown): string => value == null ? '' : typeof value === 'string' ? value : JSON.stringify(value, null, 2);
export const statusLabel = (status: string) => ({created:'준비 중',running:'실행 중',waiting_user:'입력 대기',cancelling:'중지 중',cancelled:'중지됨',completed:'완료',success:'완료',partial:'일부 완료',failed:'실패',error:'오류',empty:'자료 없음'}[status] || status);
export async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let detail = ''; try { detail = text((await response.json()).detail); } catch { /* non-JSON gateway error */ }
    throw new Error(`HTTP ${response.status}${detail ? `: ${detail}` : ' — 백엔드 연결을 확인하세요.'}`);
  }
  return response.json() as Promise<T>;
}
export const post = <T>(url: string, body?: unknown) => api<T>(url, {method:'POST', headers:{'Content-Type':'application/json'}, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
export async function* readEvents(response: Response): AsyncGenerator<StoredEvent> {
  if (!response.ok || !response.body) throw new Error(`이벤트 연결 실패 (HTTP ${response.status})`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  function parse(frame: string) {
    const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
    if (!data) return;
    const event = JSON.parse(data) as StoredEvent;
    if (!event.event_id || !Number.isInteger(event.sequence) || !event.type || !event.payload) throw new Error('잘못된 이벤트 형식입니다.');
    return event;
  }
  try {
    while (true) {
      const {value,done} = await reader.read();
      buffer += decoder.decode(value, {stream:!done});
      // Normalize only after appending: CRLF can be split across network chunks.
      buffer = buffer.replaceAll('\r\n','\n');
      let boundary: number;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const event = parse(buffer.slice(0,boundary)); buffer = buffer.slice(boundary+2);
        if (event) yield event;
      }
      if (done) { if (buffer.trim()) { const event = parse(buffer); if (event) yield event; } break; }
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
export function mergeEvents(events: StoredEvent[], event: StoredEvent) {
  if (events.some(e => e.event_id === event.event_id || e.sequence === event.sequence)) return events;
  return [...events,event].sort((a,b) => a.sequence-b.sequence);
}
export function toolTree(events: StoredEvent[], observations: Observation[]): Tool[] {
  const map = new Map<string,Tool>();
  for (const event of events) {
    if (event.type !== 'tool_started' && event.type !== 'tool_finished') continue;
    const p = event.payload; const id = text(p.invocation_id); if (!id) continue;
    const prior = map.get(id);
    map.set(id, {id, name:text(p.name) || prior?.name || '도구', parent:text(p.parent_invocation_id) || prior?.parent,
      status:text(p.status || p.state) || prior?.status || 'running', elapsed:typeof p.elapsed_seconds === 'number' ? p.elapsed_seconds : prior?.elapsed,
      summary:text(p.summary) || prior?.summary, args:prior?.args, children:[]});
  }
  for (const obs of observations) {
    const node = map.get(obs.invocation_id) || {id:obs.invocation_id, name:obs.tool_name, status:obs.status, children:[]};
    Object.assign(node, {args:obs.validated_arguments, observation:obs, summary:obs.summary, status:obs.status});
    node.elapsed ??= typeof obs.provenance?.elapsed_seconds === 'number' ? obs.provenance.elapsed_seconds : undefined;
    node.parent ||= text(obs.provenance?.parent_invocation_id) || undefined;
    map.set(node.id,node);
  }
  const roots: Tool[] = [];
  for (const node of map.values()) {
    const ancestors = new Set([node.id]); let parent = node.parent; let cycle = false;
    while (parent && map.has(parent)) { if (ancestors.has(parent)) { cycle = true; break; } ancestors.add(parent); parent = map.get(parent)?.parent; }
    if (node.parent && map.has(node.parent) && !cycle) map.get(node.parent)!.children.push(node); else roots.push(node);
  }
  return roots;
}

// Display only: ResultTable sorts the original values from the server.
export const tableText = (value: unknown): string =>
  typeof value === 'number' && Number.isFinite(value) && !Number.isInteger(value)
    ? String(Number(value.toFixed(4))) : text(value);

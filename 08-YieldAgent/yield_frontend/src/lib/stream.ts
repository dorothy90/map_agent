import type { RealStreamEvent } from "@/types";

// agent_server(:8001) 클라이언트. vite proxy 가 /session·/chat 을 백엔드로 넘긴다.

/**
 * POST 스트림(`data: {...}\n\n`)을 읽어 SSE 이벤트를 순차 파싱.
 * EventSource 는 GET 전용이라 fetch + ReadableStream 으로 직접 처리.
 * (repl_agent/frontend/src/Chat.tsx 의 sseLines 포팅)
 */
async function* sseLines(resp: Response): AsyncGenerator<unknown> {
  if (!resp.body) throw new Error("no body");
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf("\n\n")) !== -1) {
      const raw = buf.slice(0, nl);
      buf = buf.slice(nl + 2);
      const line = raw.startsWith("data: ") ? raw.slice(6) : raw;
      if (!line) continue;
      try {
        yield JSON.parse(line);
      } catch {
        // skip malformed
      }
    }
  }
}

/** 새 세션 1개 생성 → session_id */
export async function createSession(): Promise<string> {
  const resp = await fetch("/session", { method: "POST" });
  if (!resp.ok) throw new Error(`POST /session HTTP ${resp.status}`);
  const json = (await resp.json()) as { session_id: string };
  return json.session_id;
}

export interface ChatArgs {
  query: string;
  sessionId: string;
  // resume 시: plan_review=자유텍스트(str), missing_param=슬롯 dict
  resumeValue?: string | Record<string, unknown>;
  runId?: string;
  goalRevision?: number;
  interruptId?: string;
  afterSequence?: number;
}

/** POST /chat/stream → 실제 SSE 이벤트 스트림 */
export async function* streamChat({
  query,
  sessionId,
  resumeValue,
  runId,
  goalRevision,
  interruptId,
  afterSequence = 0,
}: ChatArgs): AsyncGenerator<RealStreamEvent> {
  const body: Record<string, unknown> = { query, session_id: sessionId, request_id: crypto.randomUUID(), run_id: runId, goal_revision: goalRevision, interrupt_id: interruptId, after_sequence: afterSequence };
  if (resumeValue !== undefined) body.resume_value = resumeValue;
  const seen = new Set<string>();
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      const resp = await fetch("/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text().catch(() => "")}`);
      let ended = false;
      for await (const raw of sseLines(resp)) {
        const event = raw as RealStreamEvent;
        if (event.type === "stream_start" && event.after_sequence !== undefined) body.after_sequence = event.after_sequence;
        if (event.run_id) {
          body.run_id = event.run_id;
          delete body.resume_value;
        }
        // Replay from the start on reconnect; stable IDs prevent duplicate UI items.
        if (event.type === "stream_end" || event.type === "interrupt") ended = true;
        if (event.event_id && seen.has(event.event_id)) continue;
        if (event.event_id) seen.add(event.event_id);
        if (event.type === "user_input" && event.request_id === body.request_id) continue;
        yield event;
      }
      if (ended || !body.run_id) return;
      throw new Error("연결이 끊겼습니다. 저장된 실행에 다시 연결합니다.");
    } catch (error) {
      if (attempt === 3 || !body.run_id) throw error;
      await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
    }
  }
}

export async function cancelRun(runId: string): Promise<{ status: string; answer?: string }> {
  const response = await fetch(`/runs/${runId}/cancel`, { method: "POST" });
  if (!response.ok) throw new Error("작업 중지 요청에 실패했습니다.");
  return response.json();
}

export async function steerRun(runId: string, goalRevision: number, value: string): Promise<string> {
  const response = await fetch(`/runs/${runId}/input`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "steer", request_id: crypto.randomUUID(), goal_revision: goalRevision, value }),
  });
  if (!response.ok) throw new Error("작업 수정 요청에 실패했습니다.");
  return (await response.json()).run_id;
}

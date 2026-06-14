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
}

/** POST /chat/stream → 실제 SSE 이벤트 스트림 */
export async function* streamChat({
  query,
  sessionId,
  resumeValue,
}: ChatArgs): AsyncGenerator<RealStreamEvent> {
  const body: Record<string, unknown> = { query, session_id: sessionId };
  if (resumeValue !== undefined) body.resume_value = resumeValue;
  const resp = await fetch("/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${await resp.text().catch(() => "")}`);
  }
  for await (const raw of sseLines(resp)) {
    yield raw as RealStreamEvent;
  }
}

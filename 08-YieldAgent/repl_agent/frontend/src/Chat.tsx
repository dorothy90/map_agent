import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { PlotlyMessage, type PlotSpec } from "./PlotlyMessage";

type Msg =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string; streaming: boolean }
  | { kind: "tool_call"; id: string; name: string; code: string; argsJson: string }
  | { kind: "tool_result"; tool_call_id: string; name: string; text: string }
  | { kind: "plot"; spec: PlotSpec }
  | { kind: "error"; text: string };

/**
 * POST /repl/chat 스트림을 읽어 SSE 이벤트(`data: {...}`)를 순차 파싱.
 * EventSource 는 GET 전용이라 사용하지 않고, fetch + ReadableStream 으로 직접 처리.
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

function AssistantBubble({ text, streaming }: { text: string; streaming: boolean }) {
  return (
    <div className="msg assistant">
      <div className="msg-label">assistant</div>
      <div className="md">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text || (streaming ? " " : "")}</ReactMarkdown>
      </div>
      {streaming && <span className="cursor">▍</span>}
    </div>
  );
}

function ToolCallBubble({ name, code, argsJson }: { name: string; code: string; argsJson: string }) {
  const [open, setOpen] = useState(true);
  const showCode = name === "run_python" && code.length > 0;
  return (
    <div className="msg tool-call">
      <button className="msg-head" onClick={() => setOpen((v) => !v)}>
        <span className="tool-icon">▶</span>
        <span className="tool-name">{name}</span>
        <span className="tool-hint">{open ? "접기" : "펼치기"}</span>
      </button>
      {open && (
        <pre className="code">
          <code>{showCode ? code : argsJson}</code>
        </pre>
      )}
    </div>
  );
}

function ToolResultBubble({ name, text }: { name: string; text: string }) {
  const [open, setOpen] = useState(true);
  const trimmed = text.length > 4000 ? text.slice(0, 4000) + "\n... (truncated)" : text;
  return (
    <div className="msg tool-result">
      <button className="msg-head" onClick={() => setOpen((v) => !v)}>
        <span className="tool-icon">↳</span>
        <span className="tool-name">{name || "stdout"}</span>
        <span className="tool-hint">{open ? "접기" : "펼치기"}</span>
      </button>
      {open && <pre className="stdout">{trimmed || "(empty)"}</pre>}
    </div>
  );
}

export function Chat({ sessionId }: { sessionId: string }) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);

  function addMsg(m: Msg) {
    setMessages((xs) => [...xs, m]);
  }

  function sendPreset(q: string) {
    if (streaming) return;
    setInput(q);
    // 즉시 제출
    setTimeout(() => {
      const form = document.getElementById("chat-form") as HTMLFormElement | null;
      form?.requestSubmit();
    }, 0);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const q = input.trim();
    if (!q || streaming) return;
    setInput("");
    addMsg({ kind: "user", text: q });
    setStreaming(true);

    // 이번 턴의 스트리밍 assistant 버블 위치 (토큰이 쌓이는 곳)
    let assistantIdx: number | null = null;

    function ensureAssistant(): number {
      if (assistantIdx !== null) return assistantIdx;
      let idx = -1;
      setMessages((xs) => {
        idx = xs.length;
        return [...xs, { kind: "assistant", text: "", streaming: true }];
      });
      assistantIdx = idx;
      return idx;
    }

    function appendToAssistant(chunk: string) {
      ensureAssistant();
      setMessages((xs) => {
        const next = [...xs];
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].kind === "assistant" && (next[i] as { streaming: boolean }).streaming) {
            const cur = next[i] as { kind: "assistant"; text: string; streaming: boolean };
            next[i] = { ...cur, text: cur.text + chunk };
            return next;
          }
        }
        return next;
      });
    }

    function replaceAssistant(fullText: string) {
      if (assistantIdx === null) {
        addMsg({ kind: "assistant", text: fullText, streaming: false });
        return;
      }
      setMessages((xs) => {
        const next = [...xs];
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].kind === "assistant" && (next[i] as { streaming: boolean }).streaming) {
            next[i] = { kind: "assistant", text: fullText, streaming: false };
            assistantIdx = null;
            return next;
          }
        }
        return next;
      });
    }

    function finalizeAssistant() {
      setMessages((xs) => {
        const next = [...xs];
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].kind === "assistant" && (next[i] as { streaming: boolean }).streaming) {
            const cur = next[i] as { kind: "assistant"; text: string; streaming: boolean };
            next[i] = { ...cur, streaming: false };
          }
        }
        return next;
      });
      assistantIdx = null;
    }

    try {
      const resp = await fetch("/repl/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q, session_id: sessionId }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text().catch(() => "")}`);

      for await (const raw of sseLines(resp)) {
        const evt = raw as { type?: string; [k: string]: unknown };
        switch (evt.type) {
          case "start":
            break;
          case "token": {
            const content = String(evt.content ?? "");
            if (content) appendToAssistant(content);
            break;
          }
          case "assistant": {
            // 토큰 스트림이 완결된 assistant 메시지 — 토큰이 쌓여 있으면 그대로 덮고 스트리밍 종료
            const full = String(evt.content ?? "");
            if (full) replaceAssistant(full);
            break;
          }
          case "tool_call": {
            // 현재 스트리밍 assistant 가 있다면 그 턴을 일단 마무리하고 새 tool_call 로 전환
            finalizeAssistant();
            const name = String(evt.name ?? "");
            const args = (evt.args ?? {}) as Record<string, unknown>;
            const code = typeof args.code === "string" ? args.code : "";
            const argsJson = JSON.stringify(args, null, 2);
            addMsg({ kind: "tool_call", id: String(evt.id ?? ""), name, code, argsJson });
            break;
          }
          case "tool_result": {
            const name = String(evt.name ?? "");
            const text = String(evt.content ?? "");
            addMsg({
              kind: "tool_result",
              tool_call_id: String(evt.tool_call_id ?? ""),
              name,
              text,
            });
            break;
          }
          case "plot": {
            finalizeAssistant();
            try {
              const spec = JSON.parse(String(evt.payload ?? "")) as PlotSpec;
              addMsg({ kind: "plot", spec });
            } catch {
              addMsg({ kind: "error", text: "plot spec parse error" });
            }
            break;
          }
          case "error":
            addMsg({ kind: "error", text: String(evt.message ?? "error") });
            break;
          case "end":
            finalizeAssistant();
            break;
        }
      }
    } catch (err) {
      addMsg({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      finalizeAssistant();
      setStreaming(false);
    }
  }

  return (
    <>
      <div className="messages">
        {messages.length === 0 && (
          <div className="empty-hint">
            <p>질문을 입력하거나 아래 예시를 선택하세요.</p>
            <div className="preset-row">
              <button onClick={() => sendPreset("df.head() 를 보여주고 각 컬럼의 의미를 설명해줘")}>데이터 설명해줘</button>
              <button onClick={() => sendPreset("value 컬럼의 평균/표준편차/중앙값과 히스토그램을 보여줘")}>value 분포 보여줘</button>
              <button onClick={() => sendPreset("wafer 별 value 박스플롯을 그리고 차이가 유의한지 검정해줘")}>wafer별 비교</button>
              <button onClick={() => sendPreset("날짜별 value 추이를 line chart 로 보여줘")}>시계열 추이</button>
            </div>
          </div>
        )}
        {messages.map((m, i) => {
          switch (m.kind) {
            case "user":
              return (
                <div key={i} className="msg user">
                  {m.text}
                </div>
              );
            case "assistant":
              return <AssistantBubble key={i} text={m.text} streaming={m.streaming} />;
            case "tool_call":
              return <ToolCallBubble key={i} name={m.name} code={m.code} argsJson={m.argsJson} />;
            case "tool_result":
              return <ToolResultBubble key={i} name={m.name} text={m.text} />;
            case "plot":
              return (
                <div key={i} className="msg plot">
                  <PlotlyMessage spec={m.spec} />
                </div>
              );
            case "error":
              return (
                <div key={i} className="msg error">
                  {m.text}
                </div>
              );
          }
        })}
      </div>
      <form id="chat-form" className="input-row" onSubmit={submit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="질문 입력 — 예: value 평균·표준편차, 히스토그램, outlier, wafer별 비교..."
          disabled={streaming}
        />
        <button type="submit" disabled={streaming || !input.trim()}>
          {streaming ? "..." : "보내기"}
        </button>
      </form>
    </>
  );
}

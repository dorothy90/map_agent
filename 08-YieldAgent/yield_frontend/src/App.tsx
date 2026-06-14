import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Hexagon, LayoutGrid, Lightbulb, Send, Timer } from "lucide-react";
import { AgentPlan } from "@/components/AgentPlan";
import { ArtifactPanel } from "@/components/Artifacts";
import { HitlCard } from "@/components/Hitl";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { createSession, streamChat } from "@/lib/stream";
import type { CanvasCard, ChatItem, ExecStep, RealStreamEvent } from "@/types";

const PRESETS = [
  "최근 4주 4SS 수율 보여주고 열화 원인 알려줘",
  "원인 관계도 보여줘",
  "PPT 리포트로 정리해줘",
];

type ResumeValue = string | Record<string, unknown>;

function Markdown({ text }: { text: string }) {
  return (
    <div className="text-sm leading-relaxed [&_code]:rounded [&_code]:bg-background [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.85em] [&_code]:text-[var(--warn)] [&_li]:my-0.5 [&_ul]:my-1 [&_ul]:list-disc [&_ul]:pl-5">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [chat, setChat] = useState<ChatItem[]>([]);
  const [steps, setSteps] = useState<ExecStep[]>([]);
  const [cards, setCards] = useState<CanvasCard[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(false); // 미응답 interrupt 존재 → 다음 전송은 resume
  const [elapsed, setElapsed] = useState<string | null>(null);
  const cardSeq = useRef(0);
  const stepSeq = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    createSession()
      .then(setSessionId)
      .catch(() =>
        setChat((xs) => [
          ...xs,
          { kind: "error", text: "세션 생성 실패 — 백엔드(:8001)가 떠 있는지 확인하세요." },
        ]),
      );
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [chat]);
  useEffect(() => {
    canvasRef.current?.scrollTo({ top: canvasRef.current.scrollHeight, behavior: "smooth" });
  }, [cards]);

  function push(item: ChatItem) {
    setChat((xs) => [...xs, item]);
  }

  // 실제 SSE 이벤트 → UI 상태 reduce.
  async function consume(stream: AsyncGenerator<RealStreamEvent>) {
    function appendToken(agent: string, content: string) {
      setChat((xs) => {
        const next = [...xs];
        const last = next[next.length - 1];
        if (last && last.kind === "assistant" && last.streaming && last.agent === agent) {
          next[next.length - 1] = { ...last, text: last.text + content };
          return next;
        }
        return [...next, { kind: "assistant", agent, text: content, streaming: true }];
      });
    }
    function finalizeToken(agent: string, content: string) {
      setChat((xs) => {
        const next = [...xs];
        for (let i = next.length - 1; i >= 0; i--) {
          const m = next[i];
          if (m.kind === "assistant" && m.streaming && m.agent === agent) {
            next[i] = { ...m, text: content, streaming: false };
            return next;
          }
        }
        return [...next, { kind: "assistant", agent, text: content, streaming: false }];
      });
    }
    const dropThinking = () => setChat((xs) => xs.filter((m) => m.kind !== "thinking"));

    for await (const evt of stream) {
      switch (evt.type) {
        case "node_complete":
          setElapsed(`${evt.node} · ${evt.elapsed.toFixed(1)}s`);
          setSteps((s) => [...s, { id: `s${stepSeq.current++}`, node: evt.node, elapsed: evt.elapsed }]);
          break;
        case "status":
          setSteps((s) =>
            s.length && evt.node && s[s.length - 1].node === evt.node
              ? s.map((x, i) => (i === s.length - 1 ? { ...x, detail: evt.message } : x))
              : s,
          );
          break;
        case "thinking":
          setChat((xs) => {
            const next = [...xs];
            const last = next[next.length - 1];
            if (last && last.kind === "thinking") {
              next[next.length - 1] = { ...last, text: last.text + evt.content };
              return next;
            }
            return [...next, { kind: "thinking", text: evt.content }];
          });
          break;
        case "token":
          appendToken(evt.agent, evt.content);
          break;
        case "message":
          dropThinking();
          finalizeToken(evt.agent, evt.content);
          break;
        case "artifact": {
          if (!evt.data) break;
          const id = `c${cardSeq.current++}`;
          setCards((cs) => [
            ...cs,
            {
              id,
              agent: evt.agent,
              title: evt.title,
              artifactType: evt.artifact_type,
              mime: evt.mime,
              data: evt.data,
            },
          ]);
          break;
        }
        case "suggestion":
          dropThinking();
          push({ kind: "suggestion", text: evt.content });
          break;
        case "interrupt":
          dropThinking();
          push({
            kind: "interrupt",
            payload: {
              interrupt_type: evt.interrupt_type,
              param: evt.param,
              message: evt.message,
              route: evt.route,
              options: evt.options ?? [],
              fields: evt.fields ?? [],
            },
          });
          setPending(true);
          break;
        case "error":
          push({ kind: "error", text: evt.message });
          break;
        case "stream_start":
        case "stream_end":
          break;
      }
    }
  }

  async function run(userText: string, resumeValue?: ResumeValue) {
    if (!sessionId || busy) return;
    push({ kind: "user", text: userText });
    setBusy(true);
    setPending(false);
    setElapsed(null);
    try {
      await consume(streamChat({ query: userText, sessionId, resumeValue }));
    } catch (e) {
      push({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  function submitInput() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    if (pending) run(text, text);
    else run(text);
  }

  // HITL 카드 응답 → 해당 interrupt 를 answered 표시 후 resume.
  function answerInterrupt(value: ResumeValue, label: string) {
    setChat((xs) => {
      const next = [...xs];
      for (let i = next.length - 1; i >= 0; i--) {
        const m = next[i];
        if (m.kind === "interrupt" && !m.answered) {
          next[i] = { ...m, answered: label };
          break;
        }
      }
      return next;
    });
    run(label, value);
  }

  const empty = chat.length === 0;

  return (
    <div className="flex h-screen flex-col">
      {/* ── 상단바 ── */}
      <header className="flex items-center justify-between border-b bg-gradient-to-b from-card/60 to-background px-5 py-2.5">
        <div className="flex items-center gap-3">
          <Hexagon className="size-7 fill-primary/20 text-primary drop-shadow-[0_0_8px_var(--primary)]" />
          <div>
            <h1 className="text-base font-semibold tracking-tight">Yield Agent — 검증 콘솔</h1>
            <p className="text-xs text-muted-foreground">
              멀티에이전트 실행과 HITL을 실제 백엔드(:8001)로 검증합니다
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          {elapsed && (
            <span className="flex items-center gap-1 tabular-nums">
              <Timer className="size-3.5" /> {elapsed}
            </span>
          )}
          <Badge variant={busy ? "good" : pending ? "warn" : "muted"} className="gap-1.5">
            <span
              className={cn(
                "size-1.5 rounded-full",
                busy
                  ? "animate-pulse bg-[var(--good)] shadow-[0_0_8px_var(--good)]"
                  : pending
                    ? "bg-[var(--warn)]"
                    : "bg-muted-foreground",
              )}
            />
            {busy ? "실행 중" : pending ? "HITL 대기" : sessionId ? "대기" : "연결 중"}
          </Badge>
        </div>
      </header>

      {/* ── 본문 1:3 ── */}
      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[minmax(0,1fr)_minmax(0,3fr)]">
        {/* 좌: 대화 + 실행 타임라인 */}
        <div className="flex min-h-0 min-w-0 flex-col border-r">
          <div ref={scrollRef} className="flex flex-1 flex-col gap-2.5 overflow-y-auto px-4 pt-4">
            {empty && (
              <div className="my-auto px-4 py-8 text-center text-muted-foreground">
                <h2 className="mb-2 text-lg font-medium text-foreground">
                  수율 데이터에 무엇이든 물어보세요
                </h2>
                <p className="text-sm">
                  Supervisor 가 계획을 세우고, 계획 확인(plan_review)·누락 파라미터(missing_param)에서
                  멈춰 HITL 응답을 받습니다.
                </p>
              </div>
            )}
            {chat.map((m, i) => {
              switch (m.kind) {
                case "user":
                  return (
                    <div
                      key={i}
                      className="max-w-[88%] self-end whitespace-pre-wrap rounded-2xl bg-primary px-3.5 py-2 text-sm text-primary-foreground shadow"
                    >
                      {m.text}
                    </div>
                  );
                case "assistant":
                  return (
                    <div key={i} className="w-full self-start rounded-2xl border bg-card px-3.5 py-2.5">
                      <div className="mb-1 text-[0.62rem] font-semibold uppercase tracking-wider text-primary">
                        {m.agent}
                      </div>
                      <Markdown text={m.text} />
                      {m.streaming && <span className="animate-pulse text-primary">▍</span>}
                    </div>
                  );
                case "thinking":
                  return (
                    <div key={i} className="self-start px-2 py-1 text-[0.82rem] italic text-muted-foreground">
                      💭 {m.text}
                      <span className="animate-pulse">▍</span>
                    </div>
                  );
                case "interrupt":
                  return (
                    <HitlCard
                      key={i}
                      payload={m.payload}
                      answered={m.answered}
                      busy={busy}
                      onResume={answerInterrupt}
                    />
                  );
                case "suggestion":
                  return (
                    <div
                      key={i}
                      className="flex max-w-[92%] items-start gap-2 self-start rounded-xl border border-[var(--warn)]/30 bg-[var(--warn)]/[0.06] px-3 py-2 text-[0.82rem] text-[var(--warn)]"
                    >
                      <Lightbulb className="mt-0.5 size-4 shrink-0" />
                      {m.text}
                    </div>
                  );
                case "error":
                  return (
                    <div key={i} className="self-center rounded-lg bg-destructive/20 px-3 py-2 text-sm text-destructive">
                      {m.text}
                    </div>
                  );
              }
            })}
          </div>

          <AgentPlan steps={steps} />

          <div className="flex flex-wrap gap-2 px-4 pt-3">
            {PRESETS.map((p) => (
              <Button key={p} variant="chip" size="sm" onClick={() => run(p)} disabled={busy || !sessionId}>
                {p}
              </Button>
            ))}
          </div>
          <form
            className="flex gap-2 p-4"
            onSubmit={(e) => {
              e.preventDefault();
              submitInput();
            }}
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={pending ? "HITL 응답 입력 (resume)" : "예: 최근 4주 4SS 수율 보여줘"}
              disabled={busy || !sessionId}
              className="h-10 flex-1 rounded-lg border bg-card px-3.5 text-sm outline-none focus:border-primary focus:ring-1 focus:ring-ring disabled:opacity-50"
            />
            <Button type="submit" size="icon" className="h-10 w-10" disabled={busy || !sessionId || !input.trim()}>
              <Send className="size-4" />
            </Button>
          </form>
        </div>

        {/* 우: Artifact 캔버스 */}
        <div className="flex min-h-0 min-w-0 flex-col bg-[radial-gradient(120%_80%_at_80%_0%,var(--card)_0%,var(--background)_55%)]">
          <div className="flex items-center justify-between border-b px-5 py-3 text-[0.7rem] font-medium uppercase tracking-wider text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <LayoutGrid className="size-3.5" /> Artifacts
            </span>
            {cards.length > 0 && (
              <Button variant="outline" size="sm" className="h-6 px-2 text-[0.7rem] normal-case tracking-normal" onClick={() => setCards([])}>
                지우기
              </Button>
            )}
          </div>
          <div ref={canvasRef} className="flex-1 overflow-y-auto p-5">
            {cards.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center gap-3 px-4 py-12 text-center text-sm text-muted-foreground">
                <LayoutGrid className="size-8 opacity-30" />
                도구가 생성한 artifact(HTML·markdown·PPTX)가 agent 별로 여기에 그려집니다
              </div>
            ) : (
              <ArtifactPanel cards={cards} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

import { CheckCircle2 } from "lucide-react";
import type { ExecStep } from "@/types";

// 멀티에이전트 실행 타임라인 — node_complete/status 이벤트가 실시간으로 쌓인다.
// "어느 에이전트 노드가 언제 돌았는지"를 보여 multi-agent 흐름을 검증한다.
export function AgentPlan({ steps }: { steps: ExecStep[] }) {
  if (steps.length === 0) return null;
  return (
    <div className="max-h-44 overflow-y-auto border-t bg-card/40 px-4 py-3">
      <div className="mb-2 flex items-center justify-between text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">
        <span>실행 타임라인</span>
        <span className="font-mono tabular-nums text-primary">{steps.length} steps</span>
      </div>
      <ul className="flex flex-col gap-1.5">
        {steps.map((s) => (
          <li key={s.id} className="flex items-center gap-2 text-sm">
            <CheckCircle2 className="size-4 shrink-0 text-[var(--good)]" />
            <span className="flex-1 truncate font-mono text-xs text-foreground">{s.node}</span>
            {s.detail && (
              <span className="truncate text-[0.7rem] text-muted-foreground">{s.detail}</span>
            )}
            <span className="rounded bg-background px-1.5 py-0.5 font-mono text-[0.6rem] tabular-nums text-muted-foreground">
              {s.elapsed.toFixed(1)}s
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

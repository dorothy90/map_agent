import { useEffect, useState } from "react";
import { ArtifactPanel } from "@/components/Artifacts";
import { findObservation, resultCards, type ToolObservation } from "@/lib/tool-detail";
import type { CanvasCard, ExecStep } from "@/types";

export function ToolDetail({ step, cards }: { step: ExecStep; cards: CanvasCard[] }) {
  const [observation, setObservation] = useState<ToolObservation>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setLoading(true); setError(""); setObservation(undefined);
    async function read() {
      try {
        const response = await fetch(`/runs/${encodeURIComponent(step.runId!)}`, { signal: abort.signal });
        if (!response.ok) throw new Error("실행 상세를 불러오지 못했습니다.");
        const run = await response.json();
        if (!abort.signal.aborted) setObservation(findObservation(run.observations || [], step.runId!, step.invocationId!));
      } catch (cause) {
        if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : "실행 상세를 불러오지 못했습니다.");
      } finally { if (!abort.signal.aborted) setLoading(false); }
    }
    void read();
    return () => abort.abort();
  }, [step.runId, step.invocationId, step.state, retry]);

  const outputs = observation ? resultCards(observation, cards) : [];
  const missingArtifacts = observation?.artifact_refs.filter(ref => !cards.some(card => card.id === ref.artifact_id)).length || 0;
  return <section aria-label="도구 실행 상세" className="space-y-4">
    <div className="rounded-lg border bg-card p-4 text-sm">
      <h2 className="font-medium break-words">{step.node}</h2>
      <p className="mt-1 text-xs text-muted-foreground">{{ running: "실행 중", success: "완료", partial: "일부 완료", empty: "자료 없음", error: "실패", cancelled: "중지됨" }[step.state || "running"]} · {step.elapsed.toFixed(1)}초</p>
      {loading ? <p role="status" className="mt-3">실행 상세를 불러오는 중입니다.</p>
        : error ? <div className="mt-3"><p role="alert">{error}</p><button className="mt-2 rounded border px-3 py-1" onClick={() => setRetry(n => n + 1)}>다시 불러오기</button></div>
        : observation ? <>
          {observation.summary && <details className="mt-3"><summary className="cursor-pointer">실행 요약</summary><p className="mt-2 whitespace-pre-wrap break-words">{observation.summary}</p></details>}
          <details className="mt-3"><summary className="cursor-pointer">조회 조건·입력값</summary>
            <dl className="mt-2 space-y-2">{Object.entries(observation.validated_arguments).map(([name, value]) => <div key={name}>
              <dt className="text-xs text-muted-foreground">{name}</dt><dd className="whitespace-pre-wrap break-words">{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</dd>
            </div>)}</dl>
          </details>
          {(observation.error || observation.status === "error") && <div role="alert" className="mt-3 rounded border border-destructive/30 p-3">
            <p className="font-medium text-destructive">실행 오류</p>
            <p className="whitespace-pre-wrap break-words">{String(observation.error?.message || "도구 실행이 실패했습니다. 아래 실행 결과를 확인하세요.")}</p>
            {observation.error && <details className="mt-2"><summary className="cursor-pointer text-xs">오류 상세</summary><pre className="mt-2 whitespace-pre-wrap break-words text-xs">{JSON.stringify(observation.error, null, 2)}</pre></details>}
          </div>}
        </> : <p className="mt-3 text-muted-foreground">{step.state === "running" ? "실행이 끝나면 조회 조건과 결과가 표시됩니다." : "이 실행에는 저장된 결과가 없습니다."}</p>}
    </div>
    {!loading && !error && outputs.length > 0 && <ArtifactPanel cards={outputs} />}
    {!loading && !error && observation && outputs.length === 0 && <p className="text-sm text-muted-foreground">표시할 표·파일 결과가 없습니다.</p>}
    {missingArtifacts > 0 && <p className="text-xs text-muted-foreground">현재 화면에 없는 파일 결과 {missingArtifacts}개가 있습니다. 새로고침하면 저장된 결과를 다시 불러옵니다.</p>}
  </section>;
}

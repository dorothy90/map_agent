import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { PlotlyMessage } from "./PlotlyMessage";
import type { AnalysisRun, ToolStep } from "./replReducer";

interface AnalysisCardProps {
  run: AnalysisRun;
  onCancel: () => void;
  cancelPending: boolean;
}

const statusLabels: Record<AnalysisRun["status"], string> = {
  running: "실행 중",
  completed: "완료",
  failed: "실패",
  cancelled: "중지됨",
};

const textValue = (value: unknown): string =>
  typeof value === "string" ? value : JSON.stringify(value, null, 2);

function ToolExecution({ step, index }: { step: ToolStep; index: number }) {
  const args = step.args ?? {};
  const result = step.result;
  const code = typeof args.code === "string" ? args.code : JSON.stringify(args, null, 2);
  const stdout = result && typeof result.stdout === "string" ? result.stdout : "";
  const stderr = result && typeof result.stderr === "string" ? result.stderr : "";
  const elapsed = result?.execution_time_ms;
  const error = result?.error;
  const remainder = result
    ? Object.fromEntries(Object.entries(result).filter(([key]) => ![
        "status", "stdout", "stderr", "stdout_truncated", "stderr_truncated",
        "execution_time_ms", "error",
      ].includes(key)))
    : {};

  return (
    <li className="analysis-step">
      <span className="step-index" aria-hidden="true">{index + 1}</span>
      <div className="step-content">
        <div className="step-heading">
          <span className="step-kind">Python</span>
          <code>{step.name || "도구 실행"}</code>
        </div>
        <details open>
          <summary>코드</summary>
          <pre className="code"><code>{code || "(인자 없음)"}</code></pre>
        </details>
        {result ? (
          <details open>
            <summary>
              결과
              {typeof elapsed === "number" ? <span className="result-meta">{elapsed} ms</span> : null}
              {result.stdout_truncated || result.stderr_truncated ? (
                <span className="truncation-badge">잘림</span>
              ) : null}
            </summary>
            <div className="result-output">
              {stdout ? <section><h4>stdout</h4><pre>{stdout}</pre></section> : null}
              {stderr ? <section><h4>stderr</h4><pre>{stderr}</pre></section> : null}
              {error ? <section className="execution-error"><h4>error</h4><pre>{textValue(error)}</pre></section> : null}
              {Object.keys(remainder).length > 0 ? (
                <section><h4>result</h4><pre>{JSON.stringify(remainder, null, 2)}</pre></section>
              ) : null}
              {!stdout && !stderr && !error && Object.keys(remainder).length === 0 ? (
                <p className="empty-output">출력 없음</p>
              ) : null}
            </div>
          </details>
        ) : null}
        {step.artifacts?.map((artifact) => (
          <div className="artifact" key={artifact.artifact_id}>
            <PlotlyMessage artifact={artifact} />
          </div>
        ))}
      </div>
    </li>
  );
}

export function AnalysisCard({ run, onCancel, cancelPending }: AnalysisCardProps) {
  const correlatedArtifacts = new Set(
    run.steps.flatMap((step) => step.artifacts ?? []).map((artifact) => artifact.artifact_id),
  );
  const uncorrelatedArtifacts = run.artifacts.filter(
    (artifact) => !correlatedArtifacts.has(artifact.artifact_id),
  );

  return (
    <article className={`analysis-card status-${run.status}`} aria-labelledby={`query-${run.runId}`}>
      <header className="analysis-card-header">
        <div>
          <span className="analysis-label">분석 요청</span>
          <h2 id={`query-${run.runId}`}>{run.userMessage}</h2>
        </div>
        <div className="analysis-controls">
          <span className="analysis-status">{statusLabels[run.status]}</span>
          {run.status === "running" ? (
            <button className="stop-button" type="button" onClick={onCancel} disabled={cancelPending}>
              {cancelPending ? "중지 중…" : "중지"}
            </button>
          ) : null}
        </div>
      </header>

      {run.steps.length > 0 ? (
        <ol className="analysis-steps">
          {run.steps.map((step, index) => (
            <ToolExecution key={step.toolCallId} step={step} index={index} />
          ))}
        </ol>
      ) : null}

      {uncorrelatedArtifacts.map((artifact) => (
        <div className="artifact" key={artifact.artifact_id}>
          <PlotlyMessage artifact={artifact} />
        </div>
      ))}

      {run.error ? (
        <div className="analysis-error" role="alert">
          <span>{run.error.message}</span>
        </div>
      ) : null}

      {run.assistantText || run.status === "running" ? (
        <div className="analysis-answer md">
          <span className="analysis-label">분석 결과</span>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{run.assistantText || " "}</ReactMarkdown>
          {run.status === "running" ? <span className="cursor" aria-label="응답 작성 중">▍</span> : null}
        </div>
      ) : null}
    </article>
  );
}

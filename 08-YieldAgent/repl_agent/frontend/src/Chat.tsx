import { useState } from "react";
import { AnalysisCard } from "./AnalysisCard";
import { useReplStream } from "./useReplStream";

const presets = [
  ["데이터 설명해줘", "df.head()를 보여주고 각 컬럼의 의미를 설명해줘"],
  ["fail_value 분포", "wafer 단위로 lot_id와 wf_id 중복을 제거한 뒤 fail_value 평균, 표준편차, 중앙값과 히스토그램을 보여줘"],
  ["wafer별 비교", "lot_id와 wf_id 기준 wafer 단위 fail_value 분포를 비교하고 차이가 유의한지 검정해줘"],
  ["시계열 추이", "wafer 단위로 중복을 제거한 fail_value의 end_tm 날짜별 추이를 선 그래프로 보여줘"],
] as const;

export function Chat({ sessionId }: { sessionId: string }) {
  const [input, setInput] = useState("");
  const { state, send, cancel, sending, cancelPending, cancelError } = useReplStream(sessionId);
  const locked = sending || state.runtimeLost;

  function submitQuestion(question: string) {
    const trimmed = question.trim();
    if (!trimmed || locked) return;
    setInput("");
    void send(trimmed);
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    submitQuestion(input);
  }

  async function requestCancel() {
    try {
      await cancel();
    } catch {
      // useReplStream exposes the presentation error while preserving rejection for other callers.
    }
  }

  return (
    <section className="chat-shell" aria-label="분석 대화">
      <div className="messages">
        {state.runs.length === 0 && !state.runtimeLost ? (
          <div className="empty-hint">
            <p>질문을 입력하거나 아래 예시를 선택하세요.</p>
            <div className="preset-row" role="group" aria-label="추천 질문">
              {presets.map(([label, question]) => (
                <button key={label} type="button" onClick={() => submitQuestion(question)} disabled={locked}>
                  {label}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {state.runs.map((run) => (
          <AnalysisCard
            key={run.runId}
            run={run}
            onCancel={() => void requestCancel()}
            cancelPending={cancelPending && run.status === "running"}
          />
        ))}
        {state.runtimeLost ? (
          <div className="runtime-lost" role="alert">
            Python 실행 상태가 소실되었습니다. 새 세션을 시작해주세요.
          </div>
        ) : null}
        {cancelError ? (
          <div className="cancel-error" role="alert">
            중지 요청을 보내지 못했습니다. 다시 시도해주세요. ({cancelError})
          </div>
        ) : null}
      </div>
      <form className="input-row" aria-label="분석 질문" onSubmit={submit}>
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="질문 입력 — 예: fail_value 평균, 분포, wafer별 비교..."
          disabled={locked}
        />
        <button type="submit" aria-label="질문 보내기" disabled={locked || !input.trim()}>
          {sending ? "분석 중…" : "보내기"}
        </button>
      </form>
    </section>
  );
}

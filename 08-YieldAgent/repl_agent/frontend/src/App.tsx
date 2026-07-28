import { useEffect, useState } from "react";
import { SessionForm, type SessionInfo } from "./SessionForm";
import { Chat } from "./Chat";

export function App() {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [closing, setClosing] = useState(false);
  const [closeError, setCloseError] = useState<string | null>(null);

  useEffect(() => {
    if (!session) return;
    const closeOnPageHide = () => {
      try {
        void Promise.resolve(fetch(`/repl/session/${session.session_id}`, {
          method: "DELETE",
          keepalive: true,
        })).catch(() => undefined);
      } catch {
        // Page teardown permits only a best-effort close request.
      }
    };
    window.addEventListener("pagehide", closeOnPageHide);
    return () => window.removeEventListener("pagehide", closeOnPageHide);
  }, [session]);

  async function startNewSession() {
    if (!session || closing) return;
    setClosing(true);
    setCloseError(null);
    try {
      const response = await fetch(`/repl/session/${session.session_id}`, { method: "DELETE" });
      if (response.status === 200 || response.status === 404) {
        setSession(null);
        return;
      }
      setCloseError("세션을 종료하지 못했습니다. 잠시 후 다시 시도해주세요.");
    } catch {
      setCloseError("세션을 종료하지 못했습니다. 네트워크 연결을 확인하고 다시 시도해주세요.");
    } finally {
      setClosing(false);
    }
  }

  return (
    <div className="app">
      <main className="workspace" aria-label="Yield 분석 워크스페이스">
        <header className="workspace-header">
          <div className="brand-lockup">
            <span className="brand-kicker">YIELD ANALYSIS</span>
            <h1>Yield Agent</h1>
          </div>
          {session ? (
            <span className="data-context-pill" aria-label="현재 데이터">
              {session.query.lotcd} · {session.query.fail_name}
            </span>
          ) : (
            <span className="data-context-pill is-empty">데이터 선택</span>
          )}
        </header>

        {session ? (
          <section className="data-sheet data-sheet-summary" aria-label="선택된 데이터">
            <div>
              <span className="data-sheet-label">분석 데이터</span>
              <strong>{session.query.lotcd} · {session.query.fail_name}</strong>
              <span className="data-sheet-meta">
                {session.query.start} — {session.query.end} · {session.rowcount.toLocaleString()} rows
              </span>
            </div>
            <button className="data-change-button" onClick={() => void startNewSession()} disabled={closing}>
              {closing ? "세션 종료 중…" : "데이터 변경"}
            </button>
          </section>
        ) : (
          <section className="data-sheet data-sheet-expanded" aria-label="분석 데이터 선택">
            <SessionForm onStarted={setSession} />
          </section>
        )}

        {closeError ? <div className="session-close-error" role="alert">{closeError}</div> : null}
        {session ? (
          <Chat sessionId={session.session_id} />
        ) : (
          <section className="chat-preview" aria-label="분석 대화 준비">
            <p>데이터를 선택하면 이 화면에서 바로 분석을 시작할 수 있습니다.</p>
          </section>
        )}
      </main>
    </div>
  );
}

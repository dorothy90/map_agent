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
      <header>
        <h1>Yield 검증 REPL</h1>
        {session ? (
          <p className="muted">
            세션 {session.session_id.slice(0, 8)}… ·
            LOTCD <b>{session.query.lotcd}</b> ·
            기간 <b>{session.query.start}</b> ~ <b>{session.query.end}</b> ·
            fail_name <b>{session.query.fail_name}</b> ·
            {" "}rows <b>{session.rowcount}</b> ·
            columns [{session.columns.join(", ")}]
            {" · "}
            <button className="link" onClick={() => void startNewSession()} disabled={closing}>
              {closing ? "세션 종료 중…" : "새 세션"}
            </button>
          </p>
        ) : (
          <p className="muted">세션을 시작해 데이터를 먼저 불러오세요.</p>
        )}
      </header>

      {closeError ? <div className="session-close-error" role="alert">{closeError}</div> : null}
      {session ? <Chat sessionId={session.session_id} /> : <SessionForm onStarted={setSession} />}
    </div>
  );
}

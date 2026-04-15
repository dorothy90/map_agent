import { useState } from "react";
import { SessionForm, type SessionInfo } from "./SessionForm";
import { Chat } from "./Chat";

export function App() {
  const [session, setSession] = useState<SessionInfo | null>(null);

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
            <button className="link" onClick={() => setSession(null)}>
              새 세션
            </button>
          </p>
        ) : (
          <p className="muted">세션을 시작해 데이터를 먼저 불러오세요.</p>
        )}
      </header>

      {session ? <Chat sessionId={session.session_id} /> : <SessionForm onStarted={setSession} />}
    </div>
  );
}

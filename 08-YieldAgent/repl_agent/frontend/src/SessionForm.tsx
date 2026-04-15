import { useState } from "react";

export interface SessionInfo {
  session_id: string;
  rowcount: number;
  columns: string[];
  numeric_columns: string[];
  query: { lotcd: string; start: string; end: string; fail_name: string };
  has_column_guideline?: boolean;
}

export function SessionForm({ onStarted }: { onStarted: (info: SessionInfo) => void }) {
  const [lotcd, setLotcd] = useState("L12345");
  const [start, setStart] = useState("2026-02-16");
  const [end, setEnd] = useState("2026-04-16");
  const [failName, setFailName] = useState("OPEN");
  const [columnGuideline, setColumnGuideline] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const resp = await fetch("/repl/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lotcd,
          start,
          end,
          fail_name: failName,
          column_guideline: columnGuideline.trim() || null,
        }),
      });
      if (!resp.ok) {
        const detail = await resp.text().catch(() => resp.statusText);
        throw new Error(`HTTP ${resp.status}: ${detail}`);
      }
      const info = (await resp.json()) as SessionInfo;
      onStarted(info);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <form className="session-form" onSubmit={submit}>
      <h2>새 세션</h2>
      <p className="muted">LOTCD / 기간 / fail_name 을 지정하면 서버가 데이터를 한 번 불러와 이후 여러 검증을 이어서 수행합니다.</p>

      <label>
        LOTCD
        <input value={lotcd} onChange={(e) => setLotcd(e.target.value)} required />
      </label>
      <div className="row">
        <label>
          기간 시작
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} required />
        </label>
        <label>
          기간 끝
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} required />
        </label>
      </div>
      <label>
        fail_name
        <input value={failName} onChange={(e) => setFailName(e.target.value)} required />
      </label>
      <label>
        컬럼 설명 (선택) — LLM 에 전달
        <textarea
          value={columnGuideline}
          onChange={(e) => setColumnGuideline(e.target.value)}
          placeholder={"예)\nfail_value: 이 wafer 의 불량 수치 (wafer 당 1개)\nlegend/legend_value: 공정 속성 (Equip/Chamber/Recipe/Route × 1~4)\noper: oper1~oper500 공정 step\nend_tm: wafer 완료 시각 (YYYY-MM-DD HH:MM:SS)"}
          rows={4}
        />
      </label>

      {error && <div className="error">{error}</div>}

      <button type="submit" disabled={loading}>
        {loading ? "로딩 중..." : "세션 시작"}
      </button>
    </form>
  );
}

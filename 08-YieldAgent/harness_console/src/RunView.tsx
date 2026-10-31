import { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, CheckCheck, RotateCw, Square } from 'lucide-react';
import { active, post, statusLabel, text, toolTree } from './core';
import { useRun } from './useRun';
import { Markdown } from './Markdown';
import { Timeline } from './Timeline';
import { InputForm } from './InputForm';
import { Artifact, ResultTable } from './Artifacts';
import { collectArtifacts } from './artifactUtils';
import type { Run, StoredEvent, Question } from './types';
export interface Report {run:Run;events:StoredEvent[];syncedAt:number}
export function Usage({report}: {report?:Report}) {
  const [now,setNow] = useState(Date.now());
  const running = report ? active(report.run.status) : false;
  useEffect(() => {if (!running) return;const id=setInterval(() => setNow(Date.now()),1000);return () => clearInterval(id);},[running]);
  const usage = report?.events.filter(e => e.type === 'model_usage') || [];
  const sum = (key:string) => usage.reduce((total,event) => total + (typeof event.payload[key] === 'number' ? event.payload[key] as number : 0),0);
  const known = (key:string) => usage.length > 0 && usage.every(e => typeof e.payload[key] === 'number');
  const fmt = (value:number | undefined) => value === undefined ? '—' : value.toLocaleString('ko');
  const elapsed = (report?.run.usage?.active_seconds || 0) + (running && report ? Math.max(0,(now-report.syncedAt)/1000) : 0);
  return <aside className="usage-panel"><h2><Activity size={16}/>실행 정보</h2><div className="elapsed">{Math.floor(elapsed/60).toString().padStart(2,'0')}<span>:</span>{Math.floor(elapsed%60).toString().padStart(2,'0')}</div><p className="muted">{running ? '실행 시간 · 실시간' : '실행 시간'}</p>
    <dl><div><dt>입력 토큰</dt><dd>{known('input_tokens') ? fmt(sum('input_tokens')) : '—'}</dd></div><div><dt>출력 토큰</dt><dd>{known('output_tokens') ? fmt(sum('output_tokens')) : '—'}</dd></div><div className="total"><dt>전체 토큰</dt><dd>{fmt(report?.run.usage?.tokens ?? (known('total_tokens') ? sum('total_tokens') : undefined))}</dd></div><div><dt>모델 호출</dt><dd>{fmt(report?.run.usage?.models ?? (usage.length || undefined))}</dd></div></dl>
    <p className="usage-note">— 는 서버에서 제공하지 않은 수치입니다. 전체 토큰에 예약 사용량이 포함될 수 있습니다.</p>
    {report && <details className="run-meta"><summary>실행 식별자</summary><code>{report.run.run_id}</code><p>목표 revision {report.run.goal_revision}</p></details>}
  </aside>;
}
export function RunView({initial,onReport,onArtifactSelect}: {initial:Run;onReport?:(report:Report) => void;onArtifactSelect?:(id:string) => void}) {
  const state = useRun(initial);const {run,events,error,syncedAt} = state;
  const pendingInput = useRef<{key:string;requestId:string} | undefined>(undefined);
  const [actionError,setActionError] = useState('');const [stopping,setStopping] = useState(false);
  useEffect(() => {onReport?.({run,events,syncedAt});},[run,events,syncedAt,onReport]);
  const tools = useMemo(() => toolTree(events,run.observations || []),[events,run.observations]);
  const artifacts = useMemo(() => collectArtifacts(run.observations || [],events),[events,run.observations]);
  const final = events.findLast(e => e.type === 'run_finished');
  const status = final ? text(final.payload.status) : run.status;
  const lastInput = events.findLast(e => e.type === 'input_required' || e.type === 'input_received');
  const question:Question | undefined = run.status === 'waiting_user' ? run.question?.interrupt_id ? run.question : lastInput?.type === 'input_required' ? lastInput.payload as Question : run.question : undefined;
  const progress = events.findLast(e => e.type === 'progress');
  const verifications = (run.observations || []).filter(obs => ['verify_answer','completion_review'].includes(obs.tool_name));
  const answer = text(final?.payload.answer) || (status !== 'waiting_user' ? run.answer : '');
  async function stop() {setStopping(true);setActionError('');try {state.update(await post<Run>(`/runs/${encodeURIComponent(run.run_id)}/cancel`));state.reconnect();} catch(cause) {setActionError(String(cause));} finally {setStopping(false);}}
  return <article className="run-view"><div className="user-message"><span>나</span><p>{run.query}</p></div><div className="assistant-heading"><div className="assistant-mark">H</div><strong>Harness</strong><span className={`badge state-${status}`} data-testid="run-status" data-status={status}>{active(status) && <span className="pulse"/>}{statusLabel(status)}</span>{(active(status) || status === 'waiting_user') && <button className="stop-button" disabled={stopping || status === 'cancelling'} onClick={() => void stop()}><Square size={12}/>{stopping ? '중지 요청 중' : '중지'}</button>}</div>
    <div className="assistant-body">
      {events.filter(e => ['commentary','input_received','input_required'].includes(e.type)).map(event => <div className={event.type === 'input_received' ? 'input-reply' : 'commentary'} key={event.event_id}>{event.type === 'input_received' ? <><small>추가 답변</small><pre>{text(event.payload.value)}</pre></> : event.type === 'input_required' ? <p>{text(event.payload.message || event.payload.answer)}</p> : <Markdown>{text(event.payload.content)}</Markdown>}</div>)}
      <Timeline tools={tools} runStatus={status}/>
      {active(status) && <p className="progress" role="status"><span className="pulse"/>{text(progress?.payload.message) || '요청을 처리하고 있습니다…'}</p>}
      {error && <div className="error" role="alert">{error}<button onClick={state.reconnect}><RotateCw size={14}/>다시 연결</button></div>}
      {actionError && <p className="error" role="alert">{actionError}</p>}
      {question && <InputForm key={question.interrupt_id || 'question'} question={question} onSubmit={async value => {
        const key = JSON.stringify([question.interrupt_id,run.goal_revision,value]);
        if (pendingInput.current?.key !== key) pendingInput.current = {key,requestId:crypto.randomUUID()};
        try {
          const updated = await post<Run>(`/runs/${encodeURIComponent(run.run_id)}/input`,{kind:'input',request_id:pendingInput.current.requestId,goal_revision:run.goal_revision,interrupt_id:question.interrupt_id,value});
          state.update(updated);pendingInput.current = undefined;
        } finally { state.reconnect(); }
      }}/>}
      {answer && <section className="final-answer" data-testid="final-answer"><Markdown>{answer}</Markdown></section>}
      {verifications.length > 0 && <div className="verification"><CheckCheck size={16}/><div><strong>답변 검증</strong>{verifications.map(obs => <p key={obs.invocation_id}>{obs.summary || statusLabel(obs.status)}{obs.scope?.verdict ? ` (${text(obs.scope.verdict)})` : ''}</p>)}</div></div>}
      {(run.observations || []).flatMap(obs => (obs.tables || []).filter(table => table.columns.length > 0 || table.total_rows > 0 || table.missing_reason).map(table => <ResultTable key={`${obs.result_id}:${table.table_id}`} resultId={obs.result_id} sessionId={run.session_id} table={table}/>))}
      {artifacts.map(artifact => <Artifact key={artifact.artifact_id} artifact={artifact} sessionId={run.session_id} onSelect={() => onArtifactSelect?.(artifact.artifact_id)}/>)}
      {!active(status) && status !== 'waiting_user' && <footer className="completion">{statusLabel(status)}<span>stop_reason: {text(final?.payload.reason) || run.stop_reason || '—'}</span></footer>}
    </div></article>;
}

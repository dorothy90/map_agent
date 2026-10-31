import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUp, MessageSquare, Plus, PanelLeftClose, PanelLeftOpen, RefreshCw, Cpu, ChevronRight } from 'lucide-react';
import { active, api, post } from './core';
import { RunView, Usage, type Report } from './RunView';
import { Markdown } from './Markdown';
import { Artifact } from './Artifacts';
import { collectArtifacts } from './artifactUtils';
import type { Run, Session } from './types';
export default function App() {
  const [sessions,setSessions] = useState<Session[]>([]);const [sessionId,setSessionId] = useState(() => crypto.randomUUID() as string);
  const [runs,setRuns] = useState<Run[]>([]);const [legacy,setLegacy] = useState<{role:string;content:string}[]>([]);
  const [query,setQuery] = useState('');const [error,setError] = useState('');const [sessionError,setSessionError] = useState('');const [loading,setLoading] = useState(false);const [sending,setSending] = useState(false);
  const [sidebar,setSidebar] = useState(true);const [reports,setReports] = useState<Record<string,Report>>({});
  const [panel,setPanel] = useState<'usage' | 'canvas'>('usage');
  const [selection,setSelection] = useState<{id:string}>({id:''});
  const selected = selection.id;
  const canvasItems = useRef(new Map<string,HTMLDivElement>());
  const report = reports[runs.at(-1)?.run_id || ''];
  const artifacts = useMemo(() => {
    const observations = runs.flatMap(run => (reports[run.run_id]?.run || run).observations || []);
    const events = runs.flatMap(run => reports[run.run_id]?.events || []);
    return collectArtifacts(observations,events);
  },[runs,reports]);
  const selectArtifact = useCallback((id:string) => {setPanel('canvas');setSelection({id});},[]);
  useEffect(() => {
    if (panel !== 'canvas' || !selected) return;
    const item = canvasItems.current.get(selected);
    item?.scrollIntoView({block:'nearest'});item?.focus({preventScroll:true});
  },[panel,selection,artifacts]);
  const pendingStart = useRef<{sessionId:string;query:string;requestId:string} | undefined>(undefined);
  const generation = useRef(0);const sendLock = useRef(false);const input = useRef<HTMLTextAreaElement>(null);const tail = useRef<HTMLDivElement>(null);
  const refreshSessions = useCallback(async () => {try {const list=await api<Session[]>('/sessions');setSessions([...new Map(list.map(s => [s.session_id,s])).values()]);setSessionError('');} catch(cause) {setSessionError(String(cause));}},[]);
  useEffect(() => {void refreshSessions();},[refreshSessions]);
  const onReport = useCallback((next:Report) => {if (next.run.session_id === sessionId) setReports(previous => ({...previous,[next.run.run_id]:next}));},[sessionId]);
  const latestStatus = report?.run.status;
  useEffect(() => {if (latestStatus && !active(latestStatus)) void refreshSessions();},[latestStatus,refreshSessions]);
  const blocked = sending || loading || (!!latestStatus && (active(latestStatus) || latestStatus === 'waiting_user'));
  async function openSession(id:string) {
    const current = ++generation.current;setSessionId(id);setRuns([]);setLegacy([]);setReports({});setSelection({id:''});setError('');setLoading(true);
    try {
      const all:Run[]=[];let cursor:string | null = null;
      do {
        const params = new URLSearchParams({limit:'100'});if (cursor) params.set('cursor',cursor);
        const page: {runs:Run[];next_cursor:string | null} = await api(`/harness/sessions/${encodeURIComponent(id)}?${params}`);
        if (current !== generation.current) return;all.push(...page.runs);cursor=page.next_cursor;
      } while(cursor);
      if (!all.length) {const history=await api<{turns:{role:string;content:string}[]}>(`/session/${encodeURIComponent(id)}/history`);if (current === generation.current) setLegacy(history.turns);}
      if (current === generation.current) {
        const ordered = all.reverse();setRuns(ordered);
        const latest = ordered.at(-1);if (latest) setReports({[latest.run_id]:{run:latest,events:[],syncedAt:Date.now()}});
      }
    } catch(cause) {if (current === generation.current) setError(String(cause));} finally {if (current === generation.current) setLoading(false);}
  }
  function newChat() {generation.current++;setSessionId(crypto.randomUUID());setRuns([]);setLegacy([]);setReports({});setSelection({id:''});setQuery('');setError('');setLoading(false);input.current?.focus();}
  async function send() {
    if (!query.trim() || blocked || sendLock.current) return;
    sendLock.current=true;setSending(true);setError('');const current=generation.current;const submitted=query.trim();
    if (pendingStart.current?.sessionId !== sessionId || pendingStart.current?.query !== submitted) {
      pendingStart.current = {sessionId,query:submitted,requestId:crypto.randomUUID()};
    }
    const requestId = pendingStart.current.requestId;
    try {
      const run=await post<Run>('/runs',{session_id:sessionId,request_id:requestId,query:submitted});
      pendingStart.current = undefined;
      if (current === generation.current) {setRuns(previous => previous.some(item => item.run_id === run.run_id) ? previous : [...previous,run]);setReports(previous => ({...previous,[run.run_id]:{run,events:[],syncedAt:Date.now()}}));setQuery('');setTimeout(() => tail.current?.scrollIntoView({behavior:'smooth'}),100);}
      void refreshSessions();
    } catch(cause) {if (current === generation.current) setError(String(cause));} finally {sendLock.current=false;setSending(false);}
  }
  return <div className={`shell ${sidebar ? '' : 'sidebar-closed'}`}>
    <aside className="sidebar"><a className="brand" href="/" onClick={e => {e.preventDefault();newChat();}}><span className="brand-icon"><Cpu size={22}/></span><span>Harness<span className="brand-sub">Console</span></span></a><button className="new-chat" onClick={newChat}><Plus size={17}/>새 대화<span>New chat</span></button>
      <div className="sidebar-label"><span>최근 대화</span><button title="세션 새로고침" onClick={() => void refreshSessions()}><RefreshCw size={14}/></button></div>
      <nav aria-label="세션 목록">{sessions.map(session => <button key={session.session_id} data-session-id={session.session_id} className={`session ${sessionId === session.session_id ? 'selected' : ''}`} onClick={() => void openSession(session.session_id)}><MessageSquare size={15}/><span><strong>{session.last_query || '새 대화'}</strong><small>{new Date(session.updated_at).toLocaleDateString('ko-KR')} · {session.turn_count}회 실행</small></span></button>)}</nav>
      {!sessions.length && !sessionError && <p className="sidebar-empty">아직 대화가 없습니다.<br/>첫 질문을 시작해보세요.</p>}{sessionError && <p role="alert" className="sidebar-empty error">{sessionError}</p>}
      <div className="sidebar-footer"><span className="local-dot"/>로컬 워크스페이스<small>Yield Agent</small></div></aside>
    <main><header className="topbar"><button aria-label={sidebar ? '사이드바 닫기' : '사이드바 열기'} onClick={() => setSidebar(v => !v)}>{sidebar ? <PanelLeftClose size={18}/> : <PanelLeftOpen size={18}/>}</button><span>워크스페이스</span><ChevronRight size={13}/><strong>Harness Console</strong><span className="topbar-tag">수율 분석</span></header>
      <div className={`workspace ${panel === 'canvas' ? 'canvas-open' : ''}`}><div className="conversation">
        {runs.length === 0 && !loading && legacy.length === 0 && <section className="welcome"><div className="welcome-icon"><Cpu size={30}/></div><h1>데이터에서 답을 찾는<br/>하나의 대화.</h1><p>수율 조회부터 웨이퍼 맵 분석까지.<br/>도구의 실행 과정과 근거를 함께 확인하세요.</p><button className="suggestion" onClick={() => {setQuery('최근 일주일 동안 수율 낮은 거 알려줘');input.current?.focus();}}><MessageSquare size={18}/><span>최근 일주일 동안<br/><strong>수율 낮은 거 알려줘</strong></span><ArrowUp size={16}/></button></section>}
        {loading && <p className="loading" role="status">저장된 실행을 불러오는 중…</p>}
        {legacy.length > 0 && <><p className="notice">이전 버전 대화 기록입니다. 이 기록에는 harness 도구 이벤트가 없습니다.</p>{legacy.map((turn,i) => <div className={turn.role === 'user' ? 'user-message' : 'legacy-answer'} key={i}><Markdown>{turn.content}</Markdown></div>)}</>}
        {runs.map(run => <RunView key={`${sessionId}:${run.run_id}`} initial={run} onReport={onReport} onArtifactSelect={selectArtifact}/>)}<div ref={tail}/>
        {error && <div role="alert" className="error global-error">{error}{!runs.length && <button onClick={() => void openSession(sessionId)}>다시 불러오기</button>}</div>}
      </div><aside className="session-panel" aria-label="세션 패널">
        <div className="panel-tabs" aria-label="패널 선택">
          <button aria-pressed={panel === 'usage'} onClick={() => setPanel('usage')}>실행 정보</button>
          <button aria-pressed={panel === 'canvas'} onClick={() => setPanel('canvas')}>캔버스 ({artifacts.length})</button>
        </div>
        {panel === 'usage' ? <Usage report={report}/> : <section className="canvas-panel" aria-label="캔버스">
          {!artifacts.length && <p className="muted">아직 아티팩트가 없습니다.</p>}
          {artifacts.map(artifact => <div key={`${sessionId}:${artifact.artifact_id}`} tabIndex={-1} className={selected === artifact.artifact_id ? 'canvas-item selected' : 'canvas-item'} ref={node => {if (node) canvasItems.current.set(artifact.artifact_id,node);else canvasItems.current.delete(artifact.artifact_id);}}>
            <Artifact artifact={artifact} sessionId={sessionId} canvas/>
          </div>)}
        </section>}
      </aside></div>
      <div className="composer-wrap"><form className="composer" onSubmit={e => {e.preventDefault();void send();}}><label className="sr-only" htmlFor="chat-input">분석할 내용</label><textarea id="chat-input" ref={input} data-testid="chat-input" value={query} maxLength={20000} onChange={e => setQuery(e.target.value)} placeholder={latestStatus === 'waiting_user' ? '위의 추가 정보 입력에 답변해주세요' : '분석할 내용을 입력하세요…'} rows={2} onKeyDown={e => {if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {e.preventDefault();void send();}}}/><div className="composer-bottom"><span><Cpu size={14}/>Harness agent</span><button data-testid="send" className="send" type="submit" disabled={blocked || !query.trim()} aria-label="메시지 보내기"><ArrowUp size={19}/></button></div></form><p className="composer-hint">Enter로 전송 · Shift + Enter로 줄바꿈</p></div>
    </main></div>;
}

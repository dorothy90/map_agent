import { ArrowDown, Diamond, PanelLeft, Plus, Search } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '@nanostores/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { HashRouter, useLocation, useNavigate } from 'react-router'
import { routeSessionId, sessionRoute } from '@/app/routes'
import { I18nProvider } from '@/i18n'
import { ChatRuntimeBoundary } from '@/app/chat'
import { ChatBar } from '@/app/chat/composer'
import { PRIMARY_SESSION_VIEW, SessionViewProvider } from '@/app/chat/session-view'
import { ConfirmHost } from '@/components/confirm-host'
import { Thread } from '@/components/assistant-ui/thread'
import { $gatewayState, $sessions, $turnStartedAt, $currentUsage, $sessionsLoadError, $sessionsLoading, $selectedStoredSessionId, $messages, setConnection, setSessions } from '@/store/session'
import { reportPrimaryGatewayState } from '@/store/gateway'
import { WebHostAdapter, type WebHostOptions } from '../platform/web-host'
import { webConnectionResolver } from './connection'
import { withWebGatewayCompat } from './gateway-compat'
import { $clarifyRequests, clarifyRequestsForSession } from '@/store/clarify'
import type { SessionInfo } from '@/types/hermes'
import { useSessionLabels } from './use-session-labels'
import { displaySessionLabel, matchesSessionLabel, relativeSessionTime, sessionLabelKey } from './session-labels'
import { WebPresentationProvider } from './presentation'
import { requestComposerSetDraft } from '@/app/chat/composer/focus'
import { WebSessionRow } from './WebSessionRow'
import { useWebSession } from './use-web-session'
export type ConnectionResolver = NonNullable<WebHostOptions['getWebSocketConnection']>
export interface HermesWebOptions { resolver?:ConnectionResolver; fetcher?:typeof fetch; host?:WebHostAdapter }
const browserFetch:typeof fetch = (...args) => globalThis.fetch(...args)
export function HermesWebApp({resolver,fetcher=browserFetch,host:injected}:HermesWebOptions) {
 const [client]=useState(() => new QueryClient({defaultOptions:{queries:{retry:false,refetchOnWindowFocus:false}}}))
 const host=useMemo(() => resolver ? withWebGatewayCompat(injected ?? new WebHostAdapter({rpcMode:'authority',getWebSocketConnection:webConnectionResolver(resolver),fetch:fetcher,windowEvents:window})) : null,[resolver,injected,fetcher])
 useEffect(() => () => client.clear(),[client])
 if (!host) return <main className="hermes-web"><p role="alert">인증 연결 resolver가 지정되지 않았습니다. 연결하지 않았습니다.</p></main>
 if (host.rpcMode!=='authority') return <p role="alert">채팅에는 authority 연결이 필요합니다.</p>
 return <QueryClientProvider client={client}><I18nProvider configClient={null} initialLocale="ko"><HashRouter><WebChat host={host}/></HashRouter></I18nProvider></QueryClientProvider>
}
function WebChat({host}:{host:WebHostAdapter}) {
 const navigate=useNavigate()
 const actions=useWebSession(host), gatewayState=useStore($gatewayState), sessions=useStore($sessions)
 const visibleSessions=sessions.filter(session => !session.archived && !(session as SessionInfo & {deleted?:boolean}).deleted)
 const listError=useStore($sessionsLoadError), listLoading=useStore($sessionsLoading)
 const messages=useStore($messages)
 const currentStoredId=routeSessionId(useLocation().pathname) ?? actions.selectedStoredSessionId
 const [error,setError]=useState(''),[ready,setReady]=useState(false)
 const [query,setQuery]=useState(''),[sidebarOpen,setSidebarOpen]=useState(false)
 const [draftTitle,setDraftTitle]=useState<{text:string;sessionId:string|null}|null>(null)
 const shell=useRef<HTMLElement>(null), search=useRef<HTMLInputElement>(null)
 const labelSessions=useMemo(() => currentStoredId && !sessions.some(session => session.id===currentStoredId)
  ? [...sessions,{id:currentStoredId} as SessionInfo] : sessions,[sessions,currentStoredId])
 const {labels,failed,retry,remember}=useSessionLabels(labelSessions,ready,host)
 useStore($clarifyRequests)
 const pending=clarifyRequestsForSession(actions.activeSessionId).reduce((count,request) => count+request.questions.filter(q => !Object.hasOwn(request.lockedAnswers ?? {},q.qid)).length,0)
 const selected=labelSessions.find(session => session.id===currentStoredId)
 // A deep link can point outside the currently loaded list page.
 const outsideListId=currentStoredId && !sessions.some(session => session.id===currentStoredId) ? currentStoredId : null
 const [remoteTitle,setRemoteTitle]=useState<{host:WebHostAdapter;id:string;title?:string}|null>(null)
 useEffect(() => {
  if(!ready || listLoading || !outsideListId)return
  let disposed=false
  void host.gateway.request<{title?:string}>('session.title',{session_id:outsideListId})
   .then(result => {if(!disposed)setRemoteTitle({host,id:outsideListId,title:result.title})})
   .catch(() => {if(!disposed)setRemoteTitle({host,id:outsideListId})})
  return () => {disposed=true}
 },[host,ready,listLoading,outsideListId])
 const remote=remoteTitle?.host===host && remoteTitle.id===outsideListId ? remoteTitle : null
 const question=selected && Object.hasOwn(labels,sessionLabelKey(selected)) ? labels[sessionLabelKey(selected)] : draftTitle?.sessionId===currentStoredId ? draftTitle.text : undefined
 const title=outsideListId && !remote ? '대화 불러오는 중…' : selected ? displaySessionLabel(outsideListId ? {title:remote?.title} : selected,question) : draftTitle?.text.trim() || '새 채팅'
 const [now,setNow]=useState(Date.now())
 useEffect(() => {const timer=setInterval(() => setNow(Date.now()),30000);return () => clearInterval(timer)},[])

 useEffect(() => {
  const onKey=(event:KeyboardEvent) => {if((event.metaKey || event.ctrlKey) && event.key==='k'){event.preventDefault();setSidebarOpen(true);requestAnimationFrame(() => search.current?.focus())}}
  window.addEventListener('keydown',onKey);return () => window.removeEventListener('keydown',onKey)
 },[])
 const jumpToQuestion=() => {
  const card=shell.current?.querySelector<HTMLElement>('[data-clarify-stepper], form:has([data-slot="clarify-inline"])')
  if(card){card.scrollIntoView({block:'center',behavior:'smooth'});card.querySelector<HTMLElement>('button:not(:disabled),textarea:not(:disabled)')?.focus({preventScroll:true})}
 }

 useEffect(() => {
   let disposed=false
   const releaseApi=host.installApiBridge(),releaseDesktop=host.installDesktopBridge()
   void (async () => {try {
     setConnection(await host.getConnection())
     await host.connect()
     if(disposed)return
     reportPrimaryGatewayState('open');setReady(true)
     await actions.refreshSessions()
   } catch {if(!disposed)setError('연결에 실패했습니다. 인증 resolver와 게이트웨이를 확인하세요.')}})()
   return () => {disposed=true;host.close();releaseDesktop();releaseApi();reportPrimaryGatewayState('closed')}
 },[host,actions.refreshSessions])
 return <main className="hermes-web" ref={shell} data-sidebar-open={sidebarOpen}>
  <nav aria-label="세션 목록">
   <div className="web-brand"><span aria-hidden="true"><Diamond size={19}/></span><div><h1>결</h1><p>수율 분석 앱</p></div></div>
   <button className="web-new-chat" disabled={!ready} onClick={() => {setDraftTitle(null);setSidebarOpen(false);actions.startFreshSessionDraft()}}><Plus size={17} aria-hidden="true"/> 새 채팅</button>
   <label className="web-search"><Search size={16} aria-hidden="true"/><input ref={search} aria-label="불러온 대화 검색" placeholder="대화 검색…" value={query} onChange={event => setQuery(event.target.value)}/><kbd>⌘ K</kbd></label>
   <div className="web-session-list"><p className="web-list-heading">최근 대화</p>
    {listError && <p role="alert">세션 목록을 불러오지 못했습니다. <button onClick={() => void actions.refreshSessions()}>다시 시도</button></p>}
    {failed && <button onClick={retry}>대화 제목 다시 불러오기</button>}
    {visibleSessions.filter(session => matchesSessionLabel(displaySessionLabel(session,labels[sessionLabelKey(session)]),query)).map(session => {
     const label=displaySessionLabel(session,labels[sessionLabelKey(session)])
     return <WebSessionRow key={sessionLabelKey(session)} label={label} time={relativeSessionTime(session,now)} selected={session.id===actions.selectedStoredSessionId} disabled={!ready}
      onSelect={() => {setDraftTitle(null);setSidebarOpen(false);navigate(sessionRoute(session.id))}}
      onArchive={async () => {
       await host.gateway.request('session.archive',{session_id:session.id})
       setSessions(rows => rows.filter(row => row.id!==session.id))
       if(currentStoredId===session.id){setDraftTitle(null);setSidebarOpen(false);actions.startFreshSessionDraft()}
      }}
      onDelete={async () => {
       await host.gateway.request('session.delete',{session_id:session.id})
       setSessions(rows => rows.filter(row => row.id!==session.id))
       if(currentStoredId===session.id){setDraftTitle(null);setSidebarOpen(false);actions.startFreshSessionDraft()}
      }}
      onRename={async title => {
       const result=await host.gateway.request<{title:string;pending:boolean}>('session.title',{session_id:session.id,title})
       if(!result.title?.trim() || result.pending)throw new Error('Title was not saved')
       setSessions(rows => rows.map(row => sessionLabelKey(row)===sessionLabelKey(session) ? {...row,title:result.title} : row))
      }}/>
    })}
    {query && !visibleSessions.some(session => matchesSessionLabel(displaySessionLabel(session,labels[sessionLabelKey(session)]),query)) && <p className="web-list-heading">불러온 대화에서 일치하는 대화가 없습니다.</p>}
    <button className="web-load-more" disabled={!ready} onClick={() => void actions.loadMoreSessions()}>이전 대화 더 보기</button>
   </div>
   <footer className="web-connection"><p><span aria-hidden="true">●</span> {gatewayState==='open' ? '연결됨' : gatewayState==='connecting' ? '연결 중' : '연결 끊김'}</p><p>대화를 선택하면 이전 내용을 이어서 볼 수 있습니다.</p></footer>
  </nav>
  <div className="hermes-web-chat">
   <header className="web-header"><button className="web-sidebar-toggle" aria-label="대화 목록 열기/닫기" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(value => !value)}><PanelLeft size={18}/></button><h2 title={title}>{title}</h2>{pending>0 && <button className="web-pending" onClick={jumpToQuestion}>답변할 질문 {pending}개 <ArrowDown size={16} aria-hidden="true"/></button>}</header>
   {!ready && <p className="web-connection-notice" role={error ? 'alert' : 'status'}>{error || '게이트웨이 연결 중…'}</p>}
   <ConfirmHost/>
   <WebPresentationProvider>
   <SessionViewProvider value={PRIMARY_SESSION_VIEW}><ChatRuntimeBoundary busy={actions.busy} suppressMessages={false} onCancel={actions.cancelRun} onEdit={actions.editMessage} onReload={actions.reloadFromMessage} onThreadMessagesChange={actions.handleThreadMessagesChange}>
    <div className="hermes-web-thread">{!currentStoredId && messages.length===0 && <div className="web-empty-state"><span className="web-empty-mark" aria-hidden="true">결</span><h2>수율 데이터에 대해 물어보세요</h2><div>{[
      '4SS 최근 4주 주차별 수율 리포트 작성해줘',
      '4SS 최근 4주 저수율 자재랑 나머지 자재 2그룹으로 나눠서 리포트 작성해줘',
      '4SS 최근 3개월 월별/주별 합쳐서 리포트 작성해줘',
    ].map(question => <button type="button" key={question} onClick={() => {void requestComposerSetDraft([],question,{active:true});shell.current?.querySelector<HTMLElement>('[contenteditable="true"]')?.focus()}}>{question}</button>)}</div></div>}<Thread gateway={host.gateway} sessionId={actions.activeSessionId} sessionKey={actions.selectedStoredSessionId} onCancel={actions.cancelRun}/></div>
    <div className="hermes-web-composer"><div className="web-runline"><span>{pending>0 ? '● 추가 비교를 위해 답변을 기다리고 있어요.' : actions.busy ? '● 응답을 기다리고 있어요.' : ''}</span><UsageClock/>{actions.busy && <button className="web-extra-stop" onClick={actions.cancelRun}>중지</button>}</div><ChatBar gateway={host.gateway} busy={actions.busy} disabled={!ready || gatewayState!=='open'} sessionId={actions.activeSessionId} queueSessionKey={actions.selectedStoredSessionId} onCancel={actions.cancelRun} onSubmit={async (text, ...rest) => {
       const fresh=!actions.selectedStoredSessionId
       if(fresh)setDraftTitle({text,sessionId:null})
       const sent=await actions.submitText(text,...rest)
       if(fresh && sent){
        const id=$selectedStoredSessionId.get()
        const firstUser=$messages.get().find(message => message.role==='user')
        const firstText=firstUser?.parts.filter(part => part.type==='text').map(part => part.text).join('')
        if(id && firstText===text.trim()){remember($sessions.get().find(session => session.id===id) ?? {id} as SessionInfo,text);setDraftTitle({text,sessionId:id})}
       } else if(fresh)setDraftTitle(null)
       return sent
      }} onSteer={actions.steerPrompt}
      state={{model:{model:'',provider:'',canSwitch:false},tools:{enabled:false,label:''},voice:{enabled:false,active:false}}}/><p className="web-composer-help">분석 결과는 원본 데이터와 함께 확인하세요.</p></div>
   </ChatRuntimeBoundary></SessionViewProvider></WebPresentationProvider>
  </div>
 </main>
}
function UsageClock() {
 const usage=useStore($currentUsage),started=useStore($turnStartedAt),[now,setNow]=useState(Date.now())
 useEffect(() => {if(!started)return;const id=setInterval(() => setNow(Date.now()),1000);return () => clearInterval(id)},[started])
 const hasUsage=(usage.input ?? 0)>0 || (usage.output ?? 0)>0
 if(!hasUsage && !started)return null
 const parts=hasUsage ? [usage.input != null ? `입력 ${usage.input}` : null,usage.output != null ? `출력 ${usage.output} 토큰` : null].filter(Boolean) : []
 if(started)parts.push(`${Math.max(0,Math.floor((now-started)/1000))}s`)
 return <small aria-label="사용량과 실행 시간">{parts.join(' · ')}</small>
}

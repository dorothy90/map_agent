import { useCallback, useEffect, useMemo, useState } from 'react'
import { computed } from 'nanostores'
import { useStore } from '@nanostores/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { HashRouter, useNavigate } from 'react-router'
import { sessionRoute } from '@/app/routes'
import { I18nProvider } from '@/i18n'
import { ChatRuntimeBoundary } from '@/app/chat'
import { ChatBar } from '@/app/chat/composer'
import { PRIMARY_SESSION_VIEW, SessionViewProvider } from '@/app/chat/session-view'
import { ConfirmHost } from '@/components/confirm-host'
import { Thread } from '@/components/assistant-ui/thread'
import { $gatewayState, $sessions, $turnStartedAt, $currentUsage, $sessionsLoadError, setConnection } from '@/store/session'
import { reportPrimaryGatewayState } from '@/store/gateway'
import { WebHostAdapter, type WebHostOptions } from '../platform/web-host'
import { webConnectionResolver } from './connection'
import { withWebGatewayCompat } from './gateway-compat'
import { useWebSession } from './use-web-session'
import { useYieldResults } from '../yield/use-yield-results'
import { YieldResults } from '../yield/YieldResults'
import { omitDuplicateTables, type ComparableTable } from '../yield/table-dedupe'
export type ConnectionResolver = NonNullable<WebHostOptions['getWebSocketConnection']>
export interface HermesWebOptions { resolver?:ConnectionResolver; fetcher?:typeof fetch; host?:WebHostAdapter }
const browserFetch:typeof fetch = (...args) => globalThis.fetch(...args)
export function HermesWebApp({resolver,fetcher=browserFetch,host:injected}:HermesWebOptions) {
 const [client]=useState(() => new QueryClient({defaultOptions:{queries:{retry:false,refetchOnWindowFocus:false}}}))
 const host=useMemo(() => resolver ? withWebGatewayCompat(injected ?? new WebHostAdapter({rpcMode:'authority',getWebSocketConnection:webConnectionResolver(resolver),fetch:fetcher,windowEvents:window})) : null,[resolver,injected,fetcher])
 useEffect(() => () => client.clear(),[client])
 if (!host) return <main className="hermes-web"><p role="alert">인증 연결 resolver가 지정되지 않았습니다. 연결하지 않았습니다.</p></main>
 if (host.rpcMode!=='authority') return <p role="alert">채팅에는 authority 연결이 필요합니다.</p>
 return <QueryClientProvider client={client}><I18nProvider configClient={null} initialLocale="ko"><HashRouter><WebChat host={host} fetcher={fetcher}/></HashRouter></I18nProvider></QueryClientProvider>
}
function WebChat({host,fetcher}:{host:WebHostAdapter;fetcher:typeof fetch}) {
 const navigate=useNavigate()
 const actions=useWebSession(host), gatewayState=useStore($gatewayState), sessions=useStore($sessions)
 const listError=useStore($sessionsLoadError)
 const [error,setError]=useState(''),[ready,setReady]=useState(false)
 const [artifactTables,setArtifactTables]=useState<Record<string,ComparableTable[]>>({})
 const onArtifactTables=useCallback((id:string,tables:ComparableTable[]) => setArtifactTables(current => ({...current,[id]:tables})),[])
 useEffect(() => setArtifactTables({}),[actions.selectedStoredSessionId])
 const results=useYieldResults(ready ? actions.selectedStoredSessionId : null,actions.busy,fetcher)
 useEffect(() => {
   if(ready && actions.selectedStoredSessionId && !actions.busy) void results.refetch()
 },[ready,actions.selectedStoredSessionId,actions.busy,results.refetch])
 const view=useMemo(() => ({...PRIMARY_SESSION_VIEW,$messages:computed(PRIMARY_SESSION_VIEW.$messages,messages => messages.map(message => message.role==='assistant' ? {...message,parts:message.parts.map(part => part.type==='text' ? {...part,text:omitDuplicateTables(part.text,[...(results.data?.tables.map(t => t.table) ?? []),...Object.values(artifactTables).flat()])} : part)} : message))}),[results.data,artifactTables])
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
 return <main className="hermes-web">
  {/* Upstream startFreshSessionDraft navigates to NEW_CHAT_ROUTE and clears the draft together. */}
  <nav aria-label="세션 목록"><h1>Yield 채팅</h1><button disabled={!ready} onClick={() => actions.startFreshSessionDraft()}>새 채팅</button>
   {listError && <p role="alert">세션 목록을 불러오지 못했습니다. <button onClick={() => void actions.refreshSessions()}>다시 시도</button></p>}
   {sessions.map(session => <button key={session.id} aria-current={session.id===actions.selectedStoredSessionId ? 'page' : undefined} onClick={() => navigate(sessionRoute(session.id))}>{session.title || session.id}</button>)}
   <button disabled={!ready} onClick={() => void actions.loadMoreSessions()}>더 보기</button>
  </nav>
  <div className="hermes-web-chat">
   {!ready && <p role={error ? 'alert' : 'status'}>{error || '게이트웨이 연결 중…'}</p>}
   <UsageClock/><ConfirmHost/>
   <SessionViewProvider value={view}><ChatRuntimeBoundary busy={actions.busy} suppressMessages={false} onCancel={actions.cancelRun} onEdit={actions.editMessage} onReload={actions.reloadFromMessage} onThreadMessagesChange={actions.handleThreadMessagesChange}>
    <div className="hermes-web-thread"><Thread gateway={host.gateway} sessionId={actions.activeSessionId} sessionKey={actions.selectedStoredSessionId} onCancel={actions.cancelRun}/></div>
    {results.error && <p role="alert">{results.error.message}</p>}
    {results.data && <YieldResults {...results.data} fetcher={fetcher} onArtifactTables={onArtifactTables}/>}
    <div className="hermes-web-composer"><ChatBar gateway={host.gateway} busy={actions.busy} disabled={!ready || gatewayState!=='open'} sessionId={actions.activeSessionId} queueSessionKey={actions.selectedStoredSessionId} onCancel={actions.cancelRun} onSubmit={actions.submitText} onSteer={actions.steerPrompt}
      state={{model:{model:'',provider:'',canSwitch:false},tools:{enabled:false,label:''},voice:{enabled:false,active:false}}}/></div>
   </ChatRuntimeBoundary></SessionViewProvider>
  </div>
 </main>
}
function UsageClock() {
 const usage=useStore($currentUsage),started=useStore($turnStartedAt),[now,setNow]=useState(Date.now())
 useEffect(() => {if(!started)return;const id=setInterval(() => setNow(Date.now()),1000);return () => clearInterval(id)},[started])
 return <small aria-label="사용량과 실행 시간">{`입력 ${usage.input ?? 0} · 출력 ${usage.output ?? 0} 토큰`}{started && ` · ${Math.max(0,Math.floor((now-started)/1000))}초`}</small>
}

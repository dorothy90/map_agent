import { ArrowDown, Folder, Layers, PanelLeft, Plug, SquarePen, Search } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '@nanostores/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { HashRouter, useLocation, useNavigate } from 'react-router'
import { routeSessionId, sessionRoute } from '@/app/routes'
import { isSlashCommandText } from '@/lib/chat-runtime'
import { I18nProvider } from '@/i18n'
import { ChatRuntimeBoundary } from '@/app/chat'
import { ChatBar } from '@/app/chat/composer'
import { PRIMARY_SESSION_VIEW, SessionViewProvider } from '@/app/chat/session-view'
import { ConfirmHost } from '@/components/confirm-host'
import { Thread } from '@/components/assistant-ui/thread'
import { $gatewayState, $sessions, $sessionsLoadError, $sessionsLoading, $selectedStoredSessionId, $messages, setConnection, setSessions } from '@/store/session'
import { $freshDraftKey, freshDraftScope, stashSessionDraft } from '@/store/composer'
import { reportPrimaryGatewayState } from '@/store/gateway'
import { WebHostAdapter, type WebHostOptions } from '../platform/web-host'
import { webConnectionResolver } from './connection'
import { withWebGatewayCompat } from './gateway-compat'
import { $clarifyRequests, clarifyRequestsForSession } from '@/store/clarify'
import type { SessionInfo } from '@/types/hermes'
import { useSessionLabels } from './use-session-labels'
import { displaySessionLabel, matchesSessionLabel, sessionDayGroup, sessionLabelKey } from './session-labels'
import { WebPresentationProvider } from './presentation'
import { useMediaQuery } from '@/hooks/use-media-query'
import './sidebar.css'
import { SkillsCatalog } from './SkillsCatalog'
import { ProductPicker } from './ProductPicker'
import { McpServersPage, McpComposerButton, useMcpServers } from './McpServers'
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
 return <QueryClientProvider client={client}><I18nProvider configClient={null} initialLocale="ko"><HashRouter><WebChat host={host} fetcher={fetcher}/></HashRouter></I18nProvider></QueryClientProvider>
}
function WebChat({host,fetcher}:{host:WebHostAdapter;fetcher:typeof fetch}) {
 const navigate=useNavigate()
 const location=useLocation()
 const showingSkills=new URLSearchParams(location.search).get('view')==='skills'
 const showingMcp=new URLSearchParams(location.search).get('view')==='mcp'
 const showingSettings=showingSkills || showingMcp
 const mcpServers=useMcpServers(fetcher).data ?? []
 const actions=useWebSession(host,showingSettings), gatewayState=useStore($gatewayState), sessions=useStore($sessions)
 const visibleSessions=sessions.filter(session => !session.archived && !(session as SessionInfo & {deleted?:boolean}).deleted)
 const listError=useStore($sessionsLoadError), listLoading=useStore($sessionsLoading)
 const freshDraftKey=useStore($freshDraftKey)
 const messages=useStore($messages)
 const currentStoredId=routeSessionId(location.pathname) ?? actions.selectedStoredSessionId
 const isNewChat=!currentStoredId && messages.length===0 && !actions.busy
 const [product,setProduct]=useState('')
 const [error,setError]=useState(''),[ready,setReady]=useState(false)
 const mobile=useMediaQuery('(max-width: 650px)')
 const [desktopExpanded,setDesktopExpanded]=useState(true),[drawerOpen,setDrawerOpen]=useState(false)
 const sidebarOpen=mobile ? drawerOpen : desktopExpanded
 const setSidebarOpen=mobile ? setDrawerOpen : setDesktopExpanded
 const [query,setQuery]=useState<string|null>(null)
 const searchToggle=useRef<HTMLButtonElement>(null)
 const sidebarToggle=useRef<HTMLButtonElement>(null), sidebarClose=useRef<HTMLButtonElement>(null)
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
  let disposed=false
  const unsubscribe=host.gateway.on('session.title',({payload}) => {
   // The stock handler patches loaded rows. Discard a list read that began before the push.
   if($sessionsLoading.get())void actions.refreshSessions()
   if(outsideListId && payload?.session_id===outsideListId && payload.title?.trim()){
    disposed=true
    setRemoteTitle({host,id:outsideListId,title:payload.title})
   }
  })
  if(ready && !listLoading && outsideListId)void host.gateway.request<{title?:string}>('session.title',{session_id:outsideListId})
   .then(result => {if(!disposed)setRemoteTitle({host,id:outsideListId,title:result.title})})
   .catch(() => {if(!disposed)setRemoteTitle({host,id:outsideListId})})
  return () => {disposed=true;unsubscribe()}
 },[host,ready,listLoading,outsideListId,actions.refreshSessions])
 const remote=remoteTitle?.host===host && remoteTitle.id===outsideListId ? remoteTitle : null
 const question=selected && Object.hasOwn(labels,sessionLabelKey(selected)) ? labels[sessionLabelKey(selected)] : draftTitle?.sessionId===currentStoredId ? draftTitle.text : undefined
 const title=outsideListId && !remote ? '대화 불러오는 중…' : selected ? displaySessionLabel(outsideListId ? {title:remote?.title} : selected,question) : draftTitle?.text.trim() || '새 대화'
 const [now,setNow]=useState(Date.now())
 useEffect(() => {const timer=setInterval(() => setNow(Date.now()),30000);return () => clearInterval(timer)},[])

 const groups=new Map<string,{title:string;project:boolean;rows:{session:SessionInfo;label:string}[]}>()
 for(const session of visibleSessions){
  const label=displaySessionLabel(session,labels[sessionLabelKey(session)])
  if(!matchesSessionLabel(label,query ?? ''))continue
  const project=(session as SessionInfo & {web_project?:{id:string;name:string}}).web_project
  const title=project?.name ?? sessionDayGroup(session,now)
  const key=project ? 'project:'+project.id : title
  const group=groups.get(key) ?? {title,project:Boolean(project),rows:[]}
  group.rows.push({session,label})
  groups.set(key,group)
 }
 useEffect(() => {
  document.title=(showingMcp ? 'MCP 서버' : showingSkills ? '스킬' : title)+' · 수율 분석'
 },[showingMcp,showingSkills,title])
 useEffect(() => {
  const onKey=(event:KeyboardEvent) => {
   if((event.metaKey || event.ctrlKey) && event.key.toLowerCase()==='k'){
    event.preventDefault()
    setSidebarOpen(true)
    setQuery(value => value ?? '')
    search.current?.focus()
   }
  }
  window.addEventListener('keydown',onKey)
  return () => window.removeEventListener('keydown',onKey)
 },[setSidebarOpen])
 const closeSidebar=() => {
  setSidebarOpen(false)
  requestAnimationFrame(() => sidebarToggle.current?.focus())
 }
 const startNewChat=() => {
  setProduct('')
  setDraftTitle(null)
  setDrawerOpen(false)
  actions.startFreshSessionDraft()
  requestAnimationFrame(() => shell.current?.querySelector<HTMLElement>('[contenteditable="true"]')?.focus())
 }
 const createSkill=() => {
  startNewChat()
  stashSessionDraft(freshDraftScope(),'다음 작업을 스킬로 만들어줘. ',[])
 }
 const openSkills=() => {
  setDrawerOpen(false)
  const params=new URLSearchParams(location.search)
  params.set('view','skills')
  navigate({pathname:location.pathname,search:params.toString()})
 }
 const openMcp=() => {
  setDrawerOpen(false)
  const params=new URLSearchParams(location.search)
  params.set('view','mcp')
  navigate({pathname:location.pathname,search:params.toString()})
 }
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
 return <main className="hermes-web" ref={shell}>
  {mobile && drawerOpen && <button type="button" className="web-sidebar-backdrop" tabIndex={-1} aria-label="대화 목록 닫기" onClick={closeSidebar}/>}
  <nav className="web-sidebar" id="web-sidebar" aria-label="대화 목록" hidden={!sidebarOpen}
   onKeyDown={event => {
    if(mobile && event.key==='Escape' && !event.defaultPrevented){event.preventDefault();closeSidebar()}
   }}>
   <div className="web-brand"><h1>수율 분석</h1><button ref={sidebarClose} className="web-sidebar-toggle" aria-label="사이드바 접기" aria-controls="web-sidebar" aria-expanded={sidebarOpen} onClick={closeSidebar}><PanelLeft size={19} aria-hidden="true"/></button></div>
   <button className="web-sidebar-action" disabled={!ready} onClick={startNewChat}><SquarePen size={19} aria-hidden="true"/>새 대화</button>
   <button className="web-sidebar-action" ref={searchToggle} aria-expanded={query!==null} aria-controls="web-search" onClick={() => setQuery(value => value===null ? '' : null)}><Search size={19} aria-hidden="true"/>대화 검색</button>
   {query!==null && sidebarOpen && <input autoFocus id="web-search" className="web-search" ref={search} aria-label="불러온 대화 검색" placeholder="대화 제목 검색" value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => {
    if(event.key==='Escape'){event.preventDefault();setQuery(null);searchToggle.current?.focus()}
   }}/>}
   <button className="web-sidebar-action" aria-current={showingSkills ? 'page' : undefined} onClick={openSkills}><Layers size={19} aria-hidden="true"/>스킬</button>
   <button className="web-sidebar-action" aria-current={showingMcp ? 'page' : undefined} onClick={openMcp}><Plug size={19} aria-hidden="true"/>MCP 서버<span className="mcp-sidebar-count">{mcpServers.filter(server => server.enabled).length}/{mcpServers.length}</span></button>
   <div className="web-session-list">
    {listError && <p role="alert">세션 목록을 불러오지 못했습니다. <button onClick={() => void actions.refreshSessions()}>다시 시도</button></p>}
    {failed && <button onClick={retry}>대화 제목 다시 불러오기</button>}
    {Array.from(groups,([id,{title,project,rows}]) => <section key={id} aria-label={project ? '프로젝트 '+title : title}>
     <h2 className="web-list-heading">{project && <Folder size={13} aria-hidden="true"/>}{title}</h2>
     {rows.map(({session,label}) => <WebSessionRow key={sessionLabelKey(session)} label={label} selected={!showingSettings && session.id===actions.selectedStoredSessionId} disabled={!ready}
      onSelect={() => {setDraftTitle(null);setDrawerOpen(false);navigate(sessionRoute(session.id))}}
      onArchive={async () => {
       await host.gateway.request('session.archive',{session_id:session.id})
       setSessions(rows => rows.filter(row => row.id!==session.id))
       if(currentStoredId===session.id)startNewChat()
      }}
      onDelete={async () => {
       await host.gateway.request('session.delete',{session_id:session.id})
       setSessions(rows => rows.filter(row => row.id!==session.id))
       if(currentStoredId===session.id)startNewChat()
      }}
      onRename={async title => {
       const result=await host.gateway.request<{title:string;pending:boolean}>('session.title',{session_id:session.id,title})
       if(!result.title?.trim() || result.pending)throw new Error('Title was not saved')
       setSessions(rows => rows.map(row => sessionLabelKey(row)===sessionLabelKey(session) ? {...row,title:result.title} : row))
       if($sessionsLoading.get())void actions.refreshSessions()
      }}/>)}</section>)}
    {query && !groups.size && <p className="web-list-heading">불러온 대화에서 일치하는 대화가 없습니다.</p>}
    {actions.hasMoreSessions && <button className="web-load-more" disabled={!ready || listLoading} onClick={() => void actions.loadMoreSessions()}>이전 대화 더 보기</button>}
   </div>
  </nav>
  <div className="hermes-web-chat" data-new-chat={(!showingSettings && isNewChat) || undefined} inert={mobile && drawerOpen}>
   <header className="web-header">{!sidebarOpen && <button ref={sidebarToggle} className="web-sidebar-toggle" aria-label="사이드바 열기" aria-controls="web-sidebar" aria-expanded={false} onClick={() => {setSidebarOpen(true);requestAnimationFrame(() => sidebarClose.current?.focus())}}><PanelLeft size={19} aria-hidden="true"/></button>}<h2 title={showingMcp ? 'MCP 서버' : showingSkills ? '스킬' : title}>{showingMcp ? 'MCP 서버' : showingSkills ? '스킬' : title}</h2>{!showingSettings && pending>0 && <button className="web-pending" onClick={jumpToQuestion}>답변할 질문 {pending}개 <ArrowDown size={16} aria-hidden="true"/></button>}</header>
   {!ready && <p className="web-connection-notice" role={error ? 'alert' : 'status'}>{error || '게이트웨이 연결 중…'}</p>}
   {ready && gatewayState!=='open' && <p className="web-connection-notice" role="status">{gatewayState==='connecting' ? '게이트웨이 연결 중…' : '게이트웨이 연결이 끊겼습니다.'}</p>}
   <ConfirmHost/>
   {showingSkills && <SkillsCatalog fetcher={fetcher} onCreate={createSkill}/>}
   {showingMcp && <McpServersPage fetcher={fetcher} container={shell}/>}
   <WebPresentationProvider>
   <SessionViewProvider value={PRIMARY_SESSION_VIEW}><ChatRuntimeBoundary busy={actions.busy} suppressMessages={false} onCancel={actions.cancelRun} onEdit={actions.editMessage} onReload={actions.reloadFromMessage} onThreadMessagesChange={actions.handleThreadMessagesChange}>
    <div className="web-chat-stage" hidden={showingSettings}>
    {isNewChat && <section className="web-empty-state" aria-labelledby="web-welcome">
     <h2 id="web-welcome">어떤 분석을 시작할까요?</h2>
    </section>}
    <div className="hermes-web-thread" hidden={isNewChat}><Thread gateway={host.gateway} sessionId={actions.activeSessionId} sessionKey={actions.selectedStoredSessionId} onCancel={actions.cancelRun}/></div>
    <div className="hermes-web-composer">{(pending>0 || actions.busy) && <div className="web-runline">{pending>0 && <span>● 추가 비교를 위해 답변을 기다리고 있어요.</span>}{actions.busy && <button className="web-extra-stop" onClick={actions.cancelRun}>중지</button>}</div>}<ChatBar freshDraftKey={freshDraftKey} gateway={host.gateway} busy={actions.busy} disabled={!ready || gatewayState!=='open'} sessionId={actions.activeSessionId} queueSessionKey={actions.selectedStoredSessionId} onCancel={actions.cancelRun}
      contextControl={<>{isNewChat && <ProductPicker value={product} onChange={setProduct} fetcher={fetcher} container={shell}/>}<McpComposerButton fetcher={fetcher} container={shell} onManage={openMcp}/></>}
      onSubmit={async (draft, ...rest) => {
       const text=isNewChat && product && !isSlashCommandText(draft) ? `${product} 제품 기준으로, ${draft}` : draft
       const fresh=!actions.selectedStoredSessionId
       if(fresh)setDraftTitle({text,sessionId:null})
       const sent=await actions.submitText(text,...rest)
       if(fresh && sent){
        setProduct('')
        const id=$selectedStoredSessionId.get()
        const firstUser=$messages.get().find(message => message.role==='user')
        const firstText=firstUser?.parts.filter(part => part.type==='text').map(part => part.text).join('')
        if(id && firstText===text.trim()){remember($sessions.get().find(session => session.id===id) ?? {id} as SessionInfo,text);setDraftTitle({text,sessionId:id})}
       } else if(fresh)setDraftTitle(null)
       return sent
      }} onSteer={actions.steerPrompt}
      state={{model:{model:'',provider:'',canSwitch:false},tools:{enabled:false,label:''},voice:{enabled:false,active:false}}}/></div>
    </div>
   </ChatRuntimeBoundary></SessionViewProvider></WebPresentationProvider>
  </div>
 </main>
}

import { ArrowDown, Folder, Plug } from 'lucide-react'
import { animate, AnimatePresence, motion } from 'motion/react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useStore } from '@nanostores/react'
import { QueryClient, QueryClientProvider, useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { HashRouter, useLocation, useNavigate } from 'react-router'
import { routeSessionId, sessionRoute } from '@/app/routes'
import { isSlashCommandText } from '@/lib/chat-runtime'
import { listSessions } from '@/api/sessions'
import { I18nProvider, registerAppLocale } from '@/i18n'
import { ChatRuntimeBoundary } from '@/app/chat'
import { ChatBar } from '@/app/chat/composer'
import { PRIMARY_SESSION_VIEW, SessionViewProvider } from '@/app/chat/session-view'
import { ConfirmHost } from '@/components/confirm-host'
import { Thread } from '@/components/assistant-ui/thread'
import { requestScrollToBottom } from '@/store/thread-scroll'
import { $activeSessionId, $gatewayState, $sessions, $sessionsLoadError, $sessionsLoading, $selectedStoredSessionId, $messages, setConnection, setSessions } from '@/store/session'
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
import { YuriMascot } from './YuriMascot'
import { useReplyArrival } from './reply-arrival'
import { useMediaQuery } from '@/hooks/use-media-query'
import './sidebar.css'
import './yuri.css'
import { animateYuriCollapse, captureYuriFrame } from './yuri-collapse'
import { YuriWindowResize, type YuriDesktopBridge } from './YuriWindowResize'
import { SkillsCatalog } from './SkillsCatalog'
import { ProductPicker } from './ProductPicker'
import { McpServersPage, McpComposerButton, useMcpServers } from './McpServers'
import { WebSessionRow } from './WebSessionRow'
import { useWebSession } from './use-web-session'
export type ConnectionResolver = NonNullable<WebHostOptions['getWebSocketConnection']>
export interface HermesWebOptions { resolver?:ConnectionResolver; fetcher?:typeof fetch; host?:WebHostAdapter }
registerAppLocale('ko', {
  endonym: '한국어',
  englishName: 'Korean',
  translations: {
    composer: {
      busyInput: {
        label: '실행 중 입력 방식',
        steer: '진행 방향 조정',
        queue: '대기열에 추가',
        interrupt: '중지 후 보내기',
        saveFailed: '실행 중 입력 방식을 저장하지 못했습니다.'
      },
      queued: count => `대기 ${count}건`,
      queuedPaused: count => `대기 ${count}건 · 일시정지`
    }
  }
})

const browserFetch:typeof fetch = (...args) => globalThis.fetch(...args)
const archivedSessionsKey = ['y-agent', 'archived-sessions'] as const
const archivePageSize = 40
registerAppLocale('ko', {
 endonym: '한국어',
 englishName: 'Korean',
 translations: {
  common: { cancel: '취소', close: '닫기', loading: '처리 중…', done: '완료', copy: '복사', copied: '복사됨', copyFailed: '복사 실패' },
  assistant: {
   thread: {
    refresh: '답변 다시 생성',
    restorePrevious: '이전 체크포인트로 복원',
    restoreCheckpoint: '체크포인트 복원',
    restoreFromHere: '이 메시지부터 다시 실행',
    restoreTitle: '이 체크포인트로 복원할까요?',
    restoreBody: '이 메시지 이후의 대화가 삭제되고, 이 메시지부터 다시 실행돼요.',
    restoreConfirm: '복원하고 다시 실행',
    restoreNext: '다음 체크포인트로 복원',
    goForward: '앞으로 이동'
   },
   clarify: {
    notReady: '확인 질문을 불러오는 중입니다',
    gatewayDisconnected: '게이트웨이 연결이 끊어졌습니다. 다시 연결한 뒤 전송해 주세요.',
    sendFailed: '답변을 전송하지 못했습니다',
    loadingQuestion: '질문 불러오는 중…',
    other: '직접 입력',
    placeholder: '답변을 입력하세요…',
    skip: '건너뛰기',
    skipped: '건너뜀',
    noAnswer: '답변 없음',
    confirmAndContinueLabel: '확인',
    singleSelectHint: '단일 선택',
    multiSelectHint: '복수 선택',
    oneQuestion: '질문 1개',
    questionProgress: (answered: number, total: number) => `${total}개 중 ${answered}개 답변됨`,
    notDelivered: '이 질문이 앱에 전달되지 않아 여기서 답변할 수 없습니다. 중지를 누른 뒤 채팅으로 답변해 주세요.'
   }
  }
 }
})
export function HermesWebApp({resolver,fetcher=browserFetch,host:injected}:HermesWebOptions) {
 const [client]=useState(() => new QueryClient({defaultOptions:{queries:{retry:false,refetchOnWindowFocus:false}}}))
 const host=useMemo(() => resolver ? withWebGatewayCompat(injected ?? new WebHostAdapter({rpcMode:'authority',getWebSocketConnection:webConnectionResolver(resolver),fetch:fetcher,windowEvents:window})) : null,[resolver,injected,fetcher])
 useEffect(() => () => client.clear(),[client])
 if (!host) return <main className="hermes-web"><p role="alert">인증 연결 resolver가 지정되지 않았습니다. 연결하지 않았습니다.</p></main>
 if (host.rpcMode!=='authority') return <p role="alert">채팅에는 authority 연결이 필요합니다.</p>
 return <QueryClientProvider client={client}><I18nProvider configClient={null} initialLocale="ko"><HashRouter><WebChat host={host} fetcher={fetcher}/></HashRouter></I18nProvider></QueryClientProvider>
}
function WebChat({host,fetcher}:{host:WebHostAdapter;fetcher:typeof fetch}) {
 const desktop=(window as Window & {yoriDesktop?: YuriDesktopBridge}).yoriDesktop
 const [expanded,setExpanded]=useState(!desktop)
 const [collapsing,setCollapsing]=useState(false)
 const [maximized,setMaximized]=useState(false)
 const maximizedRef=useRef(false)
 maximizedRef.current=maximized
 const [changingWindowSize,setChangingWindowSize]=useState(false)
 const [suppressSidebarMotion,setSuppressSidebarMotion]=useState(false)
 useEffect(() => {
  if (!desktop?.getWindowMode) return
  let disposed=false
  void desktop.getWindowMode().then(mode => {
   if (disposed || typeof mode?.maximized !== 'boolean') return
   setMaximized(mode.maximized)
   setDesktopExpanded(mode.maximized)
  }).catch(() => {})
  return () => { disposed=true }
 },[desktop])
 useEffect(() => { desktop?.setExpanded({open:expanded, maximized:maximizedRef.current}) },[desktop,expanded])
 const navigate=useNavigate()
 const location=useLocation()
 const showingSkills=new URLSearchParams(location.search).get('view')==='skills'
 const showingMcp=new URLSearchParams(location.search).get('view')==='mcp'
 const showingSettings=showingSkills || showingMcp
 const mcpServers=useMcpServers(fetcher).data ?? []
 const actions=useWebSession(host,showingSettings), gatewayState=useStore($gatewayState), sessions=useStore($sessions)
 const listError=useStore($sessionsLoadError), listLoading=useStore($sessionsLoading)
 const freshDraftKey=useStore($freshDraftKey)
 const messages=useStore($messages)
 const currentStoredId=routeSessionId(location.pathname) ?? actions.selectedStoredSessionId
 const isNewChat=!currentStoredId && messages.length===0 && !actions.busy
 const [product,setProduct]=useState('')
 const [error,setError]=useState(''),[ready,setReady]=useState(false)
 useStore($clarifyRequests)
 const unansweredQuestions=clarifyRequestsForSession(actions.activeSessionId).flatMap(request =>
  request.questions.filter(question => !Object.hasOwn(request.lockedAnswers ?? {},question.qid))
   .map(question => ({key:JSON.stringify([actions.activeSessionId,request.requestId,question.qid]),text:question.question})))
 const pending=unansweredQuestions.length
 const nextQuestion=unansweredQuestions[0]
 const questionKey=nextQuestion?.key
 const arrival=useReplyArrival(messages,actions.busy,currentStoredId)
 const [previewLayout,setPreviewLayout]=useState<{ mascotOffsetX: number; below?: boolean } | null>(null)
 const previewedArrival=useRef<typeof arrival>(null)
 useEffect(() => {
  const setPreview=desktop?.setReplyPreview
  if(!setPreview)return
  if(expanded) {
   previewedArrival.current=arrival
   return
  }
  if(!questionKey) {
   if(!arrival || previewedArrival.current===arrival)return
   previewedArrival.current=arrival
  }
  let disposed=false
  void setPreview(true).then(layout => {if(!disposed && typeof layout?.mascotOffsetX==='number')setPreviewLayout(layout)}).catch(() => {})
  const timer=window.setTimeout(() => {
   setPreviewLayout(null)
   void setPreview(false).catch(() => {})
  },12000)
  return () => {
   disposed=true
   window.clearTimeout(timer)
   setPreviewLayout(null)
   void setPreview(false).catch(() => {})
  }
 },[desktop,expanded,arrival,questionKey])
 const [completed,setCompleted]=useState<'result'|'success'|null>(null)
 const wasBusy=useRef(false)
 useEffect(() => {
  if(actions.busy){wasBusy.current=true;setCompleted(null);return}
  if(!wasBusy.current)return
  wasBusy.current=false
  setCompleted('result')
  const successTimer=setTimeout(() => setCompleted('success'),1500)
  const idleTimer=setTimeout(() => setCompleted(null),4500)
  return () => {clearTimeout(successTimer);clearTimeout(idleTimer)}
 },[actions.busy])
 const mascotState=error || (ready && gatewayState==='closed') ? 'error' : actions.busy ? 'thinking' : completed ?? 'idle'
 const mobile=useMediaQuery('(max-width: 650px)')
 const wideLayout=desktop ? maximized : !mobile
 const floatingComposer=Boolean(desktop) || wideLayout
 const [desktopExpanded,setDesktopExpanded]=useState(!desktop),[drawerOpen,setDrawerOpen]=useState(false)
 const overlaySidebar=!wideLayout
 const sidebarOpen=overlaySidebar ? drawerOpen : desktopExpanded
 const setSidebarOpen=overlaySidebar ? setDrawerOpen : setDesktopExpanded
 const queryClient=useQueryClient()
 const archive=useInfiniteQuery({
  queryKey:archivedSessionsKey,
  initialPageParam:0,
  queryFn:({pageParam}) => listSessions(archivePageSize,0,'only','recent',pageParam),
  getNextPageParam:last => last.offset+archivePageSize<last.total ? last.offset+archivePageSize : undefined,
  enabled:ready && sidebarOpen,
 })
 const archivedRows=useMemo(() => {
  const rows=archive.data?.pages.flatMap(page => page.sessions.filter(row => row.archived)) ?? []
  return [...new Map(rows.map(row => [row.id,row])).values()]
 },[archive.data])
 const visibleSessions=sessions.filter(session => !session.archived && !(session as SessionInfo & {deleted?:boolean}).deleted)
 const [query,setQuery]=useState<string|null>(null)
 const searchToggle=useRef<HTMLButtonElement>(null)
 const sidebarToggle=useRef<HTMLButtonElement>(null), sidebarClose=useRef<HTMLButtonElement>(null)
 const [draftTitle,setDraftTitle]=useState<{text:string;sessionId:string|null}|null>(null)
 const shell=useRef<HTMLElement>(null), search=useRef<HTMLInputElement>(null)
 const searchOpen=query!==null && sidebarOpen
 useLayoutEffect(() => {
  if(searchOpen)search.current?.focus({preventScroll:true})
 },[searchOpen])
 const pendingWindowPop=useRef<{mode:'maximize'|'restore';prevWidth:number}|null>(null)
 const windowSizeAnimation=useRef<ReturnType<typeof animate> | null>(null)
 const composer=useRef<HTMLDivElement>(null)
 const chatStageRef=useRef<HTMLDivElement>(null)
 const threadWrapRef=useRef<HTMLDivElement>(null)
 const skillsWrapRef=useRef<HTMLDivElement>(null)
 const prevViewKey=useRef<string|null>(null)
 const skipNextViewAnim=useRef(false)
 const viewKey=showingSkills ? 'skills' : isNewChat ? `new:${freshDraftKey}` : `session:${actions.selectedStoredSessionId ?? actions.activeSessionId ?? ''}`
 useLayoutEffect(() => {
  const prev=prevViewKey.current
  prevViewKey.current=viewKey
  if (prev===null || prev===viewKey) return
  if (skipNextViewAnim.current) {
   skipNextViewAnim.current=false
   return
  }
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  const target=showingSkills ? skillsWrapRef.current : isNewChat ? chatStageRef.current : threadWrapRef.current
  if (!target) return
  const anim=animate(target, {
   opacity: [0, 1],
   transform: ['translateY(7px)', 'translateY(0px)'],
  }, {
   duration: 0.2,
   ease: [0.22, 1, 0.36, 1],
   onComplete: () => {
    target.style.opacity=''
    target.style.transform=''
   },
  })
  return () => {
   anim.cancel()
   target.style.opacity=''
   target.style.transform=''
  }
 },[viewKey,showingSkills,isNewChat])
 useLayoutEffect(() => {
  const panel=shell.current
  const input=composer.current
  if (!floatingComposer || !expanded || isNewChat || showingSkills || !panel || !input) return
  const measure=() => panel.style.setProperty('--yuri-composer-overlay', `${input.offsetHeight}px`)
  const observer=new ResizeObserver(measure)
  measure()
  observer.observe(input)
  return () => {
   observer.disconnect()
   panel.style.removeProperty('--yuri-composer-overlay')
  }
 },[floatingComposer,expanded,isNewChat,showingSkills])
 const collapseCanvas=useRef<HTMLCanvasElement>(null)
 const collapseOperation=useRef<{ cancel?: () => void }>(null)
 const openingAnimation=useRef<ReturnType<typeof animate>>(null)
 useLayoutEffect(() => {
  const panel=shell.current
  if (!desktop || !expanded || !panel || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  panel.style.opacity='0'
  const reveal=() => {
   // Wait for the native window to grow so the animation starts at the mascot's screen position.
   if (window.innerWidth<=90 || window.innerHeight<=100) return
   window.removeEventListener('resize',reveal)
   openingAnimation.current=animate(panel,{
    transform:['scale(.10)','scale(1)'],
    opacity:[0,1],
   },{
    duration:.21,
    ease:[.18,.72,.26,1],
    opacity:{duration:.12,ease:'easeOut'},
    onComplete:() => { openingAnimation.current=null },
   })
  }
  window.addEventListener('resize',reveal)
  reveal()
  return () => {
   window.removeEventListener('resize',reveal)
   openingAnimation.current?.cancel()
   openingAnimation.current=null
  }
 },[desktop,expanded])
 const collapse=useCallback(async () => {
  const panel=shell.current
  if (!panel || collapseOperation.current) return
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
   setExpanded(false)
   return
  }
  const operation: { cancel?: () => void }={}
  collapseOperation.current=operation
  const opening=openingAnimation.current
  if (opening) {
   await opening
   if (collapseOperation.current!==operation) return
  }
  const canvas=collapseCanvas.current
  const frame=canvas && desktop?.captureCollapseFrame
   ? await captureYuriFrame(desktop.captureCollapseFrame)
   : null
  if (collapseOperation.current!==operation) {
   frame?.close()
   return
  }
  const target=await desktop?.getCollapseTarget?.()
  if (collapseOperation.current!==operation) { frame?.close(); return }
  const launcher=document.querySelector<HTMLElement>('.yuri-desktop-launcher')
  if (launcher && target) {
   launcher.style.setProperty('--collapse-left', target.x+'px')
   launcher.style.setProperty('--collapse-top', target.y+'px')
  }
  const genie=canvas && frame ? animateYuriCollapse(canvas,frame,target) : null
  if (genie) panel.style.visibility='hidden'
  setCollapsing(true)
  const animation=genie ?? animate(panel,{
   transformOrigin:target ? (target.x+45)+'px '+(target.y+54)+'px' : 'bottom right',
   transform:['scale(1)','scale(.80)','scale(.10)'],
   clipPath:[
    'polygon(-4% -4%,104% -4%,104% 28%,104% 54%,104% 78%,104% 104%,-4% 104%,-4% 78%,-4% 54%,-4% 28%)',
    'polygon(-4% -4%,104% -4%,104% 28%,103% 54%,100% 78%,96% 104%,54% 104%,32% 78%,13% 54%,0% 28%)',
    'polygon(15% 0%,100% 0%,100% 28%,99% 54%,97% 78%,93% 100%,65% 100%,51% 78%,36% 54%,22% 28%)',
   ],
   opacity:[1,1,0],
  },{
   duration:.32,
   times:[0,.53,1],
   ease:[.34,.04,.62,1],
   opacity:{times:[0,.76,1],ease:'linear'},
  })
  operation.cancel=() => {
   animation.cancel()
   frame?.close()
   panel.style.visibility=''
   if (canvas) canvas.style.visibility=''
  }
  void animation.then(() => {
   if (collapseOperation.current!==operation) return
   frame?.close()
   collapseOperation.current=null
   setCollapsing(false)
   setExpanded(false)
  })
 },[desktop])
 useLayoutEffect(() => {
  const panel=shell.current
  const pop=pendingWindowPop.current
  if (!panel || !pop) return
  pendingWindowPop.current=null
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
   panel.style.opacity=''
   panel.style.transform=''
   return
  }
  windowSizeAnimation.current?.cancel()
  panel.style.opacity='0'
  let started=false
  let fallbackTimer: ReturnType<typeof setTimeout> | undefined
  const runPop=() => {
   if (started) return
   started=true
   window.removeEventListener('resize', checkResize)
   clearTimeout(fallbackTimer)
   const anim=animate(panel, {
    transform: ['scale(.86)', 'scale(1)'],
    opacity: [0, 1],
   }, {
    duration: 0.21,
    ease: [0.18, 0.72, 0.26, 1],
    opacity: { duration: 0.12, ease: 'easeOut' },
    onComplete: () => {
     if (windowSizeAnimation.current === anim) windowSizeAnimation.current=null
     panel.style.opacity=''
     panel.style.transform=''
    },
   })
   windowSizeAnimation.current=anim
  }
  const checkResize=() => {
   if (window.innerWidth !== pop.prevWidth) requestAnimationFrame(runPop)
  }
  if (window.innerWidth !== pop.prevWidth) {
   requestAnimationFrame(runPop)
  } else {
   window.addEventListener('resize', checkResize)
   fallbackTimer=setTimeout(runPop, 90)
  }
  return () => {
   window.removeEventListener('resize', checkResize)
   clearTimeout(fallbackTimer)
   windowSizeAnimation.current?.cancel()
   windowSizeAnimation.current=null
   panel.style.opacity=''
   panel.style.transform=''
  }
 },[maximized])
 const toggleMaximized=async () => {
  if ((!desktop?.setMaximized && !desktop?.toggleMaximized) || changingWindowSize || collapseOperation.current) return
  const restoring=maximized
  const prevWidth=window.innerWidth
  const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const panel=shell.current
  setChangingWindowSize(true)
  if (restoring) {
   setSuppressSidebarMotion(true)
   setDesktopExpanded(false)
   setDrawerOpen(false)
  }
  if (panel && !reducedMotion) {
   windowSizeAnimation.current?.cancel()
   panel.style.opacity='0'
  }
  try {
   const nextMaximized=desktop.setMaximized
    ? (await desktop.setMaximized(!restoring))?.maximized ?? !restoring
    : await desktop.toggleMaximized!()
   if (!reducedMotion) pendingWindowPop.current={mode: nextMaximized ? 'maximize' : 'restore', prevWidth}
   setMaximized(nextMaximized)
   setDesktopExpanded(nextMaximized)
   setDrawerOpen(false)
  } catch (error) {
   console.error('Failed to change desktop window size', error)
   if (panel) panel.style.opacity=''
  } finally {
   setChangingWindowSize(false)
   if (restoring) {
    requestAnimationFrame(() => requestAnimationFrame(() => setSuppressSidebarMotion(false)))
   }
  }
 }
 const handleHeaderDoubleClick=(event:React.MouseEvent<HTMLElement>) => {
  if (!desktop?.setMaximized && !desktop?.toggleMaximized) return
  if ((event.target as HTMLElement).closest('button, a, input, textarea, [role="button"], .yuri-window-controls')) return
  void toggleMaximized()
 }
 useEffect(() => {
  const unsubscribe=desktop?.onCollapse?.(collapse)
  return () => {
   unsubscribe?.()
   collapseOperation.current?.cancel?.()
   collapseOperation.current=null
  }
 },[desktop,collapse])
 const labelSessions=useMemo(() => currentStoredId && !sessions.some(session => session.id===currentStoredId)
  ? [...sessions,{id:currentStoredId} as SessionInfo] : sessions,[sessions,currentStoredId])
 const labelSources=useMemo(() => [...labelSessions,...archivedRows.filter(row => !labelSessions.some(session => session.id===row.id))],[labelSessions,archivedRows])
 const {labels,failed,retry,remember}=useSessionLabels(labelSources,ready,host)
 const selected=labelSources.find(session => session.id===currentStoredId)
 // A deep link can point outside the currently loaded list page.
 const outsideListId=currentStoredId && ![...sessions,...archivedRows].some(session => session.id===currentStoredId) ? currentStoredId : null
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
    search.current?.focus({preventScroll:true})
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
 const updateArchived=async (session:SessionInfo) => {
  const archived=!session.archived
  const result=await host.gateway.request<{archived:boolean}>('session.archive',{session_id:session.id,archived})
  if(result.archived!==archived)throw new Error('Archive status was not saved')
  if(archived){
   setSessions(rows => rows.filter(row => row.id!==session.id))
   if(currentStoredId===session.id)startNewChat()
  }
  await queryClient.cancelQueries({queryKey:archivedSessionsKey})
  await Promise.all([
   actions.refreshSessions(),
   queryClient.invalidateQueries({queryKey:archivedSessionsKey}),
  ])
 }

 const renderSessionRow=(session:SessionInfo,label:string) => <WebSessionRow key={sessionLabelKey(session)} label={label} selected={!showingSettings && session.id===actions.selectedStoredSessionId} disabled={!ready} archived={session.archived}
  onSelect={() => {setDraftTitle(null);setDrawerOpen(false);navigate(sessionRoute(session.id))}}
  onArchive={() => updateArchived(session)}
  onDelete={async () => {
   await host.gateway.request('session.delete',{session_id:session.id})
   setSessions(rows => rows.filter(row => row.id!==session.id))
   if(currentStoredId===session.id)startNewChat()
   if(session.archived)await queryClient.invalidateQueries({queryKey:archivedSessionsKey})
  }}
  onRename={async title => {
   const result=await host.gateway.request<{title:string;pending:boolean}>('session.title',{session_id:session.id,title})
   if(!result.title?.trim() || result.pending)throw new Error('Title was not saved')
   setSessions(rows => rows.map(row => sessionLabelKey(row)===sessionLabelKey(session) ? {...row,title:result.title} : row))
   if(session.archived)await queryClient.invalidateQueries({queryKey:archivedSessionsKey})
   if($sessionsLoading.get())void actions.refreshSessions()
  }}/>

 useEffect(() => {
   let disposed=false, connected=false
   const releaseApi=host.installApiBridge(),releaseDesktop=host.installDesktopBridge()
   const markReady=() => {
    if(disposed || connected)return
    connected=true
    reportPrimaryGatewayState('open')
    setError('')
    setReady(true)
    void actions.refreshSessions()
   }
   const offState=host.gateway.onState(state => {if(state==='open')markReady()})
   void (async () => {try {
     setConnection(await host.getConnection())
     await host.connect()
     markReady()
   } catch {if(!disposed && !connected)setError('연결에 실패했습니다. 자동으로 다시 연결합니다.')}})()
   return () => {disposed=true;offState();host.close();releaseDesktop();releaseApi();reportPrimaryGatewayState('closed')}
 },[host,actions.refreshSessions])
 return <>
  {desktop && <button className="yuri-desktop-launcher" aria-label="Y-Agent 대화창 열기"
   hidden={expanded && !collapsing} disabled={expanded} data-yuri-destination={collapsing || undefined} data-preview={previewLayout ? true : undefined} data-preview-below={previewLayout?.below || undefined} style={previewLayout ? { left: previewLayout.mascotOffsetX } : undefined}
   onClick={() => setExpanded(true)}><YuriMascot state={mascotState}/></button>}
  {desktop && !expanded && previewLayout && (nextQuestion || arrival) && <button type="button" className="yuri-reply-preview" data-preview-below={previewLayout.below || undefined}
   style={{ '--yuri-preview-head': `${previewLayout.mascotOffsetX + 45}px` } as CSSProperties}
   aria-label={nextQuestion ? '추가 질문 열기' : '새 답변 열기'} onClick={() => {
    const sessionId=nextQuestion ? currentStoredId : arrival?.sessionId
    if(showingSkills)navigate(sessionId ? sessionRoute(sessionId) : '/')
    setExpanded(true)
    if(!nextQuestion)requestAnimationFrame(() => shell.current?.querySelector<HTMLElement>('[contenteditable="true"]')?.focus({preventScroll:true}))
   }}>
   <span>{(nextQuestion?.text ?? arrival?.text ?? '').replace(/\s+/g,' ')}</span>
  </button>}
  {expanded && <main className="hermes-web yuri-glass" data-yuri-desktop={desktop ? true : undefined} data-yuri-web={!desktop || undefined} data-yuri-wide={wideLayout || undefined} data-yuri-maximized={maximized || undefined} data-sidebar-open={sidebarOpen || undefined} data-suppress-sidebar-motion={suppressSidebarMotion || undefined} ref={shell}>
  <div className="yuri-window-frame">
  {!desktop && mobile && drawerOpen && <button type="button" className="web-sidebar-backdrop" tabIndex={-1} aria-label="대화 목록 닫기" onClick={closeSidebar}/>}
  <nav className="web-sidebar" id="web-sidebar" aria-label="대화 목록" hidden={!desktop && mobile && !sidebarOpen} inert={!sidebarOpen} aria-hidden={!sidebarOpen || undefined}
   onKeyDown={event => {
    if(overlaySidebar && event.key==='Escape' && !event.defaultPrevented){event.preventDefault();closeSidebar()}
   }}>
   <div className="web-brand" onDoubleClick={handleHeaderDoubleClick}>
    <button ref={sidebarClose} className="web-sidebar-toggle" aria-label={desktop && overlaySidebar ? '채팅으로 돌아가기' : '사이드바 접기'} aria-controls="web-sidebar" aria-expanded={sidebarOpen} onClick={closeSidebar}>
     <img className="web-sidebar-icon" src="/icons/sidebar-close.svg" alt="" />
    </button>
   </div>
   <button className="web-sidebar-action" aria-current={!showingSettings && isNewChat ? 'page' : undefined} disabled={!ready} onClick={startNewChat}>
    {!showingSettings && isNewChat && <motion.span layoutId="web-sidebar-active" className="web-sidebar-active-pill" transition={{ type: 'spring', stiffness: 480, damping: 38 }} />}
    <img className="web-sidebar-action-icon" src="/icons/new-chat.svg" alt="" />
    <span>새 대화</span>
   </button>
   <button className="web-sidebar-action" ref={searchToggle} aria-expanded={query!==null} aria-controls="web-search" onClick={() => setQuery(value => value===null ? '' : null)}>
    <img className="web-sidebar-action-icon" src="/icons/search.svg" alt="" />
    <span>대화 검색</span>
    <AnimatePresence initial={false}>
     {query!==null && (
      <motion.svg
       key="chevron"
       className="web-sidebar-chevron"
       viewBox="0 0 24 24"
       fill="none"
       stroke="currentColor"
       strokeWidth="1.7"
       strokeLinecap="round"
       strokeLinejoin="round"
       aria-hidden="true"
       initial={{ opacity: 0, rotate: 180, scale: 0.85 }}
       animate={{ opacity: 1, rotate: 0, scale: 1 }}
       exit={{ opacity: 0, rotate: 180, scale: 0.85 }}
       transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
      >
       <path d="m18 15-6-6-6 6" />
      </motion.svg>
     )}
    </AnimatePresence>
   </button>
   <AnimatePresence initial={false}>
    {query!==null && sidebarOpen && (
     <motion.div
      key="web-search-box"
      className="web-search-collapse"
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: 'auto', opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={{ height: { duration: 0.2, ease: [0.22, 1, 0.36, 1] }, opacity: { duration: 0.15, ease: 'easeOut' } }}
     >
      <div className="web-search-wrap">
       <input id="web-search" className="web-search" ref={search} aria-label="불러온 대화 검색" placeholder="대화 제목 검색" value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => {
        if(event.key==='Escape'){event.preventDefault();setQuery(null);searchToggle.current?.focus()}
       }}/>
      </div>
     </motion.div>
    )}
   </AnimatePresence>
   <button className="web-sidebar-action" aria-current={showingSkills ? 'page' : undefined} onClick={openSkills}>
    {showingSkills && <motion.span layoutId="web-sidebar-active" className="web-sidebar-active-pill" transition={{ type: 'spring', stiffness: 480, damping: 38 }} />}
    <img className="web-sidebar-action-icon" src="/icons/skills.svg" alt="" />
    <span>스킬</span>
   </button>
   <button className="web-sidebar-action" aria-current={showingMcp ? 'page' : undefined} onClick={openMcp}><Plug size={19} aria-hidden="true"/>MCP 서버<span className="mcp-sidebar-count">{mcpServers.filter(server => server.enabled).length}/{mcpServers.length}</span></button>
   <div className="web-session-list">
    {(archivedRows.length>0 || archive.isError) && <section className="web-session-group web-archive-section" aria-labelledby="web-archive-heading">
     <h2 className="web-list-heading" id="web-archive-heading">보관함</h2>
     {archive.isError && <p role="alert">보관한 대화를 불러오지 못했습니다. <button onClick={() => void archive.refetch()}>다시 시도</button></p>}
     {archivedRows.map(session => renderSessionRow(session,displaySessionLabel(session,labels[sessionLabelKey(session)])))}
     {archive.hasNextPage && <button className="web-load-more" disabled={archive.isFetching} onClick={() => void archive.fetchNextPage()}>보관한 대화 더 보기</button>}
    </section>}
    {listError && <p role="alert">대화 목록을 불러오지 못했습니다. <button onClick={() => void actions.refreshSessions()}>다시 시도</button></p>}
    {failed && <button onClick={retry}>대화 제목 다시 불러오기</button>}
    {Array.from(groups,([id,{title,project,rows}]) => <section className="web-session-group" key={id} aria-label={project ? '프로젝트 '+title : title}>
     <h2 className="web-list-heading">{project && <Folder size={13} aria-hidden="true"/>}{title}</h2>
     {rows.map(({session,label}) => renderSessionRow(session,label))}</section>)}
    {query && !groups.size && <p className="web-list-heading">불러온 대화에서 일치하는 대화가 없습니다.</p>}
    {actions.hasMoreSessions && <button className="web-load-more" disabled={!ready || listLoading} onClick={() => void actions.loadMoreSessions()}>이전 대화 더 보기</button>}
   </div>
  </nav>
  {desktop && overlaySidebar && sidebarOpen && <button type="button" className="yuri-sidebar-dismiss" aria-label="대화 목록 닫기" onClick={closeSidebar}/>}
  <div className="hermes-web-chat" data-new-chat={(!showingSettings && isNewChat) || undefined} inert={overlaySidebar && drawerOpen}>
   <header className="web-header yuri-chat-header" onDoubleClick={handleHeaderDoubleClick}>
    {desktop && <span className="yuri-header-drag-strip" aria-hidden="true" />}
    <div className="yuri-header-actions">
     {!sidebarOpen && <button ref={sidebarToggle} className="web-sidebar-toggle" aria-label="대화 목록 열기" aria-controls="web-sidebar" aria-expanded={false} onClick={() => {setSidebarOpen(true);requestAnimationFrame(() => sidebarClose.current?.focus())}}><img className="web-sidebar-icon" src="/icons/sidebar-open.svg" alt="" /></button>}
    </div>
    <div className="yuri-identity"><YuriMascot state={mascotState}/><h2 title={showingSkills ? '스킬' : title}><span>Y-Agent</span></h2></div>
    <div className="yuri-header-actions yuri-header-trailing">
     {desktop && <div className="yuri-window-controls" role="group" aria-label="창 제어">
      {desktop.toggleMaximized && <button type="button" className="yuri-window-control-btn" aria-label={maximized ? '창 크기 복원' : '최대화'} title={maximized ? '창 크기 복원' : '최대화'} disabled={changingWindowSize || collapsing} onClick={toggleMaximized}>
       <img className="web-window-icon" src={maximized ? '/icons/window-restore.svg' : '/icons/window-maximize.svg'} alt="" />
      </button>}
      <button type="button" className="yuri-window-control-btn" aria-label="Y-Agent 대화창 접기" title="접기" disabled={changingWindowSize} onClick={collapse}>
       <img className="web-window-icon" src="/icons/window-minimize.svg" alt="" />
      </button>
     </div>}
    </div>
   {!showingSettings && pending>0 && <button className="web-pending" onClick={jumpToQuestion}>답변할 질문 {pending}개 <ArrowDown size={16} aria-hidden="true"/></button>}
   </header>
   {!ready && <p className="web-connection-notice" role={error ? 'alert' : 'status'}>{error || '게이트웨이 연결 중…'}</p>}
   {ready && gatewayState!=='open' && <p className="web-connection-notice" role="status">{gatewayState==='connecting' ? '게이트웨이 연결 중…' : '게이트웨이 연결이 끊겼습니다.'}</p>}
   <ConfirmHost/>
   {showingSkills && <div ref={skillsWrapRef} className="web-skills-stage"><SkillsCatalog fetcher={fetcher} onCreate={createSkill}/></div>}
   {showingMcp && <McpServersPage fetcher={fetcher} container={shell}/>}
   <WebPresentationProvider newReplyId={arrival?.id}>
   <SessionViewProvider value={PRIMARY_SESSION_VIEW}><ChatRuntimeBoundary busy={actions.busy} suppressMessages={false} onCancel={actions.stopWebRun} onEdit={actions.editMessage} onReload={actions.reloadFromMessage} onThreadMessagesChange={actions.handleThreadMessagesChange}>
    <div ref={chatStageRef} className="web-chat-stage" hidden={showingSettings}>
    {isNewChat && <section className="web-empty-state" aria-labelledby="web-welcome">
     <h2 id="web-welcome">어떤 수율을 살펴볼까요?</h2>
    </section>}
    <div ref={threadWrapRef} className="hermes-web-thread" hidden={isNewChat}><Thread gateway={host.gateway} sessionId={actions.activeSessionId} sessionKey={actions.selectedStoredSessionId} onCancel={actions.stopWebRun}/></div>
    <div className="hermes-web-composer" data-clarify-waiting={pending>0 || undefined} ref={composer}>{(pending>0 || actions.busy) && <div className="web-runline">{pending>0 && <span>● 추가 비교를 위해 답변을 기다리고 있어요.</span>}{actions.busy && <button className="web-extra-stop" onClick={actions.stopWebRun}>중지</button>}</div>}<ChatBar freshDraftKey={freshDraftKey} gateway={host.gateway} busy={actions.busy} disabled={!ready || gatewayState!=='open' || actions.replacing} sessionId={actions.activeSessionId} queueSessionKey={actions.selectedStoredSessionId} onCancel={actions.cancelRun}
      contextControl={<>{isNewChat && <ProductPicker value={product} onChange={setProduct} fetcher={fetcher} container={shell}/>}<McpComposerButton fetcher={fetcher} container={shell} onManage={openMcp}/></>}
      onSubmit={async (draft, ...rest) => {
       const text=isNewChat && product && !isSlashCommandText(draft) ? `${product} 제품 기준으로, ${draft}` : draft
       const storedId=actions.selectedStoredSessionId
       const fresh=!storedId
       if(fresh){skipNextViewAnim.current=true;setDraftTitle({text,sessionId:null})}
       const sent=await actions.submitText(text,...rest)
       if(floatingComposer && sent && storedId && !isSlashCommandText(text)){
        requestAnimationFrame(() => {
         if($selectedStoredSessionId.get()!==storedId)return
         const runtimeId=$activeSessionId.get()
         if(runtimeId)requestScrollToBottom(runtimeId)
        })
       }
       if(fresh && sent){
        setProduct('')
        const id=$selectedStoredSessionId.get()
        const firstUser=$messages.get().find(message => message.role==='user')
        const firstText=firstUser?.parts.filter(part => part.type==='text').map(part => part.text).join('')
        if(id && firstText===text.trim()){remember($sessions.get().find(session => session.id===id) ?? {id} as SessionInfo,text);setDraftTitle({text,sessionId:id})}
       } else if(fresh)setDraftTitle(null)
       return sent
      }} onSteer={actions.steerWebPrompt}
      state={{model:{model:'',provider:'',canSwitch:false},tools:{enabled:false,label:''},voice:{enabled:false,active:false}}}/></div>
    </div>
   </ChatRuntimeBoundary></SessionViewProvider></WebPresentationProvider>
  </div>
  </div>
  {/* Electron applies no-drag regions after the draggable header. */}
  {desktop && !maximized && <YuriWindowResize desktop={desktop}/>}
 </main>}
  {desktop && expanded && <canvas ref={collapseCanvas} className="yuri-collapse-frame" aria-hidden="true"/>}
 </>
}

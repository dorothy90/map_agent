import { useCallback, useEffect, useRef } from 'react'
import { useStore } from '@nanostores/react'
import { useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router'
import { useSessionStateCache } from '@/app/session/hooks/use-session-state-cache'
import { useWebSessionList } from './use-web-session-list'
import { useSessionActions } from '@/app/session/hooks/use-session-actions'
import { usePromptActions } from '@/app/session/hooks/use-prompt-actions'
import { useRouteResume } from '@/app/session/hooks/use-route-resume'
import { useMessageStream } from '@/app/session/hooks/use-message-stream'
import { routeSessionId } from '@/app/routes'
import { configureGatewayRegistry, dispatchPrimaryServerRequest, setPrimaryGateway, reportPrimaryGatewayState, $gateway } from '@/store/gateway'
import { $activeSessionId, $selectedStoredSessionId, $busy, $gatewayState, $freshDraftReady, $resumeFailedSessionId, $resumeExhaustedSessionId, $sessionResumeRequest, setAwaitingResponse, setBusy, setMessages } from '@/store/session'
import type { WebHostAdapter } from '../platform/web-host'
const idle = async () => {}
/** H app/contrib/wiring.tsx:380-558,680-714; single primary web route only. */
export function useWebSession(host: WebHostAdapter, showingSkills = false) {
  const activeSessionId = useStore($activeSessionId), selectedStoredSessionId = useStore($selectedStoredSessionId), busy = useStore($busy)
  const busyRef = useRef(busy); busyRef.current = busy
  const creatingSessionRef = useRef(false)
  const cache = useSessionStateCache({activeSessionId, selectedStoredSessionId, busyRef, setAwaitingResponse, setBusy, setMessages})
  const navigate = useNavigate(), location = useLocation(), queryClient = useQueryClient()
  const locationRef = useRef(location.pathname); locationRef.current = location.pathname
  const getRouteToken = useCallback(() => locationRef.current, [])
  const getRoutedStoredSessionId = useCallback(() => routeSessionId(locationRef.current), [])
  const requestGateway = useCallback(<T,>(method:string, params?:Record<string,unknown>, timeoutMs?:number) => host.gateway.request<T>(method,params,timeoutMs), [host])
  const {refreshSessions,loadMoreSessions,hasMoreSessions} = useWebSessionList()
  const sessions = useSessionActions({...cache, activeSessionId, selectedStoredSessionId, busyRef, creatingSessionRef, getRouteToken, getRoutedStoredSessionId, navigate, requestGateway, routedSessionId:routeSessionId(location.pathname)})
  // H wiring.tsx:784-801: cache supplies activeSessionIdRef, selectedStoredSessionIdRef and runtimeIdByStoredSessionIdRef.
  useRouteResume({...cache,activeSessionId,creatingSessionRef,currentView:showingSkills ? 'skills' : 'chat',freshDraftReady:useStore($freshDraftReady),gatewayState:useStore($gatewayState),locationPathname:location.pathname,
    resumeSession:sessions.resumeSession,resumeFailedSessionId:useStore($resumeFailedSessionId),resumeExhaustedSessionId:useStore($resumeExhaustedSessionId),sessionResumeRequest:useStore($sessionResumeRequest),routedSessionId:routeSessionId(location.pathname),selectedStoredSessionId,startFreshSessionDraft:sessions.startFreshSessionDraft})
  const actions = usePromptActions({...cache, activeSessionId,busyRef,requestGateway,getRouteToken,getRoutedStoredSessionId,refreshSessions,
    createBackendSessionForSend:sessions.createBackendSessionForSend, resumeStoredSession:sessions.resumeSession, startFreshSessionDraft:sessions.startFreshSessionDraft,
    branchCurrentSession:async () => false,handleSkinCommand:() => '웹에서는 지원하지 않습니다.',openMemoryGraph:() => {},sttEnabled:false})
  const hydrate = useCallback(async (_attempts?:number, storedId?:string|null) => {
    const id = storedId ?? cache.selectedStoredSessionIdRef.current
    if (id) await sessions.resumeSession(id,false)
  },[sessions.resumeSession,cache.selectedStoredSessionIdRef])
  const stream = useMessageStream({...cache,queryClient,refreshSessions,hydrateFromStoredSession:hydrate,refreshHermesConfig:idle})
  useEffect(() => {
    configureGatewayRegistry({onEvent:stream.handleGatewayEvent,onServerRequest:stream.handleServerRequest})
    setPrimaryGateway(host.gateway); $gateway.set(host.gateway)
    const offEvent = host.gateway.onEvent(stream.handleGatewayEvent)
    const offRequest = host.gateway.onRequest(request => dispatchPrimaryServerRequest(request,'default'))
    const offState = host.gateway.onState(reportPrimaryGatewayState)
    return () => {offEvent();offRequest();offState();configureGatewayRegistry({onEvent:() => {}});setPrimaryGateway(null);$gateway.set(null)}
  },[host,stream.handleGatewayEvent,stream.handleServerRequest])
  return {...actions,...sessions,refreshSessions,loadMoreSessions,hasMoreSessions,activeSessionId,selectedStoredSessionId,busy}
}

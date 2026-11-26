import { useCallback, useEffect, useRef, useState } from 'react'
import { useStore } from '@nanostores/react'
import { useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router'
import { useBackgroundQueueDrain } from '@/app/session/hooks/use-background-queue-drain'
import { useSessionStateCache } from '@/app/session/hooks/use-session-state-cache'
import { useWebSessionList } from './use-web-session-list'
import { useSessionActions } from '@/app/session/hooks/use-session-actions'
import { usePromptActions } from '@/app/session/hooks/use-prompt-actions'
import { useRouteResume } from '@/app/session/hooks/use-route-resume'
import { useMessageStream } from '@/app/session/hooks/use-message-stream'
import { routeSessionId } from '@/app/routes'
import { isQueueParked, parkQueuedPrompts, unparkQueuedPrompts } from '@/store/composer-queue'
import { configureGatewayRegistry, dispatchPrimaryServerRequest, setPrimaryGateway, reportPrimaryGatewayState, $gateway } from '@/store/gateway'
import { $activeSessionId, $selectedStoredSessionId, $busy, $gatewayState, $freshDraftReady, $resumeFailedSessionId, $resumeExhaustedSessionId, $sessionResumeRequest, setAwaitingResponse, setBusy, setMessages } from '@/store/session'
import type { GatewayRequester } from '@/app/contrib/types'
import type { WebHostAdapter } from '../platform/web-host'
type WaitForIdle = (sessionId: string, stopResult: unknown) => Promise<void>

export async function replaceBusyWebPrompt({ stop, request, stillSelected, submit }: {
  stop: (waitForIdle: WaitForIdle) => Promise<boolean>
  request: GatewayRequester
  stillSelected: () => boolean
  submit: () => boolean | Promise<boolean>
}): Promise<boolean> {
  const stopped = await stop(async (sessionId, stopResult) => {
    if (stopResult && typeof stopResult === 'object' &&
        'execution_state' in stopResult && stopResult.execution_state === 'idle') {
      return
    }

    // Remote executors can still be unwinding after the interrupt response.
    // Only a gateway snapshot can confirm their active slot is released.
    const deadline = Date.now() + 12_000
    while (stillSelected() && Date.now() < deadline) {
      const snapshot = await request<{ running?: boolean }>(
        'session.resume', { session_id: sessionId, omit_messages: true }, 2_500
      )
      if (snapshot.running === false) {
        return
      }
      await new Promise(resolve => setTimeout(resolve, 500))
    }

    if (stillSelected()) {
      throw new Error('Timed out waiting for the gateway to stop the run')
    }
  })

  return stopped && stillSelected() ? submit() : false
}

const idle = async () => {}
/** H app/contrib/wiring.tsx:380-558,680-714; single primary web route only. */
export function useWebSession(host: WebHostAdapter, showingSkills = false) {
  const activeSessionId = useStore($activeSessionId), selectedStoredSessionId = useStore($selectedStoredSessionId), busy = useStore($busy)
  const gatewayState = useStore($gatewayState)
  const [replacing, setReplacing] = useState(false)
  const replacementRef = useRef(false)
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
  useRouteResume({...cache,activeSessionId,creatingSessionRef,currentView:showingSkills ? 'skills' : 'chat',freshDraftReady:useStore($freshDraftReady),gatewayState,locationPathname:location.pathname,
    resumeSession:sessions.resumeSession,resumeFailedSessionId:useStore($resumeFailedSessionId),resumeExhaustedSessionId:useStore($resumeExhaustedSessionId),sessionResumeRequest:useStore($sessionResumeRequest),routedSessionId:routeSessionId(location.pathname),selectedStoredSessionId,startFreshSessionDraft:sessions.startFreshSessionDraft})
  const actions = usePromptActions({...cache, activeSessionId,busyRef,requestGateway,getRouteToken,getRoutedStoredSessionId,refreshSessions,
    createBackendSessionForSend:sessions.createBackendSessionForSend, resumeStoredSession:sessions.resumeSession, startFreshSessionDraft:sessions.startFreshSessionDraft,
    branchCurrentSession:async () => false,handleSkinCommand:() => '웹에서는 지원하지 않습니다.',openMemoryGraph:() => {},sttEnabled:false})
  useBackgroundQueueDrain({
    enabled: gatewayState === 'open',
    runtimeIdByStoredSessionIdRef: cache.runtimeIdByStoredSessionIdRef,
    selectedStoredSessionId,
    submitText: actions.submitText
  })
  const cancelWebRun = useCallback(async () => {
    if (replacementRef.current) return
    parkQueuedPrompts(cache.selectedStoredSessionIdRef.current ?? cache.activeSessionIdRef.current)
    await actions.cancelRun()
  }, [actions.cancelRun, cache.activeSessionIdRef, cache.selectedStoredSessionIdRef])

  const steerWebPrompt = useCallback(async (text: string, mode: 'interrupt' | 'steer' = 'steer') => {
    if (mode === 'steer') {
      return actions.steerPrompt(text, 'steer')
    }
    if (replacementRef.current) {
      return false
    }

    const route = getRouteToken()
    const storedId = cache.selectedStoredSessionIdRef.current
    const stillSelected = () => getRouteToken() === route && cache.selectedStoredSessionIdRef.current === storedId
    const queueKey = storedId ?? cache.activeSessionIdRef.current
    const wasParked = isQueueParked(queueKey)
    parkQueuedPrompts(queueKey)
    replacementRef.current = true
    setReplacing(true)

    try {
      return await replaceBusyWebPrompt({
        stop: actions.interruptRun,
        request: requestGateway,
        stillSelected,
        submit: () => actions.submitText(text, { attachments: [] })
      })
    } finally {
      if (!wasParked) {
        unparkQueuedPrompts(queueKey)
      }
      replacementRef.current = false
      setReplacing(false)
    }
  }, [actions.interruptRun, actions.steerPrompt, actions.submitText, cache.activeSessionIdRef,
    cache.selectedStoredSessionIdRef, getRouteToken, requestGateway])
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
  return {...actions,...sessions,stopWebRun:cancelWebRun,replacing,steerWebPrompt,refreshSessions,loadMoreSessions,hasMoreSessions,activeSessionId,selectedStoredSessionId,busy}
}

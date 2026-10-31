import { registryBackendScopeKey, type GatewayEvent } from '@hermes/shared'
import { createClientSessionState } from '@/lib/chat-runtime'
import { toChatMessages } from '@/lib/chat-messages'
import { acceptExecutionEvent } from '@/lib/execution-authority'
import { resolveGatewayEventSessionId } from '@/lib/gateway-events'
import { handleSessionInfoEvent } from '@/app/session/hooks/use-message-stream/gateway-event/session-info'
import { handleLifecycleEvent } from '@/app/session/hooks/use-message-stream/gateway-event/lifecycle'
import { handleMessageStreamEvent } from '@/app/session/hooks/use-message-stream/gateway-event/message-stream'
import type { GatewayEventContext, GatewayEventDeps } from '@/app/session/hooks/use-message-stream/gateway-event/types'
import { createMessageCallbacks } from '@/app/session/hooks/use-message-stream/runtime'
import { $gateway, configureGatewayRegistry, dispatchPrimaryServerRequest, pendingSessionReplay, reportPrimaryGatewayState, setPrimaryGateway, setPrimaryGatewayConnection } from '@/store/gateway'
import { $activeSessionId, $connection, $messages, $sessionResumeRequest, setConnection, setMessages } from '@/store/session'
import { $sessionStates, noteSessionEvent, publishSessionState, recordSessionEventScope } from '@/store/session-states'
import type { ClientSessionState } from '@/app/types'
import type { SessionMessage } from '@/types/hermes'
import type { WebHostAdapter } from './web-host'

/** Explicit headless installation. Importing stores never connects or hydrates. */
export async function installGatewayStores(host: WebHostAdapter): Promise<() => void> {
  const previousGateway = $gateway.get()
  const previousConnection = $connection.get()
  const removeApi = host.installApiBridge()
  const removeDesktop = host.installDesktopBridge()
  const connection = await host.getConnection()
  const profile = connection.profile || 'default'
  const cache = { current: new Map<string, ClientSessionState>(Object.entries($sessionStates.get())) }
  const activeSessionIdRef = { current: $activeSessionId.get() }
  let disposed = false
  const updateSessionState: GatewayEventDeps['updateSessionState'] = (id, update, storedId) => {
    const current = cache.current.get(id) ?? $sessionStates.get()[id] ?? createClientSessionState(storedId)
    const next = update(current)
    cache.current.set(id, next)
    publishSessionState(id, next)
    if (id === activeSessionIdRef.current) setMessages(next.messages)
    return next
  }
  const hydrateFromStoredSession: GatewayEventDeps['hydrateFromStoredSession'] = async (_attempts, storedId, runtimeId) => {
    if (!storedId || !runtimeId) return
    // H use-session-actions/index.ts:1247-1257: replay must settle before the REST transcript read.
    const viewReplay = pendingSessionReplay(runtimeId)
    if (viewReplay) await viewReplay
    if (disposed) return
    const response = await host.api<{ messages: SessionMessage[] }>({ path: `/api/sessions/${encodeURIComponent(storedId)}/messages`, profile })
    if (!disposed) updateSessionState(runtimeId, state => ({ ...state, storedSessionId: storedId, messages: toChatMessages(response.messages) }))
  }
  const callbacks = createMessageCallbacks({ activeSessionIdRef, hydrateFromStoredSession, sessionStateByRuntimeIdRef: cache, updateSessionState })
  const deps: GatewayEventDeps = {
    ...callbacks, activeGatewayProfile: profile, activeSessionIdRef,
    lastCwdInfoSessionRef: { current: null }, hydrateFromStoredSession,
    queryClient: { invalidateQueries: async () => undefined }, refreshHermesConfig: async () => {},
    scheduleSessionsRefresh: () => {}, sessionStateByRuntimeIdRef: cache, updateSessionState
  }
  let unscoped: readonly string[] = []
  const authorities = new Map()
  const onEvent = (event: GatewayEvent) => {
    recordSessionEventScope(event)
    const route = resolveGatewayEventSessionId({ activeSessionId: activeSessionIdRef.current, eventType: event.type, explicitSessionId: event.session_id || '', unscopedStreamSessionIds: unscoped })
    unscoped = route.nextUnscopedStreamSessionIds
    if (route.drop) return
    const id = route.sessionId
    const authorityKey = `${registryBackendScopeKey(event.connectionId ?? null, event.profile ?? null)}\u0000${id}`
    if (id && !acceptExecutionEvent(authorities, authorityKey, event.type, event)) return
    const payload = event.payload as GatewayEventContext['payload']
    const ctx: GatewayEventContext = {
      deps, event, payload, sessionId: id, explicitSid: event.session_id || '',
      isActiveEvent: Boolean(id && id === activeSessionIdRef.current),
      occurredAt: typeof payload?.timestamp === 'number' && Number.isFinite(payload.timestamp) ? payload.timestamp : Date.now() / 1000,
      fromActiveSource: () => !event.profile || event.profile === profile, scheduleConfigRefresh: () => {}
    }
    try { if (!handleLifecycleEvent(ctx) && !handleSessionInfoEvent(ctx)) handleMessageStreamEvent(ctx) }
    finally { if (id) noteSessionEvent(id) }
  }
  configureGatewayRegistry({ onEvent, onServerRequest: request => { request.fail(-32601, 'Method not found') } })
  setConnection(connection)
  setPrimaryGatewayConnection(connection)
  setPrimaryGateway(host.gateway, profile)
  reportPrimaryGatewayState(host.gateway.connectionState)
  const removeEvent = host.gateway.onEvent(event => onEvent({ ...event, profile }))
  const removeRequest = host.gateway.onRequest(request => dispatchPrimaryServerRequest(request, profile))
  const removeState = host.gateway.onState(reportPrimaryGatewayState)
  const removeActive = $activeSessionId.listen(id => { activeSessionIdRef.current = id })
  const removeResume = $sessionResumeRequest.listen(request => {
    if (!request) return
    const runtimeId = [...cache.current].find(([, state]) => state.storedSessionId === request.sessionId)?.[0]
    if (!runtimeId) return
    void (async () => {
      await host.gateway.request('session.resume', { session_id: request.sessionId, profile })
      await hydrateFromStoredSession(1, request.sessionId, runtimeId)
    })().catch(() => undefined)
  })
  return () => {
    if (disposed) return
    disposed = true
    removeEvent(); removeRequest(); removeState(); removeActive(); removeResume()
    setPrimaryGateway(previousGateway, previousConnection?.profile || 'default')
    setPrimaryGatewayConnection(previousConnection)
    setConnection(previousConnection)
    removeDesktop(); removeApi()
  }
}

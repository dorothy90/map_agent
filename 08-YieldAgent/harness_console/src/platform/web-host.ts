import { isGatewayWebSocketUrl, isStableOpen, reconnectBackoffDelayMs, type ReconnectBackoffOptions } from '@hermes/shared'
import { HermesGateway } from '@/api/client'
import type { HermesApiRequest, HermesConnection } from '@/global'
import { installWebApiBridge, installWebDesktopBridge } from '@/platform-bridge'
import manifest from './yield-manifest.generated.json'
import { capabilityError, restCapabilityError, gateRequest, gateSocket, type RpcMode } from './capability-gate'

// PB:D10,414,665; web replacement for desktop store/gateway connection ownership.
export const GATEWAY_BASE_PATH = '/gateway-m1'
export interface WebHostOptions {
  rpcMode?: RpcMode
  /** Explicit deployment mount; never chosen by fallback after a refused request. */
  basePath?: string
  location?: Pick<Location, 'href'>
  profile?: string
  fetch?: typeof fetch
  socketFactory?: (url: string, protocols?: string[]) => WebSocket
  /** Obtain a fresh URL/ticket and subprotocols on EVERY dial. Q1 remains host-owned. */
  getWebSocketConnection?: (context: { location: URL; profile: string; rpcMode: RpcMode; fetch: typeof fetch }) => Promise<{ url: string; protocols?: string[] }>
  reconnect?: false | ReconnectBackoffOptions
  windowEvents?: Pick<EventTarget, 'addEventListener' | 'removeEventListener'>
}

export class WebHostAdapter {
  readonly gateway: HermesGateway
  readonly rpcMode: RpcMode
  capabilities: unknown = null
  private readonly basePath: string
  private readonly location: URL
  private readonly fetcher: typeof fetch
  private readonly profile: string
  private stopped = true
  private generation = 0
  private attempt = 0
  private openedAt: number | null = null
  private retry: ReturnType<typeof setTimeout> | undefined
  private pending: Promise<void> | undefined
  private dialSocket: WebSocket | undefined
  private removeEvents: (() => void) | undefined

  constructor(private readonly options: WebHostOptions = {}) {
    this.basePath = options.basePath ?? GATEWAY_BASE_PATH
    if (!this.basePath.startsWith('/') || this.basePath.startsWith('//') || this.basePath.includes('..') || /[?#\\]/.test(this.basePath) || this.basePath.endsWith('/')) throw new Error('Invalid gateway mount')
    this.rpcMode = options.rpcMode ?? 'legacy'
    this.location = new URL((options.location ?? globalThis.location).href)
    this.fetcher = options.fetch ?? globalThis.fetch.bind(globalThis)
    this.profile = options.profile ?? 'default'
    this.gateway = new HermesGateway({
      rpcMode: this.rpcMode,
      // Yield does not advertise gateway.ping; keep the original timer capability-gated.
      heartbeatIntervalMs: capabilityError('gateway.ping', this.rpcMode) ? 0 : undefined,
      socketFactory: () => this.dialSocket!,
    })
    // Public check precedes canonical prerequisite attachments. The socket gate checks translated calls too.
    this.gateway.request = gateRequest(this.gateway.request.bind(this.gateway), this.rpcMode)
    this.gateway.onState(state => {
      if (state === 'open') this.openedAt = Date.now()
      if ((state === 'closed' || state === 'error') && !this.stopped) this.scheduleReconnect()
    })
    // Never let a later UI registry accidentally accept a disabled native request.
    this.gateway.onRequest(request => {
      const requests: Record<string, { classification: string }> = manifest.server_requests
      if (Object.hasOwn(requests, request.method) && requests[request.method].classification === 'implemented') return false
      request.fail(-32601, 'Method not found')
    })
  }

  private defaultWsUrl(): string {
    const url = new URL(`${this.basePath}/api/ws`, this.location)
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
    return url.href
  }

  async getConnection(): Promise<HermesConnection> {
    return { baseUrl: new URL(this.basePath, this.location).href, wsUrl: this.defaultWsUrl(),
      mode: 'remote', profile: this.profile, token: '', logs: [], isFullscreen: false,
      nativeOverlayWidth: 0, windowButtonPosition: null, sharedPrimary: true }
  }

  /** Opt-in bridge installation for copied REST helpers; no global Window mutation. */
  installApiBridge(): () => void { return installWebApiBridge(this) }

  installDesktopBridge(): () => void {
    return installWebDesktopBridge({ getConnection: () => this.getConnection(), supportsSecondaryGateways: false })
  }

  connect(): Promise<void> {
    if (this.pending) return this.pending
    if (!this.stopped && this.gateway.connectionState === 'open') return Promise.resolve()
    this.stopped = false
    if (this.retry !== undefined) { clearTimeout(this.retry); this.retry = undefined }
    if (!this.removeEvents && this.options.windowEvents) {
      const events = this.options.windowEvents
      const wake = () => { if (!this.stopped && this.gateway.connectionState !== 'open') void this.connect().catch(() => undefined) }
      events.addEventListener('online', wake)
      this.removeEvents = () => events.removeEventListener('online', wake)
    }
    const generation = ++this.generation
    const current = this.dial(generation).catch(error => {
      if (!this.stopped && generation === this.generation) {
        // A capability refusal can leave the socket open; it is not initialized.
        this.gateway.invalidate('Web host connection initialization failed')
        this.scheduleReconnect()
      }
      throw error
    }).finally(() => { if (this.pending === current) this.pending = undefined })
    this.pending = current
    return current
  }

  private async dial(generation: number): Promise<void> {
    const dial = this.options.getWebSocketConnection
      ? await this.options.getWebSocketConnection({ location: new URL(this.location), profile: this.profile, rpcMode: this.rpcMode, fetch: this.fetcher })
      : { url: this.defaultWsUrl(), protocols: undefined }
    if (this.stopped || generation !== this.generation) throw new Error('Web host connection superseded')
    if (!isGatewayWebSocketUrl(dial.url)) throw new Error('Web host requires a ws:// or wss:// URL')
    // Construct before shared connect marks itself connecting. A synchronous factory
    // failure then leaves the original client retryable without patching its state machine.
    const factory = this.options.socketFactory ?? ((url: string, protocols?: string[]) => new WebSocket(url, protocols))
    const socket = gateSocket(factory(dial.url, dial.protocols), this.rpcMode)
    this.dialSocket = socket
    this.capabilities = null
    try {
      await this.gateway.connect(dial.url)
    } catch (error) {
      // Also release a constructed socket if connect failed before adopting it.
      socket.close()
      throw error
    }
    // The authority manifest has no capability methods; do not fall back to a legacy mount.
    if (!capabilityError('gateway.capabilities', this.rpcMode)) {
      const result = await this.gateway.request('gateway.capabilities')
      if (generation === this.generation) this.capabilities = result
    }
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.options.reconnect === false || this.retry !== undefined) return
    if (isStableOpen(this.openedAt)) this.attempt = 0
    this.openedAt = null
    this.retry = setTimeout(() => {
      this.retry = undefined
      void this.connect().catch(() => undefined)
    }, reconnectBackoffDelayMs(this.attempt++, this.options.reconnect || undefined))
  }

  close(): void {
    this.stopped = true
    this.generation++
    if (this.retry !== undefined) clearTimeout(this.retry)
    this.retry = undefined
    this.pending = undefined
    this.removeEvents?.(); this.removeEvents = undefined
    this.gateway.close()
    this.dialSocket = undefined
  }

  async api<T>(request: HermesApiRequest): Promise<T> {
    if (!request.path.startsWith('/api/') || request.path.includes('..') || request.upload || request.connectionId) {
      throw new Error('Web host supports only same-origin API requests on its primary connection')
    }
    const url = new URL(this.basePath + request.path, this.location)
    if (url.origin !== this.location.origin || !url.pathname.startsWith(`${this.basePath}/api/`)) throw new Error('Invalid web API path')
    const error = restCapabilityError(request.path, request.method ?? (request.body === undefined ? 'GET' : 'POST'))
    if (error) throw error
    url.searchParams.set('profile', request.profile ?? this.profile)
    const response = await this.fetcher(url, {
      method: request.method ?? (request.body === undefined ? 'GET' : 'POST'),
      ...(request.body === undefined ? {} : { body: JSON.stringify(request.body), headers: { 'Content-Type': 'application/json' } }),
      ...(request.timeoutMs && request.timeoutMs > 0 ? { signal: AbortSignal.timeout(request.timeoutMs) } : {}),
    })
    if (!response.ok) throw Object.assign(new Error(`Web API request failed (${response.status})`), {status: response.status})
    return response.json() as Promise<T>
  }
}

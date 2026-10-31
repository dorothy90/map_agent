// Node-only Web Storage/EventTarget fixtures. No DOM/jsdom and no real sockets.
// Upstream node-compatible tests use these browser primitives, not rendered UI.
import { afterEach, beforeEach, expect } from 'vitest'
import { installWebDesktopBridge, type WebDesktopBridge } from '@/platform-bridge'
class MemoryStorage implements Storage {
  private data = new Map<string, string>()
  get length() { return this.data.size }
  clear() { this.data.clear() }
  getItem(key: string) { return this.data.get(String(key)) ?? null }
  key(index: number) { return [...this.data.keys()][index] ?? null }
  removeItem(key: string) { this.data.delete(String(key)) }
  setItem(key: string, value: string) { this.data.set(String(key), String(value)) }
}
// Scope fixtures to copied store/lib tests; platform tests prove window-free operation.
const path = expect.getState().testPath ?? ''
if (path.includes('/src/hermes/store/') || (path.includes('/src/hermes/lib/') && !path.includes('/json-rpc-gateway-'))) {
  const target = new EventTarget()
  const storage = new MemoryStorage()
  Object.defineProperty(globalThis, 'window', { configurable: true, value: Object.assign(target, {
    location: new URL('https://hermes.test/'), localStorage: storage, sessionStorage: new MemoryStorage(),
    setTimeout: (...args: Parameters<typeof setTimeout>) => globalThis.setTimeout(...args),
    clearTimeout: (id: ReturnType<typeof setTimeout>) => globalThis.clearTimeout(id),
  }) })
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: storage })
  beforeEach(() => storage.clear())
}
let release: (() => void) | undefined
if ((path.includes('/src/hermes/store/gateway') || path.endsWith('/session-request-router.test.ts') || path.endsWith('/store/session.test.ts')) && !path.endsWith('/gateway-reconnect.test.ts')) {
  // Explicitly adapt original tests' descriptor fixtures, keeping their bytes untouched.
  // Production never reads window.hermesDesktop, and the WebHost disallows secondaries.
  beforeEach(() => {
    release = installWebDesktopBridge(new Proxy({} as WebDesktopBridge, { get: (_target, key) => {
      if (key === 'supportsSecondaryGateways') return true
      const fixture = window.hermesDesktop as unknown as Record<PropertyKey, unknown> | undefined
      return fixture?.[key]
    } }))
  })
  afterEach(() => { release?.(); release = undefined })
}

// Storage events carry data only; no DOM rendering is emulated.
class NodeStorageEvent extends Event {
  readonly key: string | null
  readonly newValue: string | null
  constructor(type: string, init: StorageEventInit = {}) {
    super(type); this.key = init.key ?? null; this.newValue = init.newValue ?? null
  }
}
Object.defineProperty(globalThis, 'StorageEvent', { configurable: true, value: NodeStorageEvent })

// PR-9a is an offline source-port check. Install base globals rather than vi.stubGlobal:
// upstream vi.unstubAllGlobals() must restore these guards, never real transports.
Object.defineProperty(globalThis, 'fetch', { configurable: true, writable: true, value: () => {
  throw new Error('PR-9a offline tests forbid real fetch; provide a test-local mock')
} })
Object.defineProperty(globalThis, 'WebSocket', { configurable: true, writable: true, value: class {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  constructor() {
    throw new Error('PR-9a offline tests forbid real WebSocket; provide a test-local mock')
  }
} })
Object.defineProperty(globalThis, 'EventSource', { configurable: true, writable: true, value: class {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  constructor() {
    throw new Error('PR-9a offline tests forbid real EventSource; provide a test-local mock')
  }
} })

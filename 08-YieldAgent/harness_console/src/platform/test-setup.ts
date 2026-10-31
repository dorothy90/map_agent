// Node lacks CloseEvent; original tests use the standard event only, no DOM.
if (typeof globalThis.CloseEvent === 'undefined') {
  globalThis.CloseEvent = class extends Event {
    readonly code: number
    readonly reason: string
    readonly wasClean: boolean
    constructor(type: string, init: CloseEventInit = {}) {
      super(type, init); this.code = init.code ?? 0; this.reason = init.reason ?? ''; this.wasClean = init.wasClean ?? false
    }
  }
}

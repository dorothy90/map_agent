import { dirname, resolve } from 'node:path'
// Test-only adaptation of immutable upstream native API fixtures to the web host.
// Production continues to require an explicit WebHost and never reads this global.
import { afterAll, beforeAll, afterEach, beforeEach, expect, vi } from 'vitest'
import { installWebApiBridge, installWebDesktopBridge, type WebDesktopBridge } from '../hermes/platform-bridge'

let release: (() => void) | undefined
if (expect.getState().testPath?.includes('/src/hermes/')) {
  beforeEach(() => {
    release = installWebApiBridge({ api: request => {
      const api = window.hermesDesktop?.api
      if (!api) return Promise.reject(new Error('Upstream API fixture is not installed'))
      return api(request)
    } })
  })
  afterEach(() => { release?.(); release = undefined })
}

if (['/app/gateway/hooks/use-gateway-request.test.ts', '/lib/sibling-ws-url.test.ts', '/store/session-pin-sync.test.ts'].some(path => expect.getState().testPath?.endsWith(path))) {
  let releaseDesktop: (() => void) | undefined
  beforeEach(() => {
    releaseDesktop = installWebDesktopBridge(new Proxy({} as WebDesktopBridge, {
      get: (_target, key) => {
        if (key === 'supportsSecondaryGateways') return true
        const fixture: unknown = window.hermesDesktop
        return fixture && typeof fixture === 'object' ? Reflect.get(fixture, key) : undefined
      }
    }))
  })
  afterEach(() => { releaseDesktop?.(); releaseDesktop = undefined })
}

// This original suite registers its pin watcher once in beforeAll.
if (expect.getState().testPath?.endsWith('/store/session-pin-connection-scope.test.ts')) {
  let releaseDesktop: (() => void) | undefined
  beforeAll(() => { releaseDesktop = installWebDesktopBridge({} as WebDesktopBridge) })
  afterAll(() => { releaseDesktop?.() })
}

// The immutable upstream suite reads a shared monorepo corpus outside this checkout.
// Redirect only that fixture path to its byte-identical local copy.
const corpusTestPath = expect.getState().testPath
if (corpusTestPath?.endsWith('/lib/speech-text.identifier-corpus.test.ts')) {
  vi.doMock('node:fs', async importOriginal => {
    const fs = await importOriginal<typeof import('node:fs')>()
    const upstreamPath = resolve(dirname(corpusTestPath), '../../../../tests/fixtures/identifier_speech_corpus.json')
    const localPath = resolve(dirname(corpusTestPath), '../test/fixtures/identifier_speech_corpus.json')
    const readFileSync = new Proxy(fs.readFileSync, {
      apply(target, thisArg, args) {
        return Reflect.apply(target, thisArg, args[0] === upstreamPath ? [localPath, ...args.slice(1)] : args)
      }
    })
    return { ...fs, default: { ...fs, readFileSync }, readFileSync }
  })
}

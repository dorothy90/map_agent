import { describe, expect, it, vi } from 'vitest'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { capabilityError, gateRequest } from './capability-gate'
import manifest from './yield-manifest.generated.json'
import { JsonRpcGatewayClient } from '@hermes/shared'
import { HermesGateway } from '@/api/client'

describe('capability gate', () => {
  it('aliases preserve the original inheritance', () => expect(new HermesGateway()).toBeInstanceOf(JsonRpcGatewayClient))
  it('only implemented calls reach transport', async () => {
    const send = vi.fn().mockResolvedValue({ ok: true })
    const request = gateRequest(send, 'legacy')
    await request('session.list', {})
    expect(send).toHaveBeenCalledTimes(1)
    for (const name of ['session.delete', 'terminal.run', 'not.a.method']) {
      send.mockClear()
      await expect(request(name, {})).rejects.toMatchObject({ code: -32004, message: 'unsupported', data: { feature: name, upstream: '5894e750' } })
      expect(send).not.toHaveBeenCalled()
    }
  })
  it('does not borrow a method from the other mount mode', () => {
    expect(capabilityError('prompt.receipt', 'legacy')).toMatchObject({ code: -32004 })
    expect(capabilityError('prompt.receipt', 'authority')).toBeNull()
  })
  it('allows authority session title, delete, archive, and set_hidden', () => {
    for (const name of ['session.title', 'session.delete', 'session.archive', 'session.set_hidden']) {
      expect(capabilityError(name, 'authority')).toBeNull()
    }
    expect(capabilityError('session.delete', 'legacy')).toMatchObject({ code: -32004 })
  })
  it('matches the backend JSON and its hash exactly', () => {
    const bytes = readFileSync(new URL('../../../harness/gateway/manifest.json', import.meta.url))
    const source = JSON.parse(bytes.toString())
    expect(manifest.source_sha256).toBe(createHash('sha256').update(bytes).digest('hex'))
    for (const section of ['rpc', 'events', 'server_requests'] as const) {
      expect(manifest[section]).toEqual(Object.fromEntries(source[section].map((r: any) => [r.name, section === 'rpc' ? { classification: r.classification, reason: r.reason, feature: r.feature, modes: Object.fromEntries(Object.entries(r.modes).map(([mode, entry]: [string, any]) => [mode, { classification: entry.classification, reason: entry.reason, feature: entry.feature }])) } : { classification: r.classification, reason: r.reason, feature: r.feature }])))
    }
  })
})

it('wire gate also blocks calls that bypass the public API and keeps response frames intact', async () => {
  const { gateSocket } = await import('./capability-gate')
  const { FakeWebSocket } = await import('./fake-websocket')
  const socket = new FakeWebSocket('wss://fake')
  const gated = gateSocket(socket as unknown as WebSocket, 'legacy')
  const receive = vi.fn(); gated.addEventListener('message', receive)
  gated.send(JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'session.delete' }))
  await Promise.resolve()
  expect(socket.sent).toEqual([])
  expect(JSON.parse(receive.mock.calls[0][0].data)).toMatchObject({ id: 1, error: { code: -32004, message: 'unsupported' } })
  const reply = JSON.stringify({ jsonrpc: '2.0', id: 'approval-1', result: { choice: 'once' } })
  gated.send(reply); expect(socket.sent).toEqual([reply])
})

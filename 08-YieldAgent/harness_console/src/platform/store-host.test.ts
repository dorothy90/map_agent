import { afterEach, expect, test, vi } from 'vitest'
import { WebHostAdapter } from './web-host'
import { installGatewayStores } from './store-host'
import { FakeWebSocket } from './fake-websocket'
import { capabilityError } from './capability-gate'
import { setActiveSessionId, setSessions, $messages } from '@/store/session'
import { publishSessionState } from '@/store/session-states'
import { createClientSessionState } from '@/lib/chat-runtime'
import { makeSessionInfo } from '@/test/session-info'
import { setSudoRequest, setSecretRequest, $sudoRequest, $secretRequest } from '@/store/prompts'

const disposals: Array<() => void> = []
afterEach(() => { disposals.splice(0).reverse().forEach(dispose => dispose()); vi.useRealTimers(); FakeWebSocket.instances = [] })
const flush = async () => { for (let i = 0; i < 60; i++) await Promise.resolve() }
const frames = (socket: FakeWebSocket) => socket.sent.map(frame => JSON.parse(frame))

test('installed store boot, subscription, polling and reconnect send no unsupported RPC or REST', async () => {
  vi.useFakeTimers()
  const fetcher = vi.fn().mockResolvedValue(new Response('{"messages":[]}'))
  const host = new WebHostAdapter({ rpcMode: 'authority', location: new URL('https://yield.test'), fetch: fetcher,
    socketFactory: url => new FakeWebSocket(url) as unknown as WebSocket, reconnect: false })
  disposals.push(() => host.close())
  disposals.push(await installGatewayStores(host))
  for (let dial = 0; dial < 2; dial++) {
    const connecting = host.connect(); await flush()
    const socket = FakeWebSocket.instances.at(-1)!; socket.open(); await connecting
    await vi.advanceTimersByTimeAsync(60_000)
    expect(frames(socket).every(frame => !frame.method || !capabilityError(frame.method, 'authority'))).toBe(true)
    expect(fetcher).not.toHaveBeenCalled()
    socket.close(); await flush()
  }
})

test('store replay gap waits for resume response before reading REST history', async () => {
  setActiveSessionId('gap-session')
  setSessions([makeSessionInfo({ id: 'gap-session', profile: 'default' })])
  publishSessionState('gap-session', createClientSessionState('gap-session'))
  const fetcher = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ messages: [{ role: 'assistant', content: 'recovered' }] })))
  const host = new WebHostAdapter({ rpcMode: 'authority', location: new URL('https://yield.test'), fetch: fetcher,
    socketFactory: url => new FakeWebSocket(url) as unknown as WebSocket, reconnect: false })
  disposals.push(() => host.close()); disposals.push(await installGatewayStores(host))
  const connecting = host.connect(); await flush()
  const socket = FakeWebSocket.instances.at(-1)!; socket.open(); await connecting
  socket.serverFrame({ jsonrpc: '2.0', method: 'event', params: {
    type: 'session.replay_gap', session_id: 'gap-session', payload: { replay_epoch: 'next', latest_seq: 5 }
  } }); await flush()
  const resume = frames(socket).find(frame => frame.method === 'session.resume')
  expect(resume).toBeDefined(); expect(fetcher).not.toHaveBeenCalled()
  socket.serverFrame({ jsonrpc: '2.0', id: resume.id, result: {
    session_id: 'gap-session', revision: 1, execution_generation: 1, replay_epoch: 'next', last_sequence: 5,
    prompts: [], messages: [], info: {}
  } }); await flush()
  expect(fetcher).toHaveBeenCalledOnce()
  expect(String(fetcher.mock.calls[0][0])).toContain('/api/sessions/gap-session/messages')
  expect(JSON.stringify($messages.get())).toContain('recovered')
})

test('native credential prompts cannot enter web pending state', () => {
  expect(() => setSudoRequest({ sessionId: null, requestId: 'sudo' })).toThrow('unavailable')
  expect(() => setSecretRequest({ sessionId: null, requestId: 'secret', envVar: 'credential', prompt: 'credential' })).toThrow('unavailable')
  expect($sudoRequest.get()).toBeNull(); expect($secretRequest.get()).toBeNull()
})

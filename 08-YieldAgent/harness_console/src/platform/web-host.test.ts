import { afterEach, expect, test, vi } from 'vitest'
import { WebHostAdapter } from './web-host'
import { FakeWebSocket } from './fake-websocket'
import { capabilityError } from './capability-gate'

const hosts: WebHostAdapter[] = []
afterEach(() => { hosts.forEach(h => h.close()); hosts.length = 0; FakeWebSocket.instances = []; vi.useRealTimers() })
const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve() }
const frames = (s: FakeWebSocket) => s.sent.map(x => JSON.parse(x))
const result = (s: FakeWebSocket, method: string, value: unknown) => {
  const req = frames(s).filter(x => x.method === method).at(-1)
  expect(req, method).toBeDefined()
  s.serverFrame({ jsonrpc: '2.0', id: req.id, result: value })
}
function make(mode: 'legacy' | 'authority' = 'legacy') {
  const host = new WebHostAdapter({ rpcMode: mode, location: new URL('https://yield.test/console'), socketFactory: url => new FakeWebSocket(url) as unknown as WebSocket, reconnect: { jitter: false, baseDelayMs: 10 } })
  hosts.push(host)
  return host
}
async function open(host: WebHostAdapter) {
  const pending = host.connect(); await flush()
  const socket = FakeWebSocket.instances.at(-1)!
  socket.open(); await flush()
  socket.serverFrame({ jsonrpc: '2.0', method: 'event', params: { type: 'gateway.ready', payload: {} } })
  await flush()
  if (host.rpcMode === 'legacy') {
    result(socket, 'gateway.capabilities', { per_session_exclusive_submit: false })
    result(socket, 'client.capabilities', { server_requests: ['approval', 'clarify'], declines_not_shown: false })
  }
  await pending; await flush()
  return socket
}
test('same-origin descriptor, legacy negotiation and unsupported boot/reconnect calls never reach the socket', async () => {
  vi.useFakeTimers()
  const host = make(); const socket = await open(host)
  expect(socket.url).toBe('wss://yield.test/gateway-m1/api/ws')
  expect(await host.getConnection()).toMatchObject({ profile: 'default', mode: 'remote', token: '' })
  for (const name of ['cron.list', 'terminal.run', 'session.delete', 'not.a.method']) await expect(host.gateway.request(name)).rejects.toMatchObject({ code: -32004 })
  expect(frames(socket).every(f => !capabilityError(f.method, 'legacy'))).toBe(true)
  socket.close(); await vi.advanceTimersByTimeAsync(10)
  const next = FakeWebSocket.instances.at(-1)!; expect(next).not.toBe(socket)
  next.open(); await flush(); result(next, 'gateway.capabilities', {})
  await flush()
  expect(frames(next).every(f => !capabilityError(f.method, 'legacy'))).toBe(true)
  expect(frames(next).some(f => ['cron.list', 'terminal.run', 'session.delete'].includes(f.method))).toBe(false)
})
test('authority explicitly attaches, reuses submission identity after lost ACK, reads receipt and replays on redial', async () => {
  vi.useFakeTimers()
  const host = make('authority'); const socket = await open(host)
  expect(frames(socket)).toEqual([]) // no legacy-only capability RPC on authority mount
  const submit = host.gateway.request('prompt.submit', { session_id: 's', submission_id: 'stable', text: 'hello' }, 100)
  await flush()
  result(socket, 'session.resume', { session_id: 's', revision: 1, execution_generation: 1, replay_epoch: 'e1', last_sequence: 0, prompts: [], messages: [], info: {} })
  await flush()
  const first = frames(socket).find(f => f.method === 'prompt.submit')
  expect(first.params.submission_id).toBe('stable')
  const rejected = expect(submit).rejects.toThrow()
  await vi.advanceTimersByTimeAsync(100); await rejected
  const retry = host.gateway.request('prompt.submit', { session_id: 's', submission_id: 'stable', text: 'hello' })
  await flush()
  const second = frames(socket).filter(f => f.method === 'prompt.submit').at(-1)
  expect(second.params).toEqual(first.params)
  result(socket, 'prompt.submit', { ref: { session_id: 's' }, admission_id: 'a', status: 'started' }); await retry
  const receipt = host.gateway.request('prompt.receipt', { session_id: 's', admission_id: 'a' }); await flush()
  result(socket, 'prompt.receipt', { ref: { session_id: 's' }, admission_id: 'a', status: 'completed' }); await receipt
  socket.serverFrame({ jsonrpc: '2.0', method: 'event', params: { type: 'message.complete', session_id: 's', seq: 1, replay_epoch: 'e1', payload: {} } })
  socket.close(); await vi.advanceTimersByTimeAsync(10)
  const next = FakeWebSocket.instances.at(-1)!; next.open(); await flush()
  expect(next.request('session.events.since')?.params).toMatchObject({ session_id: 's', last_seen: 1, replay_epoch: 'e1' })
  result(next, 'session.events.since', { events: [], latest_seq: 1, replay_epoch: 'e1', snapshot_required: false })
  expect(frames(next).every(f => !capabilityError(f.method, 'authority'))).toBe(true)
})
test('fresh dial credentials are injected each time; no native marker or mode fallback is added', async () => {
  let dial = 0
  const factory = vi.fn((url: string) => new FakeWebSocket(url) as unknown as WebSocket)
  const host = new WebHostAdapter({ rpcMode: 'authority', location: new URL('https://yield.test'), socketFactory: factory, getWebSocketConnection: async () => ({ url: `wss://yield.test/private?ticket=${++dial}`, protocols: ['provided'] }) })
  hosts.push(host); await open(host)
  expect(factory).toHaveBeenCalledWith('wss://yield.test/private?ticket=1', ['provided'])
  host.close(); await open(host)
  expect(factory).toHaveBeenLastCalledWith('wss://yield.test/private?ticket=2', ['provided'])
  expect(host.rpcMode).toBe('authority')
})
test('close fences an outstanding URL provider so it cannot open a late socket', async () => {
  let release!: (value: { url: string }) => void
  const factory = vi.fn()
  const host = new WebHostAdapter({ location: new URL('https://yield.test'), socketFactory: factory, getWebSocketConnection: () => new Promise(resolve => { release = resolve }) })
  hosts.push(host)
  const pending = host.connect(); await flush(); host.close(); release({ url: 'wss://yield.test/ws' })
  await expect(pending).rejects.toThrow(); expect(factory).not.toHaveBeenCalled()
})
test('REST uses injected fetch, preserves explicit profile and starts no automatic fetches', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true })))
  const host = new WebHostAdapter({ location: new URL('https://yield.test'), fetch: fetcher }); hosts.push(host)
  expect(fetcher).not.toHaveBeenCalled()
  await expect(host.api({ path: '/api/config', profile: 'default' })).resolves.toEqual({ ok: true })
  expect(String(fetcher.mock.calls[0][0])).toBe('https://yield.test/gateway-m1/api/config?profile=default')
  await expect(host.api({ path: 'https://outside.test/api/config' })).rejects.toThrow()
})

test.each(['legacy', 'authority'] as const)('%s blocks every non-implemented RPC before and after reconnect', async mode => {
  const { default: table } = await import('./yield-manifest.generated.json')
  vi.useFakeTimers()
  const host = make(mode); let socket = await open(host)
  const check = async () => {
    const before = socket.sent.length
    for (const name of [...Object.keys(table.rpc), 'unknown', 'toString', '__proto__']) {
      if (capabilityError(name, mode)) await expect(host.gateway.request(name, { session_id: 's' })).rejects.toMatchObject({ code: -32004, data: { feature: name } })
    }
    expect(socket.sent).toHaveLength(before)
  }
  await check()
  host.close(); socket = await open(host); await check()
})

test('disabled native server requests are refused even if a downstream handler would accept them', async () => {
  const host = make(); const socket = await open(host)
  const handler = vi.fn(); host.gateway.onRequest(handler)
  socket.serverFrame({ jsonrpc: '2.0', id: 'secret-1', method: 'secret', params: {} })
  expect(handler).not.toHaveBeenCalled()
  expect(frames(socket).at(-1)).toMatchObject({ id: 'secret-1', error: { code: -32601 } })
})

test('bridge is opt-in and can be detached without mutating Window', async () => {
  const { webApi } = await import('@/platform-bridge')
  await expect(webApi({ path: '/api/config' })).rejects.toThrow('not installed')
  const fetcher = vi.fn().mockResolvedValue(new Response('{}'))
  const host = new WebHostAdapter({ location: new URL('https://yield.test'), fetch: fetcher }); hosts.push(host)
  const off = host.installApiBridge()
  await webApi({ path: '/api/config' }); expect(fetcher).toHaveBeenCalledTimes(1)
  off(); await expect(webApi({ path: '/api/config' })).rejects.toThrow('not installed')
})

test('a throwing socket constructor is retried with a fresh connecting state', async () => {
  vi.useFakeTimers()
  const factory = vi.fn((url: string) => new FakeWebSocket(url) as unknown as WebSocket)
  factory.mockImplementationOnce(() => { throw new Error('constructor refused') })
  const host = new WebHostAdapter({ rpcMode: 'authority', location: new URL('https://yield.test'), socketFactory: factory, reconnect: { jitter: false, baseDelayMs: 10 } }); hosts.push(host)
  await expect(host.connect()).rejects.toThrow('constructor refused')
  await vi.advanceTimersByTimeAsync(10)
  expect(factory).toHaveBeenCalledTimes(2)
  FakeWebSocket.instances.at(-1)!.open(); await flush()
  expect(host.gateway.connectionState).toBe('open')
})

test('failed capability initialization closes the socket and negotiates again, without mode fallback', async () => {
  vi.useFakeTimers()
  const host = make()
  const pending = host.connect(); await flush()
  const socket = FakeWebSocket.instances.at(-1)!; socket.open(); await flush()
  const rejected = expect(pending).rejects.toMatchObject({ code: -32004 })
  socket.serverFrame({ jsonrpc: '2.0', id: socket.request('gateway.capabilities')!.id, error: { code: -32004, message: 'unsupported' } })
  await rejected; await vi.advanceTimersByTimeAsync(10)
  expect(FakeWebSocket.instances).toHaveLength(2)
  const next = FakeWebSocket.instances.at(-1)!; next.open(); await flush()
  result(next, 'gateway.capabilities', { per_session_exclusive_submit: false }); await flush()
  expect(host.capabilities).toEqual({ per_session_exclusive_submit: false })
  expect(host.rpcMode).toBe('legacy')
})

test('lost submit ACK followed by redial reattaches and resends the identical submission identity', async () => {
  vi.useFakeTimers()
  const host = make('authority'); const socket = await open(host)
  const params = { session_id: 's', submission_id: 'retained', text: 'hello' }
  const first = host.gateway.request('prompt.submit', params); await flush()
  result(socket, 'session.resume', { session_id: 's', revision: 1, execution_generation: 1, replay_epoch: 'e', last_sequence: 0, prompts: [], info: {} }); await flush()
  const sent = socket.request('prompt.submit')!.params
  const rejected = expect(first).rejects.toThrow()
  socket.close(); await rejected; await vi.advanceTimersByTimeAsync(10)
  const next = FakeWebSocket.instances.at(-1)!; next.open(); await flush()
  const retry = host.gateway.request('prompt.submit', params); await flush()
  expect(next.request('prompt.submit')).toBeUndefined()
  result(next, 'session.resume', { session_id: 's', revision: 1, execution_generation: 1, replay_epoch: 'e', last_sequence: 0, prompts: [], info: {} }); await flush()
  expect(next.request('prompt.submit')!.params).toEqual(sent)
  result(next, 'prompt.submit', { ref: { session_id: 's' }, admission_id: 'same-admission', status: 'started' })
  await expect(retry).resolves.toMatchObject({ submission_id: 'retained', admission_id: 'same-admission' })
})

test('invalid injected URLs are rejected before socket construction', async () => {
  const factory = vi.fn((url: string) => new FakeWebSocket(url) as unknown as WebSocket)
  const host = new WebHostAdapter({ location: new URL('https://yield.test'), socketFactory: factory, reconnect: false, getWebSocketConnection: async () => ({ url: 'https://yield.test/not-a-ws-url' }) }); hosts.push(host)
  await expect(host.connect()).rejects.toThrow()
  expect(factory).not.toHaveBeenCalled()
})

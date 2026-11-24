import { makeSessionInfo } from '../src/hermes/test/session-info'
import { afterEach, expect, inject, test } from 'vitest'
import { randomUUID } from 'node:crypto'
import { WebHostAdapter } from '../src/platform/web-host'
import { installGatewayStores } from '../src/platform/store-host'
import { createClientSessionState } from '../src/hermes/lib/chat-runtime'
import { publishSessionState } from '../src/hermes/store/session-states'
import { $messages, setActiveSessionId, setSessions } from '../src/hermes/store/session'
import { capabilityError } from '../src/platform/capability-gate'
import manifest from '../src/platform/yield-manifest.generated.json'

type Row = Record<string, any>
const disposals: Array<() => void> = []
const origin = () => inject('fixtureOrigin')
const waitFor = async <T>(read: () => T | undefined | false, label: string): Promise<T> => {
  const deadline = Date.now() + 10_000
  while (Date.now() < deadline) {
    const value = read()
    if (value !== undefined && value !== false) return value as T
    await new Promise(resolve => setTimeout(resolve, 20))
  }
  throw new Error(`Timed out waiting for ${label}`)
}
afterEach(() => { for (const dispose of disposals.splice(0).reverse()) dispose() })
const control = async (body: Row) => {
  const response = await fetch(origin() + '/__test/control', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  expect(response.status).toBe(200)
  return response.json() as Promise<Row>
}
const state = async (): Promise<Row> => (await fetch(origin() + '/__test/state')).json()
const wsUrl = (mount: string, user: string | null = 'alice') => {
  const url = new URL(mount + '/api/ws', origin())
  url.protocol = 'ws:'
  if (user !== null) { url.searchParams.set('user', user); url.searchParams.set('ticket', `${user}-${randomUUID()}`) }
  return url.href
}
async function request<T = Row>(h: WebHostAdapter, method: string, params: Row = {}, timeoutMs?: number): Promise<T> {
  try {
    return await h.gateway.request<T>(method, params, timeoutMs)
  } catch (error) {
    const rpc = error as { code?: unknown; data?: { reason?: unknown; feature?: unknown } } | null
    // Only contract diagnostics: never include params, tickets, or full error data.
    const detail = JSON.stringify({ method, code: rpc?.code ?? null,
      reason: rpc?.data?.reason ?? null, feature: rpc?.data?.feature ?? null })
    throw new Error(`RPC failed ${detail}`)
  }
}
function host(mode: 'authority' | 'legacy' = 'authority', mount = mode === 'authority' ? '/gateway-m1' : '/gateway-legacy', user = 'alice') {
  const dials: string[] = []
  const sent: Row[] = []
  const h = new WebHostAdapter({ rpcMode: mode, location: new URL(origin()), basePath: mount, reconnect: false,
    getWebSocketConnection: async () => { dials.push(mount); return { url: wsUrl(mount, user) } },
    fetch: (input, init) => { const url = new URL(String(input)); url.searchParams.set('user', user); return fetch(url, init) },
    socketFactory: (url, protocols) => {
      const socket = new WebSocket(url, protocols)
      const send = socket.send.bind(socket)
      socket.send = data => { sent.push(JSON.parse(String(data))); send(data) }
      return socket
    },
  })
  disposals.push(() => h.close())
  return { h, dials, sent }
}
async function create(h: WebHostAdapter): Promise<Row> { return request(h, 'session.create', { title: `PR8 integration ${randomUUID()}` }) }
const submit = (h: WebHostAdapter, sid: string, submission_id = randomUUID(), text = 'Synthetic question'): Promise<Row> =>
  h.gateway.request('prompt.submit', { session_id: sid, submission_id, text }, 5000)
const finish = (session_id: string, receipt: Row, answer = 'VERIFIED-FINAL') => control({ action: 'finish', user: 'alice', session_id, receipt, answer })

async function raw(mount = '/gateway-m1', user: string | null = 'alice') {
  const socket = new WebSocket(wsUrl(mount, user))
  disposals.push(() => socket.close())
  const frames: Row[] = []
  socket.addEventListener('message', event => frames.push(JSON.parse(String(event.data))))
  await new Promise<void>((resolve, reject) => {
    socket.addEventListener('open', () => resolve(), { once: true })
    socket.addEventListener('error', () => reject(new Error('WebSocket upgrade rejected')), { once: true })
  })
  let id = 0
  return { frames, socket, rpc: async (method: string, params: Row = {}) => {
    const rid = ++id
    socket.send(JSON.stringify({ jsonrpc: '2.0', id: rid, method, params }))
    return waitFor(() => frames.find(frame => frame.id === rid), method)
  } }
}

async function bindStores(h: WebHostAdapter, sid: string) {
  setActiveSessionId(sid)
  setSessions([makeSessionInfo({ id: sid, profile: 'default' })])
  publishSessionState(sid, createClientSessionState(sid))
  disposals.push(await installGatewayStores(h))
}

test('a: matching modes work; mismatch fails without a mount or mode fallback', async () => {
  for (const mode of ['authority', 'legacy'] as const) {
    const { h, dials } = host(mode)
    await h.connect()
    expect((await create(h)).session_id).toBeTypeOf('string')
    expect(dials).toEqual([mode === 'authority' ? '/gateway-m1' : '/gateway-legacy'])
  }
  for (const [mode, mount] of [['authority', '/gateway-legacy'], ['legacy', '/gateway-m1']] as const) {
    const { h, dials } = host(mode, mount)
    if (mode === 'legacy') {
      await expect(h.connect()).rejects.toThrow()
    } else {
      // Backend does not advertise a mode handshake. An authority operation
      // against legacy fails under its contract; it must never redial elsewhere.
      await h.connect()
      await expect(h.gateway.request('prompt.submit', {
        session_id: 'mismatched', submission_id: randomUUID(), text: 'Synthetic',
      }, 1000)).rejects.toThrow()
    }
    expect(dials).toEqual([mount])
    expect(h.rpcMode).toBe(mode)
  }
})

test('b: real session and admission push a completed stock message into the installed store', async () => {
  const { h } = host(); await h.connect()
  const created = await create(h)
  await bindStores(h, created.session_id)
  const receipt = await submit(h, created.session_id)
  expect(receipt).toMatchObject({ status: 'started', admission_id: receipt.run_id })
  const seen: Row[] = []; disposals.push(h.gateway.onEvent(event => seen.push(event)))
  await finish(created.session_id, receipt)
  await waitFor(() => seen.find(event => event.type === 'message.complete'), 'completed push')
  await waitFor(() => JSON.stringify($messages.get()).includes('VERIFIED-FINAL') || undefined, 'message store hydration')
  expect((await state()).launches.filter((id: string) => id === receipt.run_id)).toHaveLength(1)
})

test('c: an actually dropped submit ACK reuses identity and restores a durable terminal receipt', async () => {
  const { h, sent } = host(); await h.connect()
  const { session_id: sid } = await create(h)
  const params = { session_id: sid, submission_id: randomUUID(), text: 'Lost ACK question' }
  await control({ action: 'ack_loss' })
  await expect(request(h, 'prompt.submit', params, 1000)).rejects.toThrow()
  await waitFor(() => h.gateway.connectionState === 'closed' || undefined, 'lost ACK disconnect')
  await h.connect()
  const reconnectStart = sent.length
  // Exercise Hermes' own automatic attach on the new socket.
  const restored = await request(h, 'prompt.submit', params, 5000)
  const recovery = sent.slice(reconnectStart).filter(frame => ['session.resume', 'prompt.submit'].includes(frame.method))
  expect(recovery).toMatchObject([
    { method: 'session.resume', params: { session_id: sid, defer_history: true, omit_messages: true } },
    { method: 'prompt.submit', params },
  ])
  expect(recovery).toHaveLength(2)
  await finish(sid, restored)
  const receipt = await request(h, 'prompt.receipt', { session_id: sid, admission_id: restored.admission_id })
  for (const key of ['run_id', 'admission_id', 'ref', 'sequence', 'authority_epoch', 'execution_generation']) expect(receipt[key]).toEqual(restored[key])
  expect(receipt).toMatchObject({ status: 'terminal', outcome: 'completed' })
  expect(sent.filter(frame => frame.method === 'prompt.submit').map(frame => frame.params.submission_id)).toEqual([params.submission_id, params.submission_id])
  expect((await state()).launches.filter((id: string) => id === restored.run_id)).toHaveLength(1)
})

test('d: disconnected events replay once into the session store', async () => {
  const { h } = host(); await h.connect()
  const created = await create(h); const sid = created.session_id
  await bindStores(h, sid)
  const events: Row[] = []; disposals.push(h.gateway.onEvent(event => { if (event.session_id === sid) events.push(event) }))
  const receipt = await submit(h, sid)
  await control({ action: 'publish', user: 'alice', session_id: sid, receipt })
  await waitFor(() => events.find(event => event.seq > 0), 'initial sequence')
  await control({ action: 'drop' })
  await waitFor(() => h.gateway.connectionState === 'closed' || undefined, 'disconnect')
  await finish(sid, receipt, 'RECOVERED-AFTER-DROP')
  await h.connect()
  await waitFor(() => events.find(event => event.type === 'message.complete'), 'replayed completion')
  await waitFor(() => JSON.stringify($messages.get()).includes('RECOVERED-AFTER-DROP') || undefined, 'replayed store')
  const seq = events.filter(event => event.seq !== undefined).map(event => event.seq)
  expect(seq).toEqual([...new Set(seq)].sort((a, b) => a - b))
  expect(events.filter(event => event.type === 'message.complete')).toHaveLength(1)
})

for (const reason of ['truncated', 'epoch'] as const) test(`e: ${reason} gap resumes the store before REST history`, async () => {
  const { h } = host(); await h.connect()
  const created = await create(h); const sid = created.session_id
  await bindStores(h, sid)
  const seen: Row[] = []; disposals.push(h.gateway.onEvent(event => seen.push(event)))
  const receipt = await submit(h, sid)
  await control({ action: 'publish', user: 'alice', session_id: sid, receipt })
  await waitFor(() => seen.find(event => event.seq > 0), 'replay cursor')
  await control({ action: 'drop' })
  await waitFor(() => h.gateway.connectionState === 'closed' || undefined, 'disconnect')
  try {
    if (reason === 'truncated') {
      await control({ action: 'replay_limit', limit: 3 })
      await control({ action: 'publish', user: 'alice', session_id: sid, receipt, count: 7 })
    } else await control({ action: 'epoch', user: 'alice', session_id: sid })
    await finish(sid, receipt, 'GAP-HISTORY')
    const before = (await state()).trace.length
    await h.connect()
    await waitFor(() => seen.find(event => event.type === 'session.replay_gap'), 'gap delivered to store')
    await waitFor(() => JSON.stringify($messages.get()).includes('GAP-HISTORY') || undefined, 'REST snapshot in store')
    const trace: Row[] = (await state()).trace.slice(before)
    const replay = trace.findIndex(row => row.rpc === 'session.events.since')
    const resume = trace.findIndex(row => row.rpc === 'session.resume')
    const history = trace.findIndex(row => row.http === `/gateway-m1/api/sessions/${sid}/messages`)
    expect(replay).toBeGreaterThanOrEqual(0)
    expect(resume).toBeGreaterThan(replay)
    expect(history).toBeGreaterThan(resume)
  } finally { await control({ action: 'replay_limit', limit: 512 }) }
})

test('f: local unsupported calls emit no wire traffic; raw server retains distinct errors', async () => {
  for (const mode of ['legacy', 'authority'] as const) {
    const { h, sent } = host(mode); await h.connect()
    const baseline = sent.length
    const serverBaseline = (await state()).rpc.length
    for (const name of [...Object.keys(manifest.rpc), 'unknown.pr8']) {
      if (capabilityError(name, mode)) await expect(h.gateway.request(name)).rejects.toMatchObject({ code: -32004 })
    }
    expect(sent).toHaveLength(baseline)
    expect((await state()).rpc).toHaveLength(serverBaseline)
    const before = (await state()).http.length
    await expect(h.api({ path: '/api/cron/jobs' })).rejects.toThrow()
    expect((await state()).http.length).toBe(before) // REST gate must reject before fetch.
  }
  const peer = await raw()
  expect((await peer.rpc('unknown.pr8')).error.code).toBe(-32601)
  const unsupported = Object.entries(manifest.rpc).find(([, row]) => ('authority' in row.modes ? row.modes.authority.classification : undefined) === 'unsupported')![0]
  expect((await peer.rpc(unsupported)).error.code).toBe(-32004)
  expect((await peer.rpc('config.get')).error).toMatchObject({ code: -32005, message: 'handler_not_connected' })
  const response = await fetch(origin() + '/gateway-m1/api/config?user=alice')
  expect(response.status).toBe(503)
  expect(await response.json()).toMatchObject({ error: { code: 'handler_not_connected' } })
})

test('g: users cannot read each other\'s replay, receipt, or REST messages', async () => {
  const alice = await raw(); const bob = await raw('/gateway-m1', 'bob')
  const sid = (await alice.rpc('session.create')).result.session_id
  const receipt = (await alice.rpc('prompt.submit', { session_id: sid, submission_id: randomUUID(), text: 'Alice private' })).result
  for (const [method, params] of [
    ['session.events.since', { session_id: sid, last_sequence: 0 }],
    ['prompt.receipt', { session_id: sid, admission_id: receipt.admission_id }],
    ['session.resume', { session_id: sid, source: 'desktop', defer_history: true, omit_messages: true }],
  ] as const) expect((await bob.rpc(method, params)).error.message).toBe('not_found')
  const foreign = await fetch(origin() + `/gateway-m1/api/sessions/${sid}/messages?user=bob`)
  const missing = await fetch(origin() + '/gateway-m1/api/sessions/missing/messages?user=bob')
  expect(foreign.status).toBe(404); expect(await foreign.json()).toEqual(await missing.json())
  expect(JSON.stringify((await bob.rpc('session.list', { scope: 'stored' })).result)).not.toContain(sid)
})

test('h: legacy prompt submission stays unsupported and creates no execution', async () => {
  const { h, dials } = host('legacy'); await h.connect()
  const sid = (await create(h)).session_id; const before = (await state()).launches.length
  await expect(submit(h, sid)).rejects.toMatchObject({ code: -32004, message: 'unsupported' })
  expect((await state()).launches).toHaveLength(before)
  expect(dials).toEqual(['/gateway-legacy'])
})

test('i: implemented REST sessions, profiles, health and status use actual gateway handlers', async () => {
  const { h } = host(); await h.connect(); const sid = (await create(h)).session_id
  const receipt = await submit(h, sid); await finish(sid, receipt)
  const listing = await h.api<Row>({ path: '/api/sessions' })
  expect(listing.sessions.find((row: Row) => row.id === sid)).toMatchObject({ message_count: 2 })
  const messages = await h.api<Row>({ path: `/api/sessions/${sid}/messages` })
  expect(messages.messages).toEqual([{ role: 'user', content: 'Synthetic question' }, { role: 'assistant', content: 'VERIFIED-FINAL', interim: false }])
  expect(await h.api({ path: '/api/health' })).toEqual({ ok: true, auth_required: true })
  expect(await h.api({ path: '/api/status' })).toEqual({ auth_required: true })
  expect(await h.api({ path: '/api/profiles/active' })).toEqual({ active: 'default', current: 'default' })
})

test('j: absent or invalid test identity fails closed for HTTP and WebSocket', async () => {
  for (const suffix of ['', '?user=', '?user=mallory']) {
    const response = await fetch(origin() + '/gateway-m1/api/health' + suffix)
    expect(response.status).toBe(401)
    expect(await response.json()).toEqual({ error: { code: 'unauthorized' } })
  }
  await expect(raw('/gateway-m1', null)).rejects.toThrow('upgrade rejected')
  await expect(raw('/gateway-m1', 'mallory')).rejects.toThrow('upgrade rejected')
})

for (const mode of ['legacy', 'authority'] as const) test(`k: ${mode} deferred resume hydrates through REST after its snapshot`, async () => {
  const owner = host(); await owner.h.connect()
  const sid = (await create(owner.h)).session_id
  const receipt = await submit(owner.h, sid)
  await finish(sid, receipt, 'DEFERRED-HISTORY')
  const { h, sent } = host(mode); await h.connect()
  const before = (await state()).trace.length
  // H/resume-owner.ts:117-132: desktop cold primaryResumeParams.
  const snapshot = await request(h, 'session.resume', {
    session_id: sid, cols: 96, source: 'desktop', defer_history: true,
    omit_messages: true, profile: 'ignored-wire-profile',
  })
  expect(snapshot).toMatchObject({ messages: [], message_count: 2, hydrating: false, running: false, status: 'idle' })
  expect(sent.find(frame => frame.method === 'session.resume')?.params).toMatchObject({ defer_history: true, omit_messages: true })
  const history = await h.api<Row>({ path: `/api/sessions/${sid}/messages` })
  expect(history.messages).toEqual([
    { role: 'user', content: 'Synthetic question' }, { role: 'assistant', content: 'DEFERRED-HISTORY', interim: false },
  ])
  expect(history.messages).toHaveLength(snapshot.message_count)
  const trace: Row[] = (await state()).trace.slice(before)
  const resume = trace.findIndex(row => row.rpc === 'session.resume')
  const read = trace.findIndex(row => row.http === `${mode === 'authority' ? '/gateway-m1' : '/gateway-legacy'}/api/sessions/${sid}/messages`)
  expect(resume).toBeGreaterThanOrEqual(0)
  expect(read).toBeGreaterThan(resume)
})

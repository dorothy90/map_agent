import { expect, inject, test } from 'vitest'
import { randomUUID } from 'node:crypto'
import { WebHostAdapter } from '../src/platform/web-host'
import { installGatewayStores } from '../src/platform/store-host'
import { makeSessionInfo } from '../src/hermes/test/session-info'
import { createClientSessionState } from '../src/hermes/lib/chat-runtime'
import { chatMessageText } from '../src/hermes/lib/chat-messages'
import { publishSessionState } from '../src/hermes/store/session-states'
import { $messages, setActiveSessionId, setSessions } from '../src/hermes/store/session'

type Row = Record<string, any>
class RpcDiagnostic extends Error {}
// Never attach RPC causes, payloads or model text to assertion errors.
function check(ok: unknown, label: string): asserts ok {
  if (!ok) throw new Error(`Real-model check failed: ${label}`)
}
async function waitFor(read: () => boolean, label: string, timeout = 10_000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (read()) return
    await new Promise(resolve => setTimeout(resolve, 25))
  }
  throw new Error(`Real-model timeout: ${label}`)
}

test('one real execution survives lost ACK and reaches the installed store', async () => {
  check(process.env.PR8_REAL_MODEL === '1', 'explicit opt-in required')
  const origin = inject('fixtureOrigin')
  const disposals: Array<() => void> = []
  const sent: Row[] = []
  const host = () => {
    const h = new WebHostAdapter({ rpcMode: 'authority', location: new URL(origin), basePath: '/gateway-m1', reconnect: false,
      getWebSocketConnection: async () => {
        const url = new URL('/gateway-m1/api/ws', origin)
        url.protocol = 'ws:'
        url.searchParams.set('user', 'alice')
        url.searchParams.set('ticket', `alice-${randomUUID()}`)
        return { url: url.href }
      },
      fetch: (input, init) => {
        const url = new URL(String(input)); url.searchParams.set('user', 'alice')
        return fetch(url, init)
      },
      socketFactory: (url, protocols) => {
        const socket = new WebSocket(url, protocols)
        const send = socket.send.bind(socket)
        socket.send = data => { sent.push(JSON.parse(String(data))); send(data) }
        return socket
      },
    })
    disposals.push(() => h.close())
    return h
  }
  const rpc = async (h: WebHostAdapter, method: string, params: Row = {}, timeout = 10_000): Promise<Row> => {
    try { return await h.gateway.request(method, params, timeout) }
    catch (error) {
      const rpc = error as { code?: unknown; data?: { reason?: unknown; feature?: unknown } } | null
      throw new RpcDiagnostic(`Real-model RPC failed: ${method} ` + JSON.stringify({
        code: rpc?.code ?? null, reason: rpc?.data?.reason ?? null, feature: rpc?.data?.feature ?? null,
      }))
    }
  }
  const state = async (): Promise<Row> => {
    const response = await fetch(origin + '/__test/state')
    check(response.ok, 'fixture state'); return response.json()
  }
  let phase = 'connect and bind stores'
  try {
    const h = host(); await h.connect()
    const created = await rpc(h, 'session.create', { request_id: randomUUID() })
    const sid = created.session_id
    // Same bindStores sequence as gateway.itest.ts; installed before submission.
    setActiveSessionId(sid)
    setSessions([makeSessionInfo({ id: sid, profile: 'default' })])
    publishSessionState(sid, createClientSessionState(sid))
    disposals.push(await installGatewayStores(h))
    const seen: Row[] = []
    disposals.push(h.gateway.onEvent(event => { if (event.session_id === sid) seen.push(event) }))
    phase = 'submit with lost ACK'
    const params = { session_id: sid, submission_id: randomUUID(), text: '1+1은? 한 단어로 답해' }
    check((await fetch(origin + '/__test/control', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'ack_loss' }) })).ok, 'arm ACK loss')
    await expect(rpc(h, 'prompt.submit', params, 5000)).rejects.toThrow('Real-model RPC failed: prompt.submit')
    await waitFor(() => h.gateway.connectionState === 'closed', 'ACK disconnect')
    const before = await state()
    check(before.launches.length === 1, 'first execution recorded')
    phase = 'reconnect and resend'
    await h.connect()
    const reconnectStart = sent.length
    const restored = await rpc(h, 'prompt.submit', params)
    const recovery = sent.slice(reconnectStart).filter(frame => ['session.resume', 'prompt.submit'].includes(frame.method))
    check(recovery.length === 2 && recovery[0].method === 'session.resume' &&
      recovery[0].params.session_id === sid && recovery[0].params.defer_history === true &&
      recovery[0].params.omit_messages === true && recovery[1].method === 'prompt.submit', 'automatic deferred attach before resend')
    check(restored.run_id === before.launches[0], 'same run after ACK loss')
    phase = 'completion in installed store'
    await waitFor(() => seen.some(event => event.type === 'message.complete') &&
      $messages.get().some(message => message.role === 'assistant' && chatMessageText(message).trim().length > 0),
    'message.complete in store', 240_000)
    const complete = seen.filter(event => event.type === 'message.complete')
    check(complete.length === 1, 'one completion')
    const answer = complete[0].payload?.text
    check(typeof answer === 'string' && answer.trim().length > 0, 'nonempty final response')
    check($messages.get().some(message => message.role === 'assistant' && chatMessageText(message) === answer), 'store equals completion')
    const seqs = seen.filter(event => Number.isInteger(event.seq)).map(event => event.seq as number)
    check(seqs.length > 0 && seqs.every((seq, i) => i === 0 || seq > seqs[i - 1]), 'strictly increasing sequences')
    phase = 'fresh connection replay and receipt'
    const fresh = host(); await fresh.connect()
    const replay = await rpc(fresh, 'session.events.since', { session_id: sid, profile: 'default', replay_epoch: created.replay_epoch, last_sequence: 0 })
    const replayEvents: Row[] = replay.events ?? []
    const replaySeqs = replayEvents.map(event => event.seq)
    check(replaySeqs.length > 0 && replaySeqs.every((seq, i) => i === 0 || seq > replaySeqs[i - 1]), 'replay sequences')
    check(seqs.every(seq => replaySeqs.includes(seq)), 'push retained in replay')
    check(replayEvents.filter(event => event.type === 'message.complete').length === 1 &&
      replayEvents.find(event => event.type === 'message.complete')?.payload?.text === answer, 'replay completion')
    const receipt = await rpc(fresh, 'prompt.receipt', { session_id: sid, admission_id: restored.admission_id, include_result: true })
    check(receipt.status === 'terminal' && receipt.outcome === 'completed', 'terminal receipt')
    check(receipt.run_id === restored.run_id && receipt.result?.final_response === answer, 'receipt equals final response')
    phase = 'execution and tool budget'
    const final = await state()
    check(final.launches.length === 1 && final.launches[0] === restored.run_id, 'one execution total')
    check(final.blocked.length === 0, 'no guarded tool attempts')
    check(final.rpc.filter((method: string) => method === 'prompt.submit').length === 2, 'exactly two submissions')
  } catch (error) {
    if (error instanceof RpcDiagnostic) throw error
    // Avoid runner serialization of server errors, model output, URI or prompt.
    throw new Error(`Real-model scenario failed at ${phase} (sensitive diagnostics suppressed)`)
  } finally {
    for (const dispose of disposals.reverse()) dispose()
  }
})

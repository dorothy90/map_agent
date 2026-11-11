import { JsonRpcGatewayClient, type ServerRequest } from '@hermes/shared'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { handleServerRequest } from '@/app/session/hooks/use-message-stream/gateway-event/server-requests'
import { restorePendingClarifyFromSnapshot } from '@/app/session/hooks/use-session-actions/restore-pending-clarify'
import { $clarifyRequests, clarifyRequestsForSession, setClarifyRequest } from '@/store/clarify'
import { $gateway } from '@/store/gateway'
import { answerParkedCard } from '@/store/parked-card-answers'
import { resetServerRequestsForTests } from '@/store/server-requests'
import type { SessionResumeResult } from '@/types/hermes'

import { CanonicalDesktopProtocol } from './canonical-protocol'
import { HermesGateway } from './client'

beforeEach(() => {
  $clarifyRequests.set({})
  resetServerRequestsForTests()
})

afterEach(() => vi.restoreAllMocks())

test.each([
  { method: 'session.resume', warm: true },
  { method: 'session.activate', warm: true },
  { method: 'session.resume', warm: false }
])('a question remains answerable through $method (warm=$warm)', async ({ method, warm }) => {
  vi.spyOn(JsonRpcGatewayClient.prototype, 'connect').mockResolvedValue()

  const wire = vi.spyOn(JsonRpcGatewayClient.prototype, 'request').mockImplementation(async name => {
    if (name === 'session.resume') {
      return { session_id: 'waiting', stored_session_id: 'waiting', revision: 1, execution_generation: 3,
        running: true, messages: [], prompts: [{ kind: 'clarify', prompt_id: 'question-1',
          execution_generation: 3, question: 'Which target?', choices: ['staging', 'production'] }] } as never
    }

    return { status: 'resolved' } as never
  })

  const client = new HermesGateway()
  const previousGateway = $gateway.get()
  $gateway.set(client)

  const shown = vi.fn((request: ServerRequest) => handleServerRequest({ ...request, profile: 'default' }, {
    activeSessionIdRef: { current: 'waiting' },
    sessionInterrupted: () => false,
    sessionStateByRuntimeIdRef: { current: new Map() },
    updateSessionState: vi.fn(),
    upsertToolCall: vi.fn()
  }, 'waiting'))

  client.onRequest(shown)

  try {
    await client.connect('ws://localhost/api/ws?native_dial=fixture&ticket=fixture')
    await client.request('session.resume', { session_id: 'waiting' })
    expect(clarifyRequestsForSession('waiting')[0]?.requestId).toBe('question-1')

    // The first delivery is retained while another chat is foregrounded. A warm
    // activation must not interpret a differently-shaped snapshot as no question.
    const startedAt = Date.now() / 1000 + 1
    const snapshot = await client.request<SessionResumeResult>(method, { session_id: 'waiting' })
    const restored = restorePendingClarifyFromSnapshot(snapshot, 'waiting', startedAt, warm ? ['question-1'] : [])

    expect(restored.authoritativeAbsent).toBe(false)
    expect(restored.requests[0]?.requestId).toBe('question-1')
    expect(clarifyRequestsForSession('waiting')[0]?.requestId).toBe('question-1')
    expect(shown).toHaveBeenCalledTimes(1)

    // An answer and a switch-back snapshot may overlap. Even if that snapshot
    // still contains the question, it must not reopen the locally answered card.
    const racingSnapshot = client.request<SessionResumeResult>('session.activate', { session_id: 'waiting' })
    expect(answerParkedCard('waiting', 'staging')).toBe(true)
    const afterAnswer = restorePendingClarifyFromSnapshot(await racingSnapshot, 'waiting', startedAt, ['question-1'])
    expect(afterAnswer.requests).toEqual([])
    expect(clarifyRequestsForSession('waiting')).toEqual([])
    expect(shown).toHaveBeenCalledTimes(1)
    await vi.waitFor(() => expect(wire.mock.calls.filter(([name]) => name === 'clarify.respond')).toHaveLength(1))
    expect(wire.mock.calls.some(([name]) => name === 'prompt.submit')).toBe(false)
    expect(wire.mock.calls.find(([name]) => name === 'clarify.respond')).toEqual(['clarify.respond', {
      session_id: 'waiting', profile: 'default', execution_generation: 3, prompt_id: 'question-1', answer: 'staging'
    }, expect.any(Number), undefined])
  } finally {
    $gateway.set(previousGateway)
    client.close()
  }
})

test('two canonical questions answer through separate clarify.respond prompt ids', async () => {
  vi.spyOn(JsonRpcGatewayClient.prototype, 'connect').mockResolvedValue()

  const wire = vi.spyOn(JsonRpcGatewayClient.prototype, 'request').mockImplementation(async name => {
    if (name === 'session.resume') {
      return {
        execution_generation: 3,
        messages: [],
        prompts: [
          {
            choices: ['4SS', '8SS'],
            execution_generation: 3,
            kind: 'clarify',
            prompt_id: 'srq-1:q1',
            question: 'Which product?'
          },
          {
            choices: ['2026-Q3', '2026-Q4'],
            execution_generation: 3,
            kind: 'clarify',
            prompt_id: 'srq-1:q2',
            question: 'Which period?'
          }
        ],
        revision: 1,
        running: true,
        session_id: 'waiting',
        stored_session_id: 'waiting'
      } as never
    }

    return { status: 'resolved' } as never
  })

  const client = new HermesGateway()
  const previousGateway = $gateway.get()
  $gateway.set(client)
  client.onRequest(request =>
    handleServerRequest(
      { ...request, profile: 'default' },
      {
        activeSessionIdRef: { current: 'waiting' },
        sessionInterrupted: () => false,
        sessionStateByRuntimeIdRef: { current: new Map() },
        updateSessionState: vi.fn(),
        upsertToolCall: vi.fn()
      },
      'waiting'
    )
  )

  try {
    await client.connect('ws://localhost/api/ws?native_dial=fixture&ticket=fixture')
    await client.request('session.resume', { session_id: 'waiting' })

    expect(clarifyRequestsForSession('waiting').map(request => request.requestId)).toEqual([
      'srq-1:q1',
      'srq-1:q2'
    ])
    expect(answerParkedCard('waiting', '4SS')).toBe(true)
    expect(answerParkedCard('waiting', '2026-Q4')).toBe(true)

    await vi.waitFor(() => expect(wire.mock.calls.filter(([name]) => name === 'clarify.respond')).toHaveLength(2))
    expect(wire.mock.calls.filter(([name]) => name === 'clarify.respond').map(([, params]) => params)).toEqual([
      {
        answer: '4SS',
        execution_generation: 3,
        profile: 'default',
        prompt_id: 'srq-1:q1',
        session_id: 'waiting'
      },
      {
        answer: '2026-Q4',
        execution_generation: 3,
        profile: 'default',
        prompt_id: 'srq-1:q2',
        session_id: 'waiting'
      }
    ])
  } finally {
    $gateway.set(previousGateway)
    client.close()
  }
})

test.each([false, true])('an empty snapshot clears only questions predating it (newer=$newer)', newer => {
  const startedAt = Date.now() / 1000

  const snapshot = new CanonicalDesktopProtocol().result('session.resume', { session_id: 'waiting' }, {
    session_id: 'waiting', revision: 1, execution_generation: 3, running: false, prompts: []
  })

  const request = {
    sessionId: 'waiting', requestId: 'question-1', receivedAt: startedAt + (newer ? 1 : -1),
    questions: [{ qid: 'question-1', question: 'Which target?', choices: ['staging'], multiSelect: false }]
  }

  setClarifyRequest(request)

  const restored = restorePendingClarifyFromSnapshot(snapshot, 'waiting', startedAt)

  expect(restored.authoritativeAbsent).toBe(true)
  expect(restored.cleared).toEqual(newer ? [] : [request])
  expect(clarifyRequestsForSession('waiting')).toEqual(newer ? [request] : [])
})

test('snapshots expose batch question params without losing already locked answers', () => {
  const questions = [
    { qid: 'q1', question: 'Which target?', choices: ['staging'], multi_select: false },
    { qid: 'q2', question: 'Which mode?', choices: ['read only'], multi_select: false }
  ]

  const answers = { q1: 'staging' }

  const snapshot = new CanonicalDesktopProtocol().result('session.resume', { session_id: 'waiting' }, {
    session_id: 'waiting', revision: 1, execution_generation: 3, prompts: [
      { kind: 'clarify', prompt_id: 'question-1', questions, answers, execution_generation: 3 }
    ]
  })

  expect(snapshot.open_requests).toEqual([{
    id: 'question-1', method: 'clarify', params: { session_id: 'waiting', questions, answers }
  }])
})

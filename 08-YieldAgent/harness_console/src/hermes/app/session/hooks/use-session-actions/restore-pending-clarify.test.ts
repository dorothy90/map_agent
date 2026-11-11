import { beforeEach, describe, expect, it, vi } from 'vitest'

import type * as clarifyStore from '@/store/clarify'

const { setClarifyRequestMock, clearClarifyRequestMock } = vi.hoisted(() => ({
  clearClarifyRequestMock: vi.fn(),
  setClarifyRequestMock: vi.fn()
}))

vi.mock('@/store/clarify', async importOriginal => {
  const actual = await importOriginal<typeof clarifyStore>()

  return {
    ...actual,
    clearClarifyRequest: clearClarifyRequestMock,
    setClarifyRequest: setClarifyRequestMock
  }
})

import { $clarifyRequests } from '@/store/clarify'

import { pendingClarifyToolPayload, restorePendingClarifyFromSnapshot } from './restore-pending-clarify'

const resumeStartedAt = 1_700_000_000

describe('restorePendingClarifyFromSnapshot', () => {
  beforeEach(() => {
    clearClarifyRequestMock.mockClear()
    setClarifyRequestMock.mockClear()
    $clarifyRequests.set({})
  })

  it('hands back the card the request handler already parked for a replayed open clarify request', () => {
    $clarifyRequests.set({
      'sess-1': {
        rid1: {
          questions: [{ choices: null, multiSelect: false, qid: 'q0', question: 'Proceed?' }],
          receivedAt: resumeStartedAt + 5,
          requestId: 'rid1',
          sessionId: 'sess-1'
        }
      }
    })

    const state = restorePendingClarifyFromSnapshot(
      {
        open_requests: [{ id: 'rid1', method: 'clarify', params: { questions: [{ qid: 'q0', question: 'Proceed?' }] } }]
      },
      'sess-1',
      resumeStartedAt
    )

    expect(state.authoritativeAbsent).toBe(false)
    expect(state.requests.map(request => request.requestId)).toEqual(['rid1'])
    expect(clearClarifyRequestMock).not.toHaveBeenCalled()
  })

  it('reports an open request the handler declined (no card parked) without inventing one', () => {
    const state = restorePendingClarifyFromSnapshot(
      { open_requests: [{ id: 'rid4', method: 'clarify', params: {} }] },
      'sess-4',
      resumeStartedAt
    )

    expect(state.authoritativeAbsent).toBe(false)
    expect(state.requests).toEqual([])
    expect(setClarifyRequestMock).not.toHaveBeenCalled()
  })

  it('returns every parked prompt confirmed by a warm or cold snapshot', () => {
    $clarifyRequests.set({
      'sess-2': Object.fromEntries(
        ['q1', 'q2'].map(requestId => [
          requestId,
          {
            questions: [{ choices: null, multiSelect: false, qid: requestId, question: requestId }],
            receivedAt: resumeStartedAt + 1,
            requestId,
            sessionId: 'sess-2'
          }
        ])
      )
    })
    const snapshot = {
      open_requests: ['q1', 'q2'].map(id => ({ id, method: 'clarify', params: {} }))
    }

    for (const idsAtStart of [[], ['q1', 'q2']]) {
      const state = restorePendingClarifyFromSnapshot(snapshot, 'sess-2', resumeStartedAt, idsAtStart)
      expect(state.requests.map(request => request.requestId)).toEqual(['q1', 'q2'])
      expect(state.cleared).toEqual([])
    }
  })

  it('clears a stale local request when the snapshot has none, and leaves a newer in-flight one', () => {
    $clarifyRequests.set({
      'sess-6': {
        'old-rid': {
          questions: [{ choices: null, multiSelect: false, qid: 'q0', question: 'Old' }],
          receivedAt: resumeStartedAt - 10,
          requestId: 'old-rid',
          sessionId: 'sess-6'
        }
      }
    })

    const cleared = restorePendingClarifyFromSnapshot({}, 'sess-6', resumeStartedAt, ['old-rid'])

    expect(cleared.authoritativeAbsent).toBe(true)
    expect(cleared.cleared[0]?.requestId).toBe('old-rid')
    expect(clearClarifyRequestMock).toHaveBeenCalledWith('old-rid', 'sess-6')

    $clarifyRequests.set({
      'sess-6': {
        'new-rid': {
          questions: [{ choices: null, multiSelect: false, qid: 'q0', question: 'Newer' }],
          receivedAt: resumeStartedAt + 1,
          requestId: 'new-rid',
          sessionId: 'sess-6'
        }
      }
    })

    const kept = restorePendingClarifyFromSnapshot({}, 'sess-6', resumeStartedAt, ['old-rid'])

    expect(kept.cleared).toEqual([])
    expect(kept.requests).toEqual([])
    expect(clearClarifyRequestMock).toHaveBeenCalledTimes(1)
  })
})

describe('pendingClarifyToolPayload', () => {
  it('mirrors the batch wire shape for in-place re-arm', () => {
    expect(
      pendingClarifyToolPayload({
        questions: [{ choices: ['Yes', 'No'], multiSelect: false, qid: 'q0', question: 'Proceed?' }],
        requestId: 'rid',
        sessionId: 'sess'
      })
    ).toEqual({
      args: {
        questions: [{ choices: ['Yes', 'No'], question: 'Proceed?' }]
      },
      tool_id: 'rid'
    })
  })
})

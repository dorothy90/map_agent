import { type GatewayEventPayload, QUESTION_CARD_TOOLS } from '@/lib/chat-messages'
import { type ClarifyRequest, clarifyRequestsForSession, clearClarifyRequest } from '@/store/clarify'
import type { SessionResumeResult } from '@/types/hermes'

export interface PendingClarifyResumeState {
  authoritativeAbsent: boolean
  cleared: ClarifyRequest[]
  requests: ClarifyRequest[]
}

/**
 * Reconcile the parked clarify for `sessionId` against a resume/activate
 * snapshot.
 *
 * The snapshot's `open_requests` names every server→client request still
 * blocking the session. The shared channel has ALREADY re-delivered those to
 * the request handlers (which parked the clarify card) before the caller sees
 * the response, so this only has to (a) report the parked request when the
 * snapshot confirms it and (b) treat a snapshot WITHOUT a clarify as
 * authoritative for requests that already existed when the RPC began — a
 * newer request that arrived while the response was in flight is left alone.
 */
export function restorePendingClarifyFromSnapshot(
  response: Pick<SessionResumeResult, 'open_requests'>,
  sessionId: string,
  resumeStartedAt: number,
  requestIdsAtStart: string[] = []
): PendingClarifyResumeState {
  const pending = (response.open_requests ?? []).filter(entry => QUESTION_CARD_TOOLS.has(entry.method))
  const pendingIds = new Set(pending.map(entry => entry.id))
  const startIds = new Set(requestIdsAtStart)
  const current = clarifyRequestsForSession(sessionId)
  const cleared: ClarifyRequest[] = []

  for (const request of current) {
    if (pendingIds.has(request.requestId)) {
      continue
    }

    const existedAtStart = startIds.has(request.requestId)
    const definitelyOlder = request.receivedAt !== undefined && request.receivedAt < resumeStartedAt
    const legacyWithoutTime = request.receivedAt === undefined && requestIdsAtStart.length === 0

    if (existedAtStart || definitelyOlder || legacyWithoutTime) {
      clearClarifyRequest(request.requestId, sessionId)
      cleared.push(request)
    }
  }

  return {
    authoritativeAbsent: pending.length === 0,
    cleared,
    requests: clarifyRequestsForSession(sessionId).filter(request => pendingIds.has(request.requestId))
  }
}

export function pendingClarifyToolPayload(request: ClarifyRequest): GatewayEventPayload {
  return {
    args: {
      questions: request.questions.map(question => ({
        choices: question.choices ?? undefined,
        multi_select: question.multiSelect || undefined,
        question: question.question
      }))
    },
    tool_id: request.requestId
  }
}

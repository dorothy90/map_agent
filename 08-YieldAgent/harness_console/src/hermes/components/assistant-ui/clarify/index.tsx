'use client'

import { type ToolCallMessagePartProps, useAuiState } from '@assistant-ui/react'
import { useStore } from '@nanostores/react'
import { useMemo, useRef, useState } from 'react'

import { useSessionView } from '@/app/chat/session-view'
import { ToolFallback } from '@/components/assistant-ui/tool/fallback'
import { $settledClarifyResults, type ClarifyRequest, sessionClarifyRequests } from '@/store/clarify'
import { useWebClarifyOffset } from '../../../../web/execution-trace'

import { selectMessageRunning } from '../tool/fallback-model'

import { readClarifyArgs } from './parse'
import { ClarifyToolPending } from './pending'
import { ClarifyToolSettled } from './settled'
import { ClarifyToolStepper } from './stepper'
import { useUndeliveredClarify } from './use-undelivered'

export const ClarifyTool = (props: ToolCallMessagePartProps) => {
  // Answered → settled Q&A (ToolFallback collapsed the answer away).
  if (props.result !== undefined) {
    return <ClarifyToolSettled {...props} />
  }

  return <ClarifyToolLive {...props} />
}

// The request each tool row asked, remembered past its clearing and past a
// remount (a session switch, a stopped turn): a skip or a typed answer settles
// it in the store before (or without) `tool.complete`. Keyed by session,
// message and call: some providers name every call "call_0", and a bare call id
// would show a later card the answer of an earlier one.
const requestIdByToolCall = new Map<string, string>()

function canonicalSiblingGroup(requests: ClarifyRequest[], requestId: string): ClarifyRequest[] {
  const match = /^(.*):q(\d+)$/.exec(requestId)

  if (!match) {
    return []
  }

  const base = match[1]

  return requests
    .map(request => ({ match: /^(.*):q(\d+)$/.exec(request.requestId), request }))
    .filter(
      (entry): entry is { match: RegExpExecArray; request: ClarifyRequest } =>
        entry.match?.[1] === base && entry.request.questions.length === 1
    )
    .sort((left, right) => Number(left.match[2]) - Number(right.match[2]))
    .map(entry => entry.request)
}

function ClarifyToolLive(props: ToolCallMessagePartProps) {
  const questionOffset = useWebClarifyOffset(props.toolCallId)
  // The tool row is in whichever session's transcript rendered it — read THAT
  // session's clarify (primary or tile), not the globally-active one.
  const sessionId = useStore(useSessionView().$runtimeId)
  const $requests = useMemo(() => sessionClarifyRequests(sessionId), [sessionId])
  const requests = useStore($requests)
  const fromArgs = useMemo(() => readClarifyArgs(props.args), [props.args])
  const argQuestions = fromArgs?.questions ?? []
  const request =
    requests.find(entry => entry.requestId === props.toolCallId) ??
    (requests.length === 1 &&
    argQuestions.length === requests[0].questions.length &&
    requests[0].questions.every(requestQuestion =>
      argQuestions.some(question => question.question.trim() === requestQuestion.question)
    )
      ? requests[0]
      : null)
  const canonicalBatchObserved = useRef(false)

  if (
    !request &&
    requests.length > 1 &&
    argQuestions.length === requests.length &&
    requests.every(entry =>
      entry.questions.every(requestQuestion =>
        argQuestions.some(question => question.question.trim() === requestQuestion.question)
      )
    )
  ) {
    canonicalBatchObserved.current = true
  }
  const messageRunning = useAuiState(selectMessageRunning)
  const messageId = useAuiState(s => s.message.id)
  const rowKey = `${sessionId ?? ''}:${messageId}:${props.toolCallId}`
  // Answering clears the request a beat before `tool.complete` swaps in the
  // settled card. Latch submit so that gap doesn't demote; Stop also clears
  // the request and must still collapse an unanswered card.
  const [answered, setAnswered] = useState(false)
  const liveSiblings = request ? canonicalSiblingGroup(requests, request.requestId) : []
  const [latchedSiblings, setLatchedSiblings] = useState<{ requests: ClarifyRequest[]; toolCallId: string } | null>(
    null
  )
  const rememberedSiblings = latchedSiblings?.toolCallId === props.toolCallId ? latchedSiblings.requests : []

  if (liveSiblings.length > 1 && rememberedSiblings.length === 0) {
    setLatchedSiblings({ requests: liveSiblings, toolCallId: props.toolCallId })
  }

  const settledResults = useStore($settledClarifyResults)
  const requestId = requestIdByToolCall.get(rowKey)
  const settledResult = requestId && request?.requestId !== requestId ? settledResults[requestId] : undefined

  if (request && !settledResult && request.requestId !== requestId) {
    requestIdByToolCall.set(rowKey, request.requestId)
  }

  const undelivered = useUndeliveredClarify(sessionId, messageRunning && !request && !answered && !settledResult)

  if (settledResult) {
    return <ClarifyToolSettled {...props} result={settledResult} />
  }

  if (liveSiblings.length > 1) {
    if (props.toolCallId !== liveSiblings[0].requestId) {
      return null
    }

    return <ClarifyToolStepper onAnswered={() => setAnswered(true)} questionOffset={questionOffset} requests={liveSiblings} />
  }

  // The canonical request group has left the store (local submit or
  // clarify.settled). Keep its old tool rows hidden until their results land;
  // the latched request list must never resurrect the interactive stepper.
  if (rememberedSiblings.length > 1) {
    return null
  }

  // A provider-authored batch row is transcript history, while each canonical
  // prompt gets its own request-id row. Do not paint the provider args as a
  // third disabled copy of the same questions.
  if (!request && !requestId && (requests.length > 0 || canonicalBatchObserved.current)) {
    return null
  }

  // Stopped mid-prompt with no result — don't leave a dead interactive panel.
  // `session.info` reports running=false while clarify is blocking, so the
  // running flag alone would remount the question as a tool row. Keep the
  // card while a request is open or this instance already submitted.
  if (!messageRunning && !request && !answered) {
    return <ToolFallback {...props} />
  }

  return (
    <ClarifyToolPending
      fromArgs={fromArgs}
      questionOffset={questionOffset}
      onAnswered={() => setAnswered(true)}
      request={request}
      undelivered={undelivered}
    />
  )
}

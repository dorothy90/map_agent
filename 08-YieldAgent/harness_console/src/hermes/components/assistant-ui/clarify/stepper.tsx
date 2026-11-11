'use client'

import { useStore } from '@nanostores/react'
import { type FormEvent, useCallback, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { MessageQuestion } from '@/lib/icons'
import {
  bareChoice,
  type ClarifyQuestion,
  type ClarifyRequest,
  clearClarifyRequest,
  settleAnsweredClarify
} from '@/store/clarify'
import { $gateway } from '@/store/gateway'
import { reconnectAction } from '@/store/gateway-reconnect'
import { notifyError } from '@/store/notifications'
import { forgetServerRequest } from '@/store/server-requests'
import { requestForOwnedSession } from '@/store/session-states'

import { emptyStage, QuestionBlock } from './core/question-block'
import { CLARIFY_ICON_CLASS, ClarifyShell } from './core/shell'
import { useClarifyKeys } from './core/use-clarify-keys'
import { handleClarifySubmitShortcut } from './submit-shortcut'

type Stage = { choices: string[]; draft: string }

function answerFor(question: ClarifyQuestion, stage: Stage): string | null {
  const draft = stage.draft.trim()

  if (question.multiSelect) {
    const combined = [...stage.choices.map(bareChoice), ...(draft ? [draft] : [])]

    return combined.length > 0 ? JSON.stringify(combined) : null
  }

  return stage.choices.length > 0 ? bareChoice(stage.choices[0]) : draft || null
}

/** One visible card for canonical sibling prompts (`base:q1`, `base:q2`, …). */
export function ClarifyToolStepper({
  onAnswered,
  requests
}: {
  onAnswered: () => void
  requests: ClarifyRequest[]
}) {
  const { t } = useI18n()
  const copy = { ...t.assistant.clarify, confirmAndContinueLabel: '확인' }
  const pagination = {
    ...t.ui.pagination,
    next: '다음',
    nextAria: '다음 →',
    previous: '이전',
    previousAria: '← 이전'
  }
  const gateway = useStore($gateway)
  const [step, setStep] = useState(0)
  const [staged, setStaged] = useState<Record<string, Stage>>({})
  const [submitting, setSubmitting] = useState(false)
  const currentRequest = requests[step]
  const question = currentRequest.questions[0]
  const stage = staged[currentRequest.requestId] ?? emptyStage
  const currentAnswer = answerFor(question, stage)
  const finalStep = step === requests.length - 1

  const toggleChoice = useCallback(
    (activeQuestion: ClarifyQuestion, choice: string) => {
      setStaged(current => {
        const active = current[currentRequest.requestId] ?? emptyStage
        const choices = activeQuestion.multiSelect
          ? active.choices.includes(choice)
            ? active.choices.filter(value => value !== choice)
            : [...active.choices, choice]
          : [choice]

        return {
          ...current,
          [currentRequest.requestId]: {
            choices,
            draft: activeQuestion.multiSelect ? active.draft : ''
          }
        }
      })
    },
    [currentRequest.requestId]
  )

  const draftFor = useCallback(
    (value: string) => {
      setStaged(current => {
        const active = current[currentRequest.requestId] ?? emptyStage

        return {
          ...current,
          [currentRequest.requestId]: {
            choices: question.multiSelect ? active.choices : [],
            draft: value
          }
        }
      })
    },
    [currentRequest.requestId, question.multiSelect]
  )

  const submitAll = useCallback(async () => {
    if (!gateway) {
      notifyError(new Error(copy.gatewayDisconnected), copy.sendFailed, { action: reconnectAction() })

      return
    }

    setSubmitting(true)

    try {
      for (const request of requests) {
        const requestQuestion = request.questions[0]
        const answer = answerFor(requestQuestion, staged[request.requestId] ?? emptyStage)

        await requestForOwnedSession(
          request.sessionId,
          gateway.request.bind(gateway) as typeof gateway.request,
          'clarify.lock',
          {
            answer,
            question_id: requestQuestion.qid,
            request_id: request.requestId
          }
        )
      }

      for (const request of requests) {
        const answer = answerFor(request.questions[0], staged[request.requestId] ?? emptyStage)

        if (answer !== null) {
          settleAnsweredClarify(request, answer)
        }
        forgetServerRequest(request.requestId)
        clearClarifyRequest(request.requestId, request.sessionId)
      }

      triggerHaptic('submit')
      onAnswered()
    } catch (error) {
      notifyError(error, copy.sendFailed)
      setSubmitting(false)
    }
  }, [copy, gateway, onAnswered, requests, staged])

  const advance = useCallback(() => {
    if (currentAnswer !== null && !finalStep) {
      setStep(current => current + 1)
    }
  }, [currentAnswer, finalStep])

  const handleSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()

      if (finalStep) {
        const answered = requests.every(
          request => answerFor(request.questions[0], staged[request.requestId] ?? emptyStage) !== null
        )

        if (answered) {
          void submitAll()
        }
      } else {
        advance()
      }
    },
    [advance, finalStep, requests, staged, submitAll]
  )

  const formRef = useRef<HTMLFormElement | null>(null)
  const keys = useClarifyKeys({
    enabled: !submitting,
    formRef,
    isStaged: () => currentAnswer !== null,
    onClear: () => setStaged(current => ({ ...current, [currentRequest.requestId]: emptyStage })),
    onConfirm: finalStep ? () => void submitAll() : advance,
    onToggle: toggleChoice,
    questions: [question]
  })
  const canSubmit = requests.every(
    request => answerFor(request.questions[0], staged[request.requestId] ?? emptyStage) !== null
  )

  return (
    <form
      className="my-1.5 grid gap-4"
      data-clarify-stepper={requests.length}
      onKeyDownCapture={handleClarifySubmitShortcut}
      onSubmit={handleSubmit}
      ref={formRef}
    >
      <ClarifyShell className="grid gap-3">
        <div className="flex items-center gap-2">
          <span className="shrink-0 text-[0.6875rem] font-medium leading-4 text-primary">
            {step + 1}/{requests.length}
          </span>
          <div
            aria-label={`${step + 1}/${requests.length}`}
            aria-valuemax={requests.length}
            aria-valuemin={1}
            aria-valuenow={step + 1}
            className="h-1 flex-1 overflow-hidden rounded-full bg-(--chrome-action-hover)"
            role="progressbar"
          >
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-150"
              style={{ width: `${((step + 1) / requests.length) * 100}%` }}
            />
          </div>
          <MessageQuestion aria-hidden className={CLARIFY_ICON_CLASS} />
        </div>

        <QuestionBlock
          cursor={keys.cursorRow}
          disabled={submitting}
          onActivate={() => keys.focusQuestion(0)}
          onDraft={draftFor}
          onOtherFocus={() => keys.onOtherFocus(0)}
          onPick={choiceIndex => keys.pick(0, choiceIndex)}
          question={question}
          staged={stage}
        />
      </ClarifyShell>

      <div className="flex items-center justify-end gap-1">
        {step > 0 ? (
          <Button
            aria-label={pagination.previousAria}
            disabled={submitting}
            onClick={() => setStep(current => current - 1)}
            size="xs"
            type="button"
            variant="outline"
          >
            ← {pagination.previous}
          </Button>
        ) : null}
        {finalStep ? (
          <Button disabled={submitting || !canSubmit} loading={submitting} size="xs" type="submit">
            {copy.confirmAndContinueLabel}
          </Button>
        ) : (
          <Button
            aria-label={pagination.nextAria}
            disabled={submitting || currentAnswer === null}
            size="xs"
            type="submit"
          >
            {pagination.next} →
          </Button>
        )}
      </div>
    </form>
  )
}

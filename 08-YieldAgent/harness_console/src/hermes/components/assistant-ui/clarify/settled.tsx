'use client'

import type { ToolCallMessagePartProps } from '@assistant-ui/react'

import { ToolFallback } from '@/components/assistant-ui/tool/fallback'
import { useI18n } from '@/i18n'
import { CircleLetterA, MessageQuestion } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { bareChoice } from '@/store/clarify'
import { useWebPresentation } from '../../../../web/presentation'
import { useWebClarifyOffset } from '../../../../web/execution-trace'

import { ChoiceLabel, letterFor } from './core/choice-row'
import { ClarifyLine, ClarifyShell } from './core/shell'
import { readClarifyArgs, readClarifyResult } from './parse'

/** Settled batch card: every question with its locked (or absent) answer. */
export function ClarifyToolSettled(props: ToolCallMessagePartProps) {
  const { t } = useI18n()
  const web = useWebPresentation()
  const questionOffset = useWebClarifyOffset(props.toolCallId) ?? 0
  const copy = t.assistant.clarify
  const { outcome, responses } = readClarifyResult(props.result)
  const fromArgs = readClarifyArgs(props.args)
  const argQuestions = fromArgs.questions ?? []
  // Skip (the card's or the composer's) and a stopped turn both end the batch
  // as `cancelled`: the user chose not to answer, so it reads as skipped.
  const cancelled = outcome === 'cancelled'

  if (responses.length === 0) {
    return <ToolFallback {...props} />
  }

  if (web) {
    return (
      <div className="web-clarify-conversation" data-clarify-settled="">
        {responses.map((row, index) => {
          const matchedArg =
            argQuestions.find(entry => entry.question.trim() === (row.question ?? '').trim()) ?? argQuestions[index]
          const choices = matchedArg?.choices ?? null
          const rawAnswers = Array.isArray(row.answer)
            ? row.answer.map(v => v.trim()).filter(Boolean)
            : row.answer?.trim()
              ? [row.answer.trim()]
              : []
          const blank = rawAnswers.length === 0
          const customAnswers = choices
            ? rawAnswers.filter(ans => !choices.some(choice => bareChoice(choice) === ans || choice === ans))
            : []

          return (
            <div className="web-clarify-exchange" key={`${index}-${row.question ?? ''}`}>
              <div className="web-clarify-header flex items-center justify-between gap-2">
                <span className="web-clarify-eyebrow">
                  <img alt="" aria-hidden="true" className="web-clarify-icon" src="/icons/clarify-flag.svg" />
                  <span>{`질문 ${questionOffset + index + 1}`}</span>
                </span>
                {blank && (
                  <span className="web-clarify-mode">{row.unanswered && !cancelled ? copy.noAnswer : copy.skipped}</span>
                )}
              </div>
              {row.question && <div className="web-clarify-question">{row.question}</div>}
              {choices && choices.length > 0 ? (
                <div className="web-clarify-settled-choices" data-clarify-answer="">
                  {choices.map((choice, choiceIndex) => {
                    const bare = bareChoice(choice)
                    const isSelected = rawAnswers.includes(bare) || rawAnswers.includes(choice)
                    return (
                      <div
                        className="web-clarify-settled-option"
                        data-selected={isSelected ? 'true' : 'false'}
                        key={`${choiceIndex}-${choice}`}
                      >
                        <span className="web-clarify-keybadge" aria-hidden="true">
                          {letterFor(choiceIndex)}
                        </span>
                        <span className="web-choice-text flex-1 wrap-anywhere">
                          <ChoiceLabel choice={choice} />
                        </span>
                        {isSelected && <span className="web-clarify-check" aria-label="선택됨" />}
                      </div>
                    )
                  })}
                  {customAnswers.map((custom, customIndex) => (
                    <div
                      className="web-clarify-settled-option"
                      data-selected="true"
                      key={`custom-${customIndex}-${custom}`}
                    >
                      <span className="web-clarify-keybadge" aria-hidden="true">
                        {letterFor(choices.length + customIndex)}
                      </span>
                      <span className="web-choice-text flex-1 wrap-anywhere">{custom}</span>
                      <span className="web-clarify-check" aria-label="선택됨" />
                    </div>
                  ))}
                </div>
              ) : (
                <div className="web-clarify-answer" data-clarify-answer="" data-blank={blank ? 'true' : undefined}>
                  {!blank && <span className="web-clarify-check" aria-label="선택됨" />}
                  {blank ? (row.unanswered && !cancelled ? copy.noAnswer : copy.skipped) : rawAnswers.join(', ')}
                </div>
              )}
            </div>
          )
        })}
      </div>
    )
  }

  return (
    <ClarifyShell className="my-1.5 grid gap-2.5" data-clarify-settled="">
      {responses.map((row, index) => {
        const answer = Array.isArray(row.answer) ? row.answer.join(', ') : (row.answer ?? '')
        const blank = !answer.trim()

        return (
          <div className="grid gap-1" key={`${index}-${row.question ?? ''}`}>
            {row.question ? (
              <ClarifyLine icon={MessageQuestion}>
                <span className="whitespace-pre-wrap font-medium leading-(--conversation-line-height)">
                  {row.question}
                </span>
              </ClarifyLine>
            ) : null}
            <ClarifyLine icon={CircleLetterA}>
              <p
                className={cn(
                  'whitespace-pre-wrap leading-(--conversation-line-height)',
                  blank ? 'italic text-(--ui-text-tertiary)' : 'text-(--ui-text-secondary)'
                )}
                data-clarify-answer=""
              >
                {blank ? (row.unanswered && !cancelled ? copy.noAnswer : copy.skipped) : answer}
              </p>
            </ClarifyLine>
          </div>
        )
      })}
    </ClarifyShell>
  )
}

import { useAuiState, type ThreadMessage } from '@assistant-ui/react'
import { createContext, useCallback, useContext, useId, useLayoutEffect, useMemo, useRef, useState, type PropsWithChildren, type ReactNode } from 'react'
import { useStore } from '@nanostores/react'
import type { TimelinePartMetadata } from '@/lib/chat-messages/types'
import { useSessionView } from '@/app/chat/session-view'
import { $clarifyRequests, $settledClarifyResults, clarifyRequestsForSession } from '@/store/clarify'
import { sessionAwaitingInput } from '@/store/prompts'
import { $currentUsage } from '@/store/session'
import { createPortal } from 'react-dom'
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { TooltipIconButton } from '@/components/assistant-ui/tooltip-icon-button'
import { toolEntryDisclosureId } from '@/components/assistant-ui/tool/fallback-model/targets'
import { readClarifyArgs, readClarifyResult } from '@/components/assistant-ui/clarify/parse'
import { useNewWebReply, useWebPresentation } from './presentation'
import { toolStepLabel } from './tool-summary'
import { ReplyProgress, replyPreparingLabel } from './YuriMascot'
import { WaferItemCards } from './wafer-item-card'

export type TraceState = 'running' | 'done' | 'error' | 'stopped'
const labels = { running: '실행 중', done: '완료', error: '오류', stopped: '중지됨' }
type Note = { key: string; text: string; step?: string }
type StepSpan = { start: number; end: number }
type TimeAxis = StepSpan & { spans: Map<string, StepSpan> }
type ScopeProps = PropsWithChildren<{ indices: readonly number[] }>
const TraceContext = createContext<{
  targets: Map<string, HTMLTableSectionElement>
  setStep: (id: string, state?: TraceState) => void
  noteKeys: Set<string>
  archived: Set<string>
  questions: Map<string, number>
  active: boolean
  live: boolean
  progressVisible: boolean
  actionMessageId?: string
  timeAxis?: TimeAxis
  traceReady: boolean
} | null>(null)

export function WebAnswerAuthor() {
  return useWebPresentation() ? <div className="web-answer-author">Y-Agent</div> : null
}

export function useWebTraceProgress() {
  return useContext(TraceContext)?.progressVisible ?? false
}

export function useWebTraceNotes() {
  return useContext(TraceContext)?.noteKeys
}

export function useWebClarifyOffset(toolCallId: string) {
  const messageId = useAuiState(s => s.message.id)
  return useContext(TraceContext)?.questions.get(JSON.stringify([messageId, toolCallId]))
}

export function useWebTraceMessage(id: string) {
  const trace = useContext(TraceContext)
  return trace?.active ? { archived: trace.archived.has(id) } : null
}

function TraceTrigger({ standalone = false }: { standalone?: boolean }) {
  const label = '조회·계산 기록'
  const icon = <img src="/icons/history.svg" alt=""/>
  return <DialogTrigger asChild>
    {standalone ? <button type="button" className="web-trace-trigger">{icon}<span>{label}</span></button>
      : <TooltipIconButton className="web-trace-trigger" tooltip={label}>{icon}</TooltipIconButton>}
  </DialogTrigger>
}

export function WebTraceAction({ messageId }: { messageId: string }) {
  const trace = useContext(TraceContext)
  if (trace?.actionMessageId !== messageId) return null
  return trace.traceReady ? <TraceTrigger/> : <span className="web-trace-trigger" aria-hidden="true"/>
}

export function WebTraceScope({ indices, children }: ScopeProps) {
  return useWebPresentation() ? <ExecutionTrace indices={indices}>{children}</ExecutionTrace> : <>{children}</>
}

function isTimestamp(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
}

function measuredToolSpan(part: TimelinePartMetadata & { result?: unknown }): StepSpan | undefined {
  const { timestamp, completedAt, result, unpairedStoredToolResult } = part
  if (!isTimestamp(completedAt)) return
  const duration = result && typeof result === 'object' && 'duration_s' in result ? result.duration_s : undefined
  if (isTimestamp(duration) && duration < completedAt) return { start: completedAt - duration, end: completedAt }
  if (!unpairedStoredToolResult && isTimestamp(timestamp) && timestamp <= completedAt) {
    return { start: timestamp, end: completedAt }
  }
}

export function useWebTraceColumnCount() {
  return useContext(TraceContext)?.timeAxis ? 3 : 2
}

function timeBarStyle(axis: StepSpan, span: StepSpan) {
  const total = axis.end - axis.start
  const offset = (span.start - axis.start) / total * 100
  const width = (span.end - span.start) / total * 100
  return { left: `min(${offset}%, calc(100% - var(--trace-min-bar-width)))`, width: `${width}%` }
}

export function WebTraceTimingCell({ id }: { id: string }) {
  const axis = useContext(TraceContext)?.timeAxis
  if (!axis) return null
  const span = axis.spans.get(id)
  if (!span) return <td className="web-trace-time-cell">—</td>
  const precision = span.end - span.start < 0.1 ? 3 : 1
  const timingLabel = `시작 ${(span.start - axis.start).toFixed(precision)}초, 종료 ${(span.end - axis.start).toFixed(precision)}초`
  return <td className="web-trace-time-cell">
    <div className="web-trace-time-track" role="img" aria-label={timingLabel} title={timingLabel}>
      <span className="web-trace-time-bar" style={timeBarStyle(axis, span)}/>
    </div>
  </td>
}

// Match only public text parts, using the interim marker and their position relative
// to visible calls. A settled response can contain several tool rounds in one message.
function projectNotes(messages: readonly ThreadMessage[], indices: readonly number[], steps: Map<string, TraceState>) {
  const notes: Note[] = []
  const stepIds: string[] = []
  const spans = new Map<string, StepSpan>()
  const archived = new Set<string>()
  let nextStep: string | undefined
  for (let i = indices.length - 1; i >= 0; i--) {
    const message = messages[indices[i]!]
    if (message?.role !== 'assistant') continue
    let traceOnly = message.status.type !== 'incomplete'
    for (let j = message.content.length - 1; j >= 0; j--) {
      const part = message.content[j]!
      if (part.type === 'tool-call') {
        const id = toolEntryDisclosureId(message.id, part)
        if (steps.has(id)) {
          nextStep = id
          stepIds.push(id)
          const span = measuredToolSpan(part as typeof part & TimelinePartMetadata)
          if (span) spans.set(id, span)
        } else traceOnly = false
      } else if (part.type === 'text') {
        if (!part.text.trim()) continue
        if (nextStep || message.metadata.custom?.interim === true) {
          notes.push({ key: message.id + '/' + j, text: part.text, step: nextStep })
        } else traceOnly = false
      } else traceOnly = false
    }
    if (traceOnly) archived.add(message.id)
  }
  notes.reverse()
  return { notes, archived, spans, stepIds: stepIds.reverse(), noteKeys: new Set(notes.map(note => note.key)) }
}

function ExecutionTrace({ indices, children }: ScopeProps) {
  const [targets, setTargets] = useState(() => new Map<string, HTMLTableSectionElement>())
  const setTarget = useCallback((id: string, node: HTMLTableSectionElement | null) => {
    setTargets(current => {
      if ((current.get(id) ?? null) === node) return current
      const next = new Map(current)
      if (node) next.set(id, node)
      else next.delete(id)
      return next
    })
  }, [])
  const [steps, setSteps] = useState(() => new Map<string, TraceState>())
  const [recordOpen, setRecordOpen] = useState(false)
  const newReplyId = useNewWebReply()
  const recordDescriptionId = useId()
  const titleRef = useRef<HTMLHeadingElement>(null)
  const sessionView = useSessionView()
  const sessionId = useStore(sessionView.$runtimeId)
  const sessionBusy = useStore(sessionView.$busy)
  const awaitingPrompt = useStore(useMemo(() => sessionAwaitingInput(sessionId), [sessionId]))
  useStore($clarifyRequests)
  const settledClarify = useStore($settledClarifyResults)
  const hasSettledInStore = Object.keys(settledClarify).length > 0
  const pendingClarify = clarifyRequestsForSession(sessionId).some(request =>
    request.questions.some(q => !Object.hasOwn(request.lockedAnswers ?? {}, q.qid))
  )
  const messages = useAuiState(s => s.thread.messages)
  const threadRunning = useAuiState(s => s.thread.isRunning)
  const tail = indices.at(-1)
  const isCurrent = tail === messages.length - 1
  const counts = { running: 0, done: 0, error: 0, stopped: 0 }
  for (const state of steps.values()) counts[state]++
  const live = isCurrent && (sessionBusy || threadRunning || counts.running > 0 || messages.at(-1)?.status?.type === 'running')
  const projection = useMemo(() => projectNotes(messages, indices, steps), [messages, indices, steps])
  const questions = useMemo(() => {
    const offsets = new Map<string, number>()
    const calls: { key: string; id: string; texts: string[]; pending: boolean }[] = []
    for (const index of indices) {
      const message = messages[index]
      if (message?.role !== 'assistant') continue
      for (const part of message.content) {
        if (part.type !== 'tool-call' || part.toolName !== 'clarify') continue
        const pending = part.result === undefined
        const rows = pending ? readClarifyArgs(part.args).questions ?? [] : readClarifyResult(part.result).responses
        if (rows.length) calls.push({
          key: JSON.stringify([message.id, part.toolCallId]), id: part.toolCallId,
          texts: rows.map(row => row.question?.trim() ?? ''), pending
        })
      }
    }
    let offset = 0
    for (const [index, call] of calls.entries()) {
      // The live transcript can carry both the model's batch and the gateway's per-question rows.
      const firstSibling = /^(.*):q1$/.exec(calls[index + 1]?.id ?? '')
      const shadowedBatch = call.pending && firstSibling && call.texts.every((text, step) => {
        const sibling = calls[index + step + 1]
        return sibling?.id === `${firstSibling[1]}:q${step + 1}` &&
          sibling.texts.length === 1 && sibling.texts[0] === text
      })
      if (shadowedBatch) continue
      offsets.set(call.key, offset)
      offset += call.texts.length
    }
    return offsets
  }, [messages, indices])
  const rawActive = steps.size > 0 || projection.notes.length > 0
  // Tool rows remount when a message settles or a clarify card swaps views; their
  // step briefly unregisters. Once this turn has shown its record, keep it visible.
  const [everActive, setEverActive] = useState(false)
  if (rawActive && !everActive) setEverActive(true)
  const active = rawActive || everActive

  const { hasInlineCard, hasUnsettledClarify } = useMemo(() => {
    let inlineCard = false
    let unsettledClarify = false
    for (const idx of indices) {
      const message = messages[idx]
      if (message?.role !== 'assistant') continue
      for (let j = 0; j < message.content.length; j++) {
        const part = message.content[j]!
        if (part.type === 'tool-call') {
          const id = toolEntryDisclosureId(message.id, part)
          if (steps.has(id)) continue
          if (part.toolName === 'clarify') {
            const locallySettled = !pendingClarify && (hasSettledInStore || Boolean(settledClarify[part.toolCallId]))
            if (part.result !== undefined || locallySettled) {
              inlineCard = true
            } else {
              unsettledClarify = true
            }
          } else {
            inlineCard = true
          }
        }
      }
    }
    return {
      hasInlineCard: inlineCard,
      hasUnsettledClarify: unsettledClarify
    }
  }, [hasSettledInStore, indices, messages, pendingClarify, projection.noteKeys, settledClarify, steps])

  const waitingForInput = awaitingPrompt || pendingClarify || hasUnsettledClarify
  const replyMessageIndex = indices.findLast(index => {
    const message = messages[index]
    return message?.role === 'assistant' && message.content.some((part, partIndex) =>
      part.type === 'text' && part.text.trim() && !projection.noteKeys.has(message.id + '/' + partIndex))
  })
  const showTailTyping = live && !waitingForInput && replyMessageIndex === undefined && (active || hasInlineCard)
  const runningTool = showTailTyping ? indices.flatMap(index => {
    const message=messages[index]
    return message?.content.flatMap(part => part.type==='tool-call' &&
      steps.get(toolEntryDisclosureId(message.id,part))==='running' ? [part.toolName] : []) ?? []
  }).at(-1) : undefined
  const progressLabel = runningTool === 'run_python' ? 'Python 실행 중'
    : runningTool ? toolStepLabel(runningTool) + ' 중' : replyPreparingLabel
  const [newReplyStarted, setNewReplyStarted] = useState(false)
  if (live && replyMessageIndex !== undefined && !newReplyStarted) setNewReplyStarted(true)
  const showNew = newReplyStarted || (newReplyId && indices.some(index => messages[index]?.id === newReplyId))
  const setStep = useCallback((id: string, state?: TraceState) => {
    setSteps(current => {
      if (current.get(id) === state) return current
      const next = new Map(current)
      if (state) next.set(id, state)
      else next.delete(id)
      return next
    })
  }, [])
  const recordAvailable = active && !live
  if (!recordAvailable && recordOpen) setRecordOpen(false)
  const hostMessageId = active && replyMessageIndex !== undefined ? messages[replyMessageIndex]?.id : undefined
  const duration = tail === undefined ? undefined : messages[tail]?.metadata?.custom?.durationS
  const seconds = typeof duration === 'number' && Number.isFinite(duration) && duration >= 0 ? Math.round(duration) : undefined
  const timeAxis = useMemo(() => {
    if (!recordAvailable || projection.spans.size === 0) return
    let start = Infinity
    let end = 0
    for (const span of projection.spans.values()) {
      start = Math.min(start, span.start)
      end = Math.max(end, span.end)
    }
    const question = messages.slice(0, indices[0]).findLast(message => message.role === 'user')
    const sentAt = question?.metadata?.custom?.timelineTimestamp
    if (isTimestamp(sentAt) && sentAt <= start) {
      start = sentAt
      if (typeof duration === 'number' && Number.isFinite(duration)) end = Math.max(end, start + duration)
    }
    return { start, end: Math.max(start + 1, end), spans: projection.spans }
  }, [recordAvailable, projection.spans, messages, indices, duration])
  const columns = timeAxis ? 3 : 2
  const context = useMemo(() => ({ targets, setStep, noteKeys: projection.noteKeys, archived: projection.archived, questions, active, live, progressVisible:showTailTyping, actionMessageId:hostMessageId, timeAxis, traceReady:recordAvailable }), [targets, setStep, projection, questions, active, live, showTailTyping, hostMessageId, timeAxis, recordAvailable])
  const statusSummary = (Object.keys(labels) as TraceState[]).filter(state => counts[state])
    .map(state => counts[state] + '건 ' + labels[state]).join(' · ')

  return <TraceContext.Provider value={context}>
    <Dialog open={recordAvailable && recordOpen} onOpenChange={setRecordOpen}>
      {showNew && <div className="web-new-reply-divider" role="separator" aria-label="새 답변"><span>NEW</span></div>}
      {children}
      {showTailTyping && <ReplyProgress label={progressLabel} />}
      {recordAvailable && !hostMessageId && <div className="web-trace-standalone"><TraceTrigger standalone/></div>}
      {recordAvailable && <DialogContent className="web-trace-dialog" bodyClassName="web-trace-dialog-body" aria-describedby={recordDescriptionId}
        onOpenAutoFocus={event => { event.preventDefault(); titleRef.current?.focus() }}>
        <header className="web-trace-dialog-header">
          <DialogTitle ref={titleRef} tabIndex={-1} className="outline-none">조회·계산 기록</DialogTitle>
          <DialogDescription id={recordDescriptionId}>실행한 도구와 단계별 결과를 확인할 수 있어요.</DialogDescription>
        </header>
        <dl className="web-trace-stats">
          {seconds !== undefined && <div><dt>소요 시간</dt><dd>{seconds >= 60 && `${Math.floor(seconds / 60)}분 `}{seconds % 60}초</dd></div>}
          {projection.stepIds.length > 0 && <div><dt>도구 호출</dt><dd>{projection.stepIds.length}건<span className="sr-only"> · {statusSummary}</span></dd></div>}
          {projection.stepIds.length > 0 && <div><dt>오류</dt><dd>{counts.error}건</dd></div>}
          {isCurrent && <TraceUsage/>}
        </dl>
        <table className="web-trace-steps" data-time-axis={Boolean(timeAxis)} aria-label="실행 단계">
          <colgroup><col className="web-trace-stage-column"/>{timeAxis && <col/>}<col className="web-trace-time-column"/></colgroup>
          <thead><tr><th scope="col">단계</th>{timeAxis && <th scope="col" aria-label="시간 흐름">
            <span className="web-trace-axis-labels"><span>0초</span><span>{Math.floor((timeAxis.end - timeAxis.start) / 2)}초</span><span>{Math.ceil(timeAxis.end - timeAxis.start)}초</span></span>
          </th>}<th scope="col">소요 시간</th></tr></thead>
          {timeAxis && <tbody><tr className="web-trace-overview">
            <th scope="row">전체 실행</th>
            <td><div className="web-trace-time-track" aria-hidden="true">
              {[...timeAxis.spans].map(([id, span]) => <span key={id} className="web-trace-time-bar"
                data-tool-state={steps.get(id)} style={timeBarStyle(timeAxis, span)}/>) }
            </div></td>
            <td className="web-tool-duration">{seconds === undefined ? '—' : `${seconds}초`}</td>
          </tr></tbody>}
          {projection.stepIds.map(id => <StepTarget key={id} id={id} setTarget={setTarget} columns={columns}
            notes={projection.notes.filter(note => note.step === id)}/>)}
          {projection.notes.some(note => !note.step) && <tbody>
            {projection.notes.filter(note => !note.step).map(note => <ArchivedNote key={note.key} note={note} columns={columns}/>)}
          </tbody>}
        </table>
        {timeAxis && <div className="web-trace-legend" aria-label="시간 흐름 범례">
          <span><i/> 기록된 도구 실행</span><span><i/> 상세 기록 없는 구간</span>
        </div>}
      </DialogContent>}
    </Dialog>
    <WaferItemCards messages={messages} indices={indices}/>
  </TraceContext.Provider>
}

function StepTarget({ id, setTarget, notes, columns }: { id: string; setTarget: (id: string, node: HTMLTableSectionElement | null) => void; notes: Note[]; columns: number }) {
  const ref = useCallback((node: HTMLTableSectionElement | null) => setTarget(id, node), [id, setTarget])
  return <>
    <tbody ref={ref}/>
    {notes.length > 0 && <tbody>{notes.map(note => <ArchivedNote key={note.key} note={note} columns={columns}/>)}</tbody>}
  </>
}

function ArchivedNote({ note, columns }: { note: Note; columns: number }) {
  return <tr><td colSpan={columns}><p className="web-trace-note">{note.text}</p></td></tr>
}

// Keep tool disclosure state with its original message while rendering its table rows in the modal.
export function WebTraceStep({ id, state, children, fallback }: PropsWithChildren<{ id: string; state: TraceState; fallback: ReactNode }>) {
  const trace = useContext(TraceContext)
  const setStep = trace?.setStep
  useLayoutEffect(() => {
    setStep?.(id, state)
    return () => setStep?.(id)
  }, [id, state, setStep])
  if (!trace) return fallback
  const target = trace.targets.get(id)
  return target ? createPortal(children, target, id) : null
}

function TraceUsage() {
  const usage = useStore($currentUsage)
  if ((usage.input ?? 0) <= 0 && (usage.output ?? 0) <= 0) return null
  const tokens = [usage.input != null && `입력 ${usage.input}`, usage.output != null && `출력 ${usage.output}`].filter(Boolean)
  return <div aria-label="대화 사용량"><dt>토큰</dt><dd>{tokens.join(' · ')}</dd></div>
}

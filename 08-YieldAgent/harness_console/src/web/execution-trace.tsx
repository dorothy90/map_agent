import { useAuiState, type ThreadMessage } from '@assistant-ui/react'
import { Clock3, ChevronDown, ChevronRight } from 'lucide-react'
import { createContext, useCallback, useContext, useEffect, useId, useLayoutEffect, useMemo, useState, type PropsWithChildren } from 'react'
import { useStore } from '@nanostores/react'
import { $currentUsage, $turnStartedAt } from '@/store/session'
import { createPortal } from 'react-dom'
import { toolEntryDisclosureId } from '@/components/assistant-ui/tool/fallback-model/targets'
import { useWebPresentation } from './presentation'

export type TraceState = 'running' | 'done' | 'error' | 'stopped'
const labels = { running: '실행 중', done: '완료', error: '오류', stopped: '중지됨' }
type Note = { key: string; text: string; step?: string }
type ScopeProps = PropsWithChildren<{ indices: readonly number[] }>
const TraceContext = createContext<{
  targets: Map<string, HTMLDivElement>
  setStep: (id: string, state?: TraceState) => void
  noteKeys: Set<string>
  archived: Set<string>
  active: boolean
  live: boolean
} | null>(null)

export function WebAnswerAuthor() {
  return useWebPresentation() ? <div className="web-answer-author">수율 분석</div> : null
}

export function useWebTraceNotes() {
  return useContext(TraceContext)?.noteKeys
}

export function useWebTraceMessage(id: string) {
  const trace = useContext(TraceContext)
  return trace?.active ? { archived: trace.archived.has(id) } : null
}

export function WebTraceScope({ indices, children }: ScopeProps) {
  return useWebPresentation() ? <ExecutionTrace indices={indices}>{children}</ExecutionTrace> : <>{children}</>
}

// Match only public text parts, using the interim marker and their position relative
// to visible calls. A settled response can contain several tool rounds in one message.
function projectNotes(messages: readonly ThreadMessage[], indices: readonly number[], steps: Map<string, TraceState>, live: boolean) {
  const notes: Note[] = []
  const stepIds: string[] = []
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
        } else traceOnly = false
      } else if (part.type === 'text') {
        if (!part.text.trim()) continue
        if (nextStep || message.metadata.custom?.interim === true || (steps.size > 0 && live && message.status.type === 'running')) {
          notes.push({ key: message.id + '/' + j, text: part.text, step: nextStep })
        } else traceOnly = false
      } else traceOnly = false
    }
    if (traceOnly) archived.add(message.id)
  }
  notes.reverse()
  return { notes, archived, stepIds: stepIds.reverse(), noteKeys: new Set(notes.map(note => note.key)) }
}

function ExecutionTrace({ indices, children }: ScopeProps) {
  const [targets, setTargets] = useState(() => new Map<string, HTMLDivElement>())
  const setTarget = useCallback((id: string, node: HTMLDivElement | null) => {
    setTargets(current => {
      if ((current.get(id) ?? null) === node) return current
      const next = new Map(current)
      if (node) next.set(id, node)
      else next.delete(id)
      return next
    })
  }, [])
  const [steps, setSteps] = useState(() => new Map<string, TraceState>())
  const [override, setOverride] = useState<{ phase: string; open: boolean }>()
  const bodyId = useId()
  const messages = useAuiState(s => s.thread.messages)
  const threadRunning = useAuiState(s => s.thread.isRunning)
  const tail = indices.at(-1)
  const isCurrent = tail === messages.length - 1
  const counts = { running: 0, done: 0, error: 0, stopped: 0 }
  for (const state of steps.values()) counts[state]++
  const live = isCurrent && (threadRunning || counts.running > 0 || messages.at(-1)?.status?.type === 'running')
  const projection = useMemo(() => projectNotes(messages, indices, steps, live), [messages, indices, steps, live])
  const active = steps.size > 0 || projection.notes.length > 0
  const setStep = useCallback((id: string, state?: TraceState) => {
    setSteps(current => {
      if (current.get(id) === state) return current
      const next = new Map(current)
      if (state) next.set(id, state)
      else next.delete(id)
      return next
    })
  }, [])
  const context = useMemo(() => ({ targets, setStep, noteKeys: projection.noteKeys, archived: projection.archived, active, live }), [targets, setStep, projection, active, live])
  const phase = live ? 'running' : 'settled'
  const open = override?.phase === phase ? override.open : live
  const summary = (Object.keys(labels) as TraceState[]).filter(state => counts[state])
    .map(state => counts[state] + '건 ' + labels[state]).join(' · ')
  const latest = projection.notes.at(-1)
  const duration = tail === undefined ? undefined : messages[tail]?.metadata?.custom?.durationS
  const seconds = !live && typeof duration === 'number' && Number.isFinite(duration) && duration >= 0
    ? Math.round(duration) : undefined

  return <TraceContext.Provider value={context}>
    <section className={active ? 'web-execution-trace' : undefined} hidden={!active} aria-label="조회·계산 기록">
      <button type="button" className="web-trace-summary" aria-controls={bodyId} aria-expanded={open}
        onClick={() => setOverride({ phase, open: !open })}>
        <Clock3 size={14} aria-hidden="true"/><strong>조회·계산 기록</strong>
        {seconds !== undefined && <span>{seconds >= 60 && `${Math.floor(seconds / 60)}분 `}{seconds % 60}초</span>}
        <span role="status">{summary || (live ? '실행 중' : '완료')}</span>
        {open ? <ChevronDown size={14}/> : <ChevronRight size={14}/>}
      </button>
      <div className="web-trace-steps" hidden={!open} id={bodyId}>
        {open && isCurrent && <UsageClock/>}
        {live && <div className="web-trace-progress"><WebAnswerAuthor/>
          {latest && <p className="web-trace-live-note">{latest.text}</p>}
        </div>}
        {projection.stepIds.map(id => <div key={id}>
          <StepTarget id={id} setTarget={setTarget}/>
          {!live && projection.notes.filter(note => note.step === id).map(note => <ArchivedNote key={note.key} note={note}/>)}
        </div>)}
        {!live && projection.notes.filter(note => !note.step).map(note => <ArchivedNote key={note.key} note={note}/>)}
      </div>
    </section>
    {children}
  </TraceContext.Provider>
}

function StepTarget({ id, setTarget }: { id: string; setTarget: (id: string, node: HTMLDivElement | null) => void }) {
  const ref = useCallback((node: HTMLDivElement | null) => setTarget(id, node), [id, setTarget])
  return <div ref={ref}/>
}

function ArchivedNote({ note }: { note: Note }) {
  return <p className="web-trace-note">{note.text}</p>
}

// Keep each tool's runtime and controls when moving its row into the shared disclosure.
export function WebTraceStep({ id, state, children }: PropsWithChildren<{ id: string; state: TraceState }>) {
  const trace = useContext(TraceContext)
  const setStep = trace?.setStep
  useLayoutEffect(() => {
    setStep?.(id, state)
    return () => setStep?.(id)
  }, [id, state, setStep])
  if (!trace) return <>{children}</>
  const target = trace.targets.get(id)
  return target ? createPortal(children, target, id) : null
}

function UsageClock() {
  const usage = useStore($currentUsage)
  const started = useStore($turnStartedAt)
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!started) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [started])

  const parts: string[] = []
  if ((usage.input ?? 0) > 0 || (usage.output ?? 0) > 0) {
    const tokens = [usage.input != null && `입력 ${usage.input}`, usage.output != null && `출력 ${usage.output}`].filter(Boolean)
    parts.push(`대화 사용량 · ${tokens.join(' · ')} 토큰`)
  }
  if (started) parts.push(`실행 시간 ${Math.max(0, Math.floor((now - started) / 1000))}초`)
  return parts.length ? <small aria-label="사용량과 실행 시간">{parts.join(' · ')}</small> : null
}

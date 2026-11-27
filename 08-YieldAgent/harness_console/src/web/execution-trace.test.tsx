import type { ThreadMessage } from '@assistant-ui/react'
import { act, cleanup, fireEvent, isInaccessible, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { Thread } from '@/components/assistant-ui/thread'
import {
  assistantMessage, stubThreadEnvironment, stubThreadViewportSize, ThreadRuntime, userMessage
} from '@/components/assistant-ui/test-utils'
import { $currentUsage } from '@/store/session'
import { setShowToolActivityFromConfig } from '@/store/tool-activity'
import { WebPresentationProvider } from './presentation'

type Assistant = Extract<ThreadMessage, { role: 'assistant' }>

function reply(id: string, content: Assistant['content'], running = false): Assistant {
  const base = assistantMessage()
  if (base.role !== 'assistant') throw new Error('Expected assistant fixture')
  return { ...base, id, content, status: running ? { type: 'running' } : { type: 'complete', reason: 'stop' } }
}

function call(id: string, toolName: string, result?: string | Record<string, unknown>) {
  return { type: 'tool-call' as const, toolCallId: id, toolName, argsText: '{}', args: {}, result }
}

function chat(messages: ThreadMessage[], newReplyId?: string) {
  return <WebPresentationProvider newReplyId={newReplyId}><ThreadRuntime messages={messages}><Thread /></ThreadRuntime></WebPresentationProvider>
}

it('numbers sequential clarification calls within the same reply', async () => {
  const answer = (id: string, question: string) => reply(id, [
    call(id, 'clarify', { responses: [{ question, user_response: '확인' }] })
  ])
  const { container } = render(chat([
    userMessage(), answer('clarify-first', '어떤 맵을 그릴까요?'), answer('clarify-second', '기준 파일이 있나요?')
  ]))
  await waitFor(() => {
    expect([...container.querySelectorAll('.web-clarify-eyebrow')].map(label => label.textContent))
      .toEqual(['질문 1', '질문 2'])
  })
})

it('keeps question numbers stable when another batch arrives', async () => {
  const question = userMessage()
  const firstBatch = reply('clarify-first', [call('first', 'clarify', {
    responses: ['맵 종류', '조회 기준'].map(question => ({ question, user_response: '확인' }))
  })])
  const nextBatch = {
    ...call('second', 'clarify'),
    args: { questions: [{ question: '제품명' }, { question: 'LOT 번호' }] }
  }
  const { container, rerender } = render(chat([question, firstBatch]))
  await within(container).findByText('질문 2')
  rerender(chat([question, firstBatch, reply('clarify-second', [nextBatch], true)]))
  await within(container).findByText('질문 1')
  await within(container).findByText('질문 2')
  await within(container).findByText('질문 3')
})

beforeEach(() => {
  stubThreadEnvironment()
  stubThreadViewportSize()
  setShowToolActivityFromConfig(true)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('shows the progress mascot while preparing and again during tool activity', async () => {
  const question = userMessage()
  const pending = reply('first', [], true)
  const { container, rerender } = render(chat([question, pending]))
  const bubble = await within(container).findByRole('status', { name: '생각을 정리하는 중' })
  expect(bubble.querySelector('img')?.getAttribute('src')).toBe('/y-agent-reply-progress.png')
  expect(bubble.textContent).toBe('생각을 정리하는 중…')

  rerender(chat([question, reply('first', [{ type: 'text', text: '답변입니다.' }], true)]))
  await waitFor(() => expect(container.textContent).toContain('답변입니다.'))
  expect(container.querySelector('.web-reply-progress')).toBeNull()

  rerender(chat([question, reply('first', [call('lookup', 'query_db', '1 row')]), reply('next', [], true)]))
  await within(container).findByRole('status', { name: '생각을 정리하는 중' })
  rerender(chat([question, reply('first', [call('lookup', 'query_db')], true)]))
  await within(container).findByRole('status', { name: '데이터 조회 중' })
})

it('marks only a new answer and places the execution record after the answer', async () => {
  const previous = reply('old', [{ type: 'text', text: '이전 답변' }])
  const current = reply('new', [call('records', 'query_db', '1 row'), { type: 'text', text: '새로운 분석 답변' }])
  const { container } = render(chat([userMessage(), previous, {...userMessage(),id:'new-question'}, current], 'new'))
  const scope = within(container)
  const divider = scope.getByRole('separator', { name: '새 답변' })
  const answer = scope.getByText('새로운 분석 답변')
  const trace = await scope.findByRole('button', { name: /조회·계산 기록/ })
  expect(divider.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(answer.compareDocumentPosition(trace) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(trace.closest('[data-slot=aui_msg-actions]')).not.toBeNull()
  expect(scope.getAllByRole('separator', { name: '새 답변' })).toHaveLength(1)
})

it('shows incoming answer text outside the closed execution record after a tool call', async () => {
  const question = userMessage()
  const tool = reply('lookup', [call('rows', 'query_db', '1 row')])
  const { container, rerender } = render(chat([question, tool, reply('answer', [], true)]))
  await within(container).findByRole('status', { name: '생각을 정리하는 중' })
  expect(within(container).queryByRole('button', { name: '조회·계산 기록' })).toBeNull()
  rerender(chat([question, tool, reply('answer', [{ type: 'text', text: '조회된 수율은' }], true)]))
  const draft = await within(container).findByText('조회된 수율은')
  expect(isInaccessible(draft)).toBe(false)
  expect(draft.closest('.web-trace-dialog')).toBeNull()
  expect(container.querySelector('.web-reply-progress')).toBeNull()
  const marker = within(container).getByRole('separator', { name: '새 답변' })
  const slot = draft.closest('[data-answer-id]')?.querySelector('.web-trace-trigger')
  expect(slot?.tagName).toBe('SPAN')
  rerender(chat([question, tool, reply('answer', [{ type: 'text', text: '조회된 수율은 71%입니다.' }])], 'answer'))
  expect(within(container).getByRole('separator', { name: '새 답변' })).toBe(marker)
  await within(container).findByRole('button', { name: '조회·계산 기록' })
})

it('shows progress during tool calls and opens the finished record from the answer actions', async () => {
  const question = userMessage()
  const first = reply('first', [call('read', 'read_result', '50 rows')])
  const calculating = reply('second', [call('compute', 'run_python')], true)
  const { container, rerender } = render(chat([question, first, calculating]))

  await within(container).findByRole('status', { name: 'Python 실행 중' })
  expect(screen.queryByRole('button', { name: '조회·계산 기록' })).toBeNull()
  expect(screen.queryByRole('dialog')).toBeNull()

  const completed = reply('second', [
    { ...call('compute', 'run_python', { duration_s: 1.4 }), args: { output: 'report' } },
    call('lookup', 'query_db', '1 row'),
    { type: 'text', text: '수율을 확인했습니다.' }
  ])
  rerender(chat([question, first, { ...completed, metadata: { ...completed.metadata, custom: { durationS: 159 } } }]))
  const trigger = await screen.findByRole('button', { name: '조회·계산 기록' })
  expect(trigger.closest('[data-slot=aui_msg-actions]')).not.toBeNull()
  expect(screen.queryByRole('dialog')).toBeNull()
  fireEvent.click(trigger)
  const dialog = await screen.findByRole('dialog', { name: '조회·계산 기록' })
  expect(dialog.textContent).toContain('3건 완료')
  expect(dialog.textContent).toContain('2분 39초')
  const step = await within(dialog).findByRole('button', { name: /run_python/ })
  expect(step.getAttribute('aria-expanded')).toBe('false')
  fireEvent.click(step)
  expect(step.getAttribute('aria-expanded')).toBe('true')
  fireEvent.keyDown(dialog, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
})

it('plots overlapping recorded spans on one time axis and leaves missing spans empty', async () => {
  const question = userMessage()
  if (question.role !== 'user') throw new Error('Expected user fixture')
  const timedQuestion = { ...question, metadata: { custom: { timelineTimestamp: 100 } } }
  const message = reply('timeline', [
    { ...call('lookup', 'query_db', { duration_s: 4 }), timestamp: 102, completedAt: 106 } as Assistant['content'][number],
    { ...call('compute', 'run_python', { duration_s: 3 }), timestamp: 104, completedAt: 107 } as Assistant['content'][number],
    { ...call('short', 'render_yield_table', { duration_s: 0.076 }), timestamp: 106, completedAt: 107 } as Assistant['content'][number],
    call('missing', 'list_tables', 'rows'),
    { type: 'text', text: '집계가 끝났습니다.' }
  ])
  render(chat([timedQuestion, message]))
  fireEvent.click(await screen.findByRole('button', { name: '조회·계산 기록' }))
  const table = within(await screen.findByRole('dialog')).getByRole('table', { name: '실행 단계' })
  expect(within(table).getByRole('columnheader', { name: '시간 흐름' }).textContent).toBe('0초3초7초')
  expect(within(table).getByRole('img', { name: '시작 2.0초, 종료 6.0초' })).toBeTruthy()
  expect(within(table).getByRole('img', { name: '시작 4.0초, 종료 7.0초' })).toBeTruthy()
  const short = within(table).getByRole('img', { name: '시작 6.924초, 종료 7.000초' })
  expect(short.getAttribute('title')).toBe('시작 6.924초, 종료 7.000초')
  expect(within(table).getAllByRole('img')).toHaveLength(3)
})

it('keeps tool-only records reachable and scopes errors and usage to their turn', async () => {
  const previous = [userMessage(), reply('history', [call('old', 'query_db', '1 row')])]
  const current = [
    userMessage('follow-up', '다른 날짜도 확인해줘.'),
    reply('interrupted', [
      call('failed', 'query_db', { status: 'error', error: { message: '조회 실패' } }),
      call('stopped', 'run_python')
    ])
  ]
  render(chat([...previous, ...current]))
  const [first, second] = await waitFor(() => {
    const buttons = screen.getAllByRole('button', { name: '조회·계산 기록' })
    expect(buttons).toHaveLength(2)
    return buttons
  })
  const originalUsage = $currentUsage.get()
  try {
    act(() => { $currentUsage.set({ input: 12, output: 3, total: 15, calls: 1 }) })
    fireEvent.click(first!)
    let dialog = screen.getByRole('dialog')
    expect(dialog.textContent).toContain('1건 완료')
    expect(within(dialog).queryByLabelText('대화 사용량')).toBeNull()
    fireEvent.keyDown(dialog, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    fireEvent.click(second!)
    dialog = screen.getByRole('dialog')
    expect(dialog.textContent).toContain('1건 오류')
    expect(dialog.textContent).toContain('1건 중지됨')
    expect(dialog.textContent).not.toContain('2건 완료')
    expect(within(dialog).getByLabelText('대화 사용량').textContent).toContain('입력 12 · 출력 3')
  } finally {
    act(() => { $currentUsage.set(originalUsage) })
  }
})

it('preserves tool commentary in order without showing it as answer prose', async () => {
  const earlier = '제품의 측정 범위를 확인하겠습니다.'
  const latest = '같은 기간의 개별 측정값을 비교하겠습니다.'
  const answer = '선택한 웨이퍼의 수율은 71.0%입니다.'
  const question = userMessage()
  const initial = reply('source', [
    { type: 'text', text: earlier }, call('source-call', 'query_db', '50 rows')
  ])
  const second = reply('comparison', [
    { type: 'text', text: latest }, call('calc', 'run_python')
  ], true)
  const { container, rerender } = render(chat([question, initial, second]))
  await within(container).findByRole('status', { name: 'Python 실행 중' })
  expect(screen.queryByText(latest)).toBeNull()

  const settled = reply('comparison', [
    { type: 'text', text: latest }, call('calc', 'run_python', '0.71'), { type: 'text', text: answer }
  ])
  rerender(chat([question, initial, settled]))
  const summary = await screen.findByRole('button', { name: '조회·계산 기록' })
  expect(screen.getByText(answer)).toBeTruthy()
  expect(screen.queryByText(earlier)).toBeNull()
  fireEvent.click(summary)
  const dialog = await screen.findByRole('dialog')
  await waitFor(() => expect([...dialog.querySelectorAll('.web-trace-step, .web-trace-note')]
    .map(row => row.querySelector('code')?.textContent ?? row.textContent))
    .toEqual(['query_db', earlier, 'run_python', latest]))
  expect(dialog.textContent).not.toContain(answer)
})

it('opens only the selected historical turn’s record alongside a following plain answer', async () => {
  const note = '원본 웨이퍼 값을 조회합니다.'
  const final = '조회한 웨이퍼는 50장입니다.'
  const plain = '다음 질문도 이어서 할 수 있습니다.'
  render(chat([
    userMessage('first-turn'),
    reply('hydrated', [
      { type: 'text', text: note }, call('source', 'query_db', '50 rows'), { type: 'text', text: final }
    ]),
    userMessage('second-turn', '추가 질문도 가능한가요?'),
    reply('plain', [{ type: 'text', text: plain }])
  ]))
  const trigger = await screen.findByRole('button', { name: '조회·계산 기록' })
  expect(screen.getByText(final)).toBeTruthy()
  expect(screen.getByText(plain)).toBeTruthy()
  expect(screen.queryByText(note)).toBeNull()
  fireEvent.click(trigger)
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText(note)).toBeTruthy()
  expect(dialog.querySelectorAll('.web-tool-row')).toHaveLength(1)
})

it('shows only confirmed saved wafer membership and keeps earlier turn counts on replay', async () => {
  const groups = [{ lot_id: 'A', wf_ids: [1, 2] }]
  const saved = (members: typeof groups, added: typeof groups, count: number) => ({ status: 'success',
    wafer_item: { id: 't1', title: 'Edge', wafer_groups: members, added_wafer_groups: added, wafer_count: count } })
  const next = [{ lot_id: 'B', wf_ids: [1] }]
  const { container } = render(chat([
    userMessage(), reply('create', [call('a', 'kanban_create', saved(groups, groups, 2)), { type: 'text', text: '등록했어요.' }]),
    userMessage('more', '추가해줘'), reply('append', [call('b', 'kanban_comment', saved([...groups, ...next], next, 3)),
      call('c', 'kanban_show', saved([...groups, ...next], next, 3)), { type: 'text', text: '추가했어요.' }]),
  ]))
  const cards = await within(container).findAllByRole('group', { name: 'Edge' })
  expect(cards).toHaveLength(2)
  expect(cards[0].textContent).toContain('총 2장')
  expect(cards[1].textContent).toContain('기존 2장 + 추가 1장')
  expect(cards[1].textContent).toContain('총 3장')
  expect(within(cards[1]).getByText('B')).toBeTruthy()
})

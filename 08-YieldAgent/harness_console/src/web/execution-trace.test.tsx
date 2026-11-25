import type { ThreadMessage } from '@assistant-ui/react'
import { act, cleanup, fireEvent, isInaccessible, render, waitFor, within } from '@testing-library/react'
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

function chat(messages: ThreadMessage[]) {
  return <WebPresentationProvider><ThreadRuntime messages={messages}><Thread /></ThreadRuntime></WebPresentationProvider>
}

beforeEach(() => {
  stubThreadEnvironment()
  stubThreadViewportSize()
  setShowToolActivityFromConfig(true)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('shows the typing bubble only until the first visible text or tool step', async () => {
  const question = userMessage()
  const pending = reply('first', [], true)
  const { container, rerender } = render(chat([question, pending]))
  const bubble = await within(container).findByRole('status', { name: '응답 준비 중' })
  expect(bubble.querySelectorAll('.web-typing-bubble i')).toHaveLength(3)
  expect(bubble.textContent).toBe('')

  rerender(chat([question, reply('first', [{ type: 'text', text: '답변입니다.' }], true)]))
  await waitFor(() => expect(container.textContent).toContain('답변입니다.'))
  expect(container.querySelector('.web-typing-bubble')).toBeNull()

  rerender(chat([question, reply('first', [call('lookup', 'query_db', '1 row')]), reply('next', [], true)]))
  await within(container).findByRole('button', { name: /query_db/ })
  expect(container.querySelector('.web-typing-bubble')).toBeNull()
})

it('follows the active call across commentary and folds all steps into one record when the turn finishes', async () => {
  const question = userMessage()
  const first = reply('first', [
    call('read', 'read_result', '50 rows'),
    { type: 'text', text: '측정값을 확인했습니다.' }
  ])
  const calculating = reply('second', [call('compute', 'run_python')], true)
  const history = [question, first]
  const { container, rerender } = render(chat([question, reply('first', [call('read', 'read_result')], true)]))

  const group = await waitFor(() => {
    const groups = container.querySelectorAll<HTMLElement>('.web-execution-trace')
    expect(groups).toHaveLength(1)
    return groups[0]!
  })
  const summary = () => group.querySelector<HTMLButtonElement>('button[aria-expanded]')!
  expect(summary().getAttribute('aria-expanded')).toBe('true')
  expect(within(group).getByRole('button', { name: /read_result/ }).getAttribute('aria-expanded')).toBe('true')

  rerender(chat([...history, calculating]))
  const running = await within(group).findByRole('button', { name: /run_python/ })
  expect(running.getAttribute('aria-expanded')).toBe('true')
  expect(within(group).getByRole('button', { name: /read_result/ }).getAttribute('aria-expanded')).toBe('false')

  const parallel = reply('second', [call('compute', 'run_python'), call('lookup', 'query_db')], true)
  rerender(chat([...history, parallel]))
  const concurrent = await within(group).findByRole('button', { name: /query_db/ })
  expect(concurrent.getAttribute('aria-expanded')).toBe('true')
  expect(within(group).getByRole('button', { name: /run_python/ }).getAttribute('aria-expanded')).toBe('true')

  const completed = reply('second', [
    call('compute', 'run_python', 'correct result'),
    call('lookup', 'query_db', '1 row'),
    { type: 'text', text: '수율을 확인했습니다.' }
  ])
  rerender(chat([...history, { ...completed, metadata: { ...completed.metadata, custom: { durationS: 159 } } }]))
  await waitFor(() => expect(summary().getAttribute('aria-expanded')).toBe('false'))
  expect(summary().textContent).toContain('3건 완료')
  expect(summary().textContent).toContain('2분 39초')
  expect(within(group).queryByRole('button', { name: /run_python/ })).toBeNull()
  expect(within(container).getByText('수율을 확인했습니다.')).toBeTruthy()

  fireEvent.click(summary())
  expect(within(group).getByRole('button', { name: /run_python/ }).getAttribute('aria-expanded')).toBe('false')
})

it('keeps a previous turn collapsed and reports an error and an interrupted step without counting either as successful', async () => {
  const previous = [userMessage(), reply('history', [call('old', 'query_db', '1 row')])]
  const current = [
    userMessage('follow-up', '다른 날짜도 확인해줘.'),
    reply('interrupted', [
      call('failed', 'query_db', { status: 'error', error: { message: '조회 실패' } }),
      call('stopped', 'run_python')
    ])
  ]
  const { container } = render(chat([...previous, ...current]))

  const groups = await waitFor(() => {
    const traces = container.querySelectorAll<HTMLElement>('.web-execution-trace')
    expect(traces).toHaveLength(2)
    return traces
  })
  const first = groups[0]!.querySelector('button')!
  const second = groups[1]!.querySelector('button')!
  expect(first.textContent).toContain('1건 완료')
  expect(first.getAttribute('aria-expanded')).toBe('false')
  expect(second.textContent).toContain('오류')
  expect(second.textContent).toContain('중지')
  expect(second.textContent).not.toContain('2건 완료')
  expect(second.getAttribute('aria-expanded')).toBe('false')
  const originalUsage = $currentUsage.get()
  try {
    act(() => { $currentUsage.set({ input: 12, output: 3, total: 15, calls: 1 }) })
    fireEvent.click(first)
    expect(within(groups[0]!).queryByLabelText('사용량과 실행 시간')).toBeNull()
    expect(within(groups[1]!).queryByLabelText('사용량과 실행 시간')).toBeNull()
    fireEvent.click(second)
    expect(within(groups[1]!).getByLabelText('사용량과 실행 시간').textContent).toContain('입력 12 · 출력 3 토큰')
  } finally {
    act(() => { $currentUsage.set(originalUsage) })
  }
})


function visible(root: HTMLElement, selector: string): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(selector)].filter(element => !isInaccessible(element))
}

it('shows one live author with the latest commentary and preserves the commentary in order when reopened', async () => {
  const earlier = '제품의 측정 범위를 확인하겠습니다.'
  const latest = '같은 기간의 개별 측정값을 비교하겠습니다.'
  const answer = '선택한 웨이퍼의 수율은 71.0%입니다.'
  const question = userMessage()
  const initial = reply('source', [
    { type: 'text', text: earlier },
    call('source-call', 'query_db', '50 rows')
  ])
  const second = reply('comparison', [
    { type: 'text', text: latest }, call('calc', 'run_python')
  ], true)

  const { container, rerender } = render(chat([question, initial, second]))
  const group = await waitFor(() => {
    const groups = visible(container, '.web-execution-trace')
    expect(groups).toHaveLength(1)
    return groups[0]!
  })
  await waitFor(() => expect(visible(group, '.web-trace-live-note').map(note => note.textContent)).toEqual([latest]))
  expect(visible(container, '.web-answer-author')).toEqual(visible(group, '.web-answer-author'))
  expect(visible(group, '.web-answer-author')).toHaveLength(1)
  expect(visible(group, '.web-trace-note')).toHaveLength(0)
  const duplicateNotes = [earlier, latest].flatMap(text => within(container).queryAllByText(text))
    .filter(node => !group.contains(node) && !isInaccessible(node))
  expect(duplicateNotes).toHaveLength(0)

  const settled = reply('comparison', [
    { type: 'text', text: latest },
    call('calc', 'run_python', '0.71'),
    { type: 'text', text: answer }
  ])
  rerender(chat([question, initial, settled]))
  const summary = within(group).getByRole('button', { name: /조회·계산 기록/ })
  await waitFor(() => expect(summary.getAttribute('aria-expanded')).toBe('false'))
  expect(visible(container, '.web-answer-author, .web-trace-live-note, .web-trace-note')).toHaveLength(0)
  const finalAnswer = within(container).getByText(answer)
  expect(isInaccessible(finalAnswer)).toBe(false)
  expect(group.contains(finalAnswer)).toBe(false)
  fireEvent.click(summary)
  expect(visible(group, '.web-trace-note').map(note => note.textContent)).toEqual([earlier, latest])
  expect(visible(group, '.web-tool-row, .web-trace-note').map(row => row.querySelector('code')?.textContent ?? row.textContent))
    .toEqual(['query_db', earlier, 'run_python', latest])
  expect(within(container).getAllByText(answer)).toHaveLength(1)
})

it('restores the pre-tool commentary to its own turn and keeps a following plain answer outside the record', async () => {
  const note = '원본 웨이퍼 값을 조회합니다.'
  const final = '조회한 웨이퍼는 50장입니다.'
  const plain = '다음 질문도 이어서 할 수 있습니다.'
  const records = [
    userMessage('first-turn'),
    reply('hydrated', [
      { type: 'text', text: note }, call('source', 'query_db', '50 rows'), { type: 'text', text: final }
    ]),
    userMessage('second-turn', '추가 질문도 가능한가요?'),
    reply('plain', [{ type: 'text', text: plain }])
  ]
  const { container } = render(chat(records))
  const group = await waitFor(() => {
    const groups = visible(container, '.web-execution-trace')
    expect(groups).toHaveLength(1)
    return groups[0]!
  })
  const summary = within(group).getByRole('button', { name: /조회·계산 기록/ })
  expect(summary.getAttribute('aria-expanded')).toBe('false')
  expect(visible(group, '.web-trace-note, .web-trace-live-note, .web-answer-author')).toHaveLength(0)
  for (const text of [final, plain]) {
    const answer = within(container).getByText(text)
    expect(group.contains(answer)).toBe(false)
    expect(isInaccessible(answer)).toBe(false)
  }
  fireEvent.click(summary)
  expect(visible(group, '.web-trace-note').map(row => row.textContent)).toEqual([note])
  expect(visible(group, '.web-tool-row')).toHaveLength(1)
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

import { act, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { chatMessageText } from '@/lib/chat-messages'

import { renderMessageStream } from './test-harness'

const SID = 'live-yield-turn'
const OPENING = '4SS 최근 4주 주별 수율 요약입니다.'
const CLOSING = '이상으로 최근 4주 주별 수율을 정리했습니다.'
const ANSWER = `${OPENING}\n\n46주: 98.1%\n47주: 97.6%\n48주: 97.9%\n49주: 98.0%\n\n${CLOSING}`

afterEach(() => {
  cleanup()
})

function count(haystack: string, needle: string) {
  return haystack.split(needle).length - 1
}

function visibleAssistantText(stream: ReturnType<typeof renderMessageStream>) {
  return stream.state().messages
    .filter(message => message.role === 'assistant' && !message.hidden)
    .map(message => chatMessageText(message))
    .join('\n')
}

describe('message.complete replaces the streamed draft', () => {
  it('does not append the final after a later verify tool in the same bubble', () => {
    const stream = renderMessageStream(SID)
    const send = (type: string, payload: Record<string, unknown> = {}) =>
      act(() => stream.handleEvent({ type, payload, session_id: SID } as never))

    send('message.start')
    send('tool.start', { name: 'query_db', tool_id: 'q1' })
    send('tool.complete', { name: 'query_db', tool_id: 'q1', result: 'ok' })
    send('message.delta', { text: ANSWER })
    send('tool.start', { name: 'verify_answer', tool_id: 'v1' })
    send('tool.complete', { name: 'verify_answer', tool_id: 'v1', result: 'pass' })
    send('message.complete', { text: ANSWER })

    const text = visibleAssistantText(stream)
    expect(count(text, OPENING)).toBe(1)
    expect(count(text, CLOSING)).toBe(1)
    expect(stream.state().messages.filter(message => message.role === 'assistant' && !message.hidden)).toHaveLength(1)
  })

  it('replaces a sealed already-streamed interim instead of appending a second copy', () => {
    const stream = renderMessageStream(SID)
    const send = (type: string, payload: Record<string, unknown> = {}) =>
      act(() => stream.handleEvent({ type, payload, session_id: SID } as never))

    send('message.start')
    send('tool.start', { name: 'query_db', tool_id: 'q1' })
    send('tool.complete', { name: 'query_db', tool_id: 'q1', result: 'ok' })
    send('message.delta', { text: ANSWER })
    send('message.interim', { text: ANSWER, already_streamed: true })
    send('tool.start', { name: 'verify_answer', tool_id: 'v1' })
    send('tool.complete', { name: 'verify_answer', tool_id: 'v1', result: 'pass' })
    send('message.complete', { text: ANSWER })

    const text = visibleAssistantText(stream)
    expect(count(text, OPENING)).toBe(1)
    expect(count(text, CLOSING)).toBe(1)
    expect(stream.state().messages.filter(message => message.role === 'assistant' && !message.hidden)).toHaveLength(1)
  })
})

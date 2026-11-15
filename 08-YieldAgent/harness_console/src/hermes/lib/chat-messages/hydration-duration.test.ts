import { describe, expect, it } from 'vitest'
import { toRuntimeMessage, coalesceToolOnlyAssistants, createToolMergeCache } from '../chat-runtime'
import { toChatMessages } from './hydration'
import type { SessionMessage } from '../../types/hermes'

describe('toChatMessages duration from history', () => {
  it('copies duration_s onto a hydrated assistant message', () => {
    const messages = [
      { role: 'user', content: 'summarize yield', text: 'summarize yield' },
      {
        role: 'assistant',
        content: 'weekly yield summary',
        text: 'weekly yield summary',
        duration_s: 46,
        usage: { input: 98714, output: 6599, total: 105313, calls: 6 }
      }
    ] as SessionMessage[]

    const result = toChatMessages(messages)
    const assistant = result.find(message => message.role === 'assistant')
    expect(assistant?.durationS).toBe(46)
  })

  it('leaves durationS absent when history omitted duration_s', () => {
    const messages = [
      { role: 'assistant', content: 'weekly yield summary', text: 'weekly yield summary' }
    ] as SessionMessage[]
    const result = toChatMessages(messages)
    expect(result[0]?.durationS).toBeUndefined()
  })
})

it('preserves final duration through tool history folding and runtime conversion', () => {
  const result = toChatMessages([
    { role: 'user', content: 'yield report' },
    { role: 'assistant', content: 'Checking yield', tool_calls: [{ id: 'call-1', type: 'function', function: { name: 'query', arguments: '{}' } }] },
    { role: 'tool', content: 'rows', tool_call_id: 'call-1', name: 'query' },
    { role: 'assistant', content: 'report', duration_s: 45 },
    { role: 'assistant', content: 'finished' }
  ] as SessionMessage[])
  const assistant = result.find(message => message.role === 'assistant')!
  expect(assistant.durationS).toBe(45)
  const merged = coalesceToolOnlyAssistants([...result, {
    id: 'trailing-tools', role: 'assistant',
    parts: assistant.parts.filter(part => part.type === 'tool-call')
  }], createToolMergeCache())
  expect(merged).toHaveLength(result.length)
  expect(toRuntimeMessage(merged.find(message => message.role === 'assistant')!).metadata.custom.durationS).toBe(45)
})

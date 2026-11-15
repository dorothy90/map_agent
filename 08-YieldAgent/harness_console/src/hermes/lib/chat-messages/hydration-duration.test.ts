import { describe, expect, it } from 'vitest'
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

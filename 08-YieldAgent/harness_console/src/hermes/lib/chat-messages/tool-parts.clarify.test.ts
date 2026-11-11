import { describe, expect, it } from 'vitest'

import { restorePendingClarifyToolCall } from './tool-parts'
import type { ChatMessage } from './types'

describe('canonical per-question clarify projection', () => {
  it('keeps the provider batch row and appends one row per distinct prompt id', () => {
    const providerArgs = {
      questions: [{ question: 'Choose an option' }, { question: 'Choose an option' }]
    }
    const messages: ChatMessage[] = [
      {
        id: 'assistant-1',
        pending: true,
        role: 'assistant',
        parts: [
          {
            args: providerArgs,
            argsText: JSON.stringify(providerArgs),
            toolCallId: 'provider-batch',
            toolName: 'clarify',
            type: 'tool-call'
          }
        ]
      }
    ]

    const q1 = restorePendingClarifyToolCall(messages, {
      args: { questions: [{ question: 'Choose an option' }] },
      tool_id: 'srq-1:q1'
    })
    const q2 = restorePendingClarifyToolCall(q1.messages, {
      args: { questions: [{ question: 'Choose an option' }] },
      tool_id: 'srq-1:q2'
    })
    const parts = q2.messages[0].parts.filter(part => part.type === 'tool-call')

    expect(parts.map(part => part.toolCallId)).toEqual(['provider-batch', 'srq-1:q1', 'srq-1:q2'])
    expect(parts[0]).toMatchObject({ args: providerArgs, toolCallId: 'provider-batch' })
  })
})

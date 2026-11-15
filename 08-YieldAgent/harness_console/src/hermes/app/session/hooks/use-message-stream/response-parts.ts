import { type ChatMessagePart, mergeFinalAssistantText, normalizeWs, partsText } from '@/lib/chat-messages'

/** One live bubble may hold several model responses when an equal interim
 * callback is suppressed. The last tool round, not the bubble, bounds the
 * response that an interim/final frame is allowed to replace. */
export function currentResponseParts(parts: ChatMessagePart[]): ChatMessagePart[] {
  return parts.slice(parts.findLastIndex(part => part.type === 'tool-call') + 1)
}

export function mergeCurrentResponseText(parts: ChatMessagePart[], text: string, timestamp: number): ChatMessagePart[] {
  const boundary = parts.findLastIndex(part => part.type === 'tool-call') + 1
  const earlierText = partsText(parts.slice(0, boundary))
  const earlierNorm = normalizeWs(earlierText)
  const finalNorm = normalizeWs(text)

  // A later tool (e.g. verify_answer) can sit after the streamed draft. The
  // verified complete must replace that draft, not append it after the tool.
  if (
    earlierNorm &&
    finalNorm &&
    (earlierNorm === finalNorm || finalNorm.startsWith(earlierNorm) || earlierNorm.startsWith(finalNorm))
  ) {
    return mergeFinalAssistantText(parts, text, timestamp)
  }

  return [...parts.slice(0, boundary), ...mergeFinalAssistantText(parts.slice(boundary), text, timestamp)]
}

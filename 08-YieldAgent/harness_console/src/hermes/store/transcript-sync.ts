// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.

export type TranscriptChangedPayload = {
  /** Stored session id shared across Desktop windows. */
  sessionId: string
  /** Chat-message length after the completed turn, when the sender has one. */
  messageCount?: number
}
export function broadcastTranscriptChanged(payload: TranscriptChangedPayload): void {}
export function onTranscriptChanged(handler: (payload: TranscriptChangedPayload) => void): () => void { return () => {} }

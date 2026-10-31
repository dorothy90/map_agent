// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import type { OwnerScope } from '@/api/client'
import { type VoicePlaybackSource } from '@/store/voice-playback'
export interface VoicePlaybackOptions extends OwnerScope {
  messageId?: string | null
  /** Skip the client-direct/stream rungs and POST straight to /api/audio/speak.
   *  For callers whose stream path (client-direct, else WS relay) already
   *  answered `fallback` this reply; the relay may not have been probed. */
  syncOnly?: boolean
  source: VoicePlaybackSource
  /** Stable across a live-id rewrite. A second start of this turn must not stop the first. */
  turnKey?: string
}
export function stopVoicePlayback() {}
export async function resolveSpeakStreamUrl(owner?: OwnerScope): Promise<null | string> { return null }
export interface SpeechStreamSession {
  /** Feed more reply text as it streams in. Safe after `finish` (no-op). */
  append: (text: string) => void
  /** Release a sealed bubble's tail without ending the turn (client-direct). */
  flush?: () => void
  /** No more text coming — resolves `done` once the audio drains. */
  finish: () => void
  /**
   * 'done'    — audio fully played (or barged via stopVoicePlayback)
   * 'fallback'— no audio ever produced; caller should speak the accumulated
   *             text through `playSpeechText` instead.
   */
  done: Promise<'done' | 'fallback'>
}
export async function startSpeechStream(options: VoicePlaybackOptions): Promise<null | SpeechStreamSession> { return null }
export async function playSpeechText(text: string, options: VoicePlaybackOptions): Promise<boolean> { return false }
export function isVoicePlaybackActive() { return false }
const INTERRUPT_TTL_MS = 120_000
let interruptedAt: null | number = null

export function markVoicePlaybackInterrupted() {
  interruptedAt = Date.now()
}

export function takeVoicePlaybackInterrupted(): boolean {
  const at = interruptedAt
  interruptedAt = null

  return at !== null && Date.now() - at < INTERRUPT_TTL_MS
}

// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import type { ComposerTarget } from '../focus'
import type { ChatBarProps } from '../types'
import type { useVoiceConversation } from './use-voice-conversation'
export function useComposerVoice({
  busy,
  clearDraft,
  disabled,
  focusInput,
  insertText,
  maxRecordingSeconds,
  onInterrupt,
  onSubmit,
  onTranscribeAudio,
  sessionId,
  target
}: UseComposerVoiceArgs) {
  const noop = () => {}
  const asyncNoop = async () => {}
  const voiceStatus: import('../types').VoiceStatus = 'idle'
  const voiceActivityState: import('../types').VoiceActivityState = { level: 0, elapsedSeconds: 0, status: 'idle' }
  const conversation: ReturnType<typeof import('./use-voice-conversation').useVoiceConversation> = { end: asyncNoop, level: 0, muted: true, start: asyncNoop, status: 'idle', stopTurn: noop, toggleMute: noop }
  return { conversation, dictate: noop, endConversation: noop, handleToggleAutoSpeak: noop, startConversation: asyncNoop, voiceActivityState, voiceConversationActive: false, voiceStatus }
}
interface UseComposerVoiceArgs {
  busy: boolean
  clearDraft: () => void
  disabled: boolean
  focusInput: () => void
  insertText: (text: string) => void
  maxRecordingSeconds: number
  /** Interrupt the in-flight agent turn (Stop-button seam) — fired when the
   *  user speaks over the model while it is still generating. */
  onInterrupt?: () => Promise<void> | void
  onSubmit: ChatBarProps['onSubmit']
  onTranscribeAudio: ChatBarProps['onTranscribeAudio']
  sessionId: string | null | undefined
  /** This composer's focus-bus key — voice toggles targeting another
   *  composer (or the active one, when not us) are ignored. */
  target: ComposerTarget
}

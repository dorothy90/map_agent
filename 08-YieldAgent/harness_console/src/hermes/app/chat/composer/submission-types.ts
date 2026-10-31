// Exact upstream type excerpts until the complete prompt-actions closure is available.
import type { ComposerAttachment } from '@/store/composer'
import type { SessionOwnerScope } from '@/store/session-request-router'
export type GatewayRequest = <T>(method: string, params?: Record<string, unknown>, timeoutMs?: number) => Promise<T>
export interface SubmitTextOptions {
  /** Original slash invocation: retries must not re-expand a prepared prompt. */
  retryText?: string
  /** Captured by slash dispatch before asynchronous expansion. */
  destination?: SubmissionDestination
  /** Stable caller-owned identity; retain across uncertain admission retries. */
  submission_id?: string
  attachments?: ComposerAttachment[]
  /** The composer scope key that was actually loaded when this text was
   *  submitted (see use-composer-draft's activeQueueSessionKeyRef). Compared
   *  against the resolved submit target in sessionContextDrift — a mismatch
   *  means the composer and the session-side refs disagreed about which
   *  session this send belongs to (#59305). Omit for non-composer submits
   *  (queue drain, steer, external submit requests): the check is a no-op
   *  without it. */
  composerScope?: string | null
  /** This submit's fresh draft acquired a stored key. Never fired for navigation. */
  onComposerScopeAssigned?: (scope: string) => void
  /** What the transcript shows for this send, when it differs from the text
   *  the agent receives. A `/skill` invocation expands into the whole skill
   *  body — model-facing scaffolding the UI must never render — so the slash
   *  dispatcher passes the invocation (`/work fix the leak`) here. */
  displayText?: string
  /** `hidden` types the persisted user row (display_kind) so no bubble
   *  renders anywhere — the off-screen path for widget intents. The agent
   *  still receives the text as a normal user turn. */
  displayKind?: 'hidden'
  /** Per-turn client surface the gateway turns into a model-bound note. The
   *  HUD sets `hud` from its own store; a GPT-Live delegation passes
   *  `voice-live` (spoken transcript in, speakable prose out). */
  surface?: 'voice-live'
  /** With `surface: 'voice-live'`: the recent spoken exchange, appended to the
   *  model-bound note by the gateway (never persisted, never rendered). */
  voiceContext?: string
  /** A spoken turn from the chained voice conversation: the gateway runs it on
   *  `auxiliary.voice_chat` (the session model when that slot is on auto). */
  voiceTurn?: boolean
  fromQueue?: boolean
  /** Called once with the EXACT session identity the backend accepted the
   *  prompt into — the live runtime id after any stale-runtime recovery, plus
   *  the durable stored id when the caller knows it. A caller that must prove
   *  delivery to another surface (Quick Entry) uses this instead of guessing
   *  from the foreground session. Never called for a rejected or aborted
   *  submit, and never for slash commands, which never reach prompt.submit. */
  onAccepted?: (identity: { runtimeSessionId: string; storedSessionId: null | string }) => void
  /** Runtime session id to submit into. Queue drains pass this so a
   *  backgrounded/source session cannot be replaced by the current foreground
   *  session between enqueue and drain. */
  sessionId?: string | null
  /** Stable stored session id for optimistic/cache updates and stale-runtime
   *  recovery. Distinct from the runtime session id minted by the gateway. */
  storedSessionId?: string | null
}
export interface SubmissionDestination {
  readonly scopeKey: string
  readonly owner: SessionOwnerScope
  readonly requestGateway: GatewayRequest
}

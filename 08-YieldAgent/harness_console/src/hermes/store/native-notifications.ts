// Disabled web shim for H/apps/desktop/src/store/native-notifications.ts:226; PB:674,680,D10.
export const dispatchNativeNotification = (_options: unknown): void => {}

export interface NativeNotificationInput {
  kind: NativeNotificationKind
  title: string
  body?: string
  sessionId?: null | string
  /**
   * Not tied to a chat session (e.g. pet generation). Fires whenever the user
   * is away, bypassing the session-match gate that completion kinds normally
   * require.
   */
  global?: boolean
  silent?: boolean
  actions?: NativeNotificationAction[]
  /**
   * Extra throttle/dedupe discriminator for session-less notifications (e.g.
   * the plugin id), so unrelated emitters of the same kind don't collapse
   * into one another. Never drives click-to-focus like `sessionId` does.
   */
  tag?: string
  /** Absolute file path for the OS notification icon (Electron). */
  icon?: string
  /**
   * Resolved hash-router path to open on body click when there is no
   * `sessionId` (plugins). Same vocabulary as `hermes://index-network/intent/1`.
   */
  activate?: string
  /** Renderer-side handle so click/action can invoke registered callbacks. */
  notifyId?: string
}

export type NativeNotificationKind =
  'approval' | 'backgroundDone' | 'credits' | 'input' | 'plugin' | 'turnDone' | 'turnError'
export interface NativeNotificationAction {
  id: string
  text: string
  /** Serializable activate target echoed back on button press (plugin path). */
  activate?: string
}

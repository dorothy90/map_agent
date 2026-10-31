// PB:674–680: telemetry disabled. Public upstream types/signatures and pure classifiers retained.
import { atom } from 'nanostores'
type SharedMetricsRequester = <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
const SETTINGS_AREAS: Record<string, DesktopFeatureArea> = {
  about: 'settings_about',
  billing: 'settings_billing',
  'config:advanced': 'settings_config_advanced',
  'config:appearance': 'settings_config_appearance',
  'config:browser': 'settings_config_browser',
  'config:chat': 'settings_config_chat',
  'config:memory': 'settings_config_memory',
  'config:model': 'settings_config_model',
  'config:safety': 'settings_config_safety',
  'config:voice': 'settings_config_voice',
  'config:workspace': 'settings_config_workspace',
  // Legacy alias: the Connections tab redirects to Gateways.
  connections: 'settings_gateway',
  gateway: 'settings_gateway',
  keybinds: 'settings_keybinds',
  keys: 'settings_keys',
  notifications: 'settings_notifications',
  providers: 'settings_providers',
  sessions: 'settings_sessions',
  vault: 'settings_vault'
}
export type DesktopFeatureArea =
  | 'agents'
  | 'artifacts'
  | 'bot_mode'
  | 'browser_pane'
  | 'capabilities'
  | 'command_center'
  | 'command_palette'
  | 'cron'
  | 'extension_page'
  | 'file_pane'
  | 'find_in_page'
  | 'kanban'
  | 'messaging'
  | 'model_picker'
  | 'other'
  | 'profiles'
  | 'projects'
  | 'review_pane'
  | 'session_import'
  | 'session_picker'
  | 'session_search'
  | 'session_switcher'
  | 'settings_about'
  | 'settings_billing'
  | 'settings_config_advanced'
  | 'settings_config_appearance'
  | 'settings_config_browser'
  | 'settings_config_chat'
  | 'settings_config_memory'
  | 'settings_config_model'
  | 'settings_config_safety'
  | 'settings_config_voice'
  | 'settings_config_workspace'
  | 'settings_gateway'
  | 'settings_keybinds'
  | 'settings_keys'
  | 'settings_notifications'
  | 'settings_other'
  | 'settings_providers'
  | 'settings_sessions'
  | 'settings_vault'
  | 'skins'
  | 'starmap'
  | 'terminal_pane'
  | 'voice_conversation'
  | 'voice_dictation'
  | 'webhooks'
const ROUTE_AREAS: Record<string, DesktopFeatureArea> = {
  agents: 'agents',
  artifacts: 'artifacts',
  capabilities: 'capabilities',
  'command-center': 'command_center',
  cron: 'cron',
  kanban: 'kanban',
  messaging: 'messaging',
  profiles: 'profiles',
  'session-import': 'session_import',
  settings: 'settings_other',
  starmap: 'starmap',
  webhooks: 'webhooks'
}
export function settingsArea(view: string): DesktopFeatureArea {
  return SETTINGS_AREAS[view] ?? 'settings_other'
}
export function routeArea(pathname: string, contributedPaths: readonly string[] = []): DesktopFeatureArea | null {
  const segment = pathname.replace(/^\/+/, '').split(/[/?#]/)[0] ?? ''

  if (!segment || segment === 'settings') {
    return null
  }

  if (ROUTE_AREAS[segment]) {
    return ROUTE_AREAS[segment]
  }

  return contributedPaths.includes(`/${segment}`) ? 'extension_page' : null
}
export type DesktopFrictionKind =
  'backend_disconnect' | 'error_toast' | 'notice_dismissed' | 'renderer_crash' | 'slow_frame'
export type ErrorToastCategory =
  | 'api_key_missing'
  | 'api_key_rejected'
  | 'disk_full'
  | 'gateway_auth_failed'
  | 'method_not_allowed'
  | 'microphone_permission'
  | 'other'
  | 'pool_slot_timeout'
  | 'restart_required'
  | 'rpc_out_of_sync'
  | 'storage_failure'
  | 'timeout'
  | 'unclassified'
export type DesktopNoticeId =
  | 'artifacts_partial_load'
  | 'background_queue_stuck'
  | 'backend_skew'
  | 'billing_banner'
  | 'billing_block'
  | 'build_discontinued'
  | 'client_behind'
  | 'composer_queue_stuck'
  | 'credits'
  | 'free_tier_notice'
  | 'gateway_error'
  | 'gui_skew'
  | 'install_method'
  | 'mcp_health'
  | 'model_warning'
  | 'other'
  | 'restored_draft'
  | 'runtime_not_ready'
  | 'session_compress'
  | 'terminal_backend'
  | 'tip'
  | 'update_available'
  | 'voice_live_unavailable'
  | 'voice_stop_hint'
const NOTICE_IDS: Record<string, DesktopNoticeId> = {
  'artifacts-partial-load': 'artifacts_partial_load',
  'backend-contract-skew': 'backend_skew',
  'client-update-after-backend': 'client_behind',
  'composer-queue-stuck': 'composer_queue_stuck',
  'desktop-build-discontinued': 'build_discontinued',
  'desktop-update-available': 'update_available',
  'gui-contract-skew': 'gui_skew',
  'install-method-not-supported': 'install_method',
  'runtime-not-ready': 'runtime_not_ready',
  'terminal-backend-unavailable': 'terminal_backend',
  'voice-live-unavailable': 'voice_live_unavailable',
  'voice-stop-hint': 'voice_stop_hint'
}
const NOTICE_PREFIXES: readonly (readonly [string, DesktopNoticeId])[] = [
  ['billing-block:', 'billing_block'],
  ['composer-background-queue-stuck-', 'background_queue_stuck'],
  ['credits.', 'credits'],
  ['gateway-error:', 'gateway_error'],
  ['mcp-health-', 'mcp_health'],
  ['model-warning-confirm-', 'model_warning'],
  ['session-compress:', 'session_compress']
]
export function noticeIdForToast(id: string): DesktopNoticeId {
  return NOTICE_IDS[id] ?? NOTICE_PREFIXES.find(([prefix]) => id.startsWith(prefix))?.[1] ?? 'other'
}
export type RendererCrashDetail = 'crash' | 'killed' | 'oom' | 'other'
export type BackendDisconnectDetail = 'backend_exit' | 'network' | 'other' | 'timeout'
export type SlowFrameBucket = '100ms_to_250ms' | '1s_to_5s' | '250ms_to_1s' | 'gte_5s'
export function slowFrameBucket(durationMs: number): SlowFrameBucket | null {
  if (!(durationMs >= 100)) {
    return null
  }

  return durationMs < 250
    ? '100ms_to_250ms'
    : durationMs < 1000
      ? '250ms_to_1s'
      : durationMs < 5000
        ? '1s_to_5s'
        : 'gte_5s'
}
export type DesktopFrictionDetail =
  BackendDisconnectDetail | DesktopNoticeId | ErrorToastCategory | RendererCrashDetail | SlowFrameBucket
export type DesktopOnboardingStep =
  | 'choose_later'
  | 'consent'
  | 'first_message'
  | 'free_tier_ready'
  | 'guide'
  | 'guide_skip'
  | 'model_pick'
  | 'provider_api_key'
  | 'provider_local'
  | 'provider_oauth'
  | 'provider_setup'
  | 'sign_in'
export type DesktopOnboardingEvent = 'abandoned' | 'completed' | 'reached'
export const DESKTOP_BUTTON_ACTIONS = {
  composerAttach: 'composer.attach',
  messageCopy: 'message.copy',
  messageRetry: 'message.retry'
} as const
export type DesktopActionVia = 'click' | 'menu' | 'palette' | 'shortcut'
export type DesktopDislikeSignal =
  'cancelled' | 'feature_disabled' | 'quick_close' | 'rage_click' | 'setting_off_default' | 'undo'
export type DesktopFlowId =
  | 'command_palette'
  | 'free_tier_sign_in'
  | 'keybind_capture'
  | 'model_picker'
  | 'project_create'
  | 'provider_oauth'
  | 'session_picker'
  | 'session_switcher'
export type DesktopFeatureToggle =
  | 'backdrop'
  | 'bot_activity_toasts'
  | 'composer_popout_gestures'
  | 'intro_splash'
  | 'native_notifications'
  | 'notification_kind'
  | 'reactions'
  | 'thread_timeline'
  | 'tips'
  | 'tours'
  | 'vibe_hearts'
export type DesktopUndoTarget = 'closed_tab' | 'restored_draft'
export type DesktopMode = 'bots' | 'sessions'
const STATE_KEY = 'hermes.desktop.metrics.v1'
export const ACTIVE_IDLE_GAP_MS = 5 * 60_000
export type DesktopMetricsGate = 'off' | 'on' | null
export function recordFeatureUse(area: DesktopFeatureArea, now = Date.now()): void {}
export function noteAreaClosed(area: DesktopFeatureArea, now = Date.now()): void {}
export function trackArea(area: DesktopFeatureArea, open: boolean, now = Date.now()): void {}
export function trackFlow(flow: DesktopFlowId, open: boolean): void {}
export function completeFlow(flow: DesktopFlowId): void {}
export function noteBackendDrop(reason: 'timeout' | null, now = Date.now()): void {}
export function noteBackendExited(now = Date.now()): void {}
export function cancelPendingBackendDrop(): void {}
export function recordFriction(kind: DesktopFrictionKind, detail: DesktopFrictionDetail): void {}
export function recordDislike(
  signal: DesktopDislikeSignal,
  target: DesktopFeatureArea | DesktopFeatureToggle | DesktopFlowId | DesktopUndoTarget | string,
  setting?: string,
  profile?: null | string
): void {}
export function configPatchKeys(patch: unknown, prefix = ''): string[] {
  if (!patch || typeof patch !== 'object' || Array.isArray(patch)) {
    return prefix ? [prefix] : []
  }

  return Object.entries(patch as Record<string, unknown>).flatMap(([key, value]) =>
    configPatchKeys(value, prefix ? `${prefix}.${key}` : key)
  )
}
export function recordSettingsSaved(
  patch: unknown,
  published: Readonly<Record<string, unknown>>,
  profile?: null | string
): void {}
export function recordFeatureToggle(toggle: DesktopFeatureToggle, wasOn: boolean, isOn: boolean): void {}
export function recordAction(rawAction: string, via: DesktopActionVia, now = Date.now()): void {}
export function noteInteraction(now = Date.now(), mode: DesktopMode = 'sessions'): void {}
export function noteMessageSent(mode: DesktopMode = 'sessions'): void {}
export function setDesktopBotCount(count: number): void {}
export function recordOnboarding(step: DesktopOnboardingStep, event: DesktopOnboardingEvent): void {}
export function closeOnboardingStep(step: DesktopOnboardingStep): void {}
export function flushDesktopMetrics(): void {}
export function setDesktopMetricsGate(next: DesktopMetricsGate, decided = true): void {}
export function bindDesktopMetrics(next: SharedMetricsRequester | null, profileScope?: string): void {}
export function tickDesktopMetrics(now = Date.now()): void {}
export function resetDesktopMetricsForTests(): void {}
export const DESKTOP_METRICS_STATE_KEY = STATE_KEY
export const $desktopMetricsGate = atom<DesktopMetricsGate>('off')
// Legacy 8b export retained for its existing consumers.
export const recordComposerDraftDisposition = (..._args: unknown[]): void => {}

// PB:674–680: native preview/rail disabled. No persistence, RPC, REST, or subscriptions.
import { atom } from 'nanostores'
// Exact upstream type-only dependencies (layout.ts:84, preview-ownership.ts:77).
type RightRailTabId = `artifact:${string}` | `file:${string}` | `url:${string}`
type PreviewOwner = null | string | { profile?: null | string; runtimeId: null | string; sessionId: null | string }
export type PreviewRenderMode = 'preview' | 'source'
export interface PreviewTarget {
  binary?: boolean
  byteSize?: number
  /** Inline image bytes (a `data:` URL) when the renderer already holds them —
   * e.g. a pasted/dropped screenshot whose only on-disk copy is a transient
   * path the preview can't reliably re-read. Rendered directly and NOT
   * persisted (it would bloat localStorage). */
  dataUrl?: string
  /** `artifact` targets have nothing behind them on disk or on the network —
   * `url` is an id into the artifact registry, which owns the content. They
   * are what lets the rail preview generated HTML the workspace never saw. */
  kind: 'artifact' | 'file' | 'url'
  label: string
  large?: boolean
  language?: string
  mimeType?: string
  path?: string
  /** `directory`/`missing` are typed non-previewable results from main-process
   * normalization (#101683): they never reach `openPreview` — callers branch on
   * them for the native folder action / not-found reporting instead. */
  previewKind?: 'binary' | 'directory' | 'html' | 'image' | 'missing' | 'pdf' | 'text'
  renderMode?: PreviewRenderMode
  /** Tombstone set when a read/watch confirmed the file is gone. The tab stays
   *  open for the session showing an explicit "file no longer exists" state,
   *  but is dropped at the next restore so day-2 boots stop re-probing it. */
  missing?: boolean
  source: string
  /** Runtime-only target that cannot be restored from persisted state. */
  transient?: boolean
  url: string
}
export interface PreviewServerRestart {
  message?: string
  status: 'complete' | 'error' | 'running'
  taskId: string
  url: string
}
export interface PreviewTab {
  id: RightRailTabId
  target: PreviewTarget
  /** Stored id of the session that owns the tab. Absent on a tab opened in a
   *  fresh draft (adopted when the draft becomes a session) and on legacy rows
   *  written before session scoping (decodePreviewTabs migrates those to
   *  pinned). */
  sessionId?: string
  /** Pinned tabs render in EVERY session — the explicit cross-session
   *  workspace. Everything else is visible only in the session that owns it.
   *  Always written by this build, so a missing flag marks a legacy row. */
  pinned?: boolean
}
export function decodePreviewTabs(raw: string): PreviewTab[] { return [] }
export function setPreviewScope(scope: string) {}
export function dropPreviewTabsForProfile(profile: string) {}
export function migratePreviewTabsForProfile(oldProfile: string, newProfile: string) {}
export function previewTabsFor(owner: PreviewOwner = null): PreviewTab[] { return [] }
export function previewTabIdsVisibleTo(owner: PreviewOwner): string[] { return [] }
export function rekeyPreviewTabsSession(previousId: string, nextId: string): void {}
export function preferredVisibleTabId(): RightRailTabId | null { return null }
export function adoptDraftPreviewTabs(storedSessionId: string): void {}
export function adoptPendingRuntimeTabs(runtimeId: string, storedSessionId: string): void {}
export interface BrowserPage {
  title: string
  url: string
}
export function noteBrowserPage(tabId: string, page: BrowserPage) {}
export function forgetBrowserPage(tabId: string) {}
export function commitBrowserTabLocation(tabId: string, url: string, title?: string) {}
export function adoptPersistedBrowserTab(tabId: string) {}
export function popOutBrowserTab(tabId: string) {}
export function markBrowserTabPopped(tabId: string, popped: boolean) {}
export function previewTabId(target: PreviewTarget, sessionId?: null | string): RightRailTabId { throw new Error('Preview capability is disabled') }
export function renderedHtmlTarget(target: PreviewTarget): PreviewTarget { return target }
export function setPreviewRenderMode(tabId: string, renderMode: PreviewRenderMode) {}
export function openPreview(
  target: PreviewTarget,
  requestedOwner: null | string = null,
  /** The runtime asking: an ownerless tab is handed to it once its stored id
   *  binds. An agent event passes its own session id; anything else is the
   *  primary's runtime. */
  runtimeId: null | string = null,
  /** The asking agent's profile, when it is not the chat on screen: only that
   *  profile's pins may be reused. Omitted = the viewed profile. */
  profile?: null | string
) {}
export function markPreviewTabMissing(tabIdOrUrl: string) {}
export function openBrowserTab() {}
export function toggleBrowserTab() {}
export function newBrowserTab() {}
export function setPreviewTabPinned(tabId: string, pinned: boolean): void {}
export function prunePreviewTabsForSession(sessionId: string): void {}
export function closeRightRailTab(tabId: string) {}
export function closePreviewForSource(source: string): boolean { return false }
export function closeBrowserPreviewMatchingLiveUrl(...candidates: string[]): boolean { return false }
export function closePreviewMatching(...candidates: string[]): boolean { return false }
export function closeDockedPreviewMatching(...candidates: string[]): boolean { return false }
export function closeAgentPreview(owner: PreviewOwner, candidates: string[]): void {}
export function closeArtifactPreviewTabs() {}
export function closeRightRail() {}
export function requestPreviewReload() {}
export function beginPreviewServerRestart(taskId: string, url: string) {}
export function completePreviewServerRestart(taskId: string, text: string) {}
export function progressPreviewServerRestart(taskId: string, text: string) {}
export function failPreviewServerRestart(taskId: string, message: string) {}
export const $previewTabs = atom<PreviewTab[]>([])
export const $visiblePreviewTabs = atom<PreviewTab[]>([])
export const $previewTarget = atom<PreviewTarget | null>(null)
export const $previewTabSources = atom<string[]>([])
export const $browserPages = atom<Record<string, BrowserPage>>({})
export const $poppedBrowserTabIds = atom<ReadonlySet<string>>(new Set())
export const $dockedVisiblePreviewTabs = atom<PreviewTab[]>([])
export const $previewReloadRequest = atom(0)
export const $previewServerRestart = atom<PreviewServerRestart | null>(null)
export const $previewServerRestartStatus = atom<'complete' | 'error' | 'running' | 'idle'>('idle')

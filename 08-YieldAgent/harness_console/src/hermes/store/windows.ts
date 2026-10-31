// PB:D10,680: single web window; all native window exports retain their upstream signatures.
export function isSecondaryWindow(): boolean { return false }
export function isHudWindow(): boolean { return false }
export function isWatchWindow(): boolean { return false }
export function isBrowserWindow(): boolean { return false }
export function windowBrowserTabId(): null | string { return null }
export function isPeerInstanceWindow(search = typeof window === 'undefined' ? '' : window.location.search): boolean { return false }
export function isProfilePinnedWindow(search = typeof window === 'undefined' ? '' : window.location.search): boolean { return false }
export function windowProfileOverride(): null | string { return null }
export function windowConnectionOverride(): null | string { return null }
export function canOpenSessionWindow(): boolean { return false }
export function canOpenNewWindow(): boolean { return false }
export function canOpenBrowserWindow(): boolean { return false }
export function canOpenSessionInTerminal(): boolean { return false }
export async function openSessionInNewWindow(
  sessionId: string,
  opts?: { watch?: boolean; parentSessionId?: null | string }
): Promise<void> {}
export async function openNewWindow(route?: { connectionId: null | string; profile: string }): Promise<void> {}
export async function openBrowserInNewWindow(tabId: string): Promise<boolean> { return false }
export async function openSessionInTerminal(
  sessionId: string,
  opts?: { cwd?: string; profile?: string }
): Promise<void> {}
export const isAuxiliaryWindow = (): boolean => false
export const isMainWindow = (): boolean => true

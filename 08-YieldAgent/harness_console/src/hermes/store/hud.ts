// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import { atom } from 'nanostores'
export const $hudActive = atom(false)
export const $hudMode = atom(false)
export const $hudSession = atom<null | string>(null)
export const canUseHud = (): boolean => false
export function openHud(sessionId?: null | string): void {}
export function closeHud(): void {}
export const toggleHud = (sessionId?: null | string): void => {}
export function resetHudLayout(): void {}
export const reportHudSession = (sessionId: null | string): void => {}
export function watchHudState(onClosed?: (sessionId: null | string) => void): () => void { return () => {} }

// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import type { HermesSkin } from '@hermes/shared/skin'
import { atom } from 'nanostores'
import { type DesktopTheme } from './types'
export const localDisplaySkinName = null as null | string
export const localDisplaySkinProfile = null as null | string
export const $backendThemes = atom<Record<string, DesktopTheme>>({})
export const $backendCustomCSS = atom<Record<string, string>>({})
export const $pendingSkinApply = atom<string | null>(null)
export function __resetBackendSkinSync(): void {}
export function ingestBackendSkin(skin: HermesSkin | undefined | null, { apply }: { apply: boolean }): void {}

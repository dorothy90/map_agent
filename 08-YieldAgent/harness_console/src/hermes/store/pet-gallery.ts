// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import { atom } from 'nanostores'
export interface GalleryPet {
  slug: string
  displayName: string
  installed: boolean
  spritesheetUrl?: string
  /** petdex's hand-picked set — used only to rank "popular" pets first. */
  curated?: boolean
  /** Hatched locally by the user (createdBy=generator) — badged + ranked first. */
  generated?: boolean
}
export interface PetGallery {
  enabled: boolean
  active: string
  pets: GalleryPet[]
}
export type PetGalleryStatus = 'idle' | 'loading' | 'ready' | 'stale' | 'error'
export type GatewayRequest = <T>(
  method: string,
  params?: Record<string, unknown>,
  timeoutMs?: number,
  signal?: AbortSignal
) => Promise<T>
export const $petGallery = atom<PetGallery | null>(null)
export const $petGalleryStatus = atom<PetGalleryStatus>('idle')
export const $petGalleryError = atom<string | null>(null)
export const TOGGLE_ON = '\u0000on'
export const TOGGLE_OFF = '\u0000off'
export const $petBusy = atom<string | null>(null)
export function resetPetGallery(): void {}
export function loadPetThumb(request: GatewayRequest, slug: string, url?: string): Promise<string | null> { return Promise.resolve(null) }
export function loadPetGallery(request: GatewayRequest, options: { force?: boolean } = {}): Promise<void> { return Promise.resolve() }
export async function applyAdoptedPet(request: GatewayRequest, slug: string, displayName: string): Promise<void> { return Promise.resolve() }
export function rankedGalleryPets(gallery: PetGallery | null, query = ''): GalleryPet[] { return [] }
export function adoptPet(request: GatewayRequest, slug: string, fallback: string): Promise<boolean> { return Promise.resolve(false) }
export function setPetEnabled(
  request: GatewayRequest,
  on: boolean,
  copy: { noneAvailable: string; fallback: string }
): Promise<boolean> { return Promise.resolve(false) }
export const PET_SCALE_MIN = 0.1
export const PET_SCALE_MAX = 3.0
export const PET_SCALE_DEFAULT = 0.33
export const clampPetScale = (n: number): number => PET_SCALE_DEFAULT
export function nextScaleFromWheel(current: number | undefined, deltaY: number): number { return PET_SCALE_DEFAULT }
export function setPetScale(request: GatewayRequest, scale: number): void {}
export async function exportPet(request: GatewayRequest, slug: string, fallback: string): Promise<boolean> { return false }
export function renamePet(request: GatewayRequest, slug: string, name: string, fallback: string): Promise<boolean> { return Promise.resolve(false) }
export function removePet(request: GatewayRequest, slug: string, fallback: string): Promise<boolean> { return Promise.resolve(false) }

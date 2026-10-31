// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import { atom } from 'nanostores'
import { type PetActivity, type PetInfo } from '@/store/pet'
import { type PetPluginMessage } from '@/store/pet-plugin-messages'
export interface PetOverlayBounds {
  x: number
  y: number
  width: number
  height: number
}
export interface PetOverlayOpenRequest {
  bounds: PetOverlayBounds
  screen?: boolean
}
export interface PetOverlayStatePayload {
  info: PetInfo
  activity: PetActivity
  busy: boolean
  awaiting: boolean
  /** Drives the overlay's mail icon: a finish landed while you were away. */
  unread: boolean
  /** Latest reaction — bumping its id forwards a burst to the overlay. */
  reaction: PetReaction | null
  /** Live plugin bubble lines (`ctx.pet.say`); this renderer owns their expiry. */
  pluginMessages?: PetPluginMessage[]
}
export type PetOverlayControl =
  | { type: 'pop-in' }
  | { type: 'ready' }
  | { type: 'submit'; text: string }
  | { type: 'bounds'; bounds: PetOverlayBounds }
  | { type: 'open-app' }
  | { type: 'toggle-app' }
  | { type: 'scale'; scale: number }
export const $petOverlayActive = atom(false)
export interface PetReaction {
  id: number
  kind: string
}
export const $petReaction = atom<PetReaction | null>(null)
export const forwardPetReaction = (kind: string): void => {}
export function overlayWindowSize(frameW: number, frameH: number, scale: number): { width: number; height: number } { return { width: 0, height: 0 } }
export function popOutPet(petRect: PetOverlayBounds): void {}
export function restorePetOverlay(): void {}
export function popInPet(): void {}
export function setPetOverlaySubmitHandler(fn: ((text: string) => void) | null): void {}
export function setPetOverlayOpenAppHandler(fn: (() => void) | null): void {}
export function setPetOverlayScaleHandler(fn: ((scale: number) => void) | null): void {}
export function initPetOverlayBridge(): () => void { return () => {} }

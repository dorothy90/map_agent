// Disabled web shim for H/apps/desktop/src/store/pet.ts:179,164,168; PB:674,680,D10.
export const flashPetActivity = (_activity: unknown, _duration = 1600): void => {}
export const markPetUnread = (): void => {}
export const setPetActivity = (_activity: unknown): void => {}

import { atom } from 'nanostores'
export const $petActive = atom(false)

export type PetState = 'idle' | 'wave' | 'run' | 'failed' | 'review' | 'jump' | 'waiting'
export interface PetInfo {
  enabled: boolean
  slug?: string
  displayName?: string
  mime?: string
  spritesheetBase64?: string
  // Stable sheet revision (`mtime_ns:size`) from the gateway; lets the desktop
  // skip full sprite payload refreshes when the active pet hasn't changed.
  spritesheetRevision?: string
  frameW?: number
  frameH?: number
  framesPerState?: number
  // Real (padding-trimmed) frame count per state row, from the engine. Lets the
  // canvas step only frames that exist instead of a fixed framesPerState, which
  // would animate into the transparent padding of ragged sheets (blank flash).
  framesByState?: Record<string, number>
  // Concrete Codex row counts (e.g. running-right may have 8 frames even though
  // the Hermes "run" activity state uses the in-place running row).
  framesByRow?: Record<string, number>
  loopMs?: number
  scale?: number
  stateRows?: string[]
}
export interface PetInfoMeta {
  enabled: boolean
  slug?: string
  displayName?: string
  scale?: number
  spritesheetRevision?: string
}
export interface PetActivity {
  busy?: boolean
  awaitingInput?: boolean
  toolRunning?: boolean
  reasoning?: boolean
  error?: boolean
  justCompleted?: boolean
  celebrate?: boolean
}

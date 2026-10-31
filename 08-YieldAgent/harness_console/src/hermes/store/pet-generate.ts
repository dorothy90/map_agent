// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import { atom } from 'nanostores'
import { storedBoolean, storedString } from '@/lib/storage'
import { type PetInfo } from '@/store/pet'
import { type GatewayRequest } from '@/store/pet-gallery'
export function cleanPetName(prompt: string): string { return '' }
export interface PetDraft {
  index: number
  /** Downscaled PNG data URI preview from the gateway. */
  dataUri: string
}
export type PetGenStatus = 'idle' | 'generating' | 'ready' | 'hatching' | 'preview' | 'adopting' | 'error' | 'stale'
export interface PetHatchStage {
  phase: 'row' | 'compose' | 'save'
  state?: string
  done?: number
  total?: number
}
export const $petGenStatus = atom<PetGenStatus>('idle')
export const $petGenStage = atom<PetHatchStage | null>(null)
export const $petGenError = atom<string | null>(null)
export const $petGenAvailable = atom<boolean | null>(null)
export interface PetGenProvider {
  name: string
  label: string
  /** Whether this is the backend's default pick (no override needed). */
  default: boolean
}
export const $petGenProviders = atom<PetGenProvider[]>([])
export const $petGenProvider = atom('')
export function setPetGenProvider(name: string): void {}
export const $petGenRemixConfirmed = atom(false)
export function markRemixConfirmed(): void {}
export async function checkPetGenAvailable(request: GatewayRequest): Promise<void> {}
export const $petGenerateOpen = atom(false)
export function openPetGenerate(): void {}
export function closePetGenerate(): void {}
export const $petGenToken = atom<string | null>(null)
export const $petGenPrompt = atom<string>('')
export const $petGenDrafts = atom<PetDraft[]>([])
export const $petGenSelected = atom<number | null>(null)
export const $petGenPreview = atom<PetInfo | null>(null)
export const $petGenInput = atom('')
export const $petGenRefImage = atom<string | null>(null)
export const $petGenRefName = atom('')
export function resetPetGen(): void {}
export function cleanupPetGenOnClose(request: GatewayRequest): void {}
export function cancelGenerate(): void {}
export function discardDrafts(): void {}
export function cancelHatch(): void {}
export async function generateDrafts(request: GatewayRequest, options: GenerateOptions): Promise<boolean> { return false }
export async function hatchSelected(request: GatewayRequest, options: HatchOptions): Promise<boolean> { return false }
export interface AdoptOutcome {
  ok: boolean
  slug?: string
  displayName?: string
}
export async function adoptHatched(request: GatewayRequest, name?: string): Promise<AdoptOutcome> { return { ok: false } }
export async function discardHatched(request: GatewayRequest): Promise<void> {}
const PROVIDER_KEY = 'hermes.desktop.petgen.provider'
const REMIX_CONFIRMED_KEY = 'hermes.desktop.petgen.remixConfirmed'
interface GenerateOptions {
  prompt: string
  style?: string
  count?: number
  /** Optional data-URL reference image — every draft is grounded on it. */
  referenceImage?: string
}
interface HatchOptions {
  name: string
  description?: string
  prompt?: string
  style?: string
}

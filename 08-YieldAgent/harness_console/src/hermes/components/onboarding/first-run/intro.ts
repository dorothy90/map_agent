// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import { atom, computed } from 'nanostores'
export const $introCopy = atom<IntroCopyStage>('hidden')
export const $introHoldsThread = computed($introCopy, stage => stage === 'playing')
export const $introTurnSent = atom(false)
export function startIntro(): void {}
export function openIntro(blank: boolean): void {}
export function failIntro(): void {}
export function leaveIntro(): void {}
export function leaveIntroOnLayout(): void {}
export function skipIntro(): void {}
type IntroCopyStage = 'hidden' | 'landed' | 'playing'

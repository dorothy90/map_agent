// Disabled web shim for H/apps/desktop/src/store/free-tier-sign-in.ts:142; PB:674,680,D10.
export const noteFreeTierTurnComplete = (): void => {}

import { atom } from 'nanostores'
export const $freeTierTurnCompleted = atom(0)
export const noteFreeTierAppConnected = noteFreeTierTurnComplete
export function openFreeTierSignIn(): void {}

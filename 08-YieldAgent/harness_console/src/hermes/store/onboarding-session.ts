// Web capability boundary; original apps/desktop/src/store/onboarding-session.ts:1-120. UI/native effects disabled (PB:680).
import { atom } from 'nanostores'
export const isOnboardingChat = (_id?: string | null): boolean => false
export const onboardingChatStore = (_id?: string | null) => atom<boolean>(false)

export const $onboardingSession = atom<OnboardingSession | null>(null)

export interface OnboardingSession {
  /** The computer the run lives on (`activeGatewayConnectionId()` at its start); only its backend knows the run. */
  connectionId: null | string
  /** Names the run in `onboarding.ended` and `onboarding.state`. */
  runId: string
  runtimeId: string
  storedId: string
}

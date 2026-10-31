// Shim for H/apps/desktop/src/store/onboarding.ts:641-650; PB:674,680,D10. UI/native effects disabled.
export function requestDesktopOnboardingForCredentialWarning(_reason: null | string | undefined): void {}

export function consumePendingCredentialWarning(): null | string { return null }
export function requestDesktopOnboarding(reason: null | string = null): void {}
export function startManualProviderOAuth(providerId: string, profile?: import('@/hermes').ProfileScope): void {}
export function clearFreeTierIntro(): void {}

export function startManualOnboarding(reason: null | string = null, profile?: import('@/hermes').ProfileScope): void {}

// Original public state type; disabled onboarding has no effects or persistence.
import { atom } from 'nanostores'
// H/store/onboarding-scope.ts:5-8, type only.
interface OnboardingScope { connectionId?: null | string; profile?: null | string }
import type { AppHandoffStatus, OAuthProvider, OAuthStartResponse } from '@/types/hermes'
type PkceStart = Extract<OAuthStartResponse, { flow: 'pkce' }>
type DeviceStart = Extract<OAuthStartResponse, { flow: 'device_code' }>
export type OnboardingMode = 'apikey' | 'oauth'
export interface OnboardingSignedIn {
  email: null | string
  handoff: AppHandoffStatus | null
}
export type OnboardingFlow =
  | { status: 'idle' }
  | { provider: OAuthProvider; status: 'starting' }
  | { code: string; provider: OAuthProvider; start: PkceStart; status: 'awaiting_user' }
  | { copied: boolean; provider: OAuthProvider; start: DeviceStart; status: 'polling' }
  | { provider: OAuthProvider; start: OAuthStartResponse; status: 'submitting' }
  | { copied: boolean; provider: OAuthProvider; status: 'external_pending' }
  | { provider: OAuthProvider; status: 'success' }
  | {
      // After successful credential acquisition, before completing
      // onboarding: show the user which model they're getting and let
      // them change it. providerSlug is the model.options slug for the
      // just-authenticated provider (used to persist the chosen model
      // via /api/model/set). The change-model UI uses the existing
      // ModelPickerDialog, which fetches its own model list from
      // /api/model/options — no need to cache the list here.
      currentModel: string
      label: string
      providerSlug: string
      saving: boolean
      // A Nous sign-in's account and what the free tier's apps did, shown on the screen it happened on.
      signedIn?: OnboardingSignedIn
      status: 'confirming_model'
    }
  | { detail?: string; message: string; provider?: OAuthProvider; start?: OAuthStartResponse; status: 'error' }
export interface DesktopOnboardingState {
  /** null until the first runtime check resolves. Seeded from localStorage so
   *  returning users skip the boot overlay entirely instead of flashing it
   *  every reload. */
  configured: boolean | null
  flow: OnboardingFlow
  mode: OnboardingMode
  providers: null | OAuthProvider[]
  reason: null | string
  requested: boolean
  /** True when the user explicitly chose "I'll choose a provider later" on the
   *  first-run picker. Persisted to localStorage so the blocking overlay never
   *  re-nags on subsequent launches — the user can connect a provider any time
   *  from Settings → Providers (or the model picker's "Add provider"). Distinct
   *  from `configured`: the app still has no usable provider, so chat won't work
   *  until one is connected; we just stop forcing the choice up front. */
  firstRunSkipped: boolean
  /** True when the user explicitly opened the provider selector to add /
   *  switch providers from an already-configured app (e.g. via the model
   *  picker's "Add provider" button). Forces the overlay to show the picker
   *  even when configured === true, and adds a close affordance. */
  manual: boolean
  targetScope?: OnboardingScope
  /** True when the overlay was opened specifically to configure a local /
   *  custom OpenAI-compatible endpoint (e.g. from Settings → Model's "Set up
   *  custom endpoint"). Forces the API-key form with the local option
   *  preselected instead of the OAuth picker. */
  localEndpoint: boolean
  /** True when the backend still owes this user the one-time free-tier
   *  introduction AND the free tier is what carries inference. It makes the
   *  overlay show its "Hermes is ready" screen once even though the app is
   *  configured. The backend's `notice_pending` flag is the only source of
   *  truth — there is no renderer latch — so an ack clears it everywhere. */
  freeTierReady: boolean
}
export const $desktopOnboarding = atom<DesktopOnboardingState>({ configured: true, flow: { status: 'idle' }, mode: 'oauth', providers: null, reason: null, requested: false, firstRunSkipped: true, manual: false, localEndpoint: false, freeTierReady: false })

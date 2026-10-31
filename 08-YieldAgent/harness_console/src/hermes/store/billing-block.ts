// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import type { BillingBlock } from '@hermes/shared'
import { atom } from 'nanostores'
export interface ActiveBillingBlock {
  block: BillingBlock
  sessionId: string
  at: number
}
export const $billingBlock = atom<ActiveBillingBlock | null>(null)
export const $billingSettingsRequest = atom(0)
export function setBillingBlock(sessionId: string, block: BillingBlock): void {}
export function clearBillingBlock(sessionId?: string): void {}
export function requestBillingSettings(): void {}
export function runBillingRecovery(block: BillingBlock): void {}
export function billingCtaLabel(block: BillingBlock, copy: { addCredits: string; openBilling: string }): string { return copy.openBilling }

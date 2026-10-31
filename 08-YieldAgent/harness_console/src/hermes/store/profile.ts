// PB:674–680: profile management disabled. Upstream public parameters/types retained; no native, network, persistence or listeners.
import { atom, computed, type ReadableAtom } from 'nanostores'
import type { ProfileInfo } from '@/types/hermes'
import type { SessionOwnerRoute } from '@/store/session-request-router'
import { sortByProfileOrder as sortProfilesByOrder } from '@/lib/profile-order'
export function normalizeProfileKey(name: string | null | undefined): string {
  const value = (name ?? '').trim()

  return value || 'default'
}
export function profileLabel(profile: Pick<ProfileInfo, 'display_name' | 'name'>): string {
  return (profile.display_name ?? '').trim() || profile.name
}
export const $activeProfile = atom<string>('default')
export const $profiles = atom<ProfileInfo[]>([])
export const $profilesByConnection = atom<ReadonlyMap<string, ProfileInfo[]>>(new Map())
export function setActiveProfile(name: string): void {}
export function invalidateProfileListFetches(): void {}
export function refreshProfiles(): Promise<ProfileInfo[]> { return Promise.resolve([]) }
export const $profileOrder = atom<string[]>([])
export function setProfileOrder(names: string[]): void {}
export function sortByProfileOrder<T extends { name: string }>(items: T[], order: string[]): T[] {
  return sortProfilesByOrder(items, order, item => item.name)
}
export const $profileColors = atom<Record<string, string>>({})
export function setProfileColor(name: string, color: null | string): void {}
export async function refreshActiveProfile(): Promise<void> {}
export async function switchProfile(name: string): Promise<void> {}
export const $activeGatewayProfile = atom<string>('default')
export const $newChatProfile = atom<string | null>(null)
export type AgentProfileRoute = SessionOwnerRoute
export const $newChatRoute = atom<AgentProfileRoute | null>(null)
export const $newChatConnectionId = atom<null | string>(null)
export function currentNewChatIntent(): number { return 0 }
export function captureNewChatSource(connectionId: null | string = null): void {}
export function pinLegacyNewChatProfile(profile: string): void {}
export function isLegacyNewChatProfile(profile: string): boolean { return false }
export function resolveNewChatOwnerRoute(forProfile?: string): AgentProfileRoute | null { return null }
export function resolveActiveSourceOwnerRoute(profile: string): AgentProfileRoute | null { return null }
export const $freshSessionRequest = atom(0)
export function requestFreshSession(): void {
  $freshSessionRequest.set($freshSessionRequest.get() + 1)
}
export const $gatewaySwapTarget = atom<string | null>(null)
export const $hydrationSyncProfile = atom<string | null>(null)
export function prewarmProfileBackend(name: string, connectionId: null | string = null): void {}
export function currentProfileSelectionRevision(): number { return 0 }
export async function ensureGatewayProfile(
  profile: string | null | undefined,
  { forceLegacyRoute = false }: { forceLegacyRoute?: boolean } = {}
): Promise<void> {}
export async function openGatewayAgent(connectionId: string, profile: string): Promise<void> {}
export interface EnsureGatewayAgentOptions {
  beforeActivate?: () => boolean
  /** Revokes this caller's right to activate or publish after async work. */
  signal?: AbortSignal
}
export async function ensureGatewayAgent(
  connectionId: null | string,
  profile: string,
  { beforeActivate, signal }: EnsureGatewayAgentOptions = {}
): Promise<void> {}
export const ALL_PROFILES = '__all__'
export const sidebarProfileForScope = (profileScope: string): string =>
  profileScope === ALL_PROFILES ? 'all' : normalizeProfileKey(profileScope)
export const messagingTotalsKey = (messagingProfile: string, sourceId: string): string =>
  `${messagingProfile}:${sourceId}`
export const $showAllProfiles = atom<boolean>(false)
export const $profileScope = computed([$showAllProfiles, $activeGatewayProfile], (showAll, gateway) =>
  showAll ? ALL_PROFILES : normalizeProfileKey(gateway)
)
export function selectProfile(name: string): void {}
export function prewarmProfilePick(name: string): void {}
export function pinNewChatProfile(name: string): string { return 'default' }
export function newSessionInProfile(name: string): void {}
export function newSessionInAgent(route: AgentProfileRoute): void {}
export function setShowAllProfiles(value: boolean): void {}
export function toggleShowAllProfiles(): void {}
export function switchToDefaultProfile(): void {}
export function switchProfileToSlot(slot: number): void {}
export function cycleProfile(direction: 1 | -1): void {}
export const $profileCreateRequest = atom(0)
export function requestProfileCreate(): void {}

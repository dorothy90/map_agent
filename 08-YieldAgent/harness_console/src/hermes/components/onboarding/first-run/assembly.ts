// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import { atom } from 'nanostores'
import { type LayoutNode } from '@/components/pane-shell/tree/model'
import { type InterfaceMode } from '@/store/interface-mode'
export const $chatOnboardingThreadIds = atom<readonly string[]>([])
export const $chatLayoutPicked = atom(false)
export function openDemoLayout(): void {}
export function endChatOnboardingSolo(): void {}
export function keepChatOnboardingLayout(): void {}
export function assembleChatOnboarding(id: string, tree: LayoutNode, mode?: InterfaceMode): void {}
export interface PreviewUndo {
  keep: (send: () => boolean) => boolean
  revert: () => void
}
export function snapshotChatLayout(): PreviewUndo { return { keep: () => false, revert: () => {} } }
export function useOnboardingChatActive(): boolean { return false }
export function useSetupChatView(): boolean { return false }

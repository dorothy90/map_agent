// Web capability boundary; original apps/desktop/src/components/pane-shell/workspace-scope.ts:1-120. UI/native effects disabled (PB:680).
import { atom } from 'nanostores'
export const $workspaceMode = atom<'sessions' | 'bots'>('sessions')
import type { WorkspaceMode } from '@/contrib/types'
export function workspaceScopeKey(mode: WorkspaceMode, ownerKey: string | null): string {
  return mode === 'sessions' ? 'sessions' : `bots:${ownerKey ?? ''}`
}
export const resolveRememberedActivePane = (_scope: string, _ids: string[]): string | null => null

export function setWorkspaceScope(mode: WorkspaceMode, ownerKey: string | null = null): boolean { return false }

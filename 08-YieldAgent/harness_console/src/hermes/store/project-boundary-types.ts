// Exact type excerpts H/global.d.ts:1642–1658 and H/app/chat/new-session-drag.ts:63–70.
import type { SessionOwnerRoute as AgentProfileRoute } from '@/store/session-request-router'
import type { TileDock } from '@/store/session-states'
export interface HermesGitBranch {
  name: string
  checkedOut: boolean
  isDefault: boolean
  isRemote: boolean
  worktreePath: null | string
}
export interface HermesGitBaseBranch {
  name: string
  isRemote: boolean
  isDefault: boolean
}
export interface NewSessionPlacement {
  anchor: string
  before?: null | string
  cwd?: null | string
  dir: TileDock
  profile?: string
  route?: AgentProfileRoute | null
}

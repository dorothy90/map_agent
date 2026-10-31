// Type-only upstream global.d.ts excerpts; no native bridge or Window augmentation.
export interface DesktopProfileRoute {
  connectionId: null | string
  profile: string
}
export interface DesktopAgentRoster {
  agents: DesktopRosterAgent[]
  sources: {
    connectionId: string
    label: string
    kind: DesktopConnectionKind
    reachable: boolean
    error?: string
    needsSignIn?: boolean
    // Stable backend identity (/api/status install_id) when known.
    installId?: string
  }[]
}
export interface DesktopAuthProvider {
  name: string
  displayName: string
  // True when this provider authenticates with a username + password
  // (the gateway's /login page renders a credential form) rather than an
  // OAuth redirect. The session/cookie/ws-ticket machinery is identical;
  // only the login-page form and the desktop's button copy differ.
  supportsPassword?: boolean
}
export interface HermesGitWorktree {
  path: string
  branch: null | string
  isMain: boolean
  detached: boolean
  locked: boolean
}
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
export interface HermesRepoStatus {
  branch: null | string
  // The repo's trunk ("main" / "master" / …), so the UI can offer "branch off
  // the default" from anywhere. Null when no trunk is detected.
  defaultBranch: null | string
  detached: boolean
  ahead: number
  behind: number
  staged: number
  unstaged: number
  untracked: number
  conflicted: number
  // Total distinct changed paths (tracked modified + conflicts + untracked).
  changed: number
  // +/- line counts of tracked changes vs HEAD (staged + unstaged). Untracked
  // files aren't in the diff, so they don't contribute lines.
  added: number
  removed: number
  // Capped changed-file list (REPO_STATUS_FILE_CAP) for the diff/open actions.
  files: HermesRepoStatusFile[]
}
export type HermesReviewScope = 'branch' | 'lastTurn' | 'uncommitted'
export interface HermesReviewFile {
  path: string
  added: number
  removed: number
  // M(odified) A(dded) D(eleted) R(enamed) C(opied) U(nmerged) ?(untracked)
  status: string
  staged: boolean
}
export interface HermesBranchPullRequest {
  branch: string
  draft: boolean
  number: number
  /** `open` | `closed` | `merged`, lowercased from gh. */
  state: string
  title: string
  url: string
}
export interface HermesRepoPullRequests {
  ghReady: boolean
  prs: HermesBranchPullRequest[]
}
export interface HermesReviewShipInfo {
  ghReady: boolean
  pr: HermesReviewPr | null
}
export type DesktopConnectionKind = 'cloud' | 'local' | 'remote' | 'ssh'
export interface DesktopRosterAgent {
  connectionId: string
  connectionKind: DesktopConnectionKind
  connectionLabel: string
  profile: string
  targetProfile?: string
  handle: string
}
export interface HermesRepoStatusFile {
  path: string
  staged: boolean
  unstaged: boolean
  untracked: boolean
  conflicted: boolean
}
export interface HermesReviewPr {
  url: string
  state: string
  number: number
}

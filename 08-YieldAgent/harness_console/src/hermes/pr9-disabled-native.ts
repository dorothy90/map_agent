// PB:680: native capabilities unavailable. Type-only signatures excerpted from H/global.d.ts.
import type { TranslucencyState } from '@hermes/shared/translucency'
import type { WakeIndicatorState } from './lib/wake-indicator'
import type { PetOverlayBounds, PetOverlayControl, PetOverlayOpenRequest, PetOverlayStatePayload } from './store/pet-overlay'
export interface DesktopUpdateStatus {
  supported: boolean
  retirement?: {
    state: 'discontinued'
    destination: string
    version: string
    message?: string
  }
  /** Which mechanism owns updates for this install (see electron/updater). */
  mechanism?: UpdaterMechanismClient
  updateAvailable?: boolean
  branch?: string
  currentBranch?: string
  reason?: string
  message?: string
  error?: string
  /** Exact commits behind. null = update available, but the count is
   *  unknowable (shallow clone without a merge-base) — never render it as a
   *  literal number. */
  behind?: number | null
  currentSha?: string
  /** Backend only: the version string the backend reports for itself. */
  currentVersion?: string
  /** The R2 channel name; independent of source branch and package version. */
  channel?: string
  /** The install's source_check can persist a channel (older runtimes cannot). */
  channelSelectable?: boolean
  /** The latest release tag on a release-feed channel, e.g. `v0.18.0`. */
  latestTag?: string | null
  /** Source stable: the release version the channel resolves to, e.g. `0.21.6`. */
  sourceVersion?: string
  /** Source stable: true when forward-only (newer than the release); absent from older checkers. */
  aheadOfRelease?: boolean
  /** Source stable: the checkout already contains the release, so installing it moves back. */
  rollback?: boolean
  /** Source stable: `hermes update` would refuse the release (it predates the gateway runtime these
   *  session stores use), so it is held instead of offered. */
  held?: 'downgrade'
  targetSha?: string
  commits?: DesktopUpdateCommit[]
  dirty?: boolean
  fetchedAt?: number
}
export interface DesktopUpdateApplyOptions {
  dirtyStrategy?: DesktopUpdateDirtyStrategy
}
export interface DesktopUpdateApplyResult {
  ok: boolean
  /** False when the apply-time check found nothing to install. */
  updateAvailable?: boolean
  branch?: string
  error?: string
  message?: string

  /** True when no staged updater exists (CLI install) and the user should run
   *  `hermes update` themselves. `command` is the exact line to run. */
  manual?: boolean
  command?: string
  hermesRoot?: string
  /** True when the backend was updated but the GUI couldn't be relaunched in
   *  place (AppImage / dev run): the new version loads on next launch. */
  backendUpdated?: boolean
  /** False when the running GUI package was NOT replaced by this update
   *  (Linux GUI/backend skew, or a sandbox-blocked relaunch). Distinguishes
   *  "backend only" outcomes from a real in-place GUI relaunch. (#45205) */
  guiUpdated?: boolean
  /** True for the Linux GUI/backend-skew terminal state: backend updated but
   *  the running AppImage/.deb/.rpm shell is unchanged and must be
   *  reinstalled. Renders a closeable "update the desktop app" message. */
  guiSkew?: boolean
  /** True when the update finished but the app must be quit + reopened by hand
   *  (e.g. the rebuilt sandbox helper isn't launchable): keep a working
   *  window, don't auto-quit into a dead app. (#45205) */
  manualRestart?: boolean
  /** True when the auto-relaunch was skipped specifically because the rebuilt
   *  chrome-sandbox helper is not launchable (not root:root + setuid). */
  sandboxBlocked?: boolean
  /** True when a detached relauncher took over (macOS bundle swap / Linux
   *  re-exec): the app is about to quit and reopen itself. */
  handedOff?: boolean
}
export interface DesktopUpdateProgress {
  stage: DesktopUpdateStage
  message: string
  percent: number | null
  error: string | null
  at: number
}
export interface HermesTitleBarTheme {
  background: string
  foreground: string
}
export interface HermesWindowState {
  customWindowControls?: boolean
  darwinMajor?: number
  isFullscreen: boolean
  isMaximized?: boolean
  isMinimized?: boolean
  isVisible?: boolean
  nativeOverlayWidth: number
  windowButtonPosition: { x: number; y: number } | null
}
export interface DesktopProfileRoute {
  connectionId: null | string
  profile: string
}
export interface DesktopActiveProfile {
  // The desktop's stored profile preference, or null when unset (legacy launch
  // that defers to the sticky active_profile / default).
  profile: string | null
}
export interface DesktopConnectionConfig {
  envOverride: boolean
  // The saved connection mode. 'cloud' is a Hermes Cloud connection: it carries
  // a remote-shaped block (remoteUrl = the selected agent's dashboardUrl,
  // remoteAuthMode 'oauth') but is remembered as cloud so settings reopens into
  // the cloud picker. Resolution treats cloud exactly as remote
  // (cloud-auto-discovery Q3/Q6).
  mode: 'local' | 'remote' | 'cloud' | 'ssh'
  // The profile this config describes, or null for the global/default
  // connection. Per-profile entries let a profile point at its own backend.
  profile: null | string
  remoteAuthMode: 'oauth' | 'token'
  remoteOauthConnected: boolean
  remoteTokenPreview: string | null
  remoteTokenSet: boolean
  // Whether OS-keychain-backed encryption (Electron safeStorage) is currently
  // available on this machine. When false, a persisted remote token can only be
  // stored as plain text on disk (with an explicit opt-in).
  secureTokenStorage: boolean
  // Whether the currently-persisted remote token is stored with encoding
  // 'plain' AND this machine cannot secure it (plain text on disk in
  // connection.json on a keyring-less machine). Stays false while keychain
  // encryption is opted out — plain text is the chosen mode there.
  remoteTokenPlainText: boolean
  remoteUrl: string
  // For a 'cloud' connection: the persisted Hermes Cloud org (slug or id) the
  // connected instance was discovered under, so Settings → Gateway can reopen
  // into that org. Empty string for remote/local.
  cloudOrg: string
  sshHost: string
  sshUser: string
  sshPort: number | null
  sshKeyPath: string
  sshRemoteHermesPath: string
  sshRemoteProfile: string
}
export interface DesktopConnectionConfigInput {
  mode: 'local' | 'remote' | 'cloud' | 'ssh'
  // When set, the save/apply/test targets this profile's per-profile remote
  // override instead of the global connection.
  profile?: null | string
  remoteAuthMode?: 'oauth' | 'token'
  remoteToken?: string
  // When true and secure (OS-keychain) storage is unavailable, persist the
  // remote token as plain text on disk instead of failing. Requires an explicit
  // user opt-in from the renderer.
  allowPlainTextToken?: boolean
  remoteUrl?: string
  // For a 'cloud' connection: the selected Hermes Cloud org (slug or id) to
  // persist so Settings can reopen into it. Ignored for remote/local modes.
  cloudOrg?: string
  cloudName?: string
  sshHost?: string
  sshUser?: string
  sshPort?: number | null
  sshKeyPath?: string
  sshRemoteHermesPath?: string
  sshRemoteProfile?: string
  // For a URL-remote/cloud per-profile override: the profile name on the remote
  // host when it differs from this Desktop routing label.
  remoteProfile?: string
}
export interface DesktopConnectionTestResult {
  baseUrl?: string
  ok?: boolean
  version?: string | null
  reachable?: boolean
  sshError?:
    | 'auth-failed'
    | 'hermes-not-found'
    | 'host-key-changed'
    | 'interactive-auth'
    | 'timeout'
    | 'unreachable'
    | 'unsupported-platform'
    | 'update-required'
    | 'unknown'
    | null
  error?: string | null
  host?: string
  remoteHermesPath?: string
  remoteHermesVersion?: string
  remotePlatform?: string
}
export interface DesktopRegistryConnection {
  id: string
  kind: DesktopConnectionKind
  // Required, registry-unique device name ("Homelab", "Work laptop").
  label: string
  url?: string
  authMode?: 'oauth' | 'token'
  org?: string
  host?: string
  user?: string
  port?: number
  keyPath?: string
  remoteHermesPath?: string
  remoteProfile?: string
  tokenSet: boolean
  tokenPreview: null | string
  // Names of the stored extra gateway headers (Cloudflare Access etc.);
  // header VALUES are secrets and never cross the IPC boundary. Optional so
  // fixtures/older payloads without the field remain valid.
  headerNames?: string[]
  // Last-known stable backend identity (the /api/status `install_id`).
  // Present once a roster enumeration or connection test has seen it; two
  // connections sharing it are one physical backend registered under two
  // addresses (display-only "Same backend as …" hint in Settings).
  installId?: string
}
export interface DesktopConnectionsRegistry {
  version: number
  // id of the connection that owns the window/primary backend.
  primary: string
  // Preserve old installs by defaulting to the explicit primary; users may
  // instead resume the last successfully opened source.
  launchMode?: 'last-used' | 'primary'
  // Last source the Sessions workspace opened successfully. Optional for
  // compatibility with an older Electron main during a rolling app update.
  lastUsed?: string
  // Whether OS-keychain-backed encryption (Electron safeStorage) is available;
  // false drives the plain-text token opt-in on keyring-less Linux.
  secureTokenStorage: boolean
  connections: DesktopRegistryConnection[]
}
export interface DesktopRegistryConnectionInput {
  // Present for edits; omitted on create (the main process mints the id).
  id?: string
  kind: DesktopConnectionKind
  label: string
  url?: string
  authMode?: 'oauth' | 'token'
  // Plaintext token to store (encrypted at rest); omit to keep the saved one.
  token?: string
  allowPlainTextToken?: boolean
  // Extra gateway headers for remote/cloud entries (access proxies such as
  // Cloudflare Access). The map is authoritative when present: name → new
  // plaintext value (encrypted at rest), or null to keep the stored secret
  // for that name. Omit the field entirely to keep the saved set unchanged.
  headers?: Record<string, null | string>
  org?: string
  host?: string
  user?: string
  port?: null | number
  keyPath?: string
  remoteHermesPath?: string
  remoteProfile?: string
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
export interface DesktopConnectionUpdateResult {
  connectionId: string
  label: string
  kind: DesktopConnectionKind
  ok: boolean
  skipped?: boolean
  reason?: string
  detail?: string
  error?: string
}
export interface DesktopManagedConnectionUpdateResult {
  connectionId: string
  correlationId: string
  ok: boolean
  updateOk: boolean
  restoreOk: boolean
  outcome: DesktopManagedConnectionUpdateOutcome
  exitCode: number | null
  receipt: DesktopManagedUpdateReceipt | null
  scopes: Array<{ profile: string; restored: boolean; error?: string }>
  /** Post-commit steps a successful update still owes (named in `message`). */
  owed?: Array<{ step: string; reason: string }>
  error?: string
  message?: string
}
export interface DesktopConnectionProbeResult {
  baseUrl: string
  reachable: boolean
  authMode: 'oauth' | 'token' | 'unknown'
  providers: DesktopAuthProvider[]
  version: string | null
  error: string | null
}
export interface DesktopOauthLoginOptions {
  /**
   * Registry-draft identity for a sign-in that runs before the draft is
   * saved. The main process derives the login window's cookie partition from
   * the settled connection id; without it an unsaved draft's session lands in
   * the legacy shared jar the saved connection never reads.
   */
  connectionId?: null | string
  /** Draft label — used to mint the id when `connectionId` is absent. */
  label?: string
  /**
   * Draft entry kind — the kind the save will persist. Together with
   * `authMode` it gates the pre-save cookie jar: only a cookie-auth remote
   * draft gets its own jar; cloud and token drafts sign in on the legacy
   * shared jar, which is what they read after the save.
   */
  kind?: DesktopConnectionKind
  /** Draft auth mode ('oauth' | 'token') the save will persist. */
  authMode?: 'oauth' | 'token'
}
export interface DesktopOauthLoginResult {
  ok: boolean
  baseUrl: string
  connected: boolean
  error?: string
  /**
   * The connection id the session was written for. A pre-save sign-in should
   * pin this into the draft so the later save reuses the same id (and jar).
   */
  connectionId?: string
}
export interface DesktopOauthLogoutResult {
  ok: boolean
  connected: boolean
}
export interface HermesReadFileErrorResult {
  ok: false
  error: string
  message: string
  path?: string
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
export interface HermesReviewList {
  files: HermesReviewFile[]
  // The resolved base ref the scope diffed against (branch merge-base / turn
  // baseline), or null for the uncommitted scope.
  base: null | string
}
export interface HermesRepoPullRequests {
  ghReady: boolean
  prs: HermesBranchPullRequest[]
}
export interface HermesReviewShipInfo {
  ghReady: boolean
  pr: HermesReviewPr | null
}
export interface HermesSelectPathsOptions {
  title?: string
  defaultPath?: string
  directories?: boolean
  multiple?: boolean
  /** Backend profile that produced defaultPath; Electron uses it for WSL gating. */
  profile?: string
  filters?: Array<{ name: string; extensions: string[] }>
}
export interface DesktopUpdateCommit {
  sha: string
  summary: string
  author: string
  at: number
}
export type UpdaterMechanismClient =
  'app-installer' | 'electron-updater' | 'external' | 'microsoft-store' | 'windows-handoff' | 'posix-handoff' | 'manual'
export type DesktopUpdateDirtyStrategy = 'abort' | 'stash' | 'force'
export type DesktopUpdateStage =
  | 'idle'
  | 'prepare'
  | 'fetch'
  | 'pull'
  | 'pydeps'
  | 'update'
  | 'rebuild'
  | 'restart'
  | 'done'
  | 'manual'
  /** Backend updated but the running GUI package (AppImage/.deb/.rpm) was NOT
   *  changed — the user must update/reinstall the desktop app. Terminal,
   *  closeable; never claims the GUI was updated. (#45205) */
  | 'guiSkew'
  | 'error'
export type DesktopConnectionKind = 'cloud' | 'local' | 'remote' | 'ssh'
export interface DesktopRosterAgent {
  connectionId: string
  connectionKind: DesktopConnectionKind
  connectionLabel: string
  profile: string
  targetProfile?: string
  handle: string
}
export type DesktopManagedConnectionUpdateOutcome =
  'updated' | 'update-failed' | 'restore-failed' | 'update-and-restore-failed' | 'refused'
export interface DesktopManagedUpdateReceipt {
  correlationId: string
  // Additive receipt outcomes remain forward-compatible with newer updater
  // kernels instead of making an older renderer reject their proof.
  outcome: string
  startedAt?: string
  finishedAt?: string
  preSha?: string
  postSha?: string
  preVersion?: string
  postVersion?: string
  stopReason?: string
  followups?: Array<{ step: string; reason: string }>
  userAction?: { step: string; reason: string } | null
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
export interface HermesRepoStatusFile {
  path: string
  staged: boolean
  unstaged: boolean
  untracked: boolean
  conflicted: boolean
}
export interface HermesReviewFile {
  path: string
  added: number
  removed: number
  // M(odified) A(dded) D(eleted) R(enamed) C(opied) U(nmerged) ?(untracked)
  status: string
  staged: boolean
}
export interface HermesReviewPr {
  url: string
  state: string
  number: number
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
export interface DisabledNative {
getAgentRoster?: () => Promise<DesktopAgentRoster>
claimAmbientCue: (key: string) => Promise<boolean>
wakeIndicator?: {
        getState: () => Promise<WakeIndicatorState>
        setState: (state: WakeIndicatorState) => void
        onState: (callback: (state: WakeIndicatorState) => void) => () => void
      }
petOverlay: {
        open: (request: PetOverlayOpenRequest) => Promise<{ ok: boolean; bounds?: PetOverlayBounds }>
        close: () => Promise<{ ok: boolean }>
        setBounds: (bounds: PetOverlayBounds) => void
        setIgnoreMouse: (ignore: boolean) => void
        setFocusable: (focusable: boolean) => void
        pushState: (payload: PetOverlayStatePayload) => void
        control: (payload: PetOverlayControl) => void
        onState: (callback: (payload: PetOverlayStatePayload) => void) => () => void
        onControl: (callback: (payload: PetOverlayControl) => void) => () => void
      }
applyConnectionConfig: (payload: DesktopConnectionConfigInput) => Promise<DesktopConnectionConfig>
testConnectionConfig: (payload: DesktopConnectionConfigInput) => Promise<DesktopConnectionTestResult>
connections: {
        list: () => Promise<DesktopConnectionsRegistry>
        save: (
          payload: DesktopRegistryConnectionInput
        ) => Promise<{ ok: boolean; connection: DesktopRegistryConnection; registry: DesktopConnectionsRegistry }>
        remove: (id: string) => Promise<{ ok: boolean; registry: DesktopConnectionsRegistry }>
        setPrimary: (id: string) => Promise<{ ok: boolean; registry: DesktopConnectionsRegistry }>
        setLaunchMode?: (
          mode: 'last-used' | 'primary'
        ) => Promise<{ ok: boolean; registry: DesktopConnectionsRegistry }>
        setLastUsed?: (id: string) => Promise<{ ok: boolean; registry: DesktopConnectionsRegistry }>
        test: (id: string) => Promise<DesktopConnectionTestResult>
        // Drain/update/restore one Desktop-managed SSH install. External URL
        // and cloud sources are refused without touching their processes.
        updateManaged?: (id: string) => Promise<DesktopManagedConnectionUpdateResult>
        // Fan out `hermes update` to every eligible registered connection;
        // cloud entries are skipped (platform-managed), each row independent.
        // excludeIds skips connections the caller updates through another
        // path (the everything-update flow's active backend + local client).
        updateAll?: (options?: {
          excludeIds?: string[]
        }) => Promise<{ ok: boolean; results: DesktopConnectionUpdateResult[] }>
        // Registry lifecycle push: fired when a connection is removed or
        // materially edited so the renderer can dispose (and re-dial) the
        // secondary gateways scoped to it. Optional: older Electron mains
        // don't emit it.
        onChanged?: (
          callback: (payload: { connectionId: string; reason: 'removed' | 'saved' | 'updated' }) => void
        ) => () => void
      }
probeConnectionConfig: (remoteUrl: string) => Promise<DesktopConnectionProbeResult>
oauthLoginConnectionConfig: (
        remoteUrl: string,
        options?: DesktopOauthLoginOptions
      ) => Promise<DesktopOauthLoginResult>
oauthLogoutConnectionConfig: (remoteUrl: string) => Promise<DesktopOauthLogoutResult>
profile: {
        getDefault: () => Promise<DesktopProfileRoute | null>
        setDefault: (route: DesktopProfileRoute) => Promise<DesktopProfileRoute>
        onDefaultChanged: (callback: (route: DesktopProfileRoute | null) => void) => () => void
        get: () => Promise<DesktopActiveProfile>
        // Remembers last use without interrupting a live workspace switch or
        // replacing an explicit default route.
        remember: (name: string | null) => Promise<DesktopActiveProfile>
        // Persists the desktop's profile choice and relaunches the local
        // backend under the new HERMES_HOME (reloads the window). Pass null to
        // clear the preference.
        set: (name: string | null) => Promise<DesktopActiveProfile>
      }
requestMicrophoneAccess: () => Promise<boolean>
readWindowBelow?: () => Promise<{
        frontmost: { app: string; title: string } | null
        note?: string
        platform: string
        window: {
          app: string
          bounds: { height: number; width: number; x: number; y: number }
          id: number
          title: string
        } | null
      } | null>
readFileDataUrl: (filePath: string) => Promise<string | HermesReadFileErrorResult>
readFileDataUrlForAttach?: (filePath: string) => Promise<string | HermesReadFileErrorResult>
selectPaths: (options?: HermesSelectPathsOptions) => Promise<string[]>
selectSavePath?: (options?: {
        defaultPath?: string
        filters?: Array<{ extensions: string[]; name: string }>
        title?: string
      }) => Promise<null | string>
saveTextFileAs?: (request: {
        content: string
        defaultPath?: string
        filters?: Array<{ extensions: string[]; name: string }>
        title?: string
      }) => Promise<{ canceled?: boolean; path?: string; saved: boolean }>
writeClipboard: (text: string) => Promise<boolean>
setTitleBarTheme?: (payload: HermesTitleBarTheme) => void
setNativeTheme?: (mode: 'dark' | 'light' | 'system') => void
glassSupported?: boolean
translucencySupported?: boolean
localModelsEnabled?: boolean
assumeFreshInstall?: boolean
setTranslucency?: (payload: TranslucencyState) => void
openExternal: (url: string) => Promise<void>
freeTierChallenge?: {
        run: (request: {
          url: string
          required: boolean
          expiresIn?: number
          attempt?: number
        }) => Promise<ChallengeOutcome>
      }
resolveFavicon?: (url: string) => Promise<string>
preparedSubmissions?: {
        read: () => Promise<string>
        update: (key: string, entry: string | null) => Promise<void>
        /** Atomic in the main process: replace `key` only while it holds exactly `expected`
         *  (null = absent). `current` is the record stored afterwards. Group Send requires it. */
        compareAndSet?: (
          key: string,
          expected: string | null,
          entry: string | null
        ) => Promise<{ applied: boolean; current: string | null }>
      }
openDir?: (path: string) => Promise<{ ok: boolean; error?: string }>
logsRoot?: (profile?: string) => Promise<string>
git?: {
        worktreeList: (repoPath: string) => Promise<HermesGitWorktree[]>
        worktreeAdd: (
          repoPath: string,
          options?: { name?: string; branch?: string; base?: string; existingBranch?: string }
        ) => Promise<{ path: string; branch: string; repoRoot: string }>
        worktreeRemove: (
          repoPath: string,
          worktreePath: string,
          options?: { force?: boolean }
        ) => Promise<{ removed: string }>
        branchSwitch: (repoPath: string, branch: string) => Promise<{ branch: string }>
        // The local branches, plus the remote-tracking refs that have no local
        // branch, for the "convert a branch into a worktree" picker.
        branchList: (repoPath: string) => Promise<HermesGitBranch[]>
        // Local + remote-tracking branches for the "base branch" picker in the
        // new-worktree dialog. The remote default (origin/HEAD) is flagged so
        // the UI can preselect it.
        baseBranchList: (repoPath: string) => Promise<HermesGitBaseBranch[]>
        // Compact working-tree status for the composer coding rail. Null on a
        // non-repo / remote backend (where the Electron probe can't run).
        repoStatus: (repoPath: string) => Promise<HermesRepoStatus | null>
        // Working-tree-vs-HEAD unified diff for one file (the preview's diff
        // view). Empty string when the file is unchanged or not in a repo.
        fileDiff: (repoPath: string, filePath: string) => Promise<string>
        // Codex-style review pane: changed files per scope, per-file diff, and
        // stage / unstage / revert.
        review: {
          list: (repoPath: string, scope: HermesReviewScope, baseRef?: null | string) => Promise<HermesReviewList>
          diff: (
            repoPath: string,
            filePath: string,
            scope: HermesReviewScope,
            baseRef?: null | string,
            staged?: boolean
          ) => Promise<string>
          stage: (repoPath: string, filePath?: null | string) => Promise<{ ok: boolean }>
          unstage: (repoPath: string, filePath?: null | string) => Promise<{ ok: boolean }>
          revert: (repoPath: string, filePath?: null | string) => Promise<{ ok: boolean }>
          revParse: (repoPath: string, ref?: null | string) => Promise<null | string>
          commit: (repoPath: string, message: string, push: boolean) => Promise<{ ok: boolean }>
          // Diff (staged-or-all) + recent commit subjects for drafting a
          // commit message. Reads only; empty strings off-repo.
          commitContext: (repoPath: string) => Promise<{ diff: string; recent: string }>
          push: (repoPath: string) => Promise<{ ok: boolean }>
          shipInfo: (repoPath: string) => Promise<HermesReviewShipInfo>
          // The PR on each of the given branches — plus any known only by
          // number — for badging a list of sessions in one request instead of
          // one `pr view` per checkout.
          prList: (repoPath: string, branches: string[], numbers?: number[]) => Promise<HermesRepoPullRequests>
          createPr: (repoPath: string) => Promise<{ url: string }>
        }
        // Repo-first discovery: scan bounded roots for git repos (depth-capped).
        scanRepos: (
          roots: string[],
          options?: { maxDepth?: number; enabled?: boolean; excludePaths?: string[] }
        ) => Promise<{ root: string; label: string }[]>
      }
reachPreviewUrl?: (url: string) => Promise<string>
onWindowStateChanged?: (callback: (payload: HermesWindowState) => void) => () => void
onPowerResume?: (callback: () => void) => () => void
updates: {
        check: (opts?: { force?: boolean }) => Promise<DesktopUpdateStatus>
        apply: (opts?: DesktopUpdateApplyOptions) => Promise<DesktopUpdateApplyResult>
        /** Persist this source install's update channel and return the fresh check. */
        setChannel?: (name: 'main' | 'stable') => Promise<DesktopUpdateStatus>
        onProgress: (callback: (payload: DesktopUpdateProgress) => void) => () => void
        /** Claim the pending packaged self-update run (null when none or already claimed). */
        takePendingRun?: () => Promise<UpdateRunReport | null>
        /** `sent` deletes the claimed record; false keeps it for the next attach. */
        ackPendingRun?: (sent: boolean) => Promise<void>
        onPendingRun?: (callback: () => void) => () => void
      }
onFoundInPage: (callback: (result: { activeMatchOrdinal: number; count: number }) => void) => () => void
onOpenFindBarRequested: (callback: () => void) => () => void
}
export function disabledNative(): DisabledNative | undefined { return undefined }
export type ChallengeOutcome = 'done' | 'failed' | 'closed' | 'timeout' | 'refused' | 'error'
export type UpdateRunOutcome = 'success' | 'failed' | 'noop' | 'refused'
export type UpdateFailedStage = 'download' | 'verify' | 'apply' | 'restart' | 'other'
export interface UpdateRunReport {
  outcome: UpdateRunOutcome
  mechanism: string
  duration_ms: number
  failed_stage?: UpdateFailedStage
  from_commit_date?: number
}

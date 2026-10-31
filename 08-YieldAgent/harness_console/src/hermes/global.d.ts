// Type-only shim for H/apps/desktop/src/global.d.ts:1016,1553. PB:414,D10.
export interface HermesConnection {
  baseUrl: string
  customWindowControls?: boolean
  darwinMajor?: number
  isFullscreen: boolean
  isMaximized?: boolean
  // The live, RESOLVED connection mode. Only ever 'local' or 'remote' — a
  // 'cloud' saved-config entry resolves to a 'remote' connection under the hood
  // (cloud-auto-discovery Q3/Q6), so this never carries 'cloud'.
  mode?: 'local' | 'remote'
  // 'native': this machine's gateway; no static token, per-call private grants.
  authMode?: 'native' | 'oauth' | 'token'
  remoteHost?: string
  remoteIdentity?: string
  remoteKind?: 'cloud' | 'ssh' | 'url'
  remoteHermesVersion?: string
  nativeOverlayWidth: number
  source?: 'env' | 'local' | 'settings'
  token: string
  wsUrl: string
  logs: string[]
  // Set for pool (non-primary) backends so the renderer knows which profile a
  // connection belongs to.
  profile?: string
  // The registry connection this descriptor resolves to. Registry-scoped
  // secondaries carry it directly; legacy primary remotes preserve it from
  // their selected stored route before dialing.
  connectionId?: string
  // True only when getConnectionFor explicitly resolved a v2 registry route.
  // An inferred connectionId identifies the visible source but its v1 profile
  // name may still be a client-side routing alias rather than a backend profile.
  registryScoped?: boolean
  // True only when `profile` is a request scope on the shared primary backend.
  // A pooled backend also carries `profile`, so presence alone cannot identify
  // the shared-primary routing case.
  sharedPrimary?: boolean
  // True when `profile` is a request scope on a SHARED registry remote/cloud
  // backend (one host, many profiles) — the registry analogue of sharedPrimary.
  sharedRemote?: boolean
  windowButtonPosition: { x: number; y: number } | null
}

export interface HermesApiRequest {
  path: string
  method?: string
  body?: unknown
  // Single-file multipart upload (FastAPI UploadFile endpoints). Mutually
  // exclusive with `body`; bytes transfer over IPC as a structured-clone
  // ArrayBuffer. Token-mode backends only.
  upload?: { filename: string; contentType?: string; bytes: ArrayBuffer }
  timeoutMs?: number
  // Route this REST call to a specific profile's backend. Omit for the primary
  // (window) backend. Read-only cross-profile data is served by the primary, so
  // this is only needed for profile-scoped live/settings calls.
  profile?: string | null
  // Route this REST call to a specific REGISTERED gateway connection (v2
  // registry). Data owned by a remote gateway — cron jobs and their run
  // sessions — lives in that host's state.db, so requests for it must resolve
  // through the owning connection, not the local profile pool. Omit / '' to
  // keep the legacy profile-routed path; explicit 'local' forces this device.
  connectionId?: string | null
  // Passive background read that must never cold-start a pooled backend (#103375).
  // When true and the target profile has no warm pool entry, the main process
  // fails fast without spawning a child or consuming a pool slot, so background
  // tile reconciles cannot starve interactive opens.
  passive?: boolean
  // An interactive Settings scope selection may cold-start a profile backend.
  // Keep that intent separate from passive hydration so the pool can reserve a
  // slot for the user's visible request.
  priority?: 'foreground'
}

// Structural URL resolver compatibility only; no Electron runtime is installed.
declare global { interface Window { hermesDesktop: import('./platform-bridge').WebDesktopBridge & {
  api?<T = unknown>(request: HermesApiRequest): Promise<T>
  preparedSubmissions?: { read(): Promise<string>; update(key: string, entry: string | null): Promise<void> }
} } }

// Type-only original registry descriptors (H/global.d.ts:1180-1224).
export type DesktopConnectionKind = 'cloud' | 'local' | 'remote' | 'ssh'

// A registered agent source as the renderer sees it: token bytes never cross
// the IPC boundary (preview + set flag instead, like DesktopConnectionConfig).
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


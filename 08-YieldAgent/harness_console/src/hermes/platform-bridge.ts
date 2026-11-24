// Replaces H/apps/desktop/src/api/client.ts:633-639,700-702 Electron API calls.
// PB:414,D10. Host is explicitly installed; importing transport starts no effects.
import type { HermesApiRequest } from './global'
import type { ResolveGatewayWsUrlDeps, GatewayWsUrlResult } from '@hermes/shared'
export interface WebUrlBridge extends ResolveGatewayWsUrlDeps {
  getGatewayWsUrlFor?: (scope: { connectionId: string; profile?: string }) => Promise<GatewayWsUrlResult>
}
export interface WebApiBridge { api<T>(request: HermesApiRequest): Promise<T> }
let host: WebApiBridge | undefined
export function installWebApiBridge(next: WebApiBridge): () => void {
  if (host && host !== next) throw new Error('A web API host is already installed')
  host = next
  return () => { if (host === next) host = undefined }
}
export function webApi<T>(request: HermesApiRequest): Promise<T> {
  return host ? host.api<T>(request) : Promise.reject(new Error('Web host is not installed'))
}

// Replaces H/apps/desktop/src/store/gateway.ts:471-491,693,1128 and registry IPC.
// PB:D10,680. No ambient window lookup; the owning web host installs/removes it.
export interface WebDesktopBridge extends WebUrlBridge {
  saveGatewayFile?: (payload: {
    path: string; sessionId?: string; profile?: string; connectionId?: string; suggestedName?: string
  }) => Promise<{ saved: boolean }>
  getConnection(profile?: string): Promise<import('./global').HermesConnection>
  getConnectionFor?: (scope: { connectionId: string; profile?: string }) => Promise<import('./global').HermesConnection>
  settings?: { getDefaultProjectDir: () => Promise<{ dir?: string }> }
  sanitizeWorkspaceCwd?: (cwd: string) => Promise<{ cwd: string }>
  readonly supportsSecondaryGateways?: boolean
}
let desktop: WebDesktopBridge | undefined
export function installWebDesktopBridge(next: WebDesktopBridge): () => void {
  if (desktop && desktop !== next) throw new Error('A web desktop bridge is already installed')
  desktop = next
  return () => { if (desktop === next) desktop = undefined }
}
export function getWebDesktopBridge(): WebDesktopBridge | undefined { return desktop }

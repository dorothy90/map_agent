// PB:674–680: native filesystem disabled; upstream public signatures retained.
import type { HermesConnection } from '@/global'
import type { HermesReadDirResult, HermesReadFileErrorResult, HermesReadFileTextResult, HermesSelectPathsOptions } from './desktop-fs-types'
import { $connection } from '@/store/session'
export interface DesktopFsRemotePicker {
  selectPaths: (options?: HermesSelectPathsOptions) => Promise<string[]>
}
export function setDesktopFsRemotePicker(next: DesktopFsRemotePicker | null) {}
function connectionCacheKey(connection: HermesConnection | null) {
  if (!connection) {
    return 'local:'
  }

  // A profile belongs to a registry connection, not the whole Desktop. The
  // registry id is the isolation boundary, including for SSH connections; the
  // stable host identity below is only the fallback for legacy connections.
  if (connection.connectionId) {
    return `connection:${connection.connectionId}:${connection.profile || ''}`
  }

  const target =
    connection.remoteKind === 'ssh'
      ? connection.remoteIdentity || connection.remoteHost || ''
      : connection.baseUrl || ''

  return `${connection.mode || 'local'}:${connection.remoteKind || ''}:${connection.profile || ''}:${target}`
}
export function desktopFsCacheKey(connection: HermesConnection | null = $connection.get()) {
  return connectionCacheKey(connection)
}
export function isDesktopFsRemoteMode() { return false }
export function desktopFsProfile(): string | undefined {
  return $connection.get()?.profile || undefined
}
export function isReadFileErrorResult(value: unknown): value is HermesReadFileErrorResult {
  return !!value && typeof value === 'object' && (value as { ok?: unknown }).ok === false
}
export class DesktopFileMissingError extends Error {
  readonly code: string

  constructor(result: HermesReadFileErrorResult) {
    super(result.message || `File read failed: ${result.error}`)
    this.name = 'DesktopFileMissingError'
    this.code = result.error
  }
}
export async function readDesktopDir(path: string): Promise<HermesReadDirResult> { throw new Error('Native filesystem capability is disabled') }
export async function readDesktopFileText(path: string): Promise<HermesReadFileTextResult> { throw new Error('Native filesystem capability is disabled') }
export async function writeDesktopFileText(path: string, content: string): Promise<{ path: string }> { throw new Error('Native filesystem capability is disabled') }
export async function createRemoteDir(path: string): Promise<string> { throw new Error('Native filesystem capability is disabled') }
export async function readDesktopFileDataUrl(path: string): Promise<string> { throw new Error('Native filesystem capability is disabled') }
export async function readDesktopFileDataUrlLocalFirst(path: string): Promise<string> { throw new Error('Native filesystem capability is disabled') }
export async function desktopGitRoot(path: string): Promise<string | null> { throw new Error('Native filesystem capability is disabled') }
export async function desktopDefaultCwd(): Promise<{ branch: string; cwd: string } | null> { throw new Error('Native filesystem capability is disabled') }
export async function revealDesktopPath(path: string): Promise<void> { throw new Error('Native filesystem capability is disabled') }
export async function renameDesktopPath(path: string, newName: string): Promise<string> { throw new Error('Native filesystem capability is disabled') }
export async function trashDesktopPath(path: string): Promise<void> { throw new Error('Native filesystem capability is disabled') }
export async function copyTextToClipboard(text: string): Promise<void> { throw new Error('Native filesystem capability is disabled') }
export async function desktopFileDiff(repoRoot: string, filePath: string): Promise<string> { throw new Error('Native filesystem capability is disabled') }
export async function selectDesktopPaths(options?: HermesSelectPathsOptions): Promise<string[]> { throw new Error('Native filesystem capability is disabled') }

// Upstream global.d.ts type-only excerpt; no native bridge.
export interface HermesReadFileTextResult {
  binary?: boolean
  byteSize?: number
  language?: string
  mimeType?: string
  path: string
  text: string
  truncated?: boolean
}
export interface HermesReadFileErrorResult {
  ok: false
  error: string
  message: string
  path?: string
}
export interface HermesReadDirEntry {
  name: string
  path: string
  isDirectory: boolean
}
export interface HermesReadDirResult {
  entries: HermesReadDirEntry[]
  error?: string
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
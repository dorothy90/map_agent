// PB:674–680: plugin REST/socket/native backend discovery disabled.
import type { HermesConnection } from '@/global'
export async function activeConnection(): Promise<HermesConnection> { throw new Error('Plugin capability is disabled') }
export interface PluginRestOptions {
  method?: string
  body?: unknown
  /** Single-file multipart upload (see HermesApiRequest.upload). */
  upload?: { filename: string; contentType?: string; bytes: ArrayBuffer }
  timeoutMs?: number
}
export async function pluginRest<T>(pluginId: string, path: string, opts: PluginRestOptions = {}): Promise<T> { throw new Error('Plugin capability is disabled') }
export function pluginSocket(pluginId: string, path: string, onMessage: (data: unknown) => void): () => void { return () => {} }

// PB:674–680: preview cross-window handoff disabled. No listeners, channels, native calls or persistence.
import type { AnnotatePin } from './stack'
export const PREVIEW_ANNOTATE_HANDOFF_CHANNEL = 'hermes.desktop.preview-annotate-handoff.v1'
export const PREVIEW_ANNOTATE_WINDOW_ID_KEY = 'hermes.desktop.previewAnnotate.windowId'
export interface PreviewAnnotateComposerDestination {
  kind: 'composer'
  surfaceId: string
  target: string
  windowId: string
}
export interface PreviewAnnotateGroupDestination {
  composerKey: string
  group: string
  kind: 'group'
  windowId: string
}
export type PreviewAnnotateDestination = PreviewAnnotateComposerDestination | PreviewAnnotateGroupDestination
export interface PreviewAnnotateHandoffImage {
  dataUrl: string
  name: string
  number: number
}
export interface PreviewAnnotateHandoffRequest {
  count: number
  destination: PreviewAnnotateDestination
  images: PreviewAnnotateHandoffImage[]
  prompt: string
  requestId: string
  tabId: string
  type: 'preview-annotate-handoff'
}
export interface PreviewAnnotateHandoffAck {
  error?: string
  ok: boolean
  requestId: string
  type: 'preview-annotate-handoff-ack'
}
export interface PreviewAnnotateHandoffResult {
  error?: string
  ok: boolean
}
export function previewAnnotateWindowId(): string { return '' }
export function capturePreviewAnnotateDestination(anchor: Element | null = null): PreviewAnnotateDestination | null { return null }
export function rememberPreviewAnnotateDestination(tabId: string, destination: PreviewAnnotateDestination | null) {}
export function clearPreviewAnnotateDestination(tabId: string) {}
export function readPreviewAnnotateDestination(tabId: string): PreviewAnnotateDestination | null { return null }
export function isPreviewAnnotateHandoffRequest(value: unknown): value is PreviewAnnotateHandoffRequest { return false }
export function subscribePreviewAnnotateHandoff(
  handler: (
    request: PreviewAnnotateHandoffRequest
  ) => PreviewAnnotateHandoffResult | null | Promise<PreviewAnnotateHandoffResult | null>
): () => void { return () => {} }
export async function handoffPreviewAnnotateStack(
  tabId: string,
  pins: readonly AnnotatePin[],
  pageUrl?: string
): Promise<PreviewAnnotateHandoffResult> { return { ok: false, error: 'Preview capability is disabled' } }

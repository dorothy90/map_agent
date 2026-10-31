// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import type { PreviewOwner } from '@/store/preview-ownership'
export const PREVIEW_BROWSER_ATTR = 'data-preview-browser'
export interface PreviewNavHandle {
  back: () => void
  forward: () => void
  reload: () => void
}
export function registerPreviewNav(tabId: string, handle: PreviewNavHandle): () => void { return () => {} }
export function activePreviewNav(owner?: PreviewOwner): PreviewNavHandle | null { return null }
export function commandFocusedPreview(command: keyof PreviewNavHandle): boolean { return false }

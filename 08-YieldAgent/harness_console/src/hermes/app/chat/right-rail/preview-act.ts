// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import { type PreviewActAction, type PreviewActResult } from '@/lib/preview-act/act-in-page'
import type { PreviewOwner } from '@/store/preview-ownership'
export async function actOnActivePreview(
  action: Omit<PreviewActAction, 'kind'> & { kind: string },
  signal?: AbortSignal,
  owner?: PreviewOwner
): Promise<PreviewActResult> { return { success: false, error: 'Preview capability is disabled' } }

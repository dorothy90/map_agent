// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import type { PreviewActAction, PreviewActResult } from '@/lib/preview-act/act-in-page'
import type { PreviewOwner } from '@/store/preview-ownership'
import { type PreviewReadOptions, type PreviewReadResult } from './preview-reader'
export function hasLivePreviewSurface(owner?: PreviewOwner): boolean { return false }
export function requestPopoutPreviewAct(payload: ActPayload, owner?: PreviewOwner): Promise<PreviewActResult | null> { return Promise.resolve(null) }
export function requestPopoutPreviewRead(
  payload: PreviewReadOptions = {},
  owner?: PreviewOwner
): Promise<PreviewReadResult | null> { return Promise.resolve(null) }
export function installPopoutPreviewResponder(): () => void { return () => {} }
type ActPayload = Omit<PreviewActAction, 'kind'> & { kind: string }

// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import type { ChatBarProps } from '../types'
export function useComposerScreenshot({ sessionKey, focusKey, onAttachImageBlob }: ScreenshotComposerOptions) {}
interface ScreenshotComposerOptions {
  sessionKey: string | null
  focusKey?: string | null
  onAttachImageBlob: ChatBarProps['onAttachImageBlob']
}

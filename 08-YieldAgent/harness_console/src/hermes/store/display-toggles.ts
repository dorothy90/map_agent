// PB:674–680: model/settings synchronization is disabled in the web port.
// Upstream store/display-toggles.ts:64–79 subscribes to gateway connections
// and sends config.set. Keep the export contract without installing effects.
import type { ReadableAtom } from 'nanostores'

export function mirrorDisplayToggle(configKey: string, storageKey: string, $enabled: ReadableAtom<boolean>): void {
  void configKey
  void storageKey
  void $enabled
}

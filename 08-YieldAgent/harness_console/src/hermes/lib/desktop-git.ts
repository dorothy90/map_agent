// PB:680: native/remote git disabled; original desktopGit signature.
import type { DisabledNative } from '@/pr9-disabled-native'
type GitBridge = DisabledNative['git']
export function desktopGit(): GitBridge | undefined { return undefined }

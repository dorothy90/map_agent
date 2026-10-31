// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import type { ReactNode } from 'react'
import type { DesktopProfileRoute } from '@/pr9-native-types'
export function ProfileLaunchMenuItems({ connectionId, label, profile }: ProfileLaunchMenuProps) { return null }
export function ProfileLaunchContextMenu({ children, ...route }: ProfileLaunchMenuProps & { children: ReactNode }) { return null }
export function ProfileLaunchMenuSection(props: ProfileLaunchMenuProps) { return null }
interface ProfileLaunchMenuProps extends DesktopProfileRoute {
  label: string
}

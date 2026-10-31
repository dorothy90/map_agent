// Type-only shim for H/apps/desktop/src/app/shell/statusbar-controls.tsx:33-44; no UI runtime (PB:D03,D10).
import type { ReactNode } from 'react'
export interface StatusbarMenuItem {
  id: string
  icon?: ReactNode
  label: string
  className?: string
  disabled?: boolean
  hidden?: boolean
  href?: string
  onSelect?: () => void
  title?: string
  to?: string
}


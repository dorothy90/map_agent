// Disabled web shim for H/apps/desktop/src/components/chat/vibe-hearts.tsx:62; PB:674,680,D10.
export const burstVibeHearts = (): void => {}

import type { CSSProperties } from 'react'
import type { ParticleFieldConfig } from '@/pr9-particle-types'
export const COMPOSER_HEART_CONFIG: Partial<ParticleFieldConfig> = {
  count: 12,
  size: [6, 13],
  rise: [6.75, 15.75],
  duration: [320, 700]
}
export interface HeartFieldProps {
  config?: Partial<ParticleFieldConfig>
  className?: string
  style?: CSSProperties
}
export function HeartField({ config, className, style }: HeartFieldProps) { return null }

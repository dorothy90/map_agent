// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import type { TourPreset } from '@hermes/shared'
import { type TourResult } from '@/lib/tour'
export async function runBuiltInTour(
  preset: TourPreset,
  stillActive: () => boolean,
  notActiveError: string
): Promise<TourResult> { return { success: false, error: 'Tour capability is disabled' } }

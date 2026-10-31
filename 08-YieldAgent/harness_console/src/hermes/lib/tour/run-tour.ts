// PB:680: tours disabled. Original action signatures; no driver.js or DOM effects.
import type { PreviewOwner } from '@/store/preview-ownership'
import type { TourAction, TourResult, TourStep } from './engine'
export type TourSurface = 'app' | 'preview'
export async function runTour(
  action: TourAction,
  surface: TourSurface = 'app',
  /** The requesting session's stored id, for surface='preview': the tour runs
   *  in that session's page (omitted = the focused session's). */
  owner?: PreviewOwner
): Promise<TourResult> { return { success: false, error: 'Tour capability is disabled' } }
export const isTourActive = (): boolean => false
export const listTourTargets = (surface?: TourSurface) => runTour({ kind: 'targets' }, surface)
export const showTourStep = (step: TourStep, surface?: TourSurface) => runTour({ ...step, kind: 'show' }, surface)
export const startTour = (steps: TourStep[], surface?: TourSurface, startAt = 0) =>
  runTour({ kind: 'start', startAt, steps }, surface)
export const nextTourStep = (surface?: TourSurface) => runTour({ kind: 'next' }, surface)
export const previousTourStep = (surface?: TourSurface) => runTour({ kind: 'prev' }, surface)
export const stopTour = (surface?: TourSurface) => runTour({ kind: 'stop' }, surface)

// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import type { PointerEvent as ReactPointerEvent } from 'react'
import type { DropPosition } from '../model'
import { type DropHint } from '../store'
import { type EngineZone, type ZoneRect } from '../zones-engine'
export function snapshotZones(): EngineZone[] { return [] }
export function radialPosition(
  rect: { left: number; top: number; right: number; bottom: number },
  x: number,
  y: number,
  centerRadius = CENTER_RADIUS
): DropPosition { return 'center' }
export function subZonePosition(zones: EngineZone[], groupId: string, x: number, y: number): DropPosition { return 'center' }
export function slotBefore(
  slots: StripSlot[],
  x: number,
  exclude: readonly string[] | string = ''
): { before: null | string } { return { before: null } }
export interface StripSnapshot {
  groupId: string
  rect: ZoneRect
  slots: StripSlot[]
}
export function snapshotStrips(): StripSnapshot[] { return [] }
export const rectContains = (rect: ZoneRect, x: number, y: number, pad = 0): boolean => false
export interface DragSessionSpec {
  /** Movement crossed the drag threshold: snapshot geometry, set the drag
   *  store(s), dim/mark the source. Runs once. */
  onEngage(x: number, y: number): void
  /** Per-frame target resolution — pure math against drag-start snapshots.
   *  Returns the hint to publish; `null` = deny area (no-drop cursor, a
   *  release commits nothing). */
  resolveMove(x: number, y: number, shift: boolean): DropHint | null
  /** Release over the final published hint (already flushed to the exact
   *  release position). Only called for engaged drags. */
  onCommit(hint: DropHint | null): void
  /** Teardown for both commit and abort — undo whatever onEngage marked. */
  onEnd?(): void
  /** Sub-threshold release = a click on the handle. */
  onTap?(): void
  /** Floating chip following the pointer — for drags whose source doesn't
   *  stay visibly "held" (a sidebar row, unlike a dimmed tab). See
   *  `@/lib/drag-ghost`. */
  ghost?: { label: string }
}
export function startDragSession(e: ReactPointerEvent<HTMLElement>, spec: DragSessionSpec) {}
export function startPaneDrag(
  paneId: string,
  e: ReactPointerEvent<HTMLElement>,
  onTap?: () => void,
  reorder?: ReorderContext,
  ghostLabel?: string,
  /** Multi-tab selection riding this drag (strip order, includes `paneId`).
   *  The whole block moves/reorders together; `paneId` stays the pressed tab
   *  (it fronts at the destination). */
  selection?: readonly string[]
) {}
const CENTER_RADIUS = 0.62
interface StripSlot {
  id: string
  mid: number
}
interface ReorderContext {
  groupId: string
  /** The tab-strip element; tabs carry `data-tree-tab={paneId}`. */
  strip: HTMLElement
}

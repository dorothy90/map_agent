import { useEffect, useRef } from 'react'

const edges = ['n', 's', 'e', 'w', 'nw', 'ne', 'sw', 'se'] as const
type Edge = typeof edges[number]
type Point = { screenX: number; screenY: number }
export type WindowBounds = { x: number; y: number; width: number; height: number }
export interface YuriDesktopBridge {
  setExpanded(value: { open: boolean; maximized?: boolean }): void
  getWindowMode?(): Promise<{ maximized: boolean }>
  setReplyPreview?(show: boolean): Promise<{ mascotOffsetX: number; below?: boolean } | null>
  getRestoreBounds?(): Promise<{ from: WindowBounds; to: WindowBounds } | null>
  setMaximized?(next: boolean): Promise<{ maximized: boolean; from?: WindowBounds; to?: WindowBounds } | null>
  toggleMaximized?(): Promise<boolean>
  onCollapse(callback: () => void): () => void
  getCollapseTarget?(): Promise<{x:number;y:number} | null>
  captureCollapseFrame(): Promise<ArrayBuffer | null>
  beginResize(value: Point & { edge: Edge }): void
  moveResize(value: Point): void
  endResize(): Promise<unknown>
}

const arrowDelta: Record<string, [number, number]> = {
  ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20],
}

export function YuriWindowResize({ desktop }: { desktop: YuriDesktopBridge }) {
  const pointerId = useRef<number | null>(null)
  useEffect(() => {
    const move = (event: PointerEvent) => {
      if (pointerId.current === event.pointerId) desktop.moveResize({ screenX: event.screenX, screenY: event.screenY })
    }
    const finish = (event?: PointerEvent) => {
      if (pointerId.current === null || (event && event.pointerId !== pointerId.current)) return
      if (event?.type === 'pointerup') move(event)
      pointerId.current = null
      void desktop.endResize()
    }
    const blur = () => finish()
    window.addEventListener('pointermove', move, true)
    window.addEventListener('pointerup', finish, true)
    window.addEventListener('pointercancel', finish, true)
    window.addEventListener('blur', blur)
    return () => {
      window.removeEventListener('pointermove', move, true)
      window.removeEventListener('pointerup', finish, true)
      window.removeEventListener('pointercancel', finish, true)
      window.removeEventListener('blur', blur)
      finish()
    }
  }, [desktop])

  return <>{edges.map(edge => <button
    key={edge}
    type="button"
    className={`yuri-resize-handle yuri-resize-${edge}`}
    tabIndex={edge === 'se' ? 0 : -1}
    aria-label={edge === 'se' ? '창 크기 조절' : `${edge} 방향 창 크기 조절`}
    onPointerDown={event => {
      if (event.button !== 0 || pointerId.current !== null) return
      event.preventDefault()
      event.currentTarget.setPointerCapture(event.pointerId)
      pointerId.current = event.pointerId
      desktop.beginResize({ edge, screenX: event.screenX, screenY: event.screenY })
    }}
    onKeyDown={event => {
      const delta = arrowDelta[event.key]
      if (!delta || pointerId.current !== null) return
      event.preventDefault()
      desktop.beginResize({ edge, screenX: 0, screenY: 0 })
      desktop.moveResize({ screenX: delta[0], screenY: delta[1] })
      void desktop.endResize()
    }}
  />)}</>
}

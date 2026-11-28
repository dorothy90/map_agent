import { useLayoutEffect, useRef, useState } from 'react'

export function useComposerFlyout(open: boolean, nested: boolean, width: number, fallback: 'top' | 'bottom' = 'top') {
 const trigger = useRef<HTMLButtonElement>(null)
 const [layout, setLayout] = useState<{
  side: 'left' | 'right' | 'top' | 'bottom'
  boundary: Element | null
 }>({ side: fallback, boundary: null })

 useLayoutEffect(() => {
  const button = trigger.current
  if (!button || !open) return
  const boundary = button.closest('.yuri-window-frame') ??
   button.closest('.hermes-web')?.querySelector('.yuri-window-frame') ?? null
  const measure = () => {
   const frame = boundary?.getBoundingClientRect()
   const anchor = button.getBoundingClientRect()
   const availableRight = Math.min(frame?.right ?? innerWidth, innerWidth) - anchor.right
   const availableLeft = anchor.left - Math.max(frame?.left ?? 0, 0)
   // Keep the submenu's gap and Radix collision padding inside the frame.
   const required = width + 16
   const side = !nested ? fallback : availableRight >= required ? 'right' : availableLeft >= required ? 'left' : 'top'
   setLayout(previous => previous.side === side && previous.boundary === boundary ? previous : { side, boundary })
  }
  measure()
  window.addEventListener('resize', measure)
  const observer = typeof ResizeObserver === 'undefined' || !boundary ? null : new ResizeObserver(measure)
  if (boundary) observer?.observe(boundary)
  return () => {
   observer?.disconnect()
   window.removeEventListener('resize', measure)
  }
 }, [open, nested, width, fallback])

 return { trigger, side: layout.side, collisionBoundary: layout.boundary ?? undefined }
}

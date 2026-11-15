'use client'

import { useWebPresentation } from '../../../../web/presentation'

import { type CSSProperties, type ReactNode, useEffect, useRef, useState } from 'react'

import type { FrameEmbed } from './providers/types'
import { ScrollGate } from './scroll-gate'
import { useIsDark } from './use-is-dark'

const ALLOW = 'autoplay; encrypted-media; picture-in-picture; clipboard-write; fullscreen'
const TABLE_HEIGHT_MIN = 120
const TABLE_HEIGHT_MAX = 900

function isYieldTableHeightMessage(data: unknown): data is { type: 'yield-table-height'; height: number } {
  if (typeof data !== 'object' || data === null) {
    return false
  }

  const message = data as Record<string, unknown>
  return message.type === 'yield-table-height' && typeof message.height === 'number' && Number.isFinite(message.height)
}

export function YieldTableArtifactFrame({ children, src, title, subtitle }: { children: ReactNode; src: string; title: string; subtitle?: string }) {
  const web = useWebPresentation()
  const frameRef = useRef<HTMLIFrameElement>(null)
  const [reported, setReported] = useState<{ src: string; height: number } | null>(null)
  const height = reported?.src === src ? reported.height : null

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frameRef.current?.contentWindow || !isYieldTableHeightMessage(event.data)) {
        return
      }

      setReported({
        src,
        height: Math.round(Math.min(TABLE_HEIGHT_MAX, Math.max(TABLE_HEIGHT_MIN, event.data.height))),
      })
    }

    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [src])

  if (web) return <span className="web-report-section">
    <span className="web-report-heading"><span role="heading" aria-level={3}>{title}</span><a className="web-report-link" href={src} target="_blank" rel="noopener noreferrer">원본 열기</a></span>
    {subtitle && <span className="web-report-subtitle">{subtitle}</span>}
    <span className="web-report-scroll"><iframe className="yield-table-artifact-frame" loading="lazy" ref={frameRef} sandbox="allow-scripts" src={src} style={{height:height ?? 720}} title={title}/></span>
  </span>

  return (
    <span className={height === null
      ? 'block h-[720px] min-h-60 max-h-[85vh] resize-y overflow-auto'
      : 'block min-w-0 overflow-auto'}>
      {children}
      <a
        className="ref ml-2"
        href={src}
        rel="noopener noreferrer"
        target="_blank"
      >
        {web ? '원본 열기' : 'Open in new window'}
      </a>
      <iframe
        className={`yield-table-artifact-frame mt-2 block w-full border-0 ${height === null ? 'h-[calc(100%-2rem)]' : ''}`}
        loading="lazy"
        ref={frameRef}
        sandbox="allow-scripts"
        src={src}
        style={height === null ? undefined : { height }}
        title={title}
      />
    </span>
  )
}

// Plain iframes (not webviews): a non-scrollable cross-origin iframe lets the
// wheel chain to the transcript instead of capturing it. Maps are the one
// exception — they're interactive, so a ScrollGate blocks them until ⌘ is held.
export default function FrameEmbedRenderer({ descriptor }: { descriptor: FrameEmbed }) {
  const isDark = useIsDark()
  const isMap = descriptor.provider === 'googlemaps' || descriptor.provider === 'openstreetmap'
  // color-scheme makes the iframe's default (unpainted) backdrop follow the
  // theme instead of flashing white at the corners / during load.
  const colorScheme = isDark ? 'dark' : 'light'

  const style: CSSProperties = descriptor.aspectRatio
    ? { aspectRatio: descriptor.aspectRatio, colorScheme }
    : { colorScheme, height: descriptor.height }

  if (isMap) {
    return (
      <div className="relative w-full overflow-hidden" style={style}>
        <iframe
          allow={ALLOW}
          className="absolute inset-0 size-full border-0 bg-transparent"
          loading="lazy"
          referrerPolicy="strict-origin-when-cross-origin"
          src={descriptor.embedUrl}
          style={{ colorScheme }}
          title={`${descriptor.label} embed`}
        />
        <ScrollGate />
      </div>
    )
  }

  return (
    <iframe
      allow={ALLOW}
      allowFullScreen
      className="block w-full border-0 bg-transparent"
      loading="lazy"
      referrerPolicy="strict-origin-when-cross-origin"
      scrolling="no"
      src={descriptor.embedUrl}
      style={style}
      title={`${descriptor.label} embed`}
    />
  )
}

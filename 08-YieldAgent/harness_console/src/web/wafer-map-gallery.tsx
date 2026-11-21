import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { readWaferMaps, type WaferMapGroup, type WaferMaps } from './wafer-map-data'
import './wafer-map-gallery.css'

const palette = [[232, 244, 238], [145, 204, 195], [70, 132, 163], [77, 69, 126]]
const percent = new Intl.NumberFormat('ko-KR', { style: 'percent', maximumFractionDigits: 1 })
const count = new Intl.NumberFormat('ko-KR')
const dimensions: Record<string, string> = {
  recipe: 'RCP', equipment: '설비', chamber: '챔버', stepper_recipe: '스테퍼 RCP',
  lot: 'LOT', wafer: '웨이퍼', product: '제품', test: '검사', day: '일', week: '주', month: '월',
}

function MapCanvas({ maps, group, size }: { maps: WaferMaps; group: WaferMapGroup; size: number }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const context = ref.current?.getContext('2d')
    if (!context) return
    const side = (size - 6) / Math.max(maps.columns, maps.rows)
    const left = (size - maps.columns * side) / 2
    const top = (size - maps.rows * side) / 2
    context.clearRect(0, 0, size, size)
    for (const cell of group.cells) {
      const fraction = maps.high === maps.low ? 0.5 : (cell.fail_rate - maps.low) / (maps.high - maps.low)
      const position = fraction * (palette.length - 1)
      const i = Math.min(Math.floor(position), palette.length - 2)
      const rgb = palette[i].map((v, channel) => Math.round(v + (palette[i + 1][channel] - v) * (position - i)))
      context.fillStyle = 'rgb(' + rgb.join(',') + ')'
      const x = left + (cell.col - maps.minCol) * side
      const y = top + (cell.row - maps.minRow) * side
      context.fillRect(x, y, side * 0.98, side * 0.98)
    }
  }, [group, maps, size])
  return <canvas aria-hidden="true" ref={ref} width={size} height={size}/>
}

function groupStats(group: WaferMapGroup) {
  return count.format(group.waferCount) + '장 · ' + percent.format(group.failRate)
}

type ZoomSelection = { group: WaferMapGroup; trigger: HTMLButtonElement }

function MapZoom({ maps, group, trigger, dismiss }: ZoomSelection & { maps: WaferMaps; dismiss: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current!
    dialog.showModal()
    return () => {
      dialog.close()
      if (trigger.isConnected) trigger.focus({ preventScroll: true })
    }
  }, [trigger])
  return createPortal(<dialog ref={ref} className="wafer-map-zoom" aria-label={group.id + ' 맵 확대'}
    onCancel={event => { event.preventDefault(); dismiss() }} onClick={event => { if (event.target === ref.current) dismiss() }}>
    <div className="wafer-map-zoom-body">
      <button type="button" className="wafer-map-close" onClick={dismiss} aria-label="닫기"><X size={18}/></button>
      <MapCanvas maps={maps} group={group} size={768}/>
      <strong>{group.id}</strong><small>{groupStats(group)}</small>
    </div>
  </dialog>, document.body)
}

export function WaferMapGallery({ src, title, children }: { src: string; title: string; children: ReactNode }) {
  const [maps, setMaps] = useState<WaferMaps | null>()
  const [zoom, setZoom] = useState<ZoomSelection | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    const done = (value: WaferMaps | null) => { if (!controller.signal.aborted) setMaps(value) }
    void readWaferMaps(src, controller.signal).then(done, () => done(null))
    return () => controller.abort()
  }, [src])
  if (maps === null) return <>{children}</>
  const original = <a className="web-report-link" href={src} target="_blank" rel="noopener noreferrer">원본 열기</a>
  if (!maps) return <span className="wafer-map-loading"><span role="status">맵 불러오는 중…</span>{original}</span>
  const groupLabel = dimensions[maps.groupBy ?? '']
  const caption = groupLabel ? groupLabel + '별 누적 웨이퍼 맵' : title
  return <span className="wafer-map-gallery" role="figure" aria-label={caption}>
    <span className="wafer-map-heading"><strong>{caption}</strong>
      <span className="wafer-map-scale"><span>좌표별 불량률</span>{percent.format(maps.low)}
        {maps.low !== maps.high && <><i aria-hidden="true"/>{percent.format(maps.high)}</>}
      </span>
    </span>
    <span className="wafer-map-grid">{maps.groups.map(group =>
      <button className="wafer-map-tile" key={group.id} type="button" aria-label={group.id + ' 맵 확대'} onClick={event => setZoom({ group, trigger: event.currentTarget })}>
        <MapCanvas maps={maps} group={group} size={300}/>
        <strong>{group.id}</strong><small>{groupStats(group)}</small>
      </button>)}</span>
    <span className="wafer-map-original">{original}</span>
    {zoom && <MapZoom maps={maps} {...zoom} dismiss={() => setZoom(null)}/>}
  </span>
}

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, onTestFinished, vi } from 'vitest'
import { WaferMapGallery } from './wafer-map-gallery'

const src = '/harness/artifacts/selected?session_id=example'
const tables = ['wafer_map_groups', 'wafer_map_cells'].map(table_id => ({ table_id, complete: true }))
const observation = {
  result_id: 'right', tool_name: 'load_wafer_maps', scope: { map_aggregate: { group_by: 'recipe' } },
  tables, artifact_refs: [{ artifact_id: 'selected', mime: 'text/html' }],
}
const groups = Array.from({ length: 11 }, (_, n) => ({ group_id: 'Group ' + n, wafer_count: n + 1, fail_rate: n / 10 }))
const cells = Array.from({ length: 1001 }, (_, n) => ({
  group_id: groups[n % groups.length].group_id, row: Math.floor(n / 25) - 20, col: n % 25 - 12, fail_rate: n / 1000,
}))

function mockResults(badPage = false) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname === '/harness/sessions/example') {
      const selected = url.searchParams.has('cursor')
      const item = selected ? observation : { ...observation, result_id: 'other', artifact_refs: [{ artifact_id: 'draft', mime: 'text/html' }] }
      return Response.json({ session_id: 'example', runs: [{ observations: [item] }], next_cursor: selected ? null : 'next' })
    }
    if (url.pathname !== '/harness/results/right') throw Error('Unexpected result')
    const table_id = url.searchParams.get('table_id')
    const offset = Number(url.searchParams.get('offset'))
    const rows = table_id === 'wafer_map_groups' ? groups : cells
    return Response.json({ table_id, result_id: 'right', offset, total_rows: rows.length,
      rows: rows.slice(offset, offset + 1000), complete: !(badPage && offset > 0) })
  })
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('shows every group from the complete selected result and opens the chosen map', async () => {
  const fetch = mockResults()
  vi.stubGlobal('fetch', fetch)
  const context = { fillStyle: '', clearRect: vi.fn(), fillRect: vi.fn() }
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: { configurable: true, value(this: HTMLDialogElement) { this.open = true } },
    close: { configurable: true, value(this: HTMLDialogElement) { this.open = false } },
  })
  onTestFinished(() => {
    Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal')
    Reflect.deleteProperty(HTMLDialogElement.prototype, 'close')
  })
  render(<WaferMapGallery src={src} title="선택한 맵"><span>이전 뷰어</span></WaferMapGallery>)
  const gallery = await screen.findByRole('figure', { name: 'RCP별 누적 웨이퍼 맵' })
  expect(within(gallery).getAllByRole('button')).toHaveLength(11)
  expect(context.fillRect).toHaveBeenCalledTimes(1001)
  expect(within(gallery).getByRole('link', { name: '원본 열기' }).getAttribute('href')).toBe(src)
  const tile = within(gallery).getByRole('button', { name: 'Group 10 맵 확대' })
  expect(tile.textContent).toContain('11장 · 100%')
  fireEvent.click(tile)
  const zoom = screen.getByRole('dialog', { name: 'Group 10 맵 확대' })
  expect(zoom.textContent).toContain('11장 · 100%')
  expect(zoom.querySelector('canvas')!.width).toBeGreaterThan(tile.querySelector('canvas')!.width)
  fireEvent.click(within(zoom).getByRole('button', { name: '닫기' }))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(document.activeElement).toBe(tile)
  fireEvent.click(tile)
  fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(document.activeElement).toBe(tile)
})

it('restores the original viewer when a later page is incomplete', async () => {
  vi.stubGlobal('fetch', mockResults(true))
  render(<WaferMapGallery src={src} title="선택한 맵"><iframe src={src} sandbox="allow-scripts" title="원본"/></WaferMapGallery>)
  expect((await screen.findByTitle('원본')).getAttribute('sandbox')).toBe('allow-scripts')
  expect(screen.queryByRole('figure')).toBeNull()
})

it('opens a saved matrix without paging through its coordinate tables', async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), 'http://localhost').pathname
    if (path !== '/harness/sessions/example') throw Error('Unnecessary coordinate request')
    return Response.json({ session_id: 'example', runs: [{ observations: [{
      ...observation, scope: { map_aggregate: { matrix: { rows: { field: 'FLOW_ID' }, columns: { field: 'WF_ID' } } } },
    }] }] })
  })
  vi.stubGlobal('fetch', fetch)
  render(<WaferMapGallery src={src} title="맵"><iframe title="행렬" src={src}/></WaferMapGallery>)
  await screen.findByTitle('행렬')
  expect(fetch).toHaveBeenCalledTimes(1)
})

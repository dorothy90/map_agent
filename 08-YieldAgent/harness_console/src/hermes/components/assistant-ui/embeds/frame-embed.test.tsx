import { act, cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { YieldTableArtifactFrame } from './frame-embed'

afterEach(cleanup)

function post(frame: HTMLIFrameElement, data: unknown, source = frame.contentWindow) {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { data, source }))
  })
}

describe('YieldTableArtifactFrame', () => {
  it('uses a tall fallback, keeps the sandbox, and offers a visible new-window link', () => {
    const { getByRole, getByTitle } = render(
      <YieldTableArtifactFrame src="/artifact/table" title="table"><a href="/artifact/table">table</a></YieldTableArtifactFrame>
    )
    const frame = getByTitle('table') as HTMLIFrameElement

    expect(frame.style.height).toBe('')
    expect(frame.className).toContain('h-[calc(100%-2rem)]')
    expect(frame.parentElement?.className).toContain('h-[720px]')
    expect(frame.parentElement?.className).toContain('max-h-[85vh]')
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts')
    const openLink = getByRole('link', { name: 'Open in new window' })
    expect(openLink.getAttribute('href')).toBe('/artifact/table')
    expect(openLink.getAttribute('target')).toBe('_blank')
  })

  it('accepts only its own well-shaped height messages and clamps the height', () => {
    const { getByTitle } = render(
      <YieldTableArtifactFrame src="/artifact/table" title="table"><a href="/artifact/table">table</a></YieldTableArtifactFrame>
    )
    const frame = getByTitle('table') as HTMLIFrameElement

    post(frame, { type: 'yield-table-height', height: 80 })
    expect(frame.style.height).toBe('120px')

    post(frame, { type: 'yield-table-height', height: 1200 })
    expect(frame.style.height).toBe('900px')

    post(frame, { type: 'resize', height: 300 })
    post(frame, { type: 'yield-table-height', height: '300' })
    post(frame, { type: 'yield-table-height', height: 300 }, window)
    expect(frame.style.height).toBe('900px')

    post(frame, { type: 'yield-table-height', height: 321.4 })
    expect(frame.style.height).toBe('321px')
  })

  it('returns to the non-reporting fallback when the artifact source changes', () => {
    const { getByTitle, rerender } = render(
      <YieldTableArtifactFrame src="/artifact/table" title="artifact"><span>artifact</span></YieldTableArtifactFrame>
    )
    const tableFrame = getByTitle('artifact') as HTMLIFrameElement
    post(tableFrame, { type: 'yield-table-height', height: 321 })
    expect(tableFrame.style.height).toBe('321px')

    rerender(
      <YieldTableArtifactFrame src="/artifact/plot" title="artifact"><span>artifact</span></YieldTableArtifactFrame>
    )
    const plotFrame = getByTitle('artifact') as HTMLIFrameElement
    expect(plotFrame.style.height).toBe('')
    expect(plotFrame.parentElement?.className).toContain('h-[720px]')
  })
})

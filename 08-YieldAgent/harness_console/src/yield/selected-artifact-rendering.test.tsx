import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MarkdownTextContent } from '@/components/assistant-ui/markdown-text'
import { renderMediaTags } from '@/lib/chat-messages/parts'
import { $activeSessionId, $selectedStoredSessionId } from '@/store/session'

const session = 'selected-artifacts'
const url = (id: string) => `/harness/artifacts/${id}?session_id=${session}`
const maps = Array.from({ length: 8 }, (_, index) => ({
  title: `Same-source wafer map ${index + 1}`,
  href: url(`map-${index + 1}`),
}))
const answer = [
  `[Final report B](${url('final-b')})`,
  `![Selected PNG](${url('selected-png')})`,
  `[Presentation](${url('slides')})`,
  ...maps.map(({ title, href }) => `[${title}](${href})`),
].join('\n\n')

beforeEach(() => {
  $activeSessionId.set(session)
  $selectedStoredSessionId.set(null)
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => new Response(null, {
    headers: { 'Content-Type': String(input).includes('/slides?')
      ? 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
      : 'text/html; charset=utf-8' },
  })))
})

afterEach(() => {
  cleanup()
  $activeSessionId.set(null)
  $selectedStoredSessionId.set(null)
  vi.unstubAllGlobals()
})

describe('model-selected artifact answer rendering', () => {
  it.each(['live', 'stored'] as const)('preserves mixed artifacts and all eight maps in the %s view', async (view) => {
    if (view === 'stored') {
      $activeSessionId.set(null)
      $selectedStoredSessionId.set(session)
    }
    const { container, unmount } = render(<MarkdownTextContent isRunning={false} text={answer} />)
    await waitFor(() => expect(container.querySelectorAll('iframe')).toHaveLength(9))
    expect(screen.queryByRole('link', { name: 'Draft report A' })).toBeNull()
    expect(container.querySelector(`[src="${url('draft-a')}"]`)).toBeNull()
    expect(screen.getByRole('img', { name: 'Selected PNG' }).getAttribute('src')).toBe(url('selected-png'))
    expect(screen.getByRole('link', { name: 'Presentation' }).getAttribute('href')).toBe(url('slides'))
    expect(screen.queryByTitle('Presentation')).toBeNull()
    for (const { title, href } of maps) {
      expect(screen.getByTitle(title).getAttribute('src')).toBe(href)
    }
    const sources = Array.from(container.querySelectorAll('iframe'), frame => frame.getAttribute('src'))
    for (const frame of container.querySelectorAll('iframe')) {
      expect(frame.getAttribute('sandbox')).toBe('allow-scripts')
    }
    unmount()
    const restored = render(<MarkdownTextContent isRunning={false} text={answer} />)
    await waitFor(() => expect(restored.container.querySelectorAll('iframe')).toHaveLength(9))
    expect(Array.from(restored.container.querySelectorAll('iframe'), frame => frame.getAttribute('src'))).toEqual(sources)
  })

  it.each([403, 404, 500])('retains the authored link without embedding a failed %s probe', async status => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status, headers: { 'Content-Type': 'text/html' } }))
    render(<MarkdownTextContent isRunning={false} text={`[Unavailable report](${url('missing')})`} />)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.getByRole('link', { name: 'Unavailable report' }).getAttribute('href')).toBe(url('missing'))
    expect(screen.queryByTitle('Unavailable report')).toBeNull()
  })

  it('retains the authored link when the probe cannot reach the server', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('offline'))
    render(<MarkdownTextContent isRunning={false} text={`[Offline report](${url('offline')})`} />)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.getByRole('link', { name: 'Offline report' }).getAttribute('href')).toBe(url('offline'))
    expect(screen.queryByTitle('Offline report')).toBeNull()
  })
})

describe('artifact reference syntax', () => {
  const href = '/harness/artifacts/missing?session_id=s'

  it('renders a raw HTML image as an artifact image', async () => {
    render(<MarkdownTextContent isRunning={false} text={`<img src="${href}" alt="raw artifact">`} />)
    expect((await screen.findByRole('img', { name: 'raw artifact' })).getAttribute('src')).toBe(href)
  })

  it('probes and embeds a raw HTML artifact anchor', async () => {
    render(<MarkdownTextContent isRunning={false} text={`<a href="${href}">raw artifact</a>`} />)
    expect((await screen.findByTitle('raw artifact')).getAttribute('src')).toBe(href)
    expect(fetch).toHaveBeenCalledWith(href, expect.objectContaining({ method: 'HEAD' }))
  })

  it('does not fetch or render an artifact URL from an incomplete Markdown link', async () => {
    const { container } = render(<MarkdownTextContent isRunning={false} text={`[map](${href}`} />)
    await act(async () => {})
    expect(container.textContent).toBe('map [blocked]')
    expect(container.querySelector('a, img, iframe')).toBeNull()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('routes an opaque MEDIA artifact path to the file preview rather than an HTML frame', async () => {
    const { container } = render(<MarkdownTextContent isRunning={false} text={renderMediaTags(`MEDIA:${href}`)} />)
    await screen.findByText('missing?session_id=s')
    expect(screen.getByRole('button', { name: 'Download' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Open preview' })).toBeTruthy()
    expect(container.querySelector('a, img, iframe')).toBeNull()
    expect(fetch).not.toHaveBeenCalled()
  })
})

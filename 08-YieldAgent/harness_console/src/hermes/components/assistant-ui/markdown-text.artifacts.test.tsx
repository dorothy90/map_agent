import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { artifactsForSession, clearArtifactRegistry } from '@/store/artifacts'
import { $previewTabs } from '@/store/preview'
import { $activeSessionId, $selectedStoredSessionId } from '@/store/session'

import { MarkdownTextContent } from './markdown-text'

const HTML_DOC = `<!doctype html>
<html>
<head><title>Pomodoro Timer</title></head>
<body>
<h1>Pomodoro</h1>
<p>A tiny focus timer that counts down twenty-five minutes.</p>
<script>let seconds = 25 * 60; setInterval(() => { seconds -= 1 }, 1000)</script>
</body>
</html>`

const SMALL_SNIPPET = 'const x = 1'

function fenced(language: string, body: string): string {
  return `Here you go:\n\n\`\`\`${language}\n${body}\n\`\`\`\n`
}

// End-to-end for the artifact path: a substantial ```html fence in assistant
// markdown must come out of preprocessMarkdown -> Streamdown -> SyntaxHighlighter
// as an artifact card (registered in the store), while small fences keep the
// plain code-card path.
describe('MarkdownTextContent artifacts', () => {
  beforeEach(() => {
    $activeSessionId.set('session-artifacts')
    $selectedStoredSessionId.set(null)
    window.localStorage.clear()
    clearArtifactRegistry()
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    cleanup()
    $activeSessionId.set(null)
    $selectedStoredSessionId.set(null)
    clearArtifactRegistry()
    window.localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('renders a substantial html fence as an artifact card and registers it', async () => {
    render(<MarkdownTextContent isRunning={false} text={fenced('html', HTML_DOC)} />)

    const card = await screen.findByText('Pomodoro Timer')

    expect(card.closest('button')?.dataset.slot).toBe('aui_artifact-card')
    expect(artifactsForSession('session-artifacts')).toHaveLength(1)
    expect(artifactsForSession('session-artifacts')[0]?.kind).toBe('html')
    // Registration alone must not open the rail (offer, don't hijack).
    expect($previewTabs.get()).toHaveLength(0)
  })

  it('keeps a small fence as a plain code block', async () => {
    const { container } = render(<MarkdownTextContent isRunning={false} text={fenced('js', SMALL_SNIPPET)} />)

    // The code card mounts synchronously; Shiki may split tokens into spans,
    // so assert on the card slots rather than text content.
    expect(container.querySelector('[data-slot="code-card"]')).not.toBeNull()
    expect(container.querySelector('[data-slot="aui_artifact-card"]')).toBeNull()
    expect(artifactsForSession('session-artifacts')).toHaveLength(0)
  })

  it('does not register while the message is still streaming', async () => {
    render(<MarkdownTextContent isRunning text={fenced('html', HTML_DOC)} />)

    await screen.findByText('Pomodoro Timer')

    expect(artifactsForSession('session-artifacts')).toHaveLength(0)
  })

  it('keeps the fallback link and frames confirmed HTML artifacts', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(null, { headers: { 'Content-Type': 'text/html; charset=utf-8' } })
    )
    const href = '/harness/artifacts/opaque-id?session_id=session-artifacts'

    const { container } = render(
      <MarkdownTextContent isRunning={false} text={`[HTML report](${href})`} />
    )

    const link = await screen.findByRole('link', { name: 'HTML report' })
    const frame = await screen.findByTitle('HTML report')
    expect(link.getAttribute('href')).toBe(href)
    expect(frame.getAttribute('src')).toBe(href)
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts')
    expect(frame.className).toContain('h-[calc(100%-2rem)]')
    expect(frame.parentElement?.className).toContain('h-[720px]')
    expect(frame.parentElement?.className).toContain('max-h-[85vh]')
    expect(screen.getByRole('link', { name: 'Open in new window' }).getAttribute('href')).toBe(href)
    expect(frame.closest('p')).not.toBeNull()
    expect(container.querySelector('p > div')).toBeNull()
  })

  it('leaves non-HTML artifacts as links', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(null, { headers: { 'Content-Type': 'application/pdf' } })
    )
    const href = '/harness/artifacts/document?session_id=session-artifacts'

    render(<MarkdownTextContent isRunning={false} text={`[Document](${href})`} />)

    expect(await screen.findByRole('link', { name: 'Document' })).toBeTruthy()
    expect(screen.queryByTitle('Document')).toBeNull()
  })

  it('ignores a stale HTML probe after the artifact URL changes', async () => {
    let resolveFirst!: (response: Response) => void
    const first = new Promise<Response>((resolve) => {
      resolveFirst = resolve
    })
    vi.mocked(fetch)
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce(new Response(null, { headers: { 'Content-Type': 'text/csv' } }))
    const oldHref = '/harness/artifacts/old?session_id=session-artifacts'
    const newHref = '/harness/artifacts/new?session_id=session-artifacts'
    const view = render(<MarkdownTextContent isRunning={false} text={`[Report](${oldHref})`} />)

    view.rerender(<MarkdownTextContent isRunning={false} text={`[Report](${newHref})`} />)
    await act(async () => {
      resolveFirst(new Response(null, { headers: { 'Content-Type': 'text/html' } }))
      await first
    })

    expect((await screen.findByRole('link', { name: 'Report' })).getAttribute('href')).toBe(newHref)
    expect(screen.queryByTitle('Report')).toBeNull()
  })
})

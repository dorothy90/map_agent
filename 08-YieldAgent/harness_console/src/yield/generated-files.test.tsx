import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { MarkdownTextContent } from '@/components/assistant-ui/markdown-text'
import { toChatMessages } from '@/lib/chat-messages/hydration'
import { renderMediaTags } from '@/lib/chat-messages/parts'
import { $activeSessionId, $selectedStoredSessionId } from '@/store/session'
import { getWebDesktopBridge } from '@/platform-bridge'
import { WebHostAdapter } from '../platform/web-host'

afterEach(() => {
  cleanup()
  $activeSessionId.set(null)
  $selectedStoredSessionId.set(null)
  vi.restoreAllMocks()
})

it('rejects a malformed download ticket before starting the browser download', async () => {
  const fetcher = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    return init?.method === 'HEAD'
      ? new Response(null, { headers: { 'X-Hermes-File-Ticket': 'required' } })
      : Response.json({ ticket: [] })
  })
  const host = new WebHostAdapter({ fetch: fetcher })
  const release = host.installDesktopBridge()
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click')
  try {
    await expect(getWebDesktopBridge()!.saveGatewayFile!({
      path: '/outputs/run/result/report.xlsx', sessionId: 's',
    })).rejects.toThrow('Invalid file download ticket')
    expect(click).not.toHaveBeenCalled()
  } finally {
    release()
  }
})

it.each([
  ['결과 (1) "100%".csv', 'media', false],
  ['웨이퍼 맵.xlsx', 'media', true],
  ['발표.pptx', 'media', true],
  ['문서.docx', 'markdown', false],
  ['chart.png', 'media', false],
  ['chart.svg', 'image', true],
  ['video.mp4', 'markdown', false],
  ['report.html', 'media', false],
  ['custom.unknown', 'markdown', false],
  ['README', 'media', false],
  ['README', 'markdown', true],
] as const)('downloads %s via %s from live and restored cards (ticket=%s)', async (filename, syntax, needsTicket) => {
  const path = '/outputs/run/result/' + encodeURIComponent(filename).replaceAll('(', '%28').replaceAll(')', '%29')
  const answer = syntax === 'media' ? `MEDIA:"${path}"` : `${syntax === 'image' ? '!' : ''}[Result](${path})`
  const fetcher = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === 'HEAD') {
      return new Response(null, { headers: needsTicket ? { 'X-Hermes-File-Ticket': 'required' } : {} })
    }
    expect(JSON.parse(String(init?.body))).toEqual({ route: 'download', path, session_id: 'session', profile: 'default' })
    return Response.json({ ticket: 'synthetic' })
  })
  const host = new WebHostAdapter({ fetch: fetcher })
  const release = host.installDesktopBridge()
  const clicked: URL[] = []
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    expect(this.download).toBe(filename)
    clicked.push(new URL(this.href))
  })
  try {
    for (const stored of [false, true]) {
      $activeSessionId.set(stored ? null : 'session')
      $selectedStoredSessionId.set(stored ? 'session' : null)
      const text = stored ? toChatMessages([{ role: 'assistant', content: answer }])[0].parts[0] : null
      if (stored) expect(text?.type).toBe('text')
      const card = render(<MarkdownTextContent isRunning={false} text={stored ? (text?.type === 'text' ? text.text : '') : renderMediaTags(answer)} />)
      expect(screen.getByText(filename)).toBeTruthy()
      fireEvent.click(screen.getByRole('button', { name: 'Download' }))
      await waitFor(() => expect(clicked.length).toBe(stored ? 2 : 1))
      const url = clicked.at(-1)!
      expect(url.pathname).toBe('/gateway-m1/api/fs/download')
      expect(url.searchParams.get('path')).toBe(path)
      expect(url.searchParams.get('session_id')).toBe('session')
      expect(url.searchParams.get('profile')).toBe('default')
      expect(url.searchParams.get('ticket')).toBe(needsTicket ? 'synthetic' : null)
      card.unmount()
    }
    expect(fetcher.mock.calls.map(([, init]) => init?.method)).toEqual(needsTicket ? ['HEAD','POST','HEAD','POST'] : ['HEAD','HEAD'])
  } finally {
    release()
  }
})

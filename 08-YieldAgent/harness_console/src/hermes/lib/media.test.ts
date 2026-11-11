import { afterEach, describe, expect, it, vi } from 'vitest'

import { isHtmlHarnessArtifact } from './media'

describe('isHtmlHarnessArtifact', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('accepts same-origin HTML artifacts and preserves the queried URL', async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(null, { headers: { 'Content-Type': 'text/html; charset=utf-8' } })
    )
    vi.stubGlobal('fetch', fetch)

    const href = '/harness/artifacts/opaque-id?session_id=session%201'
    await expect(isHtmlHarnessArtifact(href)).resolves.toBe(true)
    expect(fetch).toHaveBeenCalledWith(href, {
      method: 'HEAD',
      redirect: 'error',
      signal: undefined
    })
  })

  it('rejects non-HTML, external, redirected, and failed probes', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { headers: { 'Content-Type': 'image/png' } }))
      .mockResolvedValueOnce(new Response(null, { status: 302 }))
      .mockRejectedValueOnce(new Error('offline'))
    vi.stubGlobal('fetch', fetch)

    await expect(isHtmlHarnessArtifact('/harness/artifacts/image?session_id=s')).resolves.toBe(false)
    await expect(isHtmlHarnessArtifact('https://example.com/harness/artifacts/x?session_id=s')).resolves.toBe(false)
    await expect(isHtmlHarnessArtifact('/harness/artifacts/redirect?session_id=s')).resolves.toBe(false)
    await expect(isHtmlHarnessArtifact('/harness/artifacts/error?session_id=s')).resolves.toBe(false)
    expect(fetch).toHaveBeenCalledTimes(3)
  })
})

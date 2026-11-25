import { afterEach, expect, it, vi } from 'vitest'
import { matrixArtifactPath, serveMatrixPanel } from './matrix-panel-bridge'

afterEach(() => vi.unstubAllGlobals())

it('uses only the displayed artifact and its session for panel bytes', async () => {
  vi.stubGlobal('window', { location: new URL('http://localhost/') })
  const bytes = new Uint8Array([1, 2])
  const fetcher = vi.fn(async () => new Response(bytes))
  vi.stubGlobal('fetch', fetcher)
  const reply = { postMessage: vi.fn(), close: vi.fn() }
  const source = '/harness/artifacts/owned?session_id=mine'
  const requested = { type: 'yield-matrix-panel', offset: 17, length: 2, url: 'https://untrusted.example/elsewhere' }
  await serveMatrixPanel(source, requested, reply, new AbortController().signal)
  expect(fetcher.mock.calls[0]).toEqual(['/harness/artifacts/owned/matrix-panels?session_id=mine&offset=17&length=2',
    expect.objectContaining({ redirect: 'error', credentials: 'same-origin' })])
  expect(reply.postMessage).toHaveBeenCalledWith({ bytes: expect.any(ArrayBuffer) }, [expect.any(ArrayBuffer)])
  expect(matrixArtifactPath('//external.example/harness/artifacts/a?session_id=mine')).toBeNull()
  expect(matrixArtifactPath('/harness/artifacts/x/other?session_id=mine')).toBeNull()
  expect(matrixArtifactPath('/harness/artifacts/a')).toBeNull()
  await serveMatrixPanel(source, { type: 'yield-matrix-panel', offset: -1, length: 10 }, reply, new AbortController().signal)
  expect(fetcher).toHaveBeenCalledTimes(1)
})

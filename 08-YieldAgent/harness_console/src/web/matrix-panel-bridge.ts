// The sandbox can request a slice of its own artifact, never an arbitrary fetch.
export function matrixArtifactPath(src: string): string | null {
  try {
    const url = new URL(src, window.location.href)
    const parts = url.pathname.split('/')
    const session = url.searchParams.get('session_id')
    if (url.origin !== window.location.origin || parts.length !== 4 || parts[1] !== 'harness' ||
        parts[2] !== 'artifacts' || !parts[3] || !session) return null
    return url.pathname + '?' + new URLSearchParams({ session_id: session })
  } catch {
    return null
  }
}

type Slice = { type: string; offset: number; length: number }

export async function serveMatrixPanel(src: string, data: Slice, reply: Pick<MessagePort, 'postMessage'|'close'>, signal: AbortSignal) {
  try {
    const artifact = matrixArtifactPath(src)
    if (!artifact || data.type !== 'yield-matrix-panel' || !Number.isSafeInteger(data.offset) ||
        !Number.isSafeInteger(data.length) || data.offset < 0 || data.length <= 0 || data.length > 1024 * 1024) {
      throw Error('Invalid panel range')
    }
    const url = new URL(artifact, window.location.href)
    url.searchParams.set('offset', String(data.offset))
    url.searchParams.set('length', String(data.length))
    const response = await fetch(url.pathname + '/matrix-panels?' + url.searchParams, { credentials: 'same-origin', redirect: 'error', signal })
    if (!response.ok) throw Error('Panel unavailable')
    const bytes = await response.arrayBuffer()
    if (bytes.byteLength !== data.length) throw Error('Incomplete panel')
    reply.postMessage({ bytes }, [bytes])
  } catch {
    reply.postMessage({ error: true })
  } finally {
    reply.close()
  }
}

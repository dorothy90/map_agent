import { expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { WebHostAdapter } from './web-host'
import { restCapabilityError } from './capability-gate'
import manifest from './yield-manifest.generated.json'

it('projects every backend REST policy and blocks every non-implemented route before fetch', async () => {
  const source = JSON.parse(readFileSync(new URL('../../../harness/gateway/manifest.json', import.meta.url), 'utf8'))
  expect(manifest.routes).toEqual(source.routes.map(({ method, path, classification, reason, feature }: any) => ({ method, path, classification, reason, feature })))
  const fetcher = vi.fn()
  const host = new WebHostAdapter({ location: new URL('https://yield.test'), fetch: fetcher })
  for (const row of source.routes) {
    if (row.method === 'WEBSOCKET' || row.classification === 'implemented') continue
    const path = row.path.replace(/\{[^}]+\}/g, 'sample')
    if (path.startsWith('/api/')) await expect(host.api({ path, method: row.method })).rejects.toMatchObject({ code: -32004, data: { feature: row.feature } })
    else await expect(host.api({ path, method: row.method })).rejects.toThrow('same-origin')
  }
  for (const path of ['/api/unknown', '/api/sessions/s/messages/extra', '/api/sessions/%2f/messages']) {
    await expect(host.api({ path })).rejects.toMatchObject({ code: -32004 })
  }
  expect(fetcher).not.toHaveBeenCalled()
})
it('matches method, literal route priority, parameter segments and query independently', () => {
  expect(restCapabilityError('/api/sessions/s/messages?limit=1', 'GET')).toBeNull()
  expect(restCapabilityError('/api/health', 'GET')).toBeNull()
  expect(restCapabilityError('/api/health', 'DELETE')).toMatchObject({ code: -32004 })
  expect(restCapabilityError('/api/sessions/s/messages', 'POST')).toMatchObject({ code: -32004 })
})
it('uses an explicit same-origin mount for REST and descriptor', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response('{}'))
  const host = new WebHostAdapter({ location: new URL('https://yield.test'), basePath: '/gateway-legacy', fetch: fetcher })
  await host.api({ path: '/api/health' })
  expect(String(fetcher.mock.calls[0][0])).toBe('https://yield.test/gateway-legacy/api/health?profile=default')
  expect((await host.getConnection()).baseUrl).toBe('https://yield.test/gateway-legacy')
  expect(() => new WebHostAdapter({ location: new URL('https://yield.test'), basePath: '//outside.test' })).toThrow()
})

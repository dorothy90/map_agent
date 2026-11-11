// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const probes = vi.hoisted(() => ({ mount: vi.fn() }))
vi.mock('../web/entry', () => ({ mount: probes.mount }))

beforeEach(() => {
  vi.resetModules()
  vi.clearAllMocks()
  document.body.innerHTML = '<div id="root"></div>'
})
afterEach(() => {
  window.history.replaceState({}, '', '/')
  document.body.innerHTML = ''
})

it.each(['', '?ui=old', '?ui=hermes2', '?ui=hermes', '?user=alice'])(
  'main mounts Hermes regardless of query %s', async search => {
    window.history.replaceState({}, '', '/' + search)
    await import('../main')
    await vi.dynamicImportSettled()
    expect(probes.mount).toHaveBeenCalledTimes(1)
  },
)

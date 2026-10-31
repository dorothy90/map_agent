// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const probes = vi.hoisted(() => ({
  app: vi.fn(), loaded: vi.fn(), mount: vi.fn(),
  roots: [] as import('react-dom/client').Root[],
}))
vi.mock('../App', () => ({ default: () => { probes.app(); return 'legacy App' } }))
vi.mock('../web/entry', () => { probes.loaded(); return { mount: probes.mount } })
vi.mock('../styles.css', () => ({}))
vi.mock('react-dom/client', async importOriginal => {
  const original = await importOriginal<typeof import('react-dom/client')>()
  return { ...original, createRoot: (...args: Parameters<typeof original.createRoot>) => {
    const root = original.createRoot(...args)
    probes.roots.push(root)
    return root
  } }
})

beforeEach(() => {
  vi.resetModules()
  vi.clearAllMocks()
  document.body.innerHTML = '<div id="root"></div>'
})
afterEach(async () => {
  const { act } = await import('react')
  await act(async () => { probes.roots.splice(0).forEach(root => root.unmount()) })
  window.history.replaceState({}, '', '/')
  document.body.innerHTML = ''
})

it.each(['', '?ui=old', '?ui=hermes2'])('main keeps legacy App for %s', async search => {
  window.history.replaceState({}, '', '/' + search)
  const { act } = await import('react')
  await act(async () => { await import('../main'); await vi.dynamicImportSettled() })
  expect(document.getElementById('root')?.textContent).toBe('legacy App')
  expect(probes.app).toHaveBeenCalled()
  expect(probes.loaded).not.toHaveBeenCalled()
  expect(probes.mount).not.toHaveBeenCalled()
})

it('main loads and mounts the explicit Hermes opt-in', async () => {
  window.history.replaceState({}, '', '/?ui=hermes')
  await import('../main')
  await vi.dynamicImportSettled()
  expect(probes.loaded).toHaveBeenCalledTimes(1)
  expect(probes.mount).toHaveBeenCalledTimes(1)
  expect(probes.app).not.toHaveBeenCalled()
  expect(document.getElementById('root')?.textContent).toBe('')
})

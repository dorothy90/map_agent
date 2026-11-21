import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { listSessions } from '@/api/sessions'
import { $sessions, $sessionsLoadError } from '@/store/session'
import { useWebSessionList } from './use-web-session-list'

vi.mock('@/api/sessions', () => ({listSessions: vi.fn()}))
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers() })

it('recovers the sidebar after a temporary list failure without a manual refresh', async () => {
  vi.useFakeTimers()
  const page = {sessions: [], total: 0, limit: 40, offset: 0}
  vi.mocked(listSessions).mockRejectedValueOnce(Object.assign(new Error(), {status: 500})).mockResolvedValue(page)
  const hook = renderHook(useWebSessionList)
  await act(() => hook.result.current.refreshSessions())
  await act(() => vi.advanceTimersByTimeAsync(5000))
  expect(listSessions).toHaveBeenCalledTimes(2)
  expect($sessionsLoadError.get()).toBe(false)
  expect($sessions.get()).toEqual([])
})

it('does not retry a denied request or leave timers behind when unmounted', async () => {
  vi.useFakeTimers()
  vi.mocked(listSessions).mockRejectedValueOnce(Object.assign(new Error(), {status: 403}))
    .mockRejectedValueOnce(new TypeError('Network error'))
  const hook = renderHook(useWebSessionList)
  await act(() => hook.result.current.refreshSessions())
  await act(() => vi.advanceTimersByTimeAsync(60000))
  expect(listSessions).toHaveBeenCalledTimes(1)
  await act(() => hook.result.current.refreshSessions())
  hook.unmount()
  await vi.advanceTimersByTimeAsync(60000)
  expect(listSessions).toHaveBeenCalledTimes(2)
})

it('recovers when connectivity returns after the automatic retries have ended', async () => {
  vi.useFakeTimers()
  vi.mocked(listSessions).mockRejectedValue(new TypeError('Network error'))
  const hook = renderHook(useWebSessionList)
  await act(() => hook.result.current.refreshSessions())
  await act(() => vi.advanceTimersByTimeAsync(60000))
  expect(listSessions).toHaveBeenCalledTimes(4)
  expect($sessionsLoadError.get()).toBe(true)
  vi.mocked(listSessions).mockResolvedValue({sessions: [], total: 0, limit: 40, offset: 0})
  await act(async () => { window.dispatchEvent(new Event('online')) })
  expect($sessionsLoadError.get()).toBe(false)
})

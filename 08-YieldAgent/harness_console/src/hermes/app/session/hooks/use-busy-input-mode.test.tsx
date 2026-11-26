import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { $busyInputConfig } from '@/store/busy-input-mode'
import { $activeGatewayProfile } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'

import { useBusyInputMode } from './use-busy-input-mode'

vi.mock('@/store/session-request-router', () => ({
  isSessionOwnerRoute: (owner: unknown) =>
    typeof owner === 'object' && owner !== null && 'profile' in (owner as object),
  requestForSessionProfile: (
    _owner: unknown,
    request: (method: string, params: unknown) => Promise<unknown>,
    method: string,
    params: unknown
  ) => request(method, params)
}))

afterEach(() => {
  cleanup()
  $busyInputConfig.set(null)
  $activeGatewayProfile.set('default')
  $gatewayState.set('closed')
})

it('canonical busy policy reads once per principal and updates after config.set', async () => {
  $connection.set({ mode: 'local', wsUrl: 'ws://localhost/api/ws?native_dial=fixture' } as never)
  $gatewayState.set('open')
  const request = vi.fn().mockResolvedValue({ value: 'queue' })
  const hook = renderHook(({ sessionId }) => useBusyInputMode({
    sessionId, storedSessionId: null, requestGateway: request, principalScoped: true
  }), { initialProps: { sessionId: 'first' } })

  try {
    await waitFor(() => expect(hook.result.current.mode).toBe('queue'))
    hook.rerender({ sessionId: 'second' })
    expect(hook.result.current.mode).toBe('queue')
    await act(() => hook.result.current.setMode('interrupt'))
    expect(hook.result.current.mode).toBe('interrupt')
    expect($busyInputConfig.get()?.mode).toBe('interrupt')
    expect(request.mock.calls).toEqual([
      ['config.get', { key: 'busy' }],
      ['config.set', { key: 'busy', value: 'interrupt' }]
    ])
  } finally { hook.unmount(); $connection.set(null) }
})

it('recovers a transient busy policy read without reconnecting', async () => {
  vi.useFakeTimers()
  $gatewayState.set('open')
  const request = vi.fn().mockRejectedValueOnce(new Error('temporarily unavailable')).mockResolvedValue({ value: 'steer' })
  const hook = renderHook(() => useBusyInputMode({ sessionId: 'runtime', storedSessionId: null, requestGateway: request }))

  try {
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(hook.result.current.mode).toBe('steer')
    expect(request).toHaveBeenCalledTimes(2)
  } finally { hook.unmount(); vi.useRealTimers() }
})

it('bounds failed reads and cancels retries when their session leaves', async () => {
  vi.useFakeTimers()
  $gatewayState.set('open')
  const request = vi.fn().mockRejectedValue(new Error('unavailable'))
  const hook = renderHook(({ sessionId }) => useBusyInputMode({ sessionId, storedSessionId: null, requestGateway: request }), { initialProps: { sessionId: 'first' } })

  try {
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(request).toHaveBeenCalledTimes(3)
    hook.rerender({ sessionId: 'second' })
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    hook.unmount()
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(request).toHaveBeenCalledTimes(4)
  } finally { hook.unmount(); vi.useRealTimers() }
})

it('uses only the current owner config and ignores a late response from the previous profile', async () => {
  $gatewayState.set('open')
  let resolveOld!: (value: { value: string }) => void

  const request = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise(resolve => {
          resolveOld = resolve
        })
    )
    .mockResolvedValue({ value: 'steer' })

  const hook = renderHook(() =>
    useBusyInputMode({ sessionId: 'runtime', storedSessionId: null, requestGateway: request })
  )

  expect(hook.result.current.mode).toBe('steer')
  act(() => $activeGatewayProfile.set('other'))
  await waitFor(() => expect(hook.result.current.mode).toBe('steer'))
  await act(async () => resolveOld({ value: 'queue' }))
  expect(hook.result.current.mode).toBe('steer')
  act(() =>
    $busyInputConfig.set({
      owner: JSON.stringify([$connection.get()?.connectionId ?? '', 'other']),
      connection: $connection.get(),
      mode: 'queue'
    })
  )
  expect(hook.result.current.mode).toBe('queue')
  act(() => $busyInputConfig.set({ owner: 'foreign-owner', connection: $connection.get(), mode: 'interrupt' }))
  expect(hook.result.current.mode).toBe('steer')
})

it('defaults to steer when the principal has no saved preference', async () => {
  $gatewayState.set('open')
  const request = vi.fn().mockResolvedValue({})
  const hook = renderHook(() => useBusyInputMode({
    sessionId: null, storedSessionId: null, requestGateway: request, principalScoped: true
  }))
  expect(hook.result.current.mode).toBe('steer')
  await act(async () => { await Promise.resolve() })
  expect(request).toHaveBeenCalledExactlyOnceWith('config.get', { key: 'busy' })
  expect(hook.result.current.mode).toBe('steer')
})

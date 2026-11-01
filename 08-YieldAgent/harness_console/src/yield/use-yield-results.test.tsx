import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { loadAllTableRows, useYieldResults } from './use-yield-results'

afterEach(cleanup)

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { gcTime: 0 } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const response = (status: string) => new Response(JSON.stringify({ runs: [{
    run_id: 'r', session_id: 's', query: 'question', status, goal_revision: 1,
  }] }))
  return { wrapper, response }
}

it.each(['completed', 'cancelled', 'failed'])('refreshes running data once when the run becomes %s', async status => {
  const { wrapper, response } = setup()
  const fetcher = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(response('running'))
    .mockResolvedValueOnce(response(status))
  const { result, rerender } = renderHook(({ busy }) => useYieldResults('s', busy, fetcher), {
    wrapper, initialProps: { busy: true },
  })
  await waitFor(() => expect(result.current.data?.runs[0].status).toBe('running'))
  expect(fetcher).toHaveBeenCalledTimes(1)
  rerender({ busy: false })
  await waitFor(() => expect(result.current.data?.runs[0].status).toBe(status))
  rerender({ busy: false })
  expect(fetcher).toHaveBeenCalledTimes(2)
})

it('does not refetch on initial idle render or repeated idle renders', async () => {
  const { wrapper, response } = setup()
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response('completed'))
  const { result, rerender } = renderHook(() => useYieldResults('s', false, fetcher), { wrapper })
  await waitFor(() => expect(result.current.data?.runs[0].status).toBe('completed'))
  rerender()
  rerender()
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('does not add a refetch when switching from a busy session to an idle session', async () => {
  const { wrapper, response } = setup()
  const fetcher = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(response('running'))
    .mockResolvedValueOnce(response('completed'))
  const { result, rerender } = renderHook(({ sessionId, busy }) => useYieldResults(sessionId, busy, fetcher), {
    wrapper, initialProps: { sessionId: 's', busy: true },
  })
  await waitFor(() => expect(result.current.data?.runs[0].status).toBe('running'))
  rerender({ sessionId: 'other', busy: false })
  await waitFor(() => expect(result.current.data?.runs[0].status).toBe('completed'))
  expect(fetcher).toHaveBeenCalledTimes(2)
})

it('loads every remaining result page after the initial 100 rows', async () => {
  const initial={table_id:'t',title:'결과',columns:['n'],units:{},rows:Array.from({length:100},(_,n) => ({n})),offset:0,total_rows:1201,complete:true}
  const fetcher=vi.fn<typeof fetch>()
    .mockResolvedValueOnce(new Response(JSON.stringify({...initial,offset:100,rows:Array.from({length:1000},(_,n) => ({n:n+100}))})))
    .mockResolvedValueOnce(new Response(JSON.stringify({...initial,offset:1100,rows:Array.from({length:101},(_,n) => ({n:n+1100}))})))
  const rows=await loadAllTableRows(fetcher,'session','result',initial)
  expect(rows).toHaveLength(1201)
  expect(rows.at(-1)).toEqual({n:1200})
  expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
    '/harness/results/result?session_id=session&table_id=t&offset=100&limit=1000',
    '/harness/results/result?session_id=session&table_id=t&offset=1100&limit=101',
  ])
})

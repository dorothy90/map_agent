import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { getSessionMessages } from '@/api/sessions'
import type { SessionInfo } from '@/types/hermes'
import { useSessionLabels } from './use-session-labels'
import { sessionLabelKey } from './session-labels'
vi.mock('@/api/sessions', () => ({getSessionMessages:vi.fn()}))
afterEach(() => {cleanup();vi.resetAllMocks()})
const row=(id:string) => ({id,profile:'default',connection_id:'owner'} as SessionInfo)
it('bounds concurrent reads, caches owned labels and retries failures', async () => {
 const rows=[row('a'),row('b'),row('c'),row('d')]
 const release:Array<() => void>=[]
 vi.mocked(getSessionMessages).mockImplementation(id => new Promise(resolve => release.push(() => resolve({session_id:id,messages:[{role:'user',content:id}] as never}))))
 const {result,rerender}=renderHook(({sessions}) => useSessionLabels(sessions,true),{initialProps:{sessions:rows}})
 expect(getSessionMessages).toHaveBeenCalledTimes(3)
 await act(async () => {release[0]()})
 expect(getSessionMessages).toHaveBeenCalledTimes(4)
 await act(async () => {release.slice(1).forEach(done => done())})
 rerender({sessions:[...rows]})
 expect(getSessionMessages).toHaveBeenCalledTimes(4)
 expect(result.current.labels[sessionLabelKey(rows[0])]).toBe('a')
 vi.mocked(getSessionMessages).mockRejectedValueOnce(new Error('offline'))
 const other={...rows[0],connection_id:'another-owner'}
 rerender({sessions:[other]})
 await waitFor(() => expect(result.current.failed).toBe(true))
 expect(result.current.labels[sessionLabelKey(other)]).toBeUndefined()
 vi.mocked(getSessionMessages).mockResolvedValueOnce({session_id:'a',messages:[{role:'user',content:'other question'}] as never})
 act(() => result.current.retry())
 await waitFor(() => expect(result.current.labels[sessionLabelKey(other)]).toBe('other question'))
})
it('ignores in-flight responses after unmount', async () => {
 let resolve!: (value: Awaited<ReturnType<typeof getSessionMessages>>) => void
 vi.mocked(getSessionMessages).mockImplementation(() => new Promise(done => {resolve=done}))
 const sessions=[row('a')]
 const {result,unmount}=renderHook(() => useSessionLabels(sessions,true))
 unmount()
 await act(async () => resolve({session_id:'a',messages:[{role:'user',content:'stale'}] as never}))
 expect(result.current.labels).toEqual({})
})
it('retries provisional empty history and protects a submitted first question from an in-flight empty read', async () => {
 const sessions=[row('new')]
 vi.mocked(getSessionMessages).mockResolvedValueOnce({session_id:'new',messages:[]})
 const {result,rerender}=renderHook(({rows}) => useSessionLabels(rows,true),{initialProps:{rows:sessions}})
 await waitFor(() => expect(result.current.labels[sessionLabelKey(sessions[0])]).toBeNull())
 let finish!: (value: Awaited<ReturnType<typeof getSessionMessages>>) => void
 vi.mocked(getSessionMessages).mockImplementationOnce(() => new Promise(resolve => {finish=resolve}))
 rerender({rows:[...sessions]})
 expect(getSessionMessages).toHaveBeenCalledTimes(2)
 act(() => result.current.remember(sessions[0],'First submitted question'))
 await act(async () => finish({session_id:'new',messages:[]}))
 expect(result.current.labels[sessionLabelKey(sessions[0])]).toBe('First submitted question')
})

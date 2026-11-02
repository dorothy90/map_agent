import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { HermesWebApp } from './HermesWebApp'
import { WebHostAdapter } from '../platform/web-host'
import { capabilityError, restCapabilityError } from '../platform/capability-gate'
import { stubThreadEnvironment } from '@/components/assistant-ui/test-utils'
import { NEW_CHAT_ROUTE, sessionRoute } from '@/app/routes'
import { $activeSessionId, $selectedStoredSessionId, $freshDraftReady } from '@/store/session'
stubThreadEnvironment()
afterEach(() => {cleanup();window.history.replaceState(null,'',`#${NEW_CHAT_ROUTE}`);vi.useRealTimers();vi.restoreAllMocks()})
it('fails closed without a resolver', () => {
 const spy = vi.spyOn(WebHostAdapter.prototype,'connect')
 render(<HermesWebApp />)
 expect(screen.getByRole('alert').textContent).toContain('인증 연결 resolver')
 expect(spy).not.toHaveBeenCalled()
})
it('normalizes default create options on the composed host', async () => {
 const host=new WebHostAdapter({rpcMode:'authority',location:{href:'http://localhost/'},reconnect:false})
 vi.spyOn(host,'connect').mockImplementation(() => new Promise(() => {}))
 const request=vi.spyOn(host.gateway,'request').mockResolvedValue({})
 render(<HermesWebApp resolver={async () => ({url:'ws://localhost/api/ws'})} host={host}/>)
 await act(async () => {await host.gateway.request('session.create',{profile:'default',source:'gui',request_id:'create-1',service_tier:'normal',fast:false,cwd_explicit:false})})
 expect(request).toHaveBeenCalledWith('session.create',{profile:'default',source:'gui',request_id:'create-1'})
})
it('shows connection pending before opening and audits 11 seconds', async () => {
 vi.useFakeTimers()
 const host = new WebHostAdapter({rpcMode:'authority', location:{href:'http://localhost/'}, reconnect:false})
 vi.spyOn(host,'connect').mockImplementation(() => new Promise(() => {}))
 const request = vi.spyOn(host.gateway,'request').mockRejectedValue(new Error('offline'))
 const api = vi.spyOn(host,'api').mockResolvedValue({sessions:[],total:0})
 render(<HermesWebApp resolver={async () => ({url:'ws://localhost/api/ws'})} host={host} />)
 await act(async () => {await vi.advanceTimersByTimeAsync(11000)})
 expect(screen.getByText('게이트웨이 연결 중…')).toBeTruthy()
 for (const [method] of request.mock.calls) expect(capabilityError(method,'authority')).toBeNull()
 for (const [call] of api.mock.calls) expect(restCapabilityError(call.path,call.method ?? 'GET')).toBeNull()
})

it('renders server sessions and only implemented calls after connection', async () => {
 vi.useFakeTimers()
 const host=new WebHostAdapter({rpcMode:'authority',location:{href:'http://localhost/'},reconnect:false})
 vi.spyOn(host,'connect').mockResolvedValue()
 const request=vi.spyOn(host.gateway,'request').mockRejectedValue(new Error('offline'))
 const api=vi.spyOn(host,'api').mockResolvedValue({sessions:[{id:'saved',title:'Saved session',source:'cli',started_at:0,last_active:0,message_count:1}],total:1})
 render(<HermesWebApp resolver={async () => ({url:'ws://localhost/api/ws'})} host={host}/>)
 await act(async () => {await vi.advanceTimersByTimeAsync(11000)})
 expect(screen.getByRole('button',{name:'Saved session'})).toBeTruthy()
 for(const [method] of request.mock.calls) expect(capabilityError(method,'authority')).toBeNull()
 for(const [call] of api.mock.calls) expect(restCapabilityError(call.path,call.method ?? 'GET')).toBeNull()
})

// Earlier tests mocked every gateway request as offline and only checked list rendering, never a click through the real router/resume hooks.
it('routes a sidebar click through HashRouter, resumes and keeps selection until new chat', async () => {
 vi.useFakeTimers()
 window.history.replaceState(null,'',`#${NEW_CHAT_ROUTE}`)
 const host=new WebHostAdapter({rpcMode:'authority',location:{href:'http://localhost/'},reconnect:false})
 vi.spyOn(host,'connect').mockResolvedValue()
 const request=vi.spyOn(host.gateway,'request').mockImplementation(async method => {
   if(method==='session.resume') return {session_id:'runtime-alice-result',session_key:'alice-result',resumed:'alice-result',messages:[],message_count:0,running:false,info:{}}
   return {}
 })
 vi.spyOn(host,'api').mockImplementation(async call => {
   if(call.path.split('?')[0]==='/api/sessions') return {sessions:[{id:'alice-result',title:'표와 검증 결과',source:'cli',started_at:0,last_active:0,message_count:0}],total:1}
   if(call.path.includes('/messages')) return {session_id:'alice-result',messages:[
    {id:1,role:'user',content:'표를 보여줘'},
    {id:2,role:'assistant',content:'|A|B|\n|---|---|\n|1|2|'},
   ]}
   return {}
 })
 const fetcher=vi.fn<typeof fetch>().mockImplementation(async url => new Response(JSON.stringify(String(url).startsWith('/harness/results/') ?
  {table_id:'t',title:'표',columns:['A','B'],rows:[{A:1,B:2}],offset:0,total_rows:1,complete:true,units:{}} :
  {runs:[{run_id:'run',session_id:'alice-result',query:'표를 보여줘',status:'completed',goal_revision:1,observations:[{invocation_id:'i',tool_name:'query_db',result_id:'result',status:'success',tables:[{table_id:'t',title:'표',columns:['A','B'],total_rows:1,complete:true}]}]}]})))
 render(<HermesWebApp resolver={async () => ({url:'ws://localhost/api/ws'})} host={host} fetcher={fetcher}/>)
 await act(async () => {await vi.advanceTimersByTimeAsync(100)})
 fireEvent.click(screen.getByRole('button',{name:'표와 검증 결과'}))
 await act(async () => {await vi.advanceTimersByTimeAsync(100)})
 expect(window.location.hash).toBe(`#${sessionRoute('alice-result')}`)
 expect(request.mock.calls.filter(([method]) => method==='session.resume')).toEqual([
   ['session.resume',expect.objectContaining({session_id:'alice-result',defer_history:true}),undefined],
 ])
 expect($selectedStoredSessionId.get()).toBe('alice-result')
 expect(screen.getByRole('button',{name:'표와 검증 결과'}).getAttribute('aria-current')).toBe('page')
 expect($activeSessionId.get()).toBe('runtime-alice-result')
 expect(screen.getByRole('table')).toBeTruthy()
 await act(async () => {await vi.advanceTimersByTimeAsync(11000)})
 expect($selectedStoredSessionId.get()).toBe('alice-result')
 expect(window.location.hash).toBe(`#${sessionRoute('alice-result')}`)
 fireEvent.click(screen.getByRole('button',{name:'새 채팅'}))
 await act(async () => {await vi.advanceTimersByTimeAsync(100)})
 expect(window.location.hash).toBe(`#${NEW_CHAT_ROUTE}`)
 expect($selectedStoredSessionId.get()).toBeNull()
 expect(screen.getByRole('button',{name:'표와 검증 결과'}).hasAttribute('aria-current')).toBe(false)
 expect($activeSessionId.get()).toBeNull()
 expect($freshDraftReady.get()).toBe(true)
 expect(request.mock.calls.filter(([method]) => method==='session.resume')).toHaveLength(1)
 expect(fetcher.mock.calls.some(([url]) => String(url).startsWith('/harness/sessions/'))).toBe(false)
})

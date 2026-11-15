import { JsonRpcGatewayClient } from '@hermes/shared'
import { FakeWebSocket } from '../platform/fake-websocket'
import { writeFileSync } from 'node:fs'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { HermesWebApp } from './HermesWebApp'
import { WebHostAdapter } from '../platform/web-host'
import { capabilityError, restCapabilityError } from '../platform/capability-gate'
import { stubThreadEnvironment } from '@/components/assistant-ui/test-utils'
import { NEW_CHAT_ROUTE, sessionRoute } from '@/app/routes'
import { $activeSessionId, $selectedStoredSessionId, $freshDraftReady, $currentUsage, $turnStartedAt } from '@/store/session'
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
 const api=vi.spyOn(host,'api').mockImplementation(async call => call.path.includes('/messages') ? {session_id:'saved',messages:[{role:'user',content:'Original question'}]} : {sessions:[{id:'saved',title:'Saved session',source:'cli',started_at:0,last_active:0,message_count:1}],total:1})
 render(<HermesWebApp resolver={async () => ({url:'ws://localhost/api/ws'})} host={host}/>)
 await act(async () => {await vi.advanceTimersByTimeAsync(11000)})
 expect(screen.getByRole('button',{name:'Saved session'})).toBeTruthy()
 expect(screen.getByText('수율 분석 앱')).toBeTruthy()
 expect(screen.queryByText('Original question')).toBeNull()
 fireEvent.change(screen.getByRole('textbox',{name:'불러온 대화 검색'}),{target:{value:'absent'}})
 expect(screen.queryByRole('button',{name:'Saved session'})).toBeNull()
 fireEvent.change(screen.getByRole('textbox',{name:'불러온 대화 검색'}),{target:{value:'saved'}})
 expect(screen.getByRole('button',{name:'Saved session'}).title).toBe('Saved session')
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
 if(process.env.YIELD_UI_SNAPSHOT)writeFileSync(process.env.YIELD_UI_SNAPSHOT,document.querySelector('.hermes-web')!.outerHTML)
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
 expect(screen.getByText('수율 데이터에 대해 물어보세요')).toBeTruthy()
 const before=request.mock.calls.length
 const chip=screen.getByRole('button',{name:'4SS 최근 4주 주차별 수율 리포트 작성해줘'})
 fireEvent.click(chip)
 await act(async () => {await vi.advanceTimersByTimeAsync(100)})
 expect(document.querySelector('[contenteditable="true"]')?.textContent).toBe(chip.textContent)
 expect(document.activeElement).toBe(document.querySelector('[contenteditable="true"]'))
 expect(request.mock.calls).toHaveLength(before)
 expect(window.location.hash).toBe(`#${NEW_CHAT_ROUTE}`)
 expect(document.querySelector('[data-placeholder="추가로 요청할 내용을 입력하세요…"]')).toBeTruthy()
 act(() => {$currentUsage.set({input:0,output:0,total:0,calls:0});$turnStartedAt.set(null)})
 expect(screen.queryByLabelText('사용량과 실행 시간')).toBeNull()
 act(() => {$turnStartedAt.set(Date.now())})
 expect(screen.getByLabelText('사용량과 실행 시간').textContent).toBe('0s')
 act(() => {$currentUsage.set({input:12,output:3,total:15,calls:1});$turnStartedAt.set(null)})
 expect(screen.getByLabelText('사용량과 실행 시간').textContent).toBe('입력 12 · 출력 3 토큰')
 expect(request.mock.calls.filter(([method]) => method==='session.resume')).toHaveLength(1)
 expect(fetcher.mock.calls.some(([url]) => String(url).startsWith('/harness/sessions/'))).toBe(false)
})

it.each(['', '저장된 오래된 제목'])('loads title then first-question header for a deep link outside the loaded sidebar page: %s', async serverTitle => {
 vi.useFakeTimers()
 $freshDraftReady.set(false)
 window.history.replaceState(null,'',`#${sessionRoute('old-session')}`)
 const host=new WebHostAdapter({rpcMode:'authority',location:{href:'http://localhost/'},reconnect:false})
 vi.spyOn(host,'connect').mockResolvedValue()
 vi.spyOn(JsonRpcGatewayClient.prototype,'request').mockImplementation(async method => method==='session.title' ? {title:serverTitle} : {})
 vi.spyOn(host.gateway,'request').mockImplementation(async method => method==='session.resume' ? {session_id:'old-session',session_key:'old-session',messages:[],running:false,info:{}} : {})
 vi.spyOn(host,'api').mockImplementation(async call => call.path.includes('/messages') ? {session_id:'old-session',messages:[{id:1,role:'user',content:'Older original question'}]} : {sessions:[],total:0})
 render(<HermesWebApp resolver={async () => ({url:'ws://localhost/api/ws'})} host={host}/>)
 await act(async () => {await vi.advanceTimersByTimeAsync(100)})
 expect(screen.getByRole('heading',{name:serverTitle || 'Older original question'})).toBeTruthy()
})

it.each(['save','cancel','empty','failure'])('inline rename: %s through the stored session RPC', async mode => {
 vi.useFakeTimers()
 window.history.replaceState(null,'',`#${sessionRoute('saved')}`)
 const host=new WebHostAdapter({rpcMode:'authority',location:{href:'http://localhost/'},reconnect:false})
 vi.spyOn(host,'connect').mockResolvedValue()
 const request=vi.spyOn(JsonRpcGatewayClient.prototype,'request').mockImplementation(async method => {
  if(method==='session.title'){
   if(mode==='failure')throw new Error('Title already exists')
   return {title:'서버 정리 제목',pending:false}
  }
  return {}
 })
 vi.spyOn(host.gateway,'request').mockResolvedValue({session_id:'runtime',session_key:'saved',messages:[],running:false,info:{}})
 const api=vi.spyOn(host,'api').mockImplementation(async call => call.path.includes('/messages') ? {messages:[{role:'user',content:'첫 질문'}]} : {sessions:[{id:'saved',title:'  기존 제목  ',message_count:1}],total:1})
 render(<HermesWebApp resolver={async () => ({url:'ws://localhost/api/ws'})} host={host}/>)
 await act(async () => {await vi.advanceTimersByTimeAsync(100)})
 expect(screen.getByRole('heading',{name:'기존 제목'}).title).toBe('기존 제목')
 const listReads=() => api.mock.calls.filter(([call]) => call.path.split('?')[0]==='/api/sessions').length
 const reads=listReads()
 fireEvent.click(screen.getByRole('button',{name:'기존 제목 메뉴'}))
 fireEvent.click(screen.getByRole('menuitem',{name:'이름 바꾸기'}))
 const input=screen.getByRole('textbox',{name:'대화 이름'})
 expect(input.getAttribute('maxlength')).toBe('100')
 fireEvent.change(input,{target:{value:mode==='empty' ? '   ' : '  새 제목  '}})
 fireEvent.keyDown(input,{key:mode==='cancel' ? 'Escape' : 'Enter'})
 await act(async () => {await vi.advanceTimersByTimeAsync(100)})
 const calls=request.mock.calls.filter(([method,params]) => method==='session.title' && params && 'title' in params)
 if(mode==='save' || mode==='failure')expect(calls).toEqual([['session.title',{session_id:'saved',title:'새 제목'}]])
 else expect(calls).toHaveLength(0)
 const label=mode==='save' ? '서버 정리 제목' : '기존 제목'
 expect(screen.getByRole('heading',{name:label}).title).toBe(label)
 if(mode!=='empty')expect(screen.getByRole('button',{name:label}).title).toBe(label)
 if(mode==='failure')expect(screen.getByRole('alert').textContent).toContain('이름을 바꾸지 못했습니다')
 if(mode==='empty'){
  expect((screen.getByRole('textbox',{name:'대화 이름'}) as HTMLInputElement).value).toBe('   ')
  expect(screen.getByRole('alert').textContent).toContain('이름을 입력하세요.')
 }
 expect(listReads()).toBe(reads)
 expect(window.location.hash).toBe(`#${sessionRoute('saved')}`)
})

it.each([undefined,'  '])('shows neutral loading then a trimmed first question for untitled rows: %s', async title => {
 vi.useFakeTimers()
 window.history.replaceState(null,'',`#${NEW_CHAT_ROUTE}`)
 const host=new WebHostAdapter({rpcMode:'authority',location:{href:'http://localhost/'},reconnect:false})
 vi.spyOn(host,'connect').mockResolvedValue()
 const request=vi.spyOn(host.gateway,'request').mockResolvedValue({})
 let finish!: (value:unknown) => void
 const history=new Promise(resolve => {finish=resolve})
 vi.spyOn(host,'api').mockImplementation(async call => call.path.includes('/messages') ? history : {sessions:[{id:'untitled',title,message_count:1}],total:1})
 render(<HermesWebApp resolver={async () => ({url:'ws://localhost/api/ws'})} host={host}/>)
 await act(async () => {await vi.advanceTimersByTimeAsync(100)})
 expect(screen.getByRole('button',{name:'대화 불러오는 중…'})).toBeTruthy()
 await act(async () => finish({messages:[{role:'user',content:'  첫 질문 전체 내용  '}]}))
 expect(screen.getByRole('button',{name:'첫 질문 전체 내용'}).title).toBe('첫 질문 전체 내용')
 fireEvent.change(screen.getByRole('textbox',{name:'불러온 대화 검색'}),{target:{value:'첫 질문'}})
 expect(screen.getByRole('button',{name:'첫 질문 전체 내용'})).toBeTruthy()
 const before=request.mock.calls.length
 fireEvent.click(screen.getByRole('button',{name:'첫 질문 전체 내용 메뉴'}))
 fireEvent.click(screen.getByRole('menuitem',{name:'이름 바꾸기'}))
 fireEvent.keyDown(screen.getByRole('textbox',{name:'대화 이름'}),{key:'Escape'})
 expect(window.location.hash).toBe(`#${NEW_CHAT_ROUTE}`)
 expect(request.mock.calls).toHaveLength(before)
 expect(screen.getByRole('button',{name:'첫 질문 전체 내용'})).toBeTruthy()
})

it('renames an unopened listed session through the real authority gateway without navigating', async () => {
 $selectedStoredSessionId.set(null);$activeSessionId.set(null);$freshDraftReady.set(true)
 window.history.replaceState(null,'',`#${NEW_CHAT_ROUTE}`)
 const host=new WebHostAdapter({rpcMode:'authority',location:{href:'http://localhost/'},reconnect:false,socketFactory:url => new FakeWebSocket(url) as unknown as WebSocket})
 vi.spyOn(host,'api').mockResolvedValue({sessions:[{id:'never-opened',title:'기존 제목',message_count:0}],total:1})
 const flush=async () => {for(let i=0;i<30;i++)await Promise.resolve()}
 try {
  render(<HermesWebApp resolver={async () => ({url:'ws://localhost/api/ws'})} host={host}/>)
  await act(flush)
  const socket=FakeWebSocket.instances.at(-1)!
  await act(async () => {socket.open();await flush()})
  expect(screen.getByRole('heading',{name:'새 채팅'})).toBeTruthy()
  fireEvent.click(screen.getByRole('button',{name:'기존 제목 메뉴'}))
  fireEvent.click(screen.getByRole('menuitem',{name:'이름 바꾸기'}))
  const input=screen.getByRole('textbox',{name:'대화 이름'})
  fireEvent.change(input,{target:{value:'  새 제목  '}})
  fireEvent.keyDown(input,{key:'Enter'})
  await act(flush)
  const frames=socket.sent.map(value => JSON.parse(value))
  expect(frames.map(frame => frame.method)).toEqual(['session.title'])
  expect(frames[0].params).toEqual({session_id:'never-opened',title:'새 제목'})
  await act(async () => {socket.serverFrame({jsonrpc:'2.0',id:frames[0].id,result:{title:'새 제목',pending:false}});await flush()})
  expect(screen.getByRole('button',{name:'새 제목'}).title).toBe('새 제목')
  expect(screen.getByRole('heading',{name:'새 채팅'})).toBeTruthy()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(window.location.hash).toBe(`#${NEW_CHAT_ROUTE}`)
 } finally {cleanup();host.close();FakeWebSocket.instances=[]}
})

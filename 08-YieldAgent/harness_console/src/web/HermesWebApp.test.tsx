import { JsonRpcGatewayClient } from '@hermes/shared'
import { FakeWebSocket } from '../platform/fake-websocket'
import { writeFileSync } from 'node:fs'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { HermesWebApp } from './HermesWebApp'
import { WebHostAdapter } from '../platform/web-host'
import { capabilityError, restCapabilityError } from '../platform/capability-gate'
import { stubMenuDomApis } from '@/test/jsdom'
import { stubThreadEnvironment } from '@/components/assistant-ui/test-utils'
import { settleConfirm } from '@/store/confirm'
import { NEW_CHAT_ROUTE, sessionRoute } from '@/app/routes'
import { $activeSessionId, $selectedStoredSessionId, $freshDraftReady } from '@/store/session'
stubThreadEnvironment()
afterEach(() => {settleConfirm(false);cleanup();window.history.replaceState(null,'',`#${NEW_CHAT_ROUTE}`);vi.useRealTimers();vi.restoreAllMocks()})
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
 expect(screen.getByRole('heading',{name:'수율 분석'})).toBeTruthy()
 expect(screen.getByRole('heading',{name:'이전 대화'})).toBeTruthy()
 expect(screen.queryByRole('textbox',{name:'불러온 대화 검색'})).toBeNull()
 fireEvent.click(screen.getByRole('button',{name:'사이드바 접기'}))
 expect(screen.queryByRole('navigation')).toBeNull()
 fireEvent.keyDown(window,{key:'k',ctrlKey:true})
 expect(screen.getByRole('textbox',{name:'불러온 대화 검색'})).toBe(document.activeElement)
 expect(screen.queryByText('Original question')).toBeNull()
 fireEvent.change(screen.getByRole('textbox',{name:'불러온 대화 검색'}),{target:{value:'absent'}})
 expect(screen.queryByRole('button',{name:'Saved session'})).toBeNull()
 fireEvent.change(screen.getByRole('textbox',{name:'불러온 대화 검색'}),{target:{value:'saved'}})
 expect(screen.getByRole('button',{name:'Saved session'}).title).toBe('Saved session')
 fireEvent.click(screen.getByRole('button',{name:'대화 검색'}))
 expect(screen.queryByRole('textbox',{name:'불러온 대화 검색'})).toBeNull()
 expect(screen.getByRole('button',{name:'Saved session'})).toBeTruthy()
 for(const [method] of request.mock.calls) expect(capabilityError(method,'authority')).toBeNull()
 for(const [call] of api.mock.calls) expect(restCapabilityError(call.path,call.method ?? 'GET')).toBeNull()
})

it('groups only assigned chats under the project even when it has the same name as a date heading', async () => {
 vi.useFakeTimers()
 const host=new WebHostAdapter({rpcMode:'authority',location:{href:'http://localhost/'},reconnect:false})
 vi.spyOn(host,'connect').mockResolvedValue()
 vi.spyOn(host.gateway,'request').mockResolvedValue({})
 const row={source:'cli',started_at:0,last_active:0,message_count:1}
 vi.spyOn(host,'api').mockImplementation(async call => call.path.includes('/messages')
  ? {messages:[]} : {sessions:[
    {...row,id:'first',title:'Project chat',web_project:{id:'p_1',name:'이전 대화'}},
    {...row,id:'second',title:'Personal chat'}],total:2})
 render(<HermesWebApp resolver={async () => ({url:'ws://localhost/api/ws'})} host={host}/>)
 await act(async () => {await vi.advanceTimersByTimeAsync(11000)})
 const project=screen.getByRole('region',{name:'프로젝트 이전 대화'})
 expect(within(project).getByRole('button',{name:'Project chat'})).toBeTruthy()
 expect(within(project).queryByRole('button',{name:'Personal chat'})).toBeNull()
 expect(within(screen.getByRole('region',{name:'이전 대화'})).getByRole('button',{name:'Personal chat'})).toBeTruthy()
})

it('opens mobile search and returns keyboard focus when the drawer closes', async () => {
 vi.useFakeTimers()
 const previous=window.matchMedia
 window.matchMedia=query => ({matches:query==='(max-width: 650px)',addEventListener:vi.fn(),removeEventListener:vi.fn()}) as unknown as MediaQueryList
 try {
  const host=new WebHostAdapter({rpcMode:'authority',location:{href:'http://localhost/'},reconnect:false})
  vi.spyOn(host,'connect').mockResolvedValue()
  vi.spyOn(host,'api').mockResolvedValue({sessions:[],total:0})
  render(<HermesWebApp resolver={async () => ({url:'ws://localhost/api/ws'})} host={host}/>)
  await act(async () => {await vi.advanceTimersByTimeAsync(100)})
  expect(screen.queryByRole('navigation')).toBeNull()
  fireEvent.keyDown(window,{key:'k',metaKey:true})
  const search=screen.getByRole('textbox',{name:'불러온 대화 검색'})
  expect(search).toBe(document.activeElement)
  expect(document.querySelector('.hermes-web-chat')?.hasAttribute('inert')).toBe(true)
  fireEvent.keyDown(search,{key:'Escape'})
  expect(screen.getByRole('button',{name:'대화 검색'})).toBe(document.activeElement)
  fireEvent.keyDown(document.activeElement!,{key:'Escape'})
  await act(async () => {await vi.advanceTimersByTimeAsync(20)})
  expect(screen.queryByRole('navigation')).toBeNull()
  expect(screen.getByRole('button',{name:'사이드바 열기'})).toBe(document.activeElement)
 } finally {window.matchMedia=previous}
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
 const fetcher=vi.fn<typeof fetch>().mockImplementation(async url => new Response(JSON.stringify(String(url)==='/harness/products' ? {products:['TEST-X']} : String(url).startsWith('/harness/results/') ?
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
 fireEvent.click(screen.getByRole('button',{name:'새 대화'}))
 await act(async () => {await vi.advanceTimersByTimeAsync(100)})
 expect(window.location.hash).toBe(`#${NEW_CHAT_ROUTE}`)
 expect($selectedStoredSessionId.get()).toBeNull()
 expect(screen.getByRole('button',{name:'표와 검증 결과'}).hasAttribute('aria-current')).toBe(false)
 expect($activeSessionId.get()).toBeNull()
 expect($freshDraftReady.get()).toBe(true)
 expect(screen.getByRole('heading',{name:'어떤 분석을 시작할까요?'})).toBeTruthy()
 expect(screen.queryByText('예시 질문')).toBeNull()
 stubMenuDomApis()
 const product=screen.getByRole('button',{name:'분석 대상 제품, 전체 제품'})
 const before=request.mock.calls.length
 fireEvent.click(product)
 await act(async () => {await vi.advanceTimersByTimeAsync(100)})
 const searchBox=screen.getByRole('combobox')
 fireEvent.change(searchBox,{target:{value:'test'}})
 fireEvent.click(screen.getByRole('option',{name:'TEST-X'}))
 expect(product.textContent).toBe('TEST-X')
 expect(document.querySelector('[contenteditable="true"]')?.textContent).toBe('')
 expect(request.mock.calls).toHaveLength(before)
 expect(window.location.hash).toBe(`#${NEW_CHAT_ROUTE}`)
 expect(document.querySelector('[data-placeholder="수율이나 웨이퍼에 대해 질문하세요"]')).toBeTruthy()
 expect(document.querySelector('.hermes-web-composer [aria-label="사용량과 실행 시간"]')).toBeNull()
 expect(request.mock.calls.filter(([method]) => method==='session.resume')).toHaveLength(1)
 expect(fetcher.mock.calls.some(([url]) => String(url).startsWith('/harness/sessions/'))).toBe(false)
})

it('opens a direct skill-library link and starts an editable skill request in a fresh chat', async () => {
 window.history.replaceState(null,'','#/?view=skills')
 const host=new WebHostAdapter({rpcMode:'authority',location:{href:'http://localhost/'},reconnect:false})
 vi.spyOn(host,'connect').mockResolvedValue()
 vi.spyOn(host,'api').mockResolvedValue({sessions:[],total:0})
 const request=vi.spyOn(host.gateway,'request').mockResolvedValue({})
 const fetcher=vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({skills:[]})))
 render(<HermesWebApp resolver={async () => ({url:'ws://localhost/api/ws'})} host={host} fetcher={fetcher}/>)
 const create=await screen.findByRole('button',{name:'새 스킬'})
 expect(screen.getByRole('button',{name:'스킬'}).getAttribute('aria-current')).toBe('page')
 expect(screen.queryByRole('textbox',{name:'Message'})).toBeNull()
 fireEvent.click(create)
 const editor=await screen.findByRole('textbox',{name:'Message'})
 expect(editor.textContent).toContain('스킬로 만들어줘')
 expect(window.location.hash).toBe('#/')
 expect(request.mock.calls.some(([method]) => method==='session.create')).toBe(false)
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

it('keeps the pushed title for a resumed off-page chat when its earlier title lookup finishes', async () => {
 $freshDraftReady.set(false)
 window.history.replaceState(null,'',`#${sessionRoute('off-page')}`)
 const host=new WebHostAdapter({rpcMode:'authority',location:{href:'http://localhost/'},reconnect:false,socketFactory:url => new FakeWebSocket(url) as unknown as WebSocket})
 vi.spyOn(host.gateway,'request').mockImplementation(async () => ({session_id:'runtime',session_key:'off-page',messages:[],running:false,info:{}}))
 vi.spyOn(host,'api').mockImplementation(async call => call.path.includes('/messages') ? {messages:[]} : {sessions:[],total:0})
 try {
  render(<HermesWebApp resolver={async () => ({url:'ws://localhost/api/ws'})} host={host}/>)
  await act(async () => {})
  const socket=FakeWebSocket.instances.at(-1)!
  await act(async () => {socket.open()})
  const lookup=socket.request('session.title')!
  await act(async () => {socket.serverFrame({jsonrpc:'2.0',method:'event',params:{type:'session.title',session_id:'runtime',payload:{session_id:'off-page',title:'저수율 분석'}}})})
  expect(screen.getByRole('heading',{name:'저수율 분석'})).toBeTruthy()
  await act(async () => {socket.serverFrame({jsonrpc:'2.0',id:lookup.id,result:{title:'임시 제목'}})})
  expect(screen.getByRole('heading',{name:'저수율 분석'})).toBeTruthy()
 } finally {cleanup();host.close();FakeWebSocket.instances=[]}
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
 fireEvent.click(screen.getByRole('button',{name:'대화 검색'}))
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

it('keeps a pushed auto-title and a subsequent manual rename across older list responses while in a new chat', async () => {
 $selectedStoredSessionId.set(null);$activeSessionId.set(null);$freshDraftReady.set(true)
 window.history.replaceState(null,'',`#${NEW_CHAT_ROUTE}`)
 const host=new WebHostAdapter({rpcMode:'authority',location:{href:'http://localhost/'},reconnect:false,socketFactory:url => new FakeWebSocket(url) as unknown as WebSocket})
 let title='기존 제목'
 const list=() => ({sessions:[{id:'never-opened',title,message_count:0}],total:100})
 const api=vi.spyOn(host,'api').mockImplementation(async () => list())
 const flush=async () => {for(let i=0;i<30;i++)await Promise.resolve()}
 try {
  render(<HermesWebApp resolver={async () => ({url:'ws://localhost/api/ws'})} host={host}/>)
  await act(flush)
  const socket=FakeWebSocket.instances.at(-1)!
  await act(async () => {socket.open();await flush()})
  expect(screen.getByRole('heading',{name:'새 대화'})).toBeTruthy()
  const deferList=async () => {
   const snapshot=list()
   let finish!:()=>void
   api.mockImplementationOnce(() => new Promise(resolve => {finish=() => resolve(snapshot)}))
   fireEvent.click(screen.getByRole('button',{name:'이전 대화 더 보기'}))
   await act(flush)
   return finish
  }
  const oldList=await deferList()
  title='자동 제목'
  await act(async () => {
   socket.serverFrame({jsonrpc:'2.0',method:'event',params:{type:'session.title',session_id:'runtime',payload:{session_id:'never-opened',title}}})
   await flush()
  })
  expect(screen.getByRole('button',{name:'자동 제목'})).toBeTruthy()
  await act(async () => {oldList();await flush()})
  expect(screen.getByRole('button',{name:'자동 제목'})).toBeTruthy()
  const beforeRename=await deferList()
  fireEvent.click(screen.getByRole('button',{name:'자동 제목 메뉴'}))
  fireEvent.click(screen.getByRole('menuitem',{name:'이름 바꾸기'}))
  const input=screen.getByRole('textbox',{name:'대화 이름'})
  fireEvent.change(input,{target:{value:'  새 제목  '}})
  fireEvent.keyDown(input,{key:'Enter'})
  await act(flush)
  const frames=socket.sent.map(value => JSON.parse(value))
  expect(frames.map(frame => frame.method)).toEqual(['session.title'])
  expect(frames[0].params).toEqual({session_id:'never-opened',title:'새 제목'})
  title='새 제목'
  await act(async () => {socket.serverFrame({jsonrpc:'2.0',id:frames[0].id,result:{title,pending:false}});await flush()})
  await act(async () => {beforeRename();await flush()})
  expect(screen.getByRole('button',{name:'새 제목'}).title).toBe('새 제목')
  expect(screen.getByRole('heading',{name:'새 대화'})).toBeTruthy()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(window.location.hash).toBe(`#${NEW_CHAT_ROUTE}`)
 } finally {cleanup();host.close();FakeWebSocket.instances=[]}
})

it.each(['archive','delete','cancel'])('session menu removes an open row after %s', async action => {
 vi.useFakeTimers()
 $freshDraftReady.set(false)
 window.history.replaceState(null,'',`#${sessionRoute('saved')}`)
 const host=new WebHostAdapter({rpcMode:'authority',location:{href:'http://localhost/'},reconnect:false})
 vi.spyOn(host,'connect').mockResolvedValue()
 const request=vi.spyOn(JsonRpcGatewayClient.prototype,'request').mockResolvedValue({})
 vi.spyOn(host.gateway,'request').mockResolvedValue({session_id:'saved',session_key:'saved',messages:[],running:false,info:{}})
 vi.spyOn(host,'api').mockResolvedValue({sessions:[{id:'saved',title:'관리할 대화',message_count:0}, {id:'archived',title:'보관된 대화',archived:true}, {id:'deleted',title:'삭제된 대화',deleted:true}],total:3})
 render(<HermesWebApp resolver={async () => ({url:'ws://localhost/api/ws'})} host={host}/>)
 await act(async () => {await vi.advanceTimersByTimeAsync(100)})
 expect(screen.queryByRole('button',{name:'보관된 대화'})).toBeNull()
 expect(screen.queryByRole('button',{name:'삭제된 대화'})).toBeNull()
 fireEvent.click(screen.getByRole('button',{name:'관리할 대화 메뉴'}))
 fireEvent.click(screen.getByRole('menuitem',{name:action==='archive' ? '보관' : '삭제'}))
 await act(async () => {await vi.advanceTimersByTimeAsync(100)})
 if(action!=='archive'){
  expect(request.mock.calls.filter(([method]) => method==='session.delete')).toHaveLength(0)
  expect(document.querySelector('.web-session-select[aria-label="관리할 대화"]')).toBeTruthy()
  expect(screen.getByRole('dialog',{name:'대화를 삭제할까요?'})).toBeTruthy()
  fireEvent.click(screen.getByRole('button',{name:action==='cancel' ? '취소' : '삭제'}))
  await act(async () => {await vi.advanceTimersByTimeAsync(100)})
 }
 if(action==='cancel'){
  expect(screen.getByRole('button',{name:'관리할 대화'})).toBeTruthy()
  expect(request.mock.calls.filter(([method]) => method==='session.delete')).toHaveLength(0)
 }else{
  expect(request).toHaveBeenCalledWith(`session.${action}`,{session_id:'saved'})
  expect(screen.queryByRole('button',{name:'관리할 대화'})).toBeNull()
  expect(window.location.hash).toBe(`#${NEW_CHAT_ROUTE}`)
  expect($activeSessionId.get()).toBeNull()
 }
})

it('navigates to MCP settings from the sidebar and keeps the chat stage hidden', async () => {
 const host=new WebHostAdapter({rpcMode:'authority',location:{href:'http://localhost/'},reconnect:false})
 vi.spyOn(host,'connect').mockImplementation(() => new Promise(() => {}))
 const fetcher=vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({servers:[]})))
 render(<HermesWebApp resolver={async () => ({url:'ws://localhost/api/ws'})} host={host} fetcher={fetcher}/>)
 fireEvent.click(screen.getByRole('button',{name:'MCP 서버0/0'}))
 expect(await screen.findByRole('heading',{name:'내 MCP 서버'})).toBeTruthy()
 expect(window.location.hash).toContain('view=mcp')
 expect(document.title).toBe('MCP 서버 · 수율 분석')
 expect(document.querySelector('.web-chat-stage')?.hasAttribute('hidden')).toBe(true)
})

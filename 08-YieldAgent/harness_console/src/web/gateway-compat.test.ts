import { readFileSync } from 'node:fs'
import { afterEach, expect, it, vi } from 'vitest'
import { JsonRpcGatewayClient } from '@hermes/shared'
import { WebHostAdapter } from '../platform/web-host'
import { FakeWebSocket } from '../platform/fake-websocket'
import { withWebGatewayCompat } from './gateway-compat'

const hosts:WebHostAdapter[]=[]
const flush=async () => {for(let i=0;i<20;i++) await Promise.resolve()}
function make(rpcMode:'authority'|'legacy'='authority') {
 const host=new WebHostAdapter({rpcMode,location:{href:'http://localhost/'},reconnect:false,socketFactory:url => new FakeWebSocket(url) as unknown as WebSocket})
 hosts.push(host)
 return host
}
afterEach(() => {hosts.splice(0).forEach(host => host.close());FakeWebSocket.instances=[];vi.restoreAllMocks()})

it.each(['normal','',null,undefined])('removes default/empty tier %s without mutating the caller', async tier => {
 const host=make(), request=vi.spyOn(host.gateway,'request').mockResolvedValue({})
 withWebGatewayCompat(host)
 const params=Object.freeze({service_tier:tier,cwd_explicit:false,fast:false,request_id:'r',profile:'default',source:'gui'})
 await host.gateway.request('session.create',params)
 expect(request).toHaveBeenCalledExactlyOnceWith('session.create',{request_id:'r',profile:'default',source:'gui'})
 expect(params).toHaveProperty('service_tier',tier)
 expect(params.cwd_explicit).toBe(false)
})

it.each(['priority','ultrafast'])('retains explicit selections including %s', async tier => {
 const host=make(), request=vi.spyOn(host.gateway,'request').mockResolvedValue({})
 withWebGatewayCompat(host)
 const params={model:'chosen',provider:'chosen-provider',toolsets:['tools'],reasoning:'high',cwd:'/work',cwd_explicit:true,service_tier:tier,fast:true}
 await host.gateway.request('session.create',params)
 expect(request.mock.calls[0][1]).toBe(params)
})

it('preserves an explicit tier override of fast mode', async () => {
 const host=make(), request=vi.spyOn(host.gateway,'request').mockResolvedValue({})
 withWebGatewayCompat(host)
 const params={service_tier:'normal',fast:true}
 await host.gateway.request('session.create',params)
 expect(request.mock.calls[0][1]).toBe(params)
})

it.each(['prompt.submit','session.resume'])('passes %s through with the same params, deadline, signal and promise', method => {
 const host=make(), pending=Promise.resolve({ok:true})
 const request=vi.spyOn(host.gateway,'request').mockReturnValue(pending)
 withWebGatewayCompat(host)
 const params={service_tier:'normal',cwd_explicit:false,fast:false}, signal=new AbortController().signal
 const result=host.gateway.request(method,params,456,signal)
 expect(result).toBe(pending)
 expect(request).toHaveBeenCalledExactlyOnceWith(method,params,456,signal)
 expect(request.mock.calls[0][1]).toBe(params)
})

it('installs once, preserves rejection identity and does not retry', async () => {
 const host=make(), error=Object.assign(new Error('handler_not_connected'),{code:-32005})
 const request=vi.spyOn(host.gateway,'request').mockRejectedValue(error)
 withWebGatewayCompat(host)
 const wrapped=host.gateway.request
 expect(withWebGatewayCompat(host)).toBe(host)
 expect(host.gateway.request).toBe(wrapped)
 await expect(host.gateway.request('session.create',{model:'explicit'})).rejects.toBe(error)
 expect(request).toHaveBeenCalledTimes(1)
})

it('does not wrap legacy hosts', () => {
 const host=make('legacy'), request=host.gateway.request
 withWebGatewayCompat(host)
 expect(host.gateway.request).toBe(request)
})

it('emits an authority create frame without regenerating the default tier', async () => {
 const host=withWebGatewayCompat(make())
 const connecting=host.connect();await flush()
 const socket=FakeWebSocket.instances.at(-1)!
 socket.open();await connecting
 const pending=host.gateway.request('session.create',{cols:96,source:'desktop',profile:'default',fast:false,cwd_explicit:false,request_id:'new-chat'})
 await flush()
 const frame=socket.request('session.create')!
 // Reply before assertions so an assertion failure leaves no pending rejection.
 socket.serverFrame({jsonrpc:'2.0',id:frame.id,result:{session_id:'new-session',revision:1,execution_generation:1}})
 await pending
 expect(socket.sent).toHaveLength(1)
 expect(frame.params).toEqual({source:'gui',profile:'default',request_id:'new-chat'})
})

it('dispatches title directly with the same params, deadline, signal and promise', () => {
 const host=make(), pending=Promise.resolve({title:'Renamed'})
 const canonical=vi.spyOn(host.gateway,'request')
 const raw=vi.spyOn(JsonRpcGatewayClient.prototype,'request').mockReturnValue(pending)
 withWebGatewayCompat(host)
 const params=Object.freeze({session_id:'never-opened',title:'Renamed'}),signal=new AbortController().signal
 expect(host.gateway.request('session.title',params,456,signal)).toBe(pending)
 expect(raw).toHaveBeenCalledExactlyOnceWith('session.title',params,456,signal)
 expect(raw.mock.instances[0]).toBe(host.gateway)
 expect(canonical).not.toHaveBeenCalled()
})

it.each([{session_id:'never-opened',title:'Renamed'},{session_id:'never-opened'}])('sends plain title without resume or revision: %j', async params => {
 const host=withWebGatewayCompat(make())
 const connecting=host.connect();await flush()
 const socket=FakeWebSocket.instances.at(-1)!
 socket.open();await connecting
 const outcome=host.gateway.request('session.title',params).then(value => ({value}),error => ({error}))
 await flush()
 const frame=socket.request('session.title')
 if(frame)socket.serverFrame({jsonrpc:'2.0',id:frame.id,result:{title:'Renamed',pending:false}})
 expect(await outcome).toEqual({value:{title:'Renamed',pending:false}})
 expect(socket.sent.map(value => JSON.parse(value))).toEqual([{jsonrpc:'2.0',id:expect.any(Number),method:'session.title',params}])
})

it('keeps the rename field flexible with a visible focus border', () => {
 const css=readFileSync(new URL('./sidebar.css',import.meta.url),'utf8')
 const input=css.split('\n.hermes-web .web-session-content input {')[1].split('}')[0]
 for(const rule of ['flex:1','min-width:0'])expect(input).toContain(rule)
 expect(css).toContain('.hermes-web .web-session-content input:focus { border-color:')
})

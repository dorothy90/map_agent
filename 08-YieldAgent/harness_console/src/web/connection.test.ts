import { expect,it } from 'vitest'
import { webConnectionResolver } from './connection'
const context={location:new URL('https://example.test/'),profile:'default',rpcMode:'authority' as const,fetch:globalThis.fetch}
it('preserves host-owned authentication without guessing its format', async () => {
 const connection={url:'wss://example.test/api/ws?opaque=x',protocols:['custom-protocol']}
 expect(await webConnectionResolver(async () => connection)(context)).toEqual(connection)
})
it.each(['ws://example.test/api/ws','wss://elsewhere.test/api/ws','wss://example.test/gateway-m1/api/ws'])('rejects %s without fallback',async url => {
 await expect(webConnectionResolver(async () => ({url}))(context)).rejects.toThrow('same-service')
})

import type { WebHostOptions } from '../platform/web-host'
export type ConnectionResolver = NonNullable<WebHostOptions['getWebSocketConnection']>
/** Q1 is host-owned. Enforce only D02's same-service endpoint, not an auth format. */
export function webConnectionResolver(resolve:ConnectionResolver):ConnectionResolver {
 return async context => {
  const result=await resolve(context)
  const url=new URL(result.url),expected=new URL('/api/ws',context.location)
  expected.protocol=expected.protocol==='https:' ? 'wss:' : 'ws:'
  if(url.origin!==expected.origin || url.pathname!=='/api/ws' || url.username || url.password || url.hash) throw new Error('Expected same-service /api/ws connection')
  return result
 }
}

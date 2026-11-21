import type { HermesWebOptions } from './HermesWebApp'
/** Local development connections. This module is eliminated from production builds. */
export function devConnectionOptions():HermesWebOptions {
 if (!import.meta.env.DEV) throw new Error('Development connection is disabled')
 if (import.meta.env.VITE_HARNESS_LOCAL_GATEWAY === '1') {
   return {resolver:async ({location:page}) => {
     const response = await fetch('/api/auth/ws-ticket', {method:'POST', credentials:'same-origin'})
     if (!response.ok) throw new Error('Local gateway did not issue a WebSocket ticket')
     const {ticket} = await response.json()
     if (typeof ticket !== 'string' || !ticket) throw new Error('Missing WebSocket ticket')
     const url = new URL('/api/ws', page)
     url.protocol = page.protocol === 'https:' ? 'wss:' : 'ws:'
     url.searchParams.set('ticket', ticket)
     return {url:url.href}
   }}
 }
 const user=new URL(location.href).searchParams.get('user')
 if (user!=='alice' && user!=='bob') return {}
 const fetcher:typeof fetch=(input,init) => {
   const url=new URL(input instanceof Request ? input.url : input.toString(),location.href)
   if(url.origin!==location.origin) return Promise.reject(new Error('Development host must be same-origin'))
   url.searchParams.set('user',user)
   return fetch(url,init)
 }
 return {fetcher,resolver:async ({location:page}) => {
   const url=new URL('/api/ws',page);url.protocol=page.protocol==='https:' ? 'wss:' : 'ws:'
   url.searchParams.set('user',user)
   return {url:url.href}
 }}
}

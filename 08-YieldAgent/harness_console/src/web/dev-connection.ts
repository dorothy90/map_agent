import type { HermesWebOptions } from './HermesWebApp'
/** Loopback fixture only. This module is eliminated from production builds. */
export function devConnectionOptions():HermesWebOptions {
 if (!import.meta.env.DEV) throw new Error('Development connection is disabled')
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

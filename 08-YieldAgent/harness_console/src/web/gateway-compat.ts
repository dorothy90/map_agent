import { JsonRpcGatewayClient } from '@hermes/shared'
import type { WebHostAdapter } from '../platform/web-host'

// harness/gateway/session_rpc.py:228-240 rejects the presence of authority
// unavailable fields, even defaults. Upstream session-create-request.ts's
// DROPPABLE_CREATE_FIELDS only retries a different, code-4000 validation error.
// canonical-protocol.ts:500-530 also turns Desktop fast:false into tier 'normal'.
// Only these inert defaults are omitted; explicit model/tools/cwd/tier survive.
const CREATE_DEFAULTS: Record<string, readonly unknown[]> = {
 service_tier: ['normal', '', null, undefined],
 cwd_explicit: [false, '', null, undefined],
 fast: [false],
}
const installed = new WeakSet<WebHostAdapter>()

/** Apply once to this web authority host, before its hooks register the gateway. */
export function withWebGatewayCompat(host:WebHostAdapter):WebHostAdapter {
 if(host.rpcMode!=='authority' || installed.has(host)) return host
 const request=host.gateway.request.bind(host.gateway)
 const rawRequest=JsonRpcGatewayClient.prototype.request.bind(host.gateway)
 host.gateway.request=(...args) => {
  const [method,params]=args
  // Yield session management uses stored IDs and the legacy-shaped authority schemas.
  if(['session.title','session.archive','session.delete','session.set_hidden'].includes(method)) return rawRequest(...args)
  let normalized=params
  if(method==='session.create' && params) {
   for(const [field,defaults] of Object.entries(CREATE_DEFAULTS)) {
    // An explicit normal tier overrides fast:true upstream; retain that intent.
    if(field==='service_tier' && params.fast===true && params.service_tier==='normal') continue
    if(Object.hasOwn(params,field) && defaults.includes(params[field])) {
     if(normalized===params) normalized={...params}
     delete normalized![field]
    }
   }
  }
  if(normalized!==params) args[1]=normalized
  return request(...args)
 }
 installed.add(host)
 return host
}

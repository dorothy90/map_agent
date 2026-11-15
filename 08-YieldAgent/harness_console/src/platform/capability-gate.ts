import { JsonRpcGatewayError } from '@hermes/shared'
import manifest from './yield-manifest.generated.json'

export type RpcMode = 'legacy' | 'authority'
type Policy = { classification: string; reason: string; feature: string }
const rpc: Record<string, Policy & { modes: Partial<Record<RpcMode, Policy>> }> = manifest.rpc
export function capabilityError(method: string, mode: RpcMode): JsonRpcGatewayError | null {
  const policy = Object.hasOwn(rpc, method) ? rpc[method].modes[mode] : undefined
  if (policy?.classification === 'implemented') return null
  return new JsonRpcGatewayError('unsupported', { code: -32004, data: {
    reason: policy?.reason ?? 'unknown_method_or_mode', feature: policy?.feature ?? method, upstream: '5894e750',
  } })
}
export type RpcRequest = <T>(method: string, params?: Record<string, unknown>, timeoutMs?: number, signal?: AbortSignal) => Promise<T>
export function gateRequest(request: RpcRequest, mode: RpcMode): RpcRequest {
  return (method, params, timeoutMs, signal) => {
    const error = capabilityError(method, mode)
    return error ? Promise.reject(error) : request(method, params, timeoutMs, signal)
  }
}

/** Gate at the wire as well: canonical translation and channel-owned negotiation bypass public request(). */
export function gateSocket(socket: WebSocket, mode: RpcMode): WebSocket {
  const events = new EventTarget()
  for (const name of ['open', 'close', 'error', 'message']) socket.addEventListener(name, event => {
    if (name === 'message') events.dispatchEvent(new MessageEvent(name, { data: (event as MessageEvent).data }))
    else if (name === 'close') {
      // Event with structural close fields also works in node without a DOM CloseEvent.
      events.dispatchEvent(Object.assign(new Event(name), { code: (event as CloseEvent).code, reason: (event as CloseEvent).reason }))
    } else events.dispatchEvent(new Event(name))
  })
  return new Proxy(socket, { get(target, key) {
    if (key === 'addEventListener') return events.addEventListener.bind(events)
    if (key === 'removeEventListener') return events.removeEventListener.bind(events)
    if (key === 'send') return (data: string) => {
      const frame = JSON.parse(data)
      // Responses to server requests have no method and pass through.
      const error = frame.method ? capabilityError(frame.method, mode) : null
      if (!error) return target.send(data)
      if (frame.id !== undefined) queueMicrotask(() => events.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ jsonrpc: '2.0', id: frame.id, error: { code: error.code, message: error.message, data: error.data } }) })))
    }
    const value = Reflect.get(target, key, target)
    return typeof value === 'function' ? value.bind(target) : value
  } })
}

/** Manifest templates are structural URL contracts, not natural-language routing rules. */
export function restCapabilityError(path: string, method = 'GET'): JsonRpcGatewayError | null {
  const pathname = path.split(/[?#]/, 1)[0]
  let parts: string[] = []
  try {
    parts = pathname.split('/').map(decodeURIComponent)
  } catch { /* malformed encoding has no supported route */ }
  const valid = parts.length > 0 && !parts.some(part => part.includes('/') || part.includes('\\') || part === '..' || part === '.')
  const routes = manifest.routes.filter(row => row.method === method.toUpperCase())
  const matches = (template: string): boolean => {
    const pattern = template.split('/')
    return pattern.every((part, index) => {
      if (part.startsWith('{') && part.endsWith('}')) {
        if (part.endsWith(':path}')) return parts.slice(index).join('/').length > 0
        return index < parts.length && parts[index].length > 0
      }
      return parts[index] === part
    }) && (pattern.length === parts.length || pattern.at(-1)?.endsWith(':path}') === true)
  }
  // Static paths win over parameter paths, as in the backend route registration.
  const policy = valid ? routes.find(row => row.path === pathname) ?? routes.find(row => matches(row.path)) : undefined
  if (policy?.classification === 'implemented') return null
  return new JsonRpcGatewayError('unsupported', { code: -32004, data: {
    reason: policy?.reason ?? 'unknown_route_or_method', feature: policy?.feature ?? pathname, upstream: '5894e750',
  } })
}

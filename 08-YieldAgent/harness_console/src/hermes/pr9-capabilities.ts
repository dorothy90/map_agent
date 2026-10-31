// PR-9a: consult the existing manifest before optional effects schedule requests.
import { capabilityError, restCapabilityError } from '../platform/capability-gate'

export function rpcImplemented(method: string): boolean {
  return !capabilityError(method, 'legacy') || !capabilityError(method, 'authority')
}
export function restImplemented(path: string, method = 'GET'): boolean {
  return !restCapabilityError(path, method)
}

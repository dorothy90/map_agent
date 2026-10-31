const connected = (copy: ConnectorCopy): SettledWord => ({ meta: copy.connected, tone: 'ok' })
const notConnected = (copy: ConnectorCopy): SettledWord => ({ meta: copy.notConnected })
const skipped = (copy: ConnectorCopy): SettledWord => ({ meta: copy.skipped })
// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import type { ToolCallMessagePartProps } from '@assistant-ui/react'
import type { ConnectionTargetState } from '@hermes/shared'
import { type RefObject } from 'react'
import { type ConnectorRowMark } from '@/components/ui/connector-card'
import { useI18n } from '@/i18n'
import { type ConnectionOwner, type ConnectionRequest, type ConnectionTarget } from '@/store/connection-request'
export async function openConnectionDoneLink(
  op: string,
  navigate: (to: string) => void,
  storedSessionIdFor: (runtimeSessionId: string) => string
): Promise<void> {}
export async function reissueConnectionTarget(
  owner: ConnectionOwner,
  request: ConnectionRequest,
  name: string
): Promise<null | string> { return null }
export function connectionRequestOwnsPart(props: ToolCallMessagePartProps, request: ConnectionRequest | null): boolean { return false }
export function ConnectorTool(props: ToolCallMessagePartProps) { return null }
export function ConnectorDirectoryReceipt({ result }: { result: unknown }) { return null }
export const CONNECTOR_CARD_PHASES = {
  connected: { mark: 'connected', resolved: true, settled: connected, verb: 'none' },
  expired: { mark: 'idle', resolved: false, settled: notConnected, verb: 'reissue' },
  failed: { mark: 'idle', resolved: false, settled: notConnected, verb: 'reissue' },
  initiated: { mark: 'waiting', resolved: false, settled: notConnected, verb: 'open' },
  not_connected: { mark: 'idle', resolved: false, settled: notConnected, verb: 'none' },
  pending: { mark: 'idle', resolved: false, settled: notConnected, verb: 'open' },
  skipped: { mark: 'idle', resolved: true, settled: skipped, verb: 'none' }
} satisfies Record<ConnectionTargetState, ConnectorCardPhase>
export function useConnectorFocusHandoff(
  targets: readonly ConnectionTarget[],
  cardRef: RefObject<HTMLDivElement | null>
): void {}
export const MARK_LABEL = {
  connected: (copy: ConnectorCopy) => copy.connected,
  idle: (copy: ConnectorCopy) => copy.notConnected,
  waiting: (copy: ConnectorCopy) => copy.waiting
} satisfies Record<ConnectorRowMark, (copy: ConnectorCopy) => string>
export function ConnectorOffer({ owner, request }: ConnectorOfferProps) { return null }
export function ConnectorExecution(props: ToolCallMessagePartProps) { return null }
type ConnectorCopy = ReturnType<typeof useI18n>['t']['connectors']
interface ConnectorCardPhase {
  mark: ConnectorRowMark
  resolved: boolean
  settled: (copy: ConnectorCopy) => SettledWord
  verb: ConnectorVerb
}



interface ConnectorOfferProps {
  owner: ConnectionOwner
  request: ConnectionRequest
}
type ConnectorVerb = 'none' | 'open' | 'reissue'
interface SettledWord {
  meta: string
  tone?: 'ok'
}

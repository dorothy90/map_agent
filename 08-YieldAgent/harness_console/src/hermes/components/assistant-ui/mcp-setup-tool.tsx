// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import { type ToolCallMessagePartProps } from '@assistant-ui/react'
import { type McpTarget } from '@/lib/connector-tools'
import { type ConnectionOwner, type ConnectionRequest } from '@/store/connection-request'
export const McpSetupTool = (props: ToolCallMessagePartProps) => null
export function McpSetupPending(props: ToolCallMessagePartProps) { return null }
export function McpSetupOffer({ action, owner, request }: McpSetupOfferProps) { return null }
interface McpSetupOfferProps {
  action: SetupAction
  /** Null until the session's owner resolves; only Try again needs it, so the rest of the card works. */
  owner: ConnectionOwner | null
  request: ConnectionRequest
}
type SetupAction = McpTarget['action']

// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import type { ToolCallMessagePartProps } from '@assistant-ui/react'
import { type CatalogEntry, type ConnectionRequest, type ConnectionTarget } from '@/store/connection-request'
export function CatalogInstallTool(props: ToolCallMessagePartProps) { return null }
export function CatalogInstallCard({ request }: { request: ConnectionRequest }) { return null }
export function CatalogRow({ request, target }: CatalogRowProps) { return null }
interface CatalogRowProps {
  request: ConnectionRequest
  target: CatalogTarget
}
type CatalogTarget = ConnectionTarget & { catalog: CatalogEntry; kind: 'plugin' | 'skill' }

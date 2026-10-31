// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import type { ConnectorCardModel } from '@/app/capabilities/connectors/types'
import { orderConnectorPicks } from '@/components/assistant-ui/connector-order'
export interface ConnectorDirectoryResult {
  connected: string[]
  newly_connected: string[]
  suggested: string[]
}
export interface ConnectorDirectoryCardProps {
  /** Slugs pinned at the top, in this order; unknown slugs are left out. */
  suggested?: readonly string[]
  onContinue: (result: ConnectorDirectoryResult) => void
}
export interface DirectorySections {
  rest: ConnectorCardModel[]
  suggested: ConnectorCardModel[]
}
export function directorySections(
  cards: readonly ConnectorCardModel[],
  suggested: readonly string[],
  query: string
): DirectorySections {
  const needle = query.trim().toLowerCase()
  const pinned = new Set(suggested)

  const ordered = orderConnectorPicks(
    cards.map(card => ({ card, connector: card.slug, enabled: card.state !== 'off' }))
  ).map(row => row.card)

  // A suggestion cannot bring back an app the picker leaves out.
  const bySlug = new Map(ordered.map(card => [card.slug, card]))

  return {
    rest: ordered.filter(card => !pinned.has(card.slug) && matches(card, needle)),
    suggested: suggested.flatMap(slug => {
      const card = bySlug.get(slug)

      return card && matches(card, needle) ? [card] : []
    })
  }
}
export function ConnectorDirectoryCard(props: ConnectorDirectoryCardProps) { return null }
const matches = (card: ConnectorCardModel, needle: string): boolean =>
  !needle || card.name.toLowerCase().includes(needle) || card.slug.includes(needle)

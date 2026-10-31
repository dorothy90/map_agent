/** Accept only the URL query, never a fragment that resembles a query. */
export function isHermesOptIn(search: string): boolean {
  return new URLSearchParams(search.split('#', 1)[0]).get('ui') === 'hermes'
}

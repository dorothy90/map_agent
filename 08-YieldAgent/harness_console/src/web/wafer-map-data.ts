type GroupRow = { group_id: string; wafer_count: number; fail_rate: number }
type CellRow = { group_id: string; row: number; col: number; fail_rate: number }

export type WaferMapGroup = { id: string; waferCount: number; failRate: number; cells: CellRow[] }
export type WaferMaps = {
  groups: WaferMapGroup[]
  groupBy?: string
  minCol: number
  minRow: number
  columns: number
  rows: number
  low: number
  high: number
}

type Observation = {
  tool_name: string
  result_id: string
  tables: { table_id: string; complete: boolean }[]
  artifact_refs: { artifact_id: string; mime: string }[]
  scope?: { map_aggregate?: { group_by?: string } }
}

async function fetchJson(path: string, query: URLSearchParams, signal: AbortSignal) {
  const response = await fetch(path + '?' + query, { signal, redirect: 'error' })
  if (!response.ok) throw new Error('Stored result unavailable')
  return response.json()
}

async function readTable<T>(sessionId: string, resultId: string, tableId: string, signal: AbortSignal) {
  const rows: T[] = []
  const query = new URLSearchParams({ session_id: sessionId, table_id: tableId, limit: '1000' })
  const path = '/harness/results/' + encodeURIComponent(resultId)
  for (;;) {
    query.set('offset', String(rows.length))
    const page = await fetchJson(path, query, signal)
    if (page.result_id !== resultId || page.table_id !== tableId || page.offset !== rows.length ||
        !page.complete || !Number.isSafeInteger(page.total_rows) || !Array.isArray(page.rows)) {
      throw new Error('Incomplete wafer map page')
    }
    rows.push(...page.rows)
    if (rows.length === page.total_rows) return rows
    if (!page.rows.length || rows.length > page.total_rows) throw new Error('Invalid wafer map page')
  }
}

function groupMaps(groups: GroupRow[], cells: CellRow[], groupBy?: string): WaferMaps | null {
  const byId = new Map<string, WaferMapGroup>()
  const isRate = (rate: number) => Number.isFinite(rate) && rate >= 0 && rate <= 1
  for (const group of groups) {
    if (typeof group.group_id !== 'string' || byId.has(group.group_id) ||
        !Number.isSafeInteger(group.wafer_count) || group.wafer_count < 0 || !isRate(group.fail_rate)) return null
    byId.set(group.group_id, { id: group.group_id, waferCount: group.wafer_count, failRate: group.fail_rate, cells: [] })
  }
  let minCol = Infinity, minRow = Infinity, maxCol = -Infinity, maxRow = -Infinity
  let low = 1, high = 0
  for (const cell of cells) {
    const group = byId.get(cell.group_id)
    if (!group || !Number.isSafeInteger(cell.row) || !Number.isSafeInteger(cell.col) || !isRate(cell.fail_rate)) return null
    group.cells.push(cell)
    minCol = Math.min(minCol, cell.col)
    maxCol = Math.max(maxCol, cell.col)
    minRow = Math.min(minRow, cell.row)
    maxRow = Math.max(maxRow, cell.row)
    low = Math.min(low, cell.fail_rate)
    high = Math.max(high, cell.fail_rate)
  }
  const result = [...byId.values()]
  if (!result.length || result.some(group => !group.cells.length)) return null
  return { groups: result, groupBy, minCol, minRow, columns: maxCol - minCol + 1, rows: maxRow - minRow + 1, low, high }
}

// Restored tool messages only retain status. Resolve the exact answer link against the owned session.
export async function readWaferMaps(src: string, signal: AbortSignal): Promise<WaferMaps | null> {
  const url = new URL(src, window.location.href)
  if (url.origin !== window.location.origin || !url.pathname.startsWith('/harness/artifacts/')) return null
  const sessionId = url.searchParams.get('session_id')
  const artifactId = decodeURIComponent(url.pathname.slice('/harness/artifacts/'.length))
  if (!sessionId || !artifactId) return null
  const query = new URLSearchParams({ limit: '8' })
  const seen = new Set<string>()
  for (;;) {
    const page = await fetchJson('/harness/sessions/' + encodeURIComponent(sessionId), query, signal)
    if (page.session_id !== sessionId || !Array.isArray(page.runs)) return null
    for (const run of page.runs) {
      const observation = (run.observations as Observation[] | null)?.find(item =>
        item.artifact_refs?.some(ref => ref.artifact_id === artifactId && ref.mime === 'text/html'))
      if (!observation) continue
      const complete = new Set(observation.tables?.filter(table => table.complete).map(table => table.table_id))
      if (observation.tool_name !== 'load_wafer_maps' || !complete.has('wafer_map_groups') || !complete.has('wafer_map_cells')) return null
      const [groups, cells] = await Promise.all([
        readTable<GroupRow>(sessionId, observation.result_id, 'wafer_map_groups', signal),
        readTable<CellRow>(sessionId, observation.result_id, 'wafer_map_cells', signal),
      ])
      return groupMaps(groups, cells, observation.scope?.map_aggregate?.group_by)
    }
    const cursor = page.next_cursor
    if (typeof cursor !== 'string' || !cursor || seen.has(cursor)) return null
    seen.add(cursor)
    query.set('cursor', cursor)
  }
}

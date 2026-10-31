import { unified } from 'unified'
import remarkParse from 'remark-parse'
import remarkGfm from 'remark-gfm'
import type { Nodes } from 'mdast'
import { tableText } from '../core'
export interface ComparableTable { columns: string[]; rows: Record<string, unknown>[]; complete: boolean; total_rows: number }
const normalize = (value: string) => value.normalize('NFC').replace(/\s+/g, ' ').trim()
function plain(node: Nodes): string {
  if ('value' in node) return node.value
  return 'children' in node ? node.children.map(plain).join('') : ''
}
function signature(columns: string[], rows: string[][]): string {
  const order = columns.map((name, index) => ({ name: normalize(name), index })).sort((a,b) => a.name.localeCompare(b.name))
  return JSON.stringify([order.map(x => x.name), rows.map(row => order.map(x => normalize(row[x.index] ?? '')))])
}
/** Only complete tables are evidence for hiding a markdown table. Fenced code is untouched. */
export function omitDuplicateTables(markdown: string, tables: ComparableTable[]): string {
  const known = new Set(tables.filter(t => t.complete && t.rows.length === t.total_rows).map(t => signature(t.columns, t.rows.map(row => t.columns.map(c => tableText(row[c]))))))
  const root = unified().use(remarkParse).use(remarkGfm).parse(markdown)
  const ranges: [number, number][] = []
  for (const node of root.children) {
    if (node.type !== 'table') continue
    const [head, ...body] = node.children
    if (head && known.has(signature(head.children.map(plain), body.map(row => row.children.map(plain))))) {
      const start = node.position?.start.offset, end = node.position?.end.offset
      if (start !== undefined && end !== undefined) ranges.push([start, end])
    }
  }
  for (const [start,end] of ranges.reverse()) markdown = markdown.slice(0,start) + markdown.slice(end)
  return markdown.trim()
}

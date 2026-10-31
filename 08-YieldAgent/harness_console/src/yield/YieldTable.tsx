import type { TablePage } from '../types'
import { tableText } from '../core'
const MAX_CELL_CHARS = 200
const MAX_TITLE_CHARS = 1000
function cellDisplay(text: string) {
  if (text.length <= MAX_CELL_CHARS) return { text, title: undefined }
  return {
    text: `${text.slice(0,MAX_CELL_CHARS)}… (전체 ${text.length}자)`,
    title: text.slice(0,MAX_TITLE_CHARS) + (text.length > MAX_TITLE_CHARS ? '…' : ''),
  }
}
export function YieldTable({ table }: { table: TablePage }) {
  return <section><h3>{table.title}</h3><p>{table.rows.length} / {table.total_rows}행{!table.complete && ' · 불완전한 결과'}</p>{table.missing_reason && <p>{table.missing_reason}</p>}
    <div role="region" aria-label={table.title} tabIndex={0} style={{overflowX:'auto',maxHeight:480}}><table><thead><tr>{table.columns.map(c => <th key={c} style={{position:'sticky',top:0,background:'var(--background, white)'}}>{c}{table.units[c] && ` (${table.units[c]})`}</th>)}</tr></thead>
    <tbody>{table.rows.map((row,i) => <tr key={i}>{table.columns.map(c => {
      const cell=cellDisplay(tableText(row[c]))
      return <td key={c} title={cell.title}>{cell.text}</td>
    })}</tr>)}</tbody></table></div></section>
}

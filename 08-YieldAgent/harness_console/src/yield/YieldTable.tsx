import { useState } from 'react'
import type { Data, TablePage } from '../types'
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
function csvCell(value:unknown) {
  const text=tableText(value)
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"','""')}"` : text
}
function downloadCsv(table:TablePage,rows:Data[]) {
  const csv=[table.columns.map(csvCell).join(','),...rows.map(row => table.columns.map(column => csvCell(row[column])).join(','))].join('\r\n')
  const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}))
  const link=document.createElement('a')
  link.href=url
  link.download=`${table.title || table.table_id}.csv`
  link.click()
  URL.revokeObjectURL(url)
}
export function YieldTable({ table,loadAllRows }: { table: TablePage;loadAllRows?:()=>Promise<Data[]> }) {
  const [showAll,setShowAll]=useState(false),[fullRows,setFullRows]=useState<Data[]|null>(null),[loading,setLoading]=useState(false),[error,setError]=useState('')
  const availableRows=fullRows ?? table.rows
  const visibleRows=showAll ? availableRows : availableRows.slice(0,5)
  const hiddenRows=Math.max(0,table.total_rows-5)
  async function allRows() {
    if(fullRows) return fullRows
    if(table.rows.length>=table.total_rows || !loadAllRows) return table.rows
    setLoading(true);setError('')
    try {const rows=await loadAllRows();setFullRows(rows);return rows}
    catch (cause) {setError(cause instanceof Error ? cause.message : '전체 결과 조회 실패');throw cause}
    finally {setLoading(false)}
  }
  async function toggleAll() {
    if(showAll) {setShowAll(false);return}
    try {await allRows();setShowAll(true)} catch { /* error is displayed below */ }
  }
  async function download() {
    try {downloadCsv(table,await allRows())} catch { /* error is displayed below */ }
  }
  return <section><h3>{table.title}</h3><p>{table.rows.length} / {table.total_rows}행{!table.complete && ' · 불완전한 결과'}</p>{table.missing_reason && <p>{table.missing_reason}</p>}
    <p><button type="button" disabled={loading} onClick={() => void toggleAll()}>{showAll ? '미리 보기' : '전체 보기'}</button> <button type="button" disabled={loading} onClick={() => void download()}>CSV 다운로드</button></p>
    {!showAll && hiddenRows>0 && <p>+{hiddenRows}행 더 있음</p>}{error && <p role="alert">{error}</p>}
    <div role="region" aria-label={table.title} tabIndex={0} style={{overflowX:'auto',maxHeight:480}}><table><thead><tr>{table.columns.map(c => <th key={c} style={{position:'sticky',top:0,background:'var(--background, white)'}}>{c}{table.units[c] && ` (${table.units[c]})`}</th>)}</tr></thead>
    <tbody>{visibleRows.map((row,i) => <tr key={i}>{table.columns.map(c => {
      const cell=cellDisplay(tableText(row[c]))
      return <td key={c} title={cell.title}>{cell.text}</td>
    })}</tr>)}</tbody></table></div></section>
}

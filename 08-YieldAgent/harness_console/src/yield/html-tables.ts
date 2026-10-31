import { fromHtmlIsomorphic } from 'hast-util-from-html-isomorphic'
import { toText } from 'hast-util-to-text'
import type { Element, Root } from 'hast'
import type { ComparableTable } from './table-dedupe'
function descendants(node:Root|Element, tag:string):Element[] {
 return node.children.flatMap(child => child.type!=='element' ? [] : child.tagName===tag ? [child] : descendants(child,tag))
}
/** Parse structural HTML only. Merged cells and duplicate headers cannot prove equality. */
export function htmlTables(html:string):ComparableTable[] {
 return descendants(fromHtmlIsomorphic(html,{fragment:true}),'table').flatMap(table => {
  const rows=descendants(table,'tr').map(row => row.children.filter((child):child is Element => child.type==='element' && (child.tagName==='td'||child.tagName==='th')))
  const [head,...body]=rows
  if(!head?.length || head.some(c => c.tagName!=='th') || rows.some(row => row.length!==head.length || row.some(c => Number(c.properties.colSpan ?? 1)!==1 || Number(c.properties.rowSpan ?? 1)!==1)))return []
  const columns=head.map(c => toText(c).trim())
  if(new Set(columns).size!==columns.length)return []
  return [{columns,rows:body.map(row => Object.fromEntries(row.map((cell,i) => [columns[i],toText(cell)]))),complete:true,total_rows:body.length}]
 })
}

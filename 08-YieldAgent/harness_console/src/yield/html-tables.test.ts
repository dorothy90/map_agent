import {expect,it} from 'vitest'
import {htmlTables} from './html-tables'
import {omitDuplicateTables} from './table-dedupe'
it('recognizes a complete artifact table without executing HTML',() => {
 const tables=htmlTables('<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>')
 expect(omitDuplicateTables('|A|B|\n|---|---|\n|1|2|',tables)).toBe('')
})
it('does not infer ambiguous merged-cell headers',() => {
 expect(htmlTables('<table><tr><th colspan="2">A</th></tr><tr><td>1</td><td>2</td></tr></table>')).toEqual([])
})

import { expect, it } from 'vitest'
import { omitDuplicateTables } from './table-dedupe'
const tables = [{ columns: ['A', 'B'], rows: [{ A: '1', B: '2' }], complete: true, total_rows: 1 }]
const same = '| A | B |\n|---|---|\n|1|2|'
it('removes identical cells only', () => expect(omitDuplicateTables(same, tables)).toBe(''))
it('keeps changed cells', () => expect(omitDuplicateTables(same.replace('|1|2|','|1|3|'), tables)).toContain('|1|3|'))
it('compares columns by header independent of order', () => expect(omitDuplicateTables('|B|A|\n|---|---|\n|2|1|', tables)).toBe(''))
it('removes only one of two tables', () => expect(omitDuplicateTables(same+'\n\n'+same.replace('|1|2|','|3|4|'), tables)).toContain('|3|4|'))
it('does not deduplicate incomplete pages', () => expect(omitDuplicateTables(same, [{...tables[0], complete:false}])).toBe(same))
it('does not alter fenced code', () => expect(omitDuplicateTables('```\n'+same+'\n```',tables)).toContain(same))

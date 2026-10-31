import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { YieldVerify } from './YieldVerify'
import { YieldTable } from './YieldTable'
import { YieldArtifact } from './YieldArtifact'
import { tableText } from '../core'
afterEach(cleanup)
it.each(['pending','pass','fail','none'] as const)('verification %s has no candidate body', state => {
 const { container } = render(<YieldVerify state={state} revision={2} summary="검증 요약" />)
 expect(container.textContent).toContain('검증')
 expect(container.textContent).toContain('2')
})
it('wide table has labelled scroll region and sticky headers', () => {
 render(<YieldTable table={{table_id:'t', title:'결과', columns:['A','B'], rows:[{A:1,B:2}], total_rows:1, complete:true, offset:0, units:{}}} />)
 expect(screen.getByRole('region', {name:'결과'}).style.overflowX).toBe('auto')
 expect(screen.getAllByRole('columnheader')[0].style.position).toBe('sticky')
})
it('limits a 36000-character cell and its hover preview', () => {
 const value='x'.repeat(36000)
 const { container } = render(<YieldTable table={{table_id:'t', title:'결과', columns:['map_val_json'], rows:[{map_val_json:value}], total_rows:1, complete:true, offset:0, units:{}}} />)
 const cell=screen.getByRole('cell')
 expect(cell.textContent).toBe(`${value.slice(0,200)}… (전체 36000자)`)
 expect(container.innerHTML).not.toContain(value)
 expect(cell.title).toBe(`${value.slice(0,1000)}…`)
 expect(cell.title.length).toBeLessThanOrEqual(1001)
})
it.each([200,201,1000])('applies cell display and title limits at %i characters', length => {
 const value='가'.repeat(length)
 render(<YieldTable table={{table_id:'t', title:'결과', columns:['A'], rows:[{A:value}], total_rows:1, complete:true, offset:0, units:{}}} />)
 const cell=screen.getByRole('cell')
 expect(cell.textContent).toBe(length<=200 ? value : `${value.slice(0,200)}… (전체 ${length}자)`)
 expect(cell.getAttribute('title')).toBe(length<=200 ? null : value)
})
it('preserves tableText formatting for number, null and object cells', () => {
 const values=[42,null,{value:1}]
 render(<YieldTable table={{table_id:'t', title:'결과', columns:['A'], rows:values.map(A => ({A})), total_rows:3, complete:true, offset:0, units:{}}} />)
 expect(screen.getAllByRole('cell').map(cell => cell.textContent)).toEqual(values.map(tableText))
 expect(screen.getAllByRole('cell').every(cell => !cell.hasAttribute('title'))).toBe(true)
})
it('artifact links use server ids and never local paths', () => {
 render(<YieldArtifact sessionId="s" resultId="r" artifact={{artifact_id:'/tmp/a',title:'결과'}} />)
 expect(screen.getByRole('link').getAttribute('href')).toBe('/harness/artifacts/%2Ftmp%2Fa?session_id=s')
 expect(screen.queryByRole('img')).toBeNull()
})

it('fetches an artifact only on preview, uses response MIME and reports table evidence', async () => {
 const fetcher=vi.fn<typeof fetch>().mockResolvedValue(new Response('<table><tr><th>A</th></tr><tr><td>1</td></tr></table>',{headers:{'Content-Type':'text/html'}}))
 const onTables=vi.fn()
 render(<YieldArtifact sessionId="s" resultId="r" artifact={{artifact_id:'id'}} fetcher={fetcher} onTables={onTables}/>)
 expect(fetcher).not.toHaveBeenCalled()
 fireEvent.click(screen.getByRole('button',{name:'미리보기'}))
 await waitFor(() => expect(onTables).toHaveBeenCalledOnce())
 expect(screen.getByTitle('결과 미리보기').getAttribute('sandbox')).toBe('')
 expect(screen.getByTitle('결과 미리보기').getAttribute('srcdoc')).toContain('<table>')
})

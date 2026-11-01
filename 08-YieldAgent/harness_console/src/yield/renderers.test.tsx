import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { YieldVerify } from './YieldVerify'
import { YieldTable } from './YieldTable'
import { YieldArtifact } from './YieldArtifact'
import { YieldResults } from './YieldResults'
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
it('previews five rows and toggles the complete table', async () => {
 const rows=Array.from({length:8},(_,i) => ({A:i+1}))
 render(<YieldTable table={{table_id:'t', title:'결과', columns:['A'], rows, total_rows:8, complete:true, offset:0, units:{}}} />)
 expect(screen.getAllByRole('cell').map(cell => cell.textContent)).toEqual(['1','2','3','4','5'])
 expect(screen.getByText('+3행 더 있음')).toBeTruthy()
 fireEvent.click(screen.getByRole('button',{name:'전체 보기'}))
 await waitFor(() => expect(screen.getAllByRole('cell')).toHaveLength(8))
 fireEvent.click(screen.getByRole('button',{name:'미리 보기'}))
 expect(screen.getAllByRole('cell')).toHaveLength(5)
})
it('downloads all original rows as escaped CSV', async () => {
 const long='x'.repeat(250)
 const loadAllRows=vi.fn().mockResolvedValue([{A:'첫째',B:'plain'},{A:'둘째',B:'comma, quote"\nline'},{A:long,B:null}])
 let csv:Blob|undefined
 const createObjectURL=vi.spyOn(URL,'createObjectURL').mockImplementation(blob => {csv=blob as Blob;return 'blob:test'})
 const revokeObjectURL=vi.spyOn(URL,'revokeObjectURL').mockImplementation(() => {})
 const click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(() => {})
 render(<YieldTable table={{table_id:'t', title:'결과', columns:['A','B'], rows:[{A:'첫째',B:'plain'}], total_rows:3, complete:true, offset:0, units:{}}} loadAllRows={loadAllRows}/>)
 fireEvent.click(screen.getByRole('button',{name:'CSV 다운로드'}))
 await waitFor(() => expect(click).toHaveBeenCalledOnce())
 expect(loadAllRows).toHaveBeenCalledOnce()
 expect(await csv!.text()).toBe(`A,B\r\n첫째,plain\r\n둘째,"comma, quote""\nline"\r\n${long},`)
 expect(createObjectURL).toHaveBeenCalledOnce()
 expect(revokeObjectURL).toHaveBeenCalledWith('blob:test')
})
it('groups tool tables into nested disclosures collapsed by default', () => {
 const run={run_id:'run',session_id:'session',query:'q',status:'completed',goal_revision:1,observations:[
  {invocation_id:'i1',tool_name:'query_db',result_id:'result-1',status:'success',tables:[
   {table_id:'a',title:'표 A',columns:['A'],total_rows:1,complete:true},
   {table_id:'b',title:'표 B',columns:['B'],total_rows:1,complete:true},
  ]},
  {invocation_id:'i2',tool_name:'run_python',result_id:'result-2',status:'success',tables:[
   {table_id:'c',title:'표 C',columns:['C'],total_rows:1,complete:true},
  ]},
 ]}
 const tables=[
  {runId:'run',resultId:'result-1',table:{table_id:'a',title:'표 A',columns:['A'],rows:[{A:1}],total_rows:1,complete:true,offset:0,units:{}}},
  {runId:'run',resultId:'result-1',table:{table_id:'b',title:'표 B',columns:['B'],rows:[{B:2}],total_rows:1,complete:true,offset:0,units:{}}},
  {runId:'run',resultId:'result-2',table:{table_id:'c',title:'표 C',columns:['C'],rows:[{C:3}],total_rows:1,complete:true,offset:0,units:{}}},
 ]
 render(<YieldResults runs={[run]} tables={tables} fetcher={vi.fn<typeof fetch>()}/>)
 const evidence=screen.getByRole('button',{name:'조회·계산 근거 (2개 도구)'})
 expect(evidence.getAttribute('aria-expanded')).toBe('false')
 expect(screen.queryByRole('button',{name:/query_db/})).toBeNull()
 fireEvent.click(evidence)
 const query=screen.getByRole('button',{name:'query_db · 2개 표'})
 expect(query.getAttribute('aria-expanded')).toBe('false')
 expect(screen.queryByRole('region',{name:'표 A'})).toBeNull()
 fireEvent.click(query)
 expect(screen.getByRole('region',{name:'표 A'})).toBeTruthy()
 expect(screen.getByRole('region',{name:'표 B'})).toBeTruthy()
 expect(screen.queryByRole('region',{name:'표 C'})).toBeNull()
})
it('resets disclosures when the session-keyed panel changes', () => {
 const result={runId:'run',resultId:'result',table:{table_id:'t',title:'표',columns:['A'],rows:[{A:1}],total_rows:1,complete:true,offset:0,units:{}}}
 const run={run_id:'run',session_id:'first',query:'q',status:'completed',goal_revision:1,observations:[{invocation_id:'i',tool_name:'query_db',result_id:'result',status:'success'}]}
 const {rerender}=render(<YieldResults key="first" runs={[run]} tables={[result]} fetcher={vi.fn<typeof fetch>()}/>)
 fireEvent.click(screen.getByRole('button',{name:'조회·계산 근거 (1개 도구)'}))
 expect(screen.getByRole('button',{name:'query_db · 1개 표'})).toBeTruthy()
 rerender(<YieldResults key="second" runs={[{...run,session_id:'second'}]} tables={[result]} fetcher={vi.fn<typeof fetch>()}/>)
 expect(screen.getByRole('button',{name:'조회·계산 근거 (1개 도구)'}).getAttribute('aria-expanded')).toBe('false')
 expect(screen.queryByRole('button',{name:'query_db · 1개 표'})).toBeNull()
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

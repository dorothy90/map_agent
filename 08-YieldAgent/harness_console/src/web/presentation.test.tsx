import { cleanup, isInaccessible, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { Thread } from '@/components/assistant-ui/thread'
import { assistantMessage, ThreadRuntime, stubThreadEnvironment, stubThreadViewportSize } from '@/components/assistant-ui/test-utils'
import { setShowToolActivityFromConfig } from '@/store/tool-activity'
import { WebPresentationProvider } from './presentation'
import { toolArgumentSummary } from './tool-summary'
stubThreadEnvironment()
stubThreadViewportSize()
afterEach(() => {cleanup();vi.unstubAllGlobals()})
it('shows compact tools and moves the matching report title and subtitle to its iframe', async () => {
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('',{headers:{'content-type':'text/html'}})))
 setShowToolActivityFromConfig(true)
 const base=assistantMessage()
 if(base.role!=='assistant')throw new Error('Expected assistant fixture')
 const message:typeof base={...base,content:[{type:'tool-call',toolCallId:'table',toolName:'render_yield_table',args:{title:'실제 리포트 제목',lot_cd:'4SS',period_label:'46–49주'},argsText:'{}',result:{ok:true,duration_s:1.4,artifact_refs:[{artifact_id:'table',href:'/harness/artifacts/table?session_id=test'}]}},{type:'text',text:'주차별 두 그룹을 비교했습니다.\n\n[실제 리포트 제목](/harness/artifacts/table?session_id=test)'}]}
 const later={...message,id:'later',content:[{type:'tool-call' as const,toolCallId:'later-table',toolName:'render_yield_table',args:{title:'실제 리포트 제목',lot_cd:'3AS',period_label:'다른 기간'},argsText:'{}',result:{artifact_refs:[{artifact_id:'other'}]}}]}
 const {container}=render(<main className="hermes-web"><WebPresentationProvider><ThreadRuntime messages={[message,later]}><Thread/></ThreadRuntime></WebPresentationProvider></main>)
 expect(container.querySelector('[data-slot="tool-block"] h3')).toBeNull()
 expect(container.querySelector('.web-tool-row')?.textContent).toContain('render_yield_table')
 expect(container.querySelector('.web-tool-row')?.textContent).toContain('1.4')
 expect(container.querySelector('.web-tool-row')?.textContent).toContain('완료')
 expect(screen.getAllByRole('button',{name:'답변 복사'}).length).toBeGreaterThan(0)
 expect([...container.querySelectorAll<HTMLElement>('.web-execution-trace .web-answer-author')]
   .filter(author => !isInaccessible(author))).toHaveLength(0)
 const heading=await screen.findByRole('heading',{name:'실제 리포트 제목'})
 expect(heading.closest('[data-slot="tool-block"]')).toBeNull()
 const section=heading.closest('.web-report-section')!
 expect(section.querySelector('iframe')?.getAttribute('src')).toBe('/harness/artifacts/table?session_id=test')
 expect(section.textContent).toContain('4SS · 46–49주')
 expect(section.querySelector('a')?.textContent).toBe('원본 열기')
})
it('summarizes only supplied structured arguments', () => {
 expect(toolArgumentSummary({table:'DF_ALL_PARA_PRB',filters:[{column:'LOT_CD',op:'=',value:'4SS'}]})).toBe('DF_ALL_PARA_PRB · LOT_CD=4SS')
 expect(toolArgumentSummary({})).toBe('')
})

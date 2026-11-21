import { expect, it, vi } from 'vitest'
import { displaySessionLabel, readFirstQuestion, sessionDayGroup, matchesSessionLabel, sessionLabelKey } from './session-labels'
import type { SessionInfo } from '@/types/hermes'
it('reads oldest pages until the first user, preserving original text and owner', async () => {
 const read=vi.fn().mockResolvedValueOnce({messages:[{role:'system',content:'system'}],pagination:{limit:1}}).mockResolvedValueOnce({messages:[{role:'user',content:'Original question\nsecond line'}]})
 const session={id:'s',profile:'p',connection_id:'c'} as SessionInfo
 expect(await readFirstQuestion(session,read)).toBe('Original question\nsecond line')
 expect(read).toHaveBeenLastCalledWith('s',{profile:'p',connectionId:'c'},{order:'oldest',limit:40,offset:1,includeCompacted:true},{passive:true})
 expect(sessionLabelKey(session)).not.toBe(sessionLabelKey({...session,connection_id:'other'}))
})
it('prefers trimmed server titles, then trimmed first questions without inventing a title', async () => {
 expect(displaySessionLabel({title:'  서버 제목  '},'첫 질문')).toBe('서버 제목')
 expect(displaySessionLabel({title:'  '},'  첫 질문  ')).toBe('첫 질문')
 expect(displaySessionLabel({},'  첫 질문  ')).toBe('첫 질문')
 expect(displaySessionLabel({},undefined)).toBe('대화 불러오는 중…')
 expect(matchesSessionLabel(displaySessionLabel({title:'서버 제목'},'첫 질문'),'서버')).toBe(true)
 expect(matchesSessionLabel(displaySessionLabel({title:'서버 제목'},'첫 질문'),'질문')).toBe(false)
 expect(await readFirstQuestion({id:'s',title:'Generated title'} as SessionInfo,vi.fn().mockResolvedValue({messages:[]}))).toBeNull()
 await expect(readFirstQuestion({id:'s'} as SessionInfo,vi.fn().mockRejectedValue(new Error('offline')))).rejects.toThrow('offline')
})
it('filters only loaded labels and groups real epoch timestamps at local midnight', () => {
 expect(matchesSessionLabel(undefined,'query')).toBe(false)
 expect(matchesSessionLabel('Original QUERY','query')).toBe(true)
 expect(matchesSessionLabel(undefined,'')).toBe(true)
 const midnight=new Date(2026,10,20).getTime()
 const now=midnight+60000
 expect(sessionDayGroup({last_active:midnight/1000,started_at:1},now)).toBe('오늘')
 expect(sessionDayGroup({started_at:midnight/1000-1},now)).toBe('이전 대화')
 expect(sessionDayGroup({},now)).toBe('이전 대화')
})

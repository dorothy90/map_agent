import { expect, it, vi } from 'vitest'
import { readFirstQuestion, relativeSessionTime, matchesSessionLabel, sessionLabelKey } from './session-labels'
import type { SessionInfo } from '@/types/hermes'
it('reads oldest pages until the first user, preserving original text and owner', async () => {
 const read=vi.fn().mockResolvedValueOnce({messages:[{role:'system',content:'system'}],pagination:{limit:1}}).mockResolvedValueOnce({messages:[{role:'user',content:'Original question\nsecond line'}]})
 const session={id:'s',profile:'p',connection_id:'c'} as SessionInfo
 expect(await readFirstQuestion(session,read)).toBe('Original question\nsecond line')
 expect(read).toHaveBeenLastCalledWith('s',{profile:'p',connectionId:'c'},{order:'oldest',limit:40,offset:1,includeCompacted:true},{passive:true})
 expect(sessionLabelKey(session)).not.toBe(sessionLabelKey({...session,connection_id:'other'}))
})
it('does not substitute server titles or invent missing questions', async () => {
 expect(await readFirstQuestion({id:'s',title:'Generated title'} as SessionInfo,vi.fn().mockResolvedValue({messages:[]}))).toBeNull()
 await expect(readFirstQuestion({id:'s'} as SessionInfo,vi.fn().mockRejectedValue(new Error('offline')))).rejects.toThrow('offline')
})
it('filters only loaded labels and formats real epoch timestamps', () => {
 expect(matchesSessionLabel(undefined,'query')).toBe(false)
 expect(matchesSessionLabel('Original QUERY','query')).toBe(true)
 expect(matchesSessionLabel(undefined,'')).toBe(true)
 expect(relativeSessionTime({last_active:67,started_at:1},100000)).toBe('33초 전')
 expect(relativeSessionTime({started_at:67},100000)).toBe('33초 전')
})

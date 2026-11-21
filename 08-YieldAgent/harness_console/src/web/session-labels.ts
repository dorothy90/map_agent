import type { SessionInfo } from '@/types/hermes'
import { getSessionMessages } from '@/api/sessions'
export const sessionLabelKey = (session: SessionInfo) => JSON.stringify([session.connection_id ?? '',session.profile ?? 'default',session.id])
export async function readFirstQuestion(session: SessionInfo, read = getSessionMessages): Promise<string | null> {
 let offset=0
 const owner=session.connection_id ? {connectionId:session.connection_id,profile:session.profile ?? 'default'} : session.profile
 while (true) {
  const page=await read(session.id,owner,{order:'oldest',limit:40,offset,includeCompacted:true},{passive:true})
  const first=page.messages.find(message => message.role==='user' && typeof message.content==='string' && message.content.trim())
  if(first && typeof first.content==='string') return first.content
  if(!page.pagination || page.messages.length===0 || page.messages.length<page.pagination.limit) return null
  offset+=page.messages.length
 }
}
export const matchesSessionLabel = (label: string | null | undefined, query: string) => !query.trim() || Boolean(label?.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
export function sessionDayGroup(session: {last_active?:number|null;started_at?:number|null}, now=Date.now()) {
 const timestamp=session.last_active ?? session.started_at
 return timestamp!=null && new Date(timestamp*1000).toDateString()===new Date(now).toDateString() ? '오늘' : '이전 대화'
}

export function displaySessionLabel(session: {title?:string|null}, question?:string|null) {
 return session.title?.trim() || question?.trim() || (question===undefined ? '대화 불러오는 중…' : '첫 질문 없음')
}

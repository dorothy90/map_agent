import { useCallback, useEffect, useMemo, useState } from 'react'
import type { SessionInfo } from '@/types/hermes'
import { readFirstQuestion, sessionLabelKey } from './session-labels'
/** Host-local cache, additionally keyed by connection/profile/session ownership. */
export function useSessionLabels(sessions: SessionInfo[], enabled: boolean, owner?: object) {
 const cache=useMemo(() => new Map<string,string|null>(),[owner])
 const [snapshot,setSnapshot]=useState({cache,labels:{} as Record<string,string|null>})
 const [attempt,setAttempt]=useState(0),[failed,setFailed]=useState(false)
 const publish=useCallback(() => setSnapshot({cache,labels:Object.fromEntries(cache)}),[cache])
 const remember=useCallback((session:SessionInfo,label:string) => {
  const key=sessionLabelKey(session)
  if(!cache.get(key) && label.trim()){cache.set(key,label);publish()}
 },[cache,publish])
 useEffect(() => {
  if(!enabled)return
  let disposed=false,index=0
  // Empty history is provisional: the optimistic session row can precede its first send.
  const pending=sessions.filter(session => !session.title?.trim() && !cache.get(sessionLabelKey(session)))
  setFailed(false)
  async function worker() {
   while(!disposed && index<pending.length) {
    const session=pending[index++],key=sessionLabelKey(session)
    try {
     const label=await readFirstQuestion(session)
     if(disposed)return
     if(!cache.get(key))cache.set(key,label)
     publish()
    } catch {if(!disposed)setFailed(true)}
   }
  }
  void Promise.all(Array.from({length:Math.min(3,pending.length)},worker))
  return () => {disposed=true}
 },[sessions,enabled,attempt,cache,publish])
 return {labels:snapshot.cache===cache ? snapshot.labels : {},failed,remember,retry:() => setAttempt(value => value+1)}
}

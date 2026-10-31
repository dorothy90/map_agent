import { useCallback, useEffect, useRef } from 'react'
import { listSessions } from '@/api/sessions'
import { setSessions, setSessionsLoading, setSessionsLoadError } from '@/store/session'
/** H use-session-list-actions:328-520, narrowed to a single profile without cron refresh. */
export function useWebSessionList() {
 const limit=useRef(40),generation=useRef(0)
 useEffect(() => () => {generation.current++},[])
 const refreshSessions=useCallback(async () => {
   const request=++generation.current
   setSessionsLoading(true);setSessionsLoadError(false)
   try {const result=await listSessions(limit.current,1,'exclude','recent');if(request===generation.current)setSessions(result.sessions)}
   catch {if(request===generation.current)setSessionsLoadError(true)}
   finally {if(request===generation.current)setSessionsLoading(false)}
 },[])
 const loadMoreSessions=useCallback(async () => {limit.current+=40;await refreshSessions()},[refreshSessions])
 return {refreshSessions,loadMoreSessions}
}

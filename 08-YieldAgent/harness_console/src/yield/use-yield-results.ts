import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { Run, TablePage } from '../types'
export function useYieldResults(sessionId:string|null, busy:boolean, fetcher:typeof fetch) {
  const previous = useRef({sessionId,busy})
  const query = useQuery({queryKey:['yield-results',sessionId],enabled:!!sessionId,refetchInterval:busy ? 3000 : false,retry:false,
    queryFn:async ({signal}) => {
      async function read<T>(url:string):Promise<T> { const response=await fetcher(url,{signal});if(!response.ok) throw new Error(`결과 조회 실패 (${response.status})`);return response.json() }
      const history=await read<{runs:Run[]}>(`/harness/sessions/${encodeURIComponent(sessionId!)}?limit=50`)
      const tables=await Promise.all(history.runs.flatMap(run => (run.observations ?? []).flatMap(obs => (obs.tables ?? []).map(async table => ({
        runId:run.run_id,resultId:obs.result_id,table:await read<TablePage>(`/harness/results/${encodeURIComponent(obs.result_id)}?${new URLSearchParams({session_id:sessionId!,table_id:table.table_id,offset:'0',limit:'100'})}`)
      })))))
      return {runs:history.runs,tables}
    }})
  const { refetch } = query
  useEffect(() => {
    const last = previous.current
    previous.current = {sessionId,busy}
    if (sessionId && last.sessionId === sessionId && last.busy && !busy) void refetch()
  }, [sessionId,busy,refetch])
  return query
}

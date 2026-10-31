import type { ComparableTable } from './table-dedupe'
import type { Run, TablePage } from '../types'
import { YieldVerify, type VerificationState } from './YieldVerify'
import { YieldTable } from './YieldTable'
import { YieldArtifact } from './YieldArtifact'
export function YieldResults({runs,tables,fetcher,onArtifactTables}:{runs:Run[];tables:{runId:string;resultId:string;table:TablePage}[];fetcher:typeof fetch;onArtifactTables?:(id:string,tables:ComparableTable[]) => void}) {
 return <aside aria-label="결과 및 검증" className="hermes-yield-results">{runs.map(run => {
   const reviews=(run.observations ?? []).filter(o => o.tool_name==='verify_answer' || o.tool_name==='completion_review')
   const review=reviews.at(-1), verdict=review?.scope?.verdict
   const state:VerificationState=verdict==='pass' || verdict==='fail' || verdict==='unknown' ? verdict : run.status==='running' || run.status==='pending' ? 'pending' : run.stop_reason?.startsWith('verification_') ? 'fail' : 'none'
   return <section key={run.run_id}><YieldVerify state={state} revision={run.goal_revision} summary={review?.summary}/>
    {tables.filter(t => t.runId===run.run_id).map(t => <YieldTable key={`${t.resultId}:${t.table.table_id}`} table={t.table}/>)}
    {(run.observations ?? []).flatMap(o => (o.artifact_refs ?? []).map(a => <YieldArtifact key={`${o.result_id}:${a.artifact_id}`} sessionId={run.session_id} resultId={o.result_id} artifact={a} fetcher={fetcher} onTables={onArtifactTables}/>))}</section>
 })}</aside>
}

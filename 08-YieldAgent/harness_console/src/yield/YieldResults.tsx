import { useState } from 'react'
import { DisclosureCaret } from '@/components/ui/disclosure-caret'
import type { ComparableTable } from './table-dedupe'
import type { Run, TablePage } from '../types'
import { YieldVerify, type VerificationState } from './YieldVerify'
import { YieldTable } from './YieldTable'
import { YieldArtifact } from './YieldArtifact'
import { loadAllTableRows } from './use-yield-results'

type LoadedTable={runId:string;resultId:string;table:TablePage}

function ToolResultCard({run,resultId,tables,fetcher}:{run:Run;resultId:string;tables:LoadedTable[];fetcher:typeof fetch}) {
 const [open,setOpen]=useState(false)
 const observation=(run.observations ?? []).find(item => item.result_id===resultId)
 const title=`${observation?.tool_name ?? resultId} · ${tables.length}개 표`
 return <section className="hermes-yield-tool-card"><button className="hermes-yield-disclosure" type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}><DisclosureCaret open={open}/><span>{title}</span></button>
  {open && tables.map(item => <YieldTable key={item.table.table_id} table={item.table} loadAllRows={() => loadAllTableRows(fetcher,run.session_id,resultId,item.table)}/>)}</section>
}

function EvidenceDisclosure({runs,tables,fetcher}:{runs:Run[];tables:LoadedTable[];fetcher:typeof fetch}) {
 const [open,setOpen]=useState(false)
 const groups=Array.from(tables.reduce((items,item) => {
   const key=`${item.runId}:${item.resultId}`
   const group=items.get(key)
   if(group) group.tables.push(item)
   else items.set(key,{run:runs.find(run => run.run_id===item.runId),resultId:item.resultId,tables:[item]})
   return items
 },new Map<string,{run:Run|undefined;resultId:string;tables:LoadedTable[]}>()).values()).filter((group):group is {run:Run;resultId:string;tables:LoadedTable[]} => !!group.run)
 if(!groups.length) return null
 const title=`조회·계산 근거 (${groups.length}개 도구)`
 return <section className="hermes-yield-evidence"><button className="hermes-yield-disclosure" type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}><DisclosureCaret open={open}/><span>{title}</span></button>
  {open && groups.map(group => <ToolResultCard key={`${group.run.run_id}:${group.resultId}`} {...group} fetcher={fetcher}/>)}</section>
}
export function YieldResults({runs,tables,fetcher,onArtifactTables}:{runs:Run[];tables:{runId:string;resultId:string;table:TablePage}[];fetcher:typeof fetch;onArtifactTables?:(id:string,tables:ComparableTable[]) => void}) {
 return <aside aria-label="결과 및 검증" className="hermes-yield-results">{runs.map(run => {
   const reviews=(run.observations ?? []).filter(o => o.tool_name==='verify_answer' || o.tool_name==='completion_review')
   const review=reviews.at(-1), verdict=review?.scope?.verdict
   const state:VerificationState=verdict==='pass' || verdict==='fail' || verdict==='unknown' ? verdict : run.status==='running' || run.status==='pending' ? 'pending' : run.stop_reason?.startsWith('verification_') ? 'fail' : 'none'
   return <section key={run.run_id}><YieldVerify state={state} revision={run.goal_revision} summary={review?.summary}/>
    {(run.observations ?? []).flatMap(o => (o.artifact_refs ?? []).map(a => <YieldArtifact key={`${o.result_id}:${a.artifact_id}`} sessionId={run.session_id} resultId={o.result_id} artifact={a} fetcher={fetcher} onTables={onArtifactTables}/>))}</section>
 })}<EvidenceDisclosure runs={runs} tables={tables} fetcher={fetcher}/></aside>
}

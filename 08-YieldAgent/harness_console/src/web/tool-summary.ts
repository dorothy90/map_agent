/** Presentation of declared tool arguments only; never interpret the user's prose. */
export function toolArgumentSummary(args: Record<string,unknown>) {
 const values=[args.table,args.lot_cd,args.period_label]
 if(Array.isArray(args.filters)) {
  for(const filter of args.filters.slice(0,2)) {
   if(filter && typeof filter==='object' && typeof filter.column==='string' && typeof filter.op==='string' && ['string','number'].includes(typeof filter.value)) values.push(`${filter.column}${filter.op}${filter.value}`)
  }
 }
 return values.filter(value => typeof value==='string' || typeof value==='number').join(' · ')
}

const stepLabels: Record<string, string> = {
  query_db: '데이터 조회', read_result: '조회 결과 확인', render_yield_table: '수율표 생성',
  load_wafer_maps: '웨이퍼 맵 조회', run_python: '데이터 계산', list_tables: '테이블 목록 확인',
}
export function toolStepLabel(name: string) {
  return stepLabels[name] ?? '도구 실행'
}

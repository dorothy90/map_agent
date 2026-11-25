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
  cronjob_manage: '예약 관리',
  kanban_create: '불량 아이템 등록', kanban_comment: '불량 아이템 갱신',
  kanban_list: '불량 아이템 목록 조회', kanban_show: '불량 아이템 확인', kanban_complete: '불량 조사 완료',
  kanban_attach: '불량 아이템에 파일 첨부', kanban_attachments: '불량 아이템 첨부파일 조회',
  query_db: '데이터 조회', read_result: '조회 결과 확인', render_yield_table: '수율표 생성',
  load_wafer_maps: '웨이퍼 맵 조회', execute_code: '데이터 계산', run_python: '데이터 계산', list_tables: '테이블 목록 확인',
}
export function toolStepLabel(name: string) {
  const mcp = parseMcpToolName(name)
  return mcp ? `MCP · ${mcp.server} / ${mcp.tool}` : stepLabels[name] ?? '도구 실행'
}

export function parseMcpToolName(name: string) {
  if (!name.startsWith('mcp__')) return null
  const separator = name.indexOf('__', 5)
  if (separator <= 5 || separator + 2 >= name.length) return null
  return { server: name.slice(5, separator), tool: name.slice(separator + 2) }
}

export function resolveToolDisplay(name: string, args: Record<string, unknown>) {
  if (name === 'tool_call') {
    const call = Array.isArray(args.calls) ? (args.calls.length === 1 ? args.calls[0] : null) : args
    if (call && typeof call === 'object' && typeof call.name === 'string' && parseMcpToolName(call.name)) {
      let innerArgs = call.arguments
      if (typeof innerArgs === 'string') {
        try { innerArgs = JSON.parse(innerArgs) } catch { innerArgs = {} }
      }
      return { name: call.name as string, args: innerArgs && typeof innerArgs === 'object' && !Array.isArray(innerArgs) ? innerArgs as Record<string, unknown> : {} }
    }
  }
  return { name, args }
}

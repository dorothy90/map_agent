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

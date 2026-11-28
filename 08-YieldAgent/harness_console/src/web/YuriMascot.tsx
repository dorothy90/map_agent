export function YuriMascot({state}:{state:'idle'|'thinking'|'result'|'success'|'error'}) {
 const image = `/y-agent-${state === 'result' ? 'idle' : state}.png`
 return <span className="yuri-mascot" data-mascot-state={state} aria-hidden="true">
  <img className="yuri-mascot-image" src={image} alt="" />
  {state==='result' && <img className="yuri-mascot-star" src="/y-agent-result.png" alt="" />}
  {state==='thinking' && <>
   <img className="yuri-thinking-cover yuri-thinking-cover-second" src={image} alt="" />
   <img className="yuri-thinking-cover yuri-thinking-cover-third" src={image} alt="" />
  </>}
  {state==='idle' && <span className="yuri-eyes">
   <img className="yuri-eye-skin" src={image} alt="" />
   <img className="yuri-eye-pixels" src={image} alt="" />
  </span>}
 </span>
}

export const replyPreparingLabel = '생각을 정리하는 중'

export function ReplyProgress({ label, meta, details }: {
 label: string
 meta?: string
 details?: { id: string; open: boolean; toggle: () => void }
}) {
 const body = <span className="web-reply-progress-copy">
  <span className="web-reply-progress-label">{label}…</span>
  {meta && <span className="web-reply-progress-meta">
   <span>{meta}</span>
   {details && <span className="web-reply-progress-chevron" aria-hidden="true" />}
  </span>}
 </span>
 return <div className="web-reply-progress" data-slot="aui_response-loading" role="status" aria-label={label}>
  <img className="web-reply-progress-mascot" src="/y-agent-reply-progress.png" alt="" width={64} height={64}/>
  {details ? <button type="button" className="web-trace-progress-toggle"
   aria-label="조회·계산 기록" title="조회·계산 기록" aria-controls={details.id}
   aria-expanded={details.open} onClick={details.toggle}>
   {body}
  </button> : body}
 </div>
}

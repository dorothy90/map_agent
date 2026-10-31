import { useEffect, useState } from 'react'
import type { ArtifactRef } from '../types'
import { htmlTables } from './html-tables'
import type { ComparableTable } from './table-dedupe'
import { artifactKind } from '../artifactUtils'
/** The server authorizes the session/artifact pair; never trust a filesystem path. */
export function YieldArtifact({ sessionId, resultId, artifact, fetcher = fetch, onTables }: { sessionId:string; resultId:string; artifact:ArtifactRef; fetcher?:typeof fetch; onTables?:(id:string,tables:ComparableTable[]) => void }) {
  const url = `/harness/artifacts/${encodeURIComponent(artifact.artifact_id)}?${new URLSearchParams({session_id:sessionId})}`
  const [previewKind,setPreviewKind]=useState<ReturnType<typeof artifactKind>>('download'),[imageUrl,setImageUrl]=useState('')
  const [open,setOpen] = useState(false), [body,setBody] = useState(''), [error,setError] = useState('')
  useEffect(() => {
    setBody('');setError('');setImageUrl('')
    if (!open) return
    const abort = new AbortController()
    let objectUrl:string|undefined
    void fetcher(url,{signal:abort.signal}).then(async response => {
      if (!response.ok) throw new Error(`미리보기 요청 실패 (${response.status})`)
      const kind = artifactKind(artifact.mime,response.headers.get('Content-Type') ?? '')
      if(kind==='image') {const blob=await response.blob();if(!abort.signal.aborted){objectUrl=URL.createObjectURL(blob);setImageUrl(objectUrl)};return}
      if (kind !== 'html' && kind !== 'code' && kind !== 'markdown') throw new Error('이 형식은 다운로드로 확인하세요.')
      const text = await response.text()
      if (!abort.signal.aborted) {setPreviewKind(kind);setBody(text);if(kind==='html')onTables?.(artifact.artifact_id,htmlTables(text))}
    }).catch(e => {if (!abort.signal.aborted) setError(String(e))})
    return () => {abort.abort();if(objectUrl)URL.revokeObjectURL(objectUrl)}
  },[open,url,artifact.mime,artifact.artifact_id,fetcher,onTables])
  return <figure data-result-id={resultId}><figcaption>{artifact.title || '결과 파일'} · {resultId}</figcaption><a href={url} download>다운로드</a> <button onClick={() => setOpen(v => !v)}>미리보기</button>
    {open && (error ? <p role="alert">{error}</p> : imageUrl ? <img src={imageUrl} alt={artifact.title || '결과 이미지'}/> : body ? <iframe title={artifact.title || '결과 미리보기'} sandbox="" srcDoc={previewKind==='html' ? body : `<pre>${body.replaceAll('&','&amp;').replaceAll('<','&lt;')}</pre>`} /> : <p role="status">불러오는 중</p>)}</figure>
}

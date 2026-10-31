import { useEffect, useMemo, useState } from 'react';
import { Download, ArrowDownUp, FileImage, Table2 } from 'lucide-react';
import { api, text, tableText } from './core';
import { Markdown } from './Markdown';
import { artifactKind, artifactMime, iframeHeight } from './artifactUtils';
import type { ArtifactRef, TablePage, TableRef } from './types';
export function Artifact({artifact,sessionId,canvas=false,onSelect}: {artifact: ArtifactRef;sessionId:string;canvas?:boolean;onSelect?:() => void}) {
  const [file,setFile] = useState<{url:string;mime:string;data:string}>();
  const [error,setError] = useState(''); const [retry,setRetry] = useState(0);
  useEffect(() => {
    const abort = new AbortController(); let objectUrl = '';
    setFile(undefined);setError('');
    void (async () => {
      const response = await fetch(`/harness/artifacts/${encodeURIComponent(artifact.artifact_id)}?${new URLSearchParams({session_id:sessionId})}`,{signal:abort.signal});
      if (!response.ok) throw new Error(`파일을 불러오지 못했습니다 (HTTP ${response.status})`);
      const mime = artifactMime(artifact.mime,response.headers.get('Content-Type') || '');
      const kind = artifactKind(mime);
      const isText = kind === 'html' || kind === 'code' || kind === 'markdown';
      const data = isText ? await response.text() : '';
      const blob = isText ? new Blob([data],{type:`${mime};charset=utf-8`}) : await response.blob();
      if (abort.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob);setFile({url:objectUrl,mime,data});
    })().catch(cause => {if (!abort.signal.aborted) setError(String(cause));});
    return () => {abort.abort();if (objectUrl) URL.revokeObjectURL(objectUrl);};
  },[artifact.artifact_id,artifact.mime,sessionId,retry]);
  const title = artifact.title || '실행 파일';
  const kind = artifactKind(file?.mime);
  return <figure className="artifact"><figcaption>
    {onSelect ? <button className="artifact-title" onClick={onSelect} title="캔버스에서 보기"><FileImage size={16}/>{title}</button> : <span><FileImage size={16}/>{title}</span>}
    <span className="artifact-actions">{onSelect && <button onClick={onSelect}>캔버스</button>}{file && <>{kind === 'html' && <a href={file.url} target="_blank" rel="noopener noreferrer">새 창</a>}<a href={file.url} download={artifact.title || artifact.artifact_id} aria-label="파일 다운로드"><Download size={16}/></a></>}</span>
  </figcaption>
    {error ? <p role="alert">{error} <button onClick={() => setRetry(n => n+1)}>재시도</button></p> : !file ? <p className="muted">파일 불러오는 중…</p> :
      kind === 'html' ? <iframe data-testid="html-artifact" title={title} srcDoc={file.data} sandbox="allow-scripts" style={{height:canvas ? Math.max(720,iframeHeight(title,file.data)) : iframeHeight(title,file.data)}}/> :
      kind === 'code' ? <pre className="artifact-code"><code>{file.data}</code></pre> :
      kind === 'markdown' ? <Markdown>{file.data}</Markdown> :
      kind === 'image' ? <img src={file.url} alt={artifact.title || '웨이퍼 맵 실행 결과'}/> : <a href={file.url} download={artifact.title || artifact.artifact_id}>파일 다운로드</a>}
  </figure>;
}
export function ResultTable({resultId,table,sessionId}: {resultId:string;table:TableRef;sessionId:string}) {
  const [page,setPage] = useState<TablePage>(); const [loading,setLoading] = useState(false); const [error,setError] = useState('');
  const [retry,setRetry] = useState(0); const [sort,setSort] = useState<{column:string;direction:number}>();
  useEffect(() => {
    const abort = new AbortController();setLoading(true);setError('');
    const query = new URLSearchParams({session_id:sessionId,table_id:table.table_id,offset:'0',limit:'100'});
    void api<TablePage>(`/harness/results/${encodeURIComponent(resultId)}?${query}`,{signal:abort.signal})
      .then(data => {if (!abort.signal.aborted) setPage(data);})
      .catch(cause => {if (!abort.signal.aborted) setError(String(cause));})
      .finally(() => {if (!abort.signal.aborted) setLoading(false);});
    return () => abort.abort();
  },[resultId,table.table_id,sessionId,retry]);
  async function more() {
    if (!page || loading) return;setLoading(true);setError('');
    try {
      const query = new URLSearchParams({session_id:sessionId,table_id:table.table_id,offset:String(page.rows.length),limit:'100'});
      const next = await api<TablePage>(`/harness/results/${encodeURIComponent(resultId)}?${query}`);
      setPage(current => current ? {...next,rows:[...current.rows,...next.rows]} : next);
    } catch(cause) {setError(String(cause));} finally {setLoading(false);}
  }
  const rows = useMemo(() => {
    if (!page) return []; if (!sort) return page.rows;
    return [...page.rows].sort((a,b) => {const x = a[sort.column], y = b[sort.column]; return sort.direction * (x == null ? (y == null ? 0 : 1) : y == null ? -1 : typeof x === 'number' && typeof y === 'number' ? x-y : text(x).localeCompare(text(y),'ko',{numeric:true}));});
  },[page,sort]);
  return <section className="result-table"><header><Table2 size={16}/><strong>{table.title}</strong><span className="muted">{page?.total_rows ?? table.total_rows}행</span></header>
    {page && <><div className="table-scroll"><table><thead><tr>{page.columns.map(column => <th key={column} aria-sort={sort?.column === column ? sort.direction === 1 ? 'ascending' : 'descending' : 'none'}><button onClick={() => setSort(current => ({column,direction:current?.column === column ? -current.direction : 1}))}>{column}{page.units[column] ? ` (${page.units[column]})` : ''}<ArrowDownUp size={12}/></button></th>)}</tr></thead><tbody>{rows.map((row,index) => <tr key={index}>{page.columns.map(column => <td key={column}>{tableText(row[column])}</td>)}</tr>)}</tbody></table></div>
    {!rows.length && <p className="muted">조회된 행이 없습니다.</p>}
    <footer><span>{rows.length} / {page.total_rows}행 · 불러온 행 기준 정렬</span>{rows.length < page.total_rows && <button disabled={loading} onClick={() => void more()}>{loading ? '불러오는 중…' : '100행 더 보기'}</button>}</footer>
    {!page.complete && <p className="warning">일부 자료: {page.missing_reason || '완전성 미확인'}</p>}</>}
    {!page && loading && <p className="muted">표 불러오는 중…</p>}
    {error && <p role="alert">{error} <button disabled={loading} onClick={() => page ? void more() : setRetry(n => n+1)}>재시도</button></p>}
  </section>;
}

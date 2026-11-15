import { useRef, useState } from 'react'
import { confirm } from '@/store/confirm'

export function WebSessionRow({label,time,selected,disabled,onSelect,onRename,onArchive,onDelete}:{label:string;time:string;selected:boolean;disabled:boolean;onSelect:()=>void;onRename:(title:string)=>Promise<void>;onArchive:()=>Promise<void>;onDelete:()=>Promise<void>}) {
 const [menu,setMenu]=useState(false),[editing,setEditing]=useState(false)
 const [draft,setDraft]=useState(''),[saving,setSaving]=useState(false),[error,setError]=useState('')
 const inFlight=useRef(false), trigger=useRef<HTMLButtonElement>(null)
 const cancel=() => {setEditing(false);setError('');trigger.current?.focus()}
 const save=async () => {
  if(inFlight.current)return
  const title=draft.trim()
  if(!title){setError('이름을 입력하세요.');return}
  inFlight.current=true;setSaving(true);setError('')
  try {await onRename(title);setEditing(false)}
  catch {setEditing(false);setError('이름을 바꾸지 못했습니다. 다시 시도하세요.')}
  finally {inFlight.current=false;setSaving(false);trigger.current?.focus()}
 }
 const remove=async (action:'archive'|'delete') => {
  if(inFlight.current)return
  inFlight.current=true;setSaving(true);setMenu(false);setError('')
  try {
   if(action==='delete' && !await confirm({title:'대화를 삭제할까요?',description:'이 대화가 목록에서 사라집니다.',destructive:true,confirmLabel:'삭제',cancelLabel:'취소'}))return
   await (action==='delete' ? onDelete() : onArchive())
  }catch {setError(action==='delete' ? '대화를 삭제하지 못했습니다. 다시 시도하세요.' : '대화를 보관하지 못했습니다. 다시 시도하세요.')}
  finally {inFlight.current=false;setSaving(false);trigger.current?.focus()}
 }
 return <div className="web-session" aria-current={selected ? 'page' : undefined}>
  <div className="web-session-content">
   {editing ? <input autoFocus aria-label="대화 이름" aria-invalid={Boolean(error)} maxLength={100} value={draft} disabled={saving}
    onFocus={event => event.target.select()} onChange={event => {setDraft(event.target.value);setError('')}}
    onKeyDown={event => {
     if(event.nativeEvent.isComposing)return
     if(event.key==='Escape' && !saving){event.preventDefault();cancel()}
     if(event.key==='Enter'){event.preventDefault();void save()}
    }}/> : <button className="web-session-select" title={label} aria-label={label} aria-current={selected ? 'page' : undefined} onClick={onSelect}><strong>{label}</strong><small>{time}</small></button>}
   <button ref={trigger} className="web-session-more" aria-label={label+' 메뉴'} aria-haspopup="menu" aria-expanded={menu} disabled={disabled || saving}
    onClick={() => setMenu(value => !value)}>⋯</button>
  </div>
  {menu && <div role="menu" className="web-session-menu" onKeyDown={event => {if(event.key==='Escape'){setMenu(false);trigger.current?.focus()}}}>
   <button disabled={disabled || saving} autoFocus role="menuitem" onClick={() => {setDraft(label);setError('');setEditing(true);setMenu(false)}}>이름 바꾸기</button>
   <button role="menuitem" disabled={disabled || saving} onClick={() => void remove('archive')}>보관</button>
   <button role="menuitem" disabled={disabled || saving} onClick={() => void remove('delete')}>삭제</button>
  </div>}
  {error && <p className="web-session-error" role="alert">{error}</p>}
 </div>
}

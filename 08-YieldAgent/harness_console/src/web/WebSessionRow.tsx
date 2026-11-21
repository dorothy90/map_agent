import { useEffect, useRef, useState } from 'react'
import { Check, Ellipsis, X } from 'lucide-react'
import { confirm } from '@/store/confirm'

export function WebSessionRow({label,selected,disabled,onSelect,onRename,onArchive,onDelete}:{label:string;selected:boolean;disabled:boolean;onSelect:()=>void;onRename:(title:string)=>Promise<void>;onArchive:()=>Promise<void>;onDelete:()=>Promise<void>}) {
 const [menu,setMenu]=useState(false),[editing,setEditing]=useState(false)
 const [draft,setDraft]=useState(''),[saving,setSaving]=useState(false),[error,setError]=useState('')
 const inFlight=useRef(false), trigger=useRef<HTMLButtonElement>(null)
 const row=useRef<HTMLDivElement>(null)
 useEffect(() => {
  if(!menu)return
  const dismiss=(event:PointerEvent) => {
   if(!row.current?.contains(event.target as Node))setMenu(false)
  }
  document.addEventListener('pointerdown',dismiss)
  return () => document.removeEventListener('pointerdown',dismiss)
 },[menu])
 const restoreTrigger=() => requestAnimationFrame(() => trigger.current?.focus())
 const cancel=() => {setEditing(false);setError('');restoreTrigger()}
 const save=async () => {
  if(inFlight.current)return
  const title=draft.trim()
  if(!title){setError('이름을 입력하세요.');return}
  inFlight.current=true;setSaving(true);setError('')
  try {await onRename(title);setEditing(false)}
  catch {setEditing(false);setError('이름을 바꾸지 못했습니다. 다시 시도하세요.')}
  finally {inFlight.current=false;setSaving(false);restoreTrigger()}
 }
 const remove=async (action:'archive'|'delete') => {
  if(inFlight.current)return
  inFlight.current=true;setSaving(true);setMenu(false);setError('')
  try {
   if(action==='delete' && !await confirm({title:'대화를 삭제할까요?',description:'이 대화가 목록에서 사라집니다.',destructive:true,confirmLabel:'삭제',cancelLabel:'취소'}))return
   await (action==='delete' ? onDelete() : onArchive())
  }catch {setError(action==='delete' ? '대화를 삭제하지 못했습니다. 다시 시도하세요.' : '대화를 보관하지 못했습니다. 다시 시도하세요.')}
  finally {inFlight.current=false;setSaving(false);restoreTrigger()}
 }
 return <div ref={row} className="web-session" onBlur={event => {if(!event.currentTarget.contains(event.relatedTarget))setMenu(false)}} aria-current={selected ? 'page' : undefined}>
  <div className="web-session-content">
   {editing ? <input autoFocus aria-label="대화 이름" aria-invalid={Boolean(error)} maxLength={100} value={draft} disabled={saving}
    onFocus={event => event.target.select()} onChange={event => {setDraft(event.target.value);setError('')}}
    onKeyDown={event => {
     if(event.nativeEvent.isComposing)return
     if(event.key==='Escape' && !saving){event.preventDefault();cancel()}
     if(event.key==='Enter'){event.preventDefault();void save()}
    }}/> : <button className="web-session-select" title={label} aria-label={label} aria-current={selected ? 'page' : undefined} onClick={onSelect}><strong>{label}</strong></button>}
   {editing && <>
    <button className="web-row-icon" aria-label="이름 저장" disabled={saving} onClick={() => void save()}><Check size={15}/></button>
    <button className="web-row-icon" aria-label="이름 수정 취소" disabled={saving} onClick={cancel}><X size={15}/></button>
   </>}
   <button ref={trigger} hidden={editing} className="web-row-icon web-session-more" aria-label={label+' 메뉴'} aria-haspopup="menu" aria-expanded={menu} disabled={disabled || saving}
    onKeyDown={event => {if(event.key==='ArrowDown'){event.preventDefault();setMenu(true)}}}
    onClick={() => setMenu(value => !value)}><Ellipsis size={16}/></button>
  </div>
  {menu && <div role="menu" aria-label={label+' 메뉴'} className="web-session-menu" onKeyDown={event => {
   if(event.key==='Escape'){event.preventDefault();setMenu(false);trigger.current?.focus();return}
   const keys=['ArrowDown','ArrowUp','Home','End']
   if(!keys.includes(event.key))return
   event.preventDefault()
   const items=Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button'))
   const index=items.indexOf(document.activeElement as HTMLButtonElement)
   const next=event.key==='Home' ? 0 : event.key==='End' ? items.length-1 : (index+(event.key==='ArrowDown' ? 1 : -1)+items.length)%items.length
   items[next]?.focus()
  }}>
   <button disabled={disabled || saving} autoFocus role="menuitem" onClick={() => {setDraft(label);setError('');setEditing(true);setMenu(false)}}>이름 바꾸기</button>
   <button role="menuitem" disabled={disabled || saving} onClick={() => void remove('archive')}>보관</button>
   <button role="menuitem" disabled={disabled || saving} onClick={() => void remove('delete')}>삭제</button>
  </div>}
  {error && <p className="web-session-error" role="alert">{error}</p>}
 </div>
}

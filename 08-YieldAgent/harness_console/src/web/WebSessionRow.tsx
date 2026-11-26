import { type ComponentPropsWithoutRef, type RefObject, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Check, Ellipsis, X } from 'lucide-react'
import { AnimatePresence, motion, usePresence } from 'motion/react'
import { confirm } from '@/store/confirm'

function SessionMenu({ menuRef, ...props }: ComponentPropsWithoutRef<'div'> & { menuRef: RefObject<HTMLDivElement | null> }) {
 const [present, remove] = usePresence()

 useLayoutEffect(() => {
  if (present) return
  const exit = menuRef.current?.getAnimations?.().find(animation =>
   'animationName' in animation && animation.animationName === 'yagent-popover-out'
  )
  if (!exit) {
   remove?.()
   return
  }

  // A reopened menu can cancel the exit animation while retaining its DOM node.
  let cancelled = false
  const finish = () => { if (!cancelled) remove?.() }
  void exit.finished.then(finish, finish)
  return () => { cancelled = true }
 }, [menuRef, present, remove])

 return <div {...props} ref={menuRef} className="web-session-menu yagent-popover"
  data-state={present ? 'open' : 'closed'} aria-hidden={!present || undefined} inert={!present}/>
}

export function WebSessionRow({label,selected,disabled,archived,onSelect,onRename,onArchive,onDelete}:{label:string;selected:boolean;disabled:boolean;archived?:boolean;onSelect:()=>void;onRename:(title:string)=>Promise<void>;onArchive:()=>Promise<void>;onDelete:()=>Promise<void>}) {
 const [menu,setMenu]=useState(false),[editing,setEditing]=useState(false)
 const [draft,setDraft]=useState(''),[saving,setSaving]=useState(false),[error,setError]=useState('')
 const inFlight=useRef(false), trigger=useRef<HTMLButtonElement>(null)
 const row=useRef<HTMLDivElement>(null)
 const titleInput=useRef<HTMLInputElement>(null)
 const menuRef=useRef<HTMLDivElement>(null), keyboardMenu=useRef(false)
 useLayoutEffect(() => {
  if(!editing)return
  titleInput.current?.focus({preventScroll:true})
  titleInput.current?.select()
 },[editing])
 useLayoutEffect(() => {
  const popup=menuRef.current
  const anchor=trigger.current
  if(!menu || !popup || !anchor)return
  const list=anchor.closest('.web-session-list')?.getBoundingClientRect()
  const bounds=anchor.getBoundingClientRect()
  const height=popup.offsetHeight+8
  popup.dataset.side=list && list.bottom-bounds.bottom<height && bounds.top-list.top>list.bottom-bounds.bottom ? 'top' : 'bottom'
  const focusTarget=keyboardMenu.current ? popup.querySelector<HTMLButtonElement>('button:not(:disabled)') : popup
  focusTarget?.focus({preventScroll:true})
 },[menu])
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
  }catch {setError(action==='delete' ? '대화를 삭제하지 못했습니다. 다시 시도하세요.' : archived ? '보관을 해제하지 못했습니다. 다시 시도하세요.' : '대화를 보관하지 못했습니다. 다시 시도하세요.')}
  finally {inFlight.current=false;setSaving(false);restoreTrigger()}
 }
 return <div ref={row} className="web-session" onBlur={event => {if(!event.currentTarget.contains(event.relatedTarget))setMenu(false)}} aria-current={selected ? 'page' : undefined}>
  {selected && <motion.span layoutId="web-sidebar-active" className="web-sidebar-active-pill" transition={{ type: 'spring', stiffness: 480, damping: 38 }} />}
  <div className="web-session-content">
   {editing ? <input ref={titleInput} aria-label="대화 이름" aria-invalid={Boolean(error)} maxLength={100} value={draft} disabled={saving}
    onChange={event => {setDraft(event.target.value);setError('')}}
    onKeyDown={event => {
     if(event.nativeEvent.isComposing)return
     if(event.key==='Escape' || event.key==='Enter'){
      event.preventDefault()
      event.stopPropagation()
      if(!saving)event.key==='Escape' ? cancel() : void save()
     }
    }}/> : <button className="web-session-select" title={label} aria-label={label} aria-current={selected ? 'page' : undefined} onClick={onSelect}><strong>{label}</strong></button>}
   {editing && <>
    <button className="web-row-icon" aria-label="이름 저장" disabled={saving} onClick={() => void save()}><Check size={15}/></button>
    <button className="web-row-icon" aria-label="이름 수정 취소" disabled={saving} onClick={cancel}><X size={15}/></button>
   </>}
   <button ref={trigger} hidden={editing} className="web-row-icon web-session-more" aria-label={label+' 메뉴'} aria-haspopup="menu" aria-expanded={menu} disabled={disabled || saving}
    onKeyDown={event => {if(event.key==='ArrowDown'){event.preventDefault();keyboardMenu.current=true;setMenu(true)}}}
    onClick={event => {keyboardMenu.current=event.detail===0;setMenu(value => !value)}}><Ellipsis size={16}/></button>
  </div>
  <AnimatePresence>
   {menu && <SessionMenu menuRef={menuRef} role="menu" tabIndex={-1} aria-label={label+' 메뉴'}
    onKeyDown={event => {
    if(event.key==='Escape'){event.preventDefault();setMenu(false);trigger.current?.focus();return}
    const keys=['ArrowDown','ArrowUp','Home','End']
    if(!keys.includes(event.key))return
    event.preventDefault()
    const items=Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button'))
    const index=items.indexOf(document.activeElement as HTMLButtonElement)
    const next=event.key==='Home' ? 0 : event.key==='End' ? items.length-1 : index<0
     ? event.key==='ArrowDown' ? 0 : items.length-1
     : (index+(event.key==='ArrowDown' ? 1 : -1)+items.length)%items.length
    items[next]?.focus()
   }}>
    <button type="button" disabled={disabled || saving} role="menuitem" onClick={() => {setDraft(label);setError('');setEditing(true);setMenu(false)}}><span className="web-session-icon" data-icon="rename" aria-hidden="true"/>이름 바꾸기</button>
    <button type="button" role="menuitem" disabled={disabled || saving} onClick={() => void remove('archive')}><span className="web-session-icon" data-icon="archive" aria-hidden="true"/>{archived ? '보관 해제' : '보관'}</button>
    <button type="button" role="menuitem" className="web-session-menu-delete" disabled={disabled || saving} onClick={() => void remove('delete')}><span className="web-session-icon" data-icon="delete" aria-hidden="true"/>삭제</button>
   </SessionMenu>}
  </AnimatePresence>
  {error && <p className="web-session-error" role="alert">{error}</p>}
 </div>
}

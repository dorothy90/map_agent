import { useEffect, useEffectEvent, useRef, useState, type ReactNode } from 'react'

export const chatFileLimit = 24 * 1024
const extensions = '.txt,.md,.csv,.tsv,.json,.log'
const validExtension = /\.(txt|md|csv|tsv|json|log)$/i
export type ChatFile = { name: string; text: string; size: number }

export function ChatFiles({ files, onChange, disabled, renderOptions }: {
 files: ChatFile[]
 onChange: (update: (files: ChatFile[]) => ChatFile[]) => void
 disabled: boolean
 renderOptions: (options: { onPickFile: () => void; fileDisabled: boolean; notice: string }) => ReactNode
}) {
 const input = useRef<HTMLInputElement>(null)
 const [working, setWorking] = useState(false)
 const [notice, setNotice] = useState('')
 useEffect(() => {
  if (!notice) return
  const timer = window.setTimeout(() => setNotice(''), 2500)
  return () => window.clearTimeout(timer)
 }, [notice])
 const available = chatFileLimit - files.reduce((total, file) => total + file.size, 0)
 const add = async (read: () => Promise<ChatFile[]>) => {
  setNotice('')
  setWorking(true)
  try {
   const next = await read()
   if (next.reduce((total, file) => total + file.size, 0) > available) throw new Error('첨부는 한 메시지에 총 24KB까지 가능해요.')
   onChange(current => [...current, ...next])
   setNotice(next.length === 1 ? '파일을 첨부했어요' : `${next.length}개 파일을 첨부했어요`)
  } catch (error) {
   setNotice(error instanceof Error ? error.message : '파일을 읽지 못했어요.')
  } finally {
   setWorking(false)
  }
 }
 const attach = async (selected: File[]) => {
  if (selected.some(file => !validExtension.test(file.name))) throw new Error('TXT, MD, CSV, TSV, JSON, LOG 파일을 첨부할 수 있어요.')
  if (selected.reduce((total, file) => total + file.size, 0) > available) throw new Error('첨부는 한 메시지에 총 24KB까지 가능해요.')
  return Promise.all(selected.map(async file => ({
   name: file.name, size: file.size,
   text: new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer()),
  })))
 }
 const addSelected = useEffectEvent((selected: File[]) => {
  if (selected.length && !disabled && !working) void add(() => attach(selected))
 })
 useEffect(() => {
  const chat = input.current?.closest('.hermes-web-chat')
  if (!chat) return
  const onDragOver = (event: Event) => {
   const drag = event as DragEvent
   if (!drag.dataTransfer?.types.includes('Files')) return
   drag.preventDefault()
   drag.stopPropagation()
   drag.dataTransfer.dropEffect = disabled || working ? 'none' : 'copy'
  }
  const onDrop = (event: Event) => {
   const drop = event as DragEvent
   if (!drop.dataTransfer?.types.includes('Files')) return
   drop.preventDefault()
   drop.stopPropagation()
   addSelected(Array.from(drop.dataTransfer.files))
  }
  chat.addEventListener('dragover', onDragOver, true)
  chat.addEventListener('drop', onDrop, true)
  return () => {
   chat.removeEventListener('dragover', onDragOver, true)
   chat.removeEventListener('drop', onDrop, true)
  }
 }, [disabled, working])
 return <div className="web-chat-files">
  <input type="file" ref={input} accept={extensions} multiple hidden onChange={event => {
   const selected = Array.from(event.currentTarget.files ?? [])
   event.currentTarget.value = ''
   if (selected.length && !disabled && !working) void add(() => attach(selected))
  }}/>
  {renderOptions({ onPickFile: () => input.current?.click(), fileDisabled: disabled || working, notice })}
  {files.length > 0 && <div className="web-chat-file-chips">{files.map((file, index) => (
   <button type="button" className="web-chat-file-chip" key={index} disabled={disabled || working} aria-label={file.name + ' 제거'}
    onClick={() => onChange(rows => rows.filter((_, i) => i !== index))}>{file.name} ×</button>
  ))}</div>}
  <span className="sr-only" role="status">{notice}</span>
 </div>
}

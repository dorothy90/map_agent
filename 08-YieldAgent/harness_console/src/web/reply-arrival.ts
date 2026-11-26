import { useEffect, useRef, useState } from 'react'
import { chatMessageText, type ChatMessage } from '@/lib/chat-messages'

function latestReply(messages: readonly ChatMessage[]) {
 for (let i=messages.length-1;i>=0;i--) {
  const message=messages[i]
  if (message.role==='user') break
  if (message.role!=='assistant' || message.pending || message.interim || message.hidden || message.interrupted || message.error) continue
  const text=chatMessageText(message).trim()
  if (text) return {id:message.id,text}
 }
 return null
}

export function useReplyArrival(messages: readonly ChatMessage[], busy: boolean, sessionId: string | null) {
 const [arrival,setArrival]=useState<{id:string;text:string;sessionId:string|null}|null>(null)
 const turn=useRef<{sessionId:string|null;baseline:ReturnType<typeof latestReply>}|null>(null)

 useEffect(() => {
  if (busy) {
   if (!turn.current || (turn.current.sessionId && turn.current.sessionId!==sessionId)) {
    turn.current={sessionId,baseline:latestReply(messages)}
    setArrival(null)
   } else if (!turn.current.sessionId) {
    turn.current.sessionId=sessionId
   }
   return
  }
  if (!turn.current) return
  if (turn.current.sessionId && turn.current.sessionId!==sessionId) {
   turn.current=null
   return
  }
  const reply=latestReply(messages)
  if (reply && (reply.id!==turn.current.baseline?.id || reply.text!==turn.current.baseline?.text)) {
   setArrival({...reply,sessionId})
   turn.current=null
  }
 },[messages,busy,sessionId])

 return arrival?.sessionId===sessionId ? arrival : null
}

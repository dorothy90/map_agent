import { createContext, useContext, type PropsWithChildren } from 'react'
const WebPresentation = createContext(false)
const NewReply = createContext<string | undefined>(undefined)
export const useWebPresentation = () => useContext(WebPresentation)
export const useNewWebReply = () => useContext(NewReply)
export function WebPresentationProvider({children,newReplyId}:PropsWithChildren<{newReplyId?:string}>) {
 return <WebPresentation.Provider value={true}><NewReply.Provider value={newReplyId}>{children}</NewReply.Provider></WebPresentation.Provider>
}

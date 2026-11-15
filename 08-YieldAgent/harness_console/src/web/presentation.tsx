import { createContext, useContext, type PropsWithChildren } from 'react'
const WebPresentation = createContext(false)
export const useWebPresentation = () => useContext(WebPresentation)
export function WebPresentationProvider({children}:PropsWithChildren) {
 return <WebPresentation.Provider value={true}>{children}</WebPresentation.Provider>
}

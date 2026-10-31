import { createRoot } from 'react-dom/client'
import { HermesWebApp, type HermesWebOptions } from './HermesWebApp'
import '../hermes/styles.css'
import './web.css'
declare global { interface Window { hermesWebOptions?: HermesWebOptions } }
export async function mount() {
 let options=window.hermesWebOptions ?? {}
 if (import.meta.env.DEV && !options.resolver) options=await import('./dev-connection').then(module => module.devConnectionOptions())
 createRoot(document.getElementById('root')!).render(<HermesWebApp {...options}/>)
}
